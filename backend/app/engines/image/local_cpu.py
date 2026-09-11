"""Local CPU image adapter — real pixel operations, no external weights.

Implements every image mode in the platform using the deterministic renderer
plus Pillow/numpy image processing. It is honest about what it is: assets carry
`engine: local-cpu-image`, `diffusion: false`, `deterministic: true`.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from app.core.errors import StudioError
from app.core.logging import get_logger
from app.engines.base import (
    Artifact,
    BaseModelAdapter,
    Capabilities,
    Estimate,
    GenerateRequest,
    JobContext,
    ValidationResult,
)
from app.engines.image.render import PALETTES, analyze_prompt, hash_seed, render_scene
from app.media import image_ops

log = get_logger("engines.image.local_cpu")

MODES = [
    "text-to-image", "image-to-image", "image-edit", "inpaint", "outpaint",
    "object-removal", "object-replacement", "background-remove", "background-replace",
    "background-generate", "upscale", "face-restore", "colorize", "sketch-to-image",
    "depth-to-image", "edge-to-image", "pose-to-image", "reference-to-image",
    "multi-reference-image", "style-transfer", "variations", "enhance",
]


def _first_image(request: GenerateRequest) -> Optional[Image.Image]:
    for ref in request.references:
        if ref.kind == "image" and ref.role != "mask" and Path(ref.path).exists():
            return image_ops.to_rgb(image_ops.load(ref.path))
    return None


def _mask(request: GenerateRequest, size: tuple[int, int]) -> Optional[Image.Image]:
    for ref in request.references:
        if ref.role == "mask" and Path(ref.path).exists():
            return image_ops.load(ref.path).convert("L").resize(size)
    box = request.params.get("mask_box")
    if isinstance(box, (list, tuple)) and len(box) == 4:
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).ellipse(
            [float(box[0]) * size[0], float(box[1]) * size[1], float(box[2]) * size[0], float(box[3]) * size[1]],
            fill=255,
        )
        return mask.filter(ImageFilter.GaussianBlur(max(1.0, size[0] / 300)))
    return None


def _size(request: GenerateRequest, base: Optional[Image.Image]) -> tuple[int, int]:
    explicit_w = request.params.get("width")
    explicit_h = request.params.get("height")
    if explicit_w and explicit_h:
        return int(explicit_w), int(explicit_h)
    if base is not None and not request.params.get("force_aspect"):
        return base.size
    return image_ops.resolve_size(request.params.get("aspect", "1:1"), request.params.get("resolution", "1024"))


class LocalCPUImageAdapter(BaseModelAdapter):
    id = "local-cpu-image"
    display_name = "Local CPU Image Engine (deterministic)"
    version = "1.0.0"
    family = "image"
    provider = "local"
    license = "Apache-2.0 (platform code)"
    size_mb = 0
    vram_mb = 0
    speed = "fast"
    is_local = True
    ready_out_of_the_box = True
    notes = (
        "Deterministic CPU renderer + real image processing. NOT a diffusion model: "
        "use it offline or for testing; install a diffusion adapter for neural generation."
    )
    capabilities = Capabilities(
        family="image",
        modes=MODES,
        max_resolution="2048p",
        supports_negative_prompt=True,
        supports_seed=True,
        supports_reference_images=True,
        supports_multi_reference_weights=True,
        supports_mask=True,
        supports_character=True,
        supports_style=True,
        supports_upscale=True,
        max_references=8,
        notes="Runs on any CPU. Deterministic for a given seed.",
    )

    # ------------------------------------------------------------------ #

    def param_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "aspect": {"type": "string", "enum": list(image_ops.ASPECTS.keys()), "default": "16:9"},
                "resolution": {"type": "string", "enum": ["512", "768", "1024", "1536", "2048"], "default": "1024"},
                "style": {"type": "string", "default": "cinematic"},
                "lighting": {"type": "string", "enum": sorted(PALETTES.keys()), "default": "cinematic"},
                "camera": {"type": "string", "default": "wide"},
                "strength": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.65},
                "guidance": {"type": "number", "minimum": 0, "maximum": 20, "default": 7.5},
                "steps": {"type": "integer", "minimum": 1, "maximum": 150, "default": 30},
                "upscale_factor": {"type": "integer", "enum": [1, 2, 3, 4], "default": 2},
                "count": {"type": "integer", "minimum": 1, "maximum": 8, "default": 1},
            },
        }

    def validate(self, request: GenerateRequest) -> ValidationResult:
        result = ValidationResult()
        needs_source = request.mode in {
            "image-to-image", "image-edit", "inpaint", "outpaint", "object-removal",
            "object-replacement", "background-remove", "background-replace", "background-generate",
            "upscale", "face-restore", "colorize", "sketch-to-image", "depth-to-image",
            "edge-to-image", "pose-to-image", "style-transfer", "variations", "enhance",
        }
        if needs_source and _first_image(request) is None:
            result.add_error("This mode needs a source image. Attach one in the Reference panel.")
        if request.mode in ("inpaint", "object-removal", "object-replacement"):
            if _mask(request, (64, 64)) is None:
                result.add_warning(
                    "No mask provided — a centred elliptical mask will be used. "
                    "Paint a mask for precise control."
                )
        if request.mode == "style-transfer" and len([r for r in request.references if r.kind == "image"]) < 2:
            result.add_error("Style transfer needs both a content image and a style reference.")
        self._validate_prompt(request, result)
        return result

    def estimate(self, request: GenerateRequest) -> Estimate:
        base = 0.6
        n = max(1, int(request.params.get("count", 1)))
        w, h = _size(request, None)
        pixels = w * h / (1024 * 1024)
        seconds = base + 0.9 * pixels * n + (2.4 * n if request.mode == "upscale" else 0)
        return Estimate(seconds=round(seconds, 2), vram_mb=0, credits=round(max(n, 1) * 0.5, 2),
                        notes="CPU renderer estimate")

    # ------------------------------------------------------------------ #

    def generate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        self._require_mode(request)
        ctx.progress(5, "preparing")
        source = _first_image(request)
        width, height = _size(request, source)
        seed = request.seed
        count = max(1, min(8, int(request.params.get("count", 1))))
        lighting = request.params.get("lighting") or analyze_prompt(request.prompt)["lighting"]
        camera = request.params.get("camera", "wide")
        style = request.params.get("style", "cinematic")
        artifacts: list[Artifact] = []

        for i in range(count):
            ctx.check_cancelled()
            image = self._render_one(request, ctx, source, width, height, seed + i * 1013,
                                     lighting=lighting, camera=camera, style=style, index=i)
            name = f"image_{ctx.job_id}_{i}.png" if count > 1 else f"image_{ctx.job_id}.png"
            out = image_ops.save(image, ctx.path(name))
            artifacts.append(
                Artifact(
                    path=out,
                    kind="image",
                    mime="image/png",
                    name=Path(name).stem,
                    meta={
                        "engine": self.id,
                        "engine_name": self.display_name,
                        "mode": request.mode,
                        "deterministic": True,
                        "diffusion": False,
                        "seed": seed + i * 1013,
                        "width": image.width,
                        "height": image.height,
                        "prompt": request.prompt,
                        "negative_prompt": request.negative_prompt,
                        "params": request.params,
                        "lighting": lighting,
                        "camera": camera,
                        "style": style,
                    },
                )
            )
            ctx.progress(10 + (90 * (i + 1) / count), f"rendered {i + 1}/{count}")
        return artifacts

    # -- mode dispatch -------------------------------------------------- #

    def _render_one(self, request: GenerateRequest, ctx: JobContext, source: Optional[Image.Image],
                    width: int, height: int, seed: int, *, lighting: str, camera: str, style: str,
                    index: int) -> Image.Image:
        mode = request.mode
        strength = float(request.params.get("strength", 0.65))
        ctx.progress(25, "rendering")

        def scene() -> Image.Image:
            return render_scene(
                request.prompt or request.params.get("background_prompt", "cinematic scene"),
                width=width, height=height, seed=seed, lighting=lighting, camera=camera, style=style,
                negative_prompt=request.negative_prompt,
                character=request.character,
                reference_images=[r.path for r in request.references if r.kind == "image"],
            )

        if mode == "text-to-image":
            return scene()

        if source is None:
            raise StudioError("A source image is required for this mode.",
                              suggested_action="Attach a source image in the Reference panel.")

        if mode == "image-to-image":
            base = image_ops.resize(source, width, height, keep_aspect=False)
            generated = scene()
            return Image.blend(base, image_ops.resize(generated, width, height, keep_aspect=False),
                               float(np.clip(strength, 0, 1)))

        if mode in ("image-edit", "enhance"):
            return self._edit(source, request, width, height, scene)

        if mode == "inpaint":
            mask = _mask(request, source.size) or self._default_mask(source.size)
            return image_ops.inpaint(source, mask, radius=int(request.params.get("inpaint_radius", 9)))

        if mode == "object-removal":
            mask = _mask(request, source.size) or self._default_mask(source.size)
            return image_ops.inpaint(source, mask, radius=12)

        if mode == "object-replacement":
            mask = _mask(request, source.size) or self._default_mask(source.size)
            generated = image_ops.resize(scene(), *source.size, keep_aspect=False)
            soft = mask.filter(ImageFilter.GaussianBlur(max(1.0, source.width / 260)))
            return Image.composite(generated, source, soft)

        if mode == "outpaint":
            pad = int(request.params.get("padding", max(32, min(width, height) // 6)))
            side = request.params.get("outpaint_side", "all")
            expanded = image_ops.outpaint(
                source,
                left=pad if side in ("all", "left") else 0,
                right=pad if side in ("all", "right") else 0,
                top=pad if side in ("all", "top") else 0,
                bottom=pad if side in ("all", "bottom") else 0,
            )
            return image_ops.resize(expanded, width, height, keep_aspect=False)

        if mode == "background-remove":
            return image_ops.remove_background(
                source, tolerance=float(request.params.get("tolerance", 0.28))
            )

        if mode in ("background-replace", "background-generate"):
            bg_prompt = request.params.get("background_prompt") or request.prompt or "modern office interior"
            bg = render_scene(bg_prompt, width=width, height=height, seed=seed + 7,
                              lighting=lighting, camera=camera, style=style)
            return image_ops.replace_background(source, bg)

        if mode == "upscale":
            factor = int(request.params.get("upscale_factor", 2))
            return image_ops.upscale(source, factor=factor, sharpen=request.params.get("sharpen", True) is not False)

        if mode == "face-restore":
            return image_ops.face_restore(source, strength=strength or 0.8)

        if mode == "colorize":
            return image_ops.colorize(source, tint=float(request.params.get("tint", 0.85)))

        if mode == "sketch-to-image":
            guide = image_ops.sketch(source) if request.params.get("guide_from_source", True) else scene()
            return self._guided(guide, scene(), structure=0.35, color=0.75)

        if mode == "depth-to-image":
            guide = image_ops.depth_map(source)
            return self._guided(guide, scene(), structure=0.30, color=0.85)

        if mode == "edge-to-image":
            guide = image_ops.edges(source)
            return self._guided(guide, scene(), structure=0.42, color=0.7)

        if mode == "pose-to-image":
            joints = request.params.get("joints")
            guide = image_ops.pose_skeleton(source, joints=[tuple(j) for j in joints] if joints else None)
            return self._guided(guide, scene(), structure=0.28, color=0.8)

        if mode in ("reference-to-image", "multi-reference-image"):
            return self._multi_reference(request, scene, width, height, strength)

        if mode == "style-transfer":
            images = [r for r in request.references if r.kind == "image" and Path(r.path).exists()]
            if len(images) < 2:
                raise StudioError("Style transfer needs a content image and a style image.",
                                  suggested_action="Attach both images.")
            content = image_ops.to_rgb(image_ops.load(images[0].path))
            style_img = image_ops.to_rgb(image_ops.load(images[1].path))
            return image_ops.style_transfer(content, style_img, strength=strength)

        if mode == "variations":
            jitter = render_scene(request.prompt, width=width, height=height, seed=seed + index * 7919,
                                  lighting=lighting, camera=camera, style=style)
            return Image.blend(image_ops.resize(source, width, height, keep_aspect=False), jitter, 0.55)

        raise StudioError(f"Unsupported image mode: {mode}.",
                          suggested_action="Choose another mode or model.")

    # -- helpers ---------------------------------------------------------- #

    def _default_mask(self, size: tuple[int, int]) -> Image.Image:
        w, h = size
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).ellipse([w * 0.32, h * 0.28, w * 0.68, h * 0.72], fill=255)
        return mask.filter(ImageFilter.GaussianBlur(max(2.0, w / 120)))

    def _guided(self, guide: Image.Image, generated: Image.Image, *, structure: float, color: float) -> Image.Image:
        """Blend a conditioning map's structure with a rendered scene's colour."""
        g = image_ops.resize(guide, *generated.size, keep_aspect=False)
        lum_g = np.asarray(ImageOps.grayscale(image_ops.to_rgb(g)), dtype=np.float32) / 255.0
        arr = np.asarray(generated, dtype=np.float32)
        lum_c = np.asarray(ImageOps.grayscale(generated), dtype=np.float32) / 255.0
        shaped = np.clip(lum_c * (1 - structure) + lum_g * structure, 0, 1)
        mean_c = arr.mean(axis=(0, 1), keepdims=True)
        out = mean_c * (1 - color) / 255.0 + 0
        target = (shaped[..., None] * 255.0)
        tinted = np.clip(target * (mean_c / 255.0 * 1.35) + mean_c * 0.25, 0, 255)
        return Image.fromarray(np.clip(arr * (1 - color) + tinted * color, 0, 255).astype(np.uint8), "RGB")

    def _multi_reference(self, request: GenerateRequest, scene_callable, width: int, height: int,
                         strength: float) -> Image.Image:
        refs = [r for r in request.references if r.kind == "image" and Path(r.path).exists()]
        if not refs:
            return scene_callable()
        total = sum(max(0.0, float(r.weight or 1.0)) for r in refs) or 1.0
        acc = np.zeros((height, width, 3), dtype=np.float32)
        for ref in refs:
            img = image_ops.resize(image_ops.to_rgb(image_ops.load(ref.path)), width, height, keep_aspect=False)
            acc += np.asarray(img, dtype=np.float32) * (float(ref.weight or 1.0) / total)
        blended = Image.fromarray(np.clip(acc, 0, 255).astype(np.uint8), "RGB")
        generated = scene_callable()
        return Image.blend(blended, image_ops.resize(generated, width, height, keep_aspect=False),
                           float(np.clip(strength, 0, 1)))

    def _edit(self, source: Image.Image, request: GenerateRequest, width: int, height: int,
              scene_callable) -> Image.Image:
        """Instruction-driven edits parsed from the prompt (real operations)."""
        prompt = (request.prompt or "").lower()
        img = image_ops.resize(source, width, height, keep_aspect=False)
        strength = float(request.params.get("strength", 0.65))

        if "bright" in prompt or "exposure" in prompt:
            img = ImageEnhance.Brightness(img).enhance(1.0 + 0.45 * strength)
        if "dark" in prompt or "dramatic" in prompt or "moody" in prompt:
            img = ImageEnhance.Brightness(img).enhance(max(0.35, 1.0 - 0.4 * strength))
        if "contrast" in prompt or "punchy" in prompt:
            img = ImageEnhance.Contrast(img).enhance(1.0 + 0.5 * strength)
        if "saturat" in prompt or "vivid" in prompt or "colorful" in prompt:
            img = ImageEnhance.Color(img).enhance(1.0 + 0.6 * strength)
        if "desaturat" in prompt or "muted" in prompt or "black and white" in prompt or "monochrome" in prompt:
            img = ImageEnhance.Color(img).enhance(max(0.0, 1.0 - 0.95 * strength))
        if "warm" in prompt or "golden" in prompt or "sunset" in prompt:
            r, g, b = img.split()
            img = Image.merge("RGB", (r.point(lambda v: min(255, int(v * 1.14))), g.point(lambda v: int(v * 1.03)),
                                      b.point(lambda v: int(v * 0.90))))
        if "cool" in prompt or "blue" in prompt or "night" in prompt:
            r, g, b = img.split()
            img = Image.merge("RGB", (r.point(lambda v: int(v * 0.92)), g.point(lambda v: int(v * 0.98)),
                                      b.point(lambda v: min(255, int(v * 1.12)))))
        if "sharp" in prompt or "crisp" in prompt or "detail" in prompt:
            img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=int(120 * strength), threshold=2))
        if "soft" in prompt or "dreamy" in prompt or "blur background" in prompt:
            img = Image.blend(img, img.filter(ImageFilter.GaussianBlur(6 * strength)), 0.6)
        if "background" in prompt:
            bg = scene_callable()
            img = image_ops.replace_background(img, image_ops.resize(bg, width, height, keep_aspect=False))
        if "vignette" in prompt or "cinematic" in prompt:
            img = image_ops.vignette(img, strength=0.35)
        if "grain" in prompt or "film" in prompt:
            img = image_ops.add_grain(img, amount=0.05 * strength, seed=request.seed)
        if "rotate" in prompt:
            img = img.rotate(float(request.params.get("rotate", 0)), expand=True)
        if "flip" in prompt or "mirror" in prompt:
            img = ImageOps.mirror(img)
        return img


class LocalCPUUpscaleAdapter(LocalCPUImageAdapter):
    """Specialised upscaler entry so Model Manager can show it separately."""

    id = "local-cpu-upscale"
    display_name = "Local CPU Upscaler (Lanczos + sharpen)"
    version = "1.0.0"
    family = "upscale"
    capabilities = Capabilities(
        family="upscale", modes=["upscale", "enhance", "face-restore"],
        max_resolution="4096p", supports_seed=False, supports_upscale=True,
        notes="Deterministic resampling. Real-ESRGAN adapter supersedes it when installed.",
    )
    notes = "Non-neural upscaler. Install Real-ESRGAN for neural super-resolution."

    def param_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "upscale_factor": {"type": "integer", "enum": [2, 3, 4], "default": 2},
                "sharpen": {"type": "boolean", "default": True},
            },
        }


ADAPTERS = [LocalCPUImageAdapter(), LocalCPUUpscaleAdapter()]
