"""Procedural image adapter - genuinely executes on CPU.

Capabilities that really work here:
  text_to_image, image_to_image, inpainting, outpainting (canvas extension),
  background_replace (alpha/mask compositing), style_transfer, sketch_to_image,
  reference_to_image / multi_reference_image, palette_transfer

Honest limits are stated in `description` and stamped into every asset's
provenance metadata: this is seeded procedural art used to validate and run the
whole pipeline on machines with no GPU. It is not a diffusion model.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

from app.adapters.base import (
    GeneratedFile,
    GenerationRequest,
    GenerationResult,
    ImageModelAdapter,
    ProgressCallback,
    timed,
)
from app.services.procedural_render import (
    CharacterIdentity,
    build_identity,
    draw_character,
    fbm,
    render_scene,
    seed_from_text,
)
from app.core.errors import ErrorCode, StudioError


def _out(req: GenerationRequest, name: str) -> str:
    d = Path(req.output_dir)
    d.mkdir(parents=True, exist_ok=True)
    return str(d / name)


def _dominant_palette(img: Image.Image, k: int = 5) -> list[tuple[int, int, int]]:
    """Real colour extraction (median cut quantisation)."""
    small = img.convert("RGB").resize((128, 128), Image.Resampling.LANCZOS)
    q = small.quantize(colors=k, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette() or []
    counts = sorted(q.getcolors() or [], reverse=True)
    out = []
    for _, idx in counts[:k]:
        out.append(tuple(int(v) for v in pal[idx * 3:idx * 3 + 3]))
    return out or [(0, 0, 0)]


def _palette_transfer(img: Image.Image, target: list[tuple[int, int, int]]) -> Image.Image:
    """Shift an image's colours toward a target palette (real, deterministic)."""
    if not target:
        return img
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    src = np.asarray(_dominant_palette(img, len(target)), np.float32)
    dst = np.asarray(target, np.float32)
    d = np.linalg.norm(arr[:, :, None, :] - src[None, None, :, :], axis=3)
    w = np.exp(-d / 60.0)
    w /= np.clip(w.sum(axis=2, keepdims=True), 1e-6, None)
    out = (w[..., None] * dst[None, None, :, :]).sum(axis=2)
    return Image.fromarray(np.clip(0.55 * arr + 0.45 * out, 0, 255).astype(np.uint8), "RGB")


class ProceduralImageAdapter(ImageModelAdapter):
    key = "procedural_image"
    name = "Procedural Scene Renderer (CPU)"
    capability = "text_to_image"
    capabilities = (
        "text_to_image", "image_to_image", "inpainting", "outpainting",
        "background_replace", "style_transfer", "sketch_to_image",
        "reference_to_image", "multi_reference_image", "character_consistency",
    )
    version = "1.2"
    license = "MIT (this renderer code)"
    source_url = "built-in"
    description = (
        "Seeded procedural renderer. Runs on any CPU and produces real, deterministic "
        "images so the full pipeline works without a GPU. Not a diffusion model and not "
        "photorealistic - install a diffusion model in Model Manager for photographic output."
    )
    quality = "draft"
    ram_gb = 0.4
    size_gb = 0.0
    vram_gb = 0.0

    def validate(self, req: GenerationRequest) -> None:
        super().validate(req)
        if req.width > 4096 or req.height > 4096:
            raise StudioError("Procedural renderer supports up to 4096x4096.", code=ErrorCode.INVALID_REQUEST)

    def estimate(self, req: GenerationRequest):
        est = super().estimate(req)
        est.eta_seconds = max(1.0, (req.width * req.height) / 900_000.0)
        est.notes = "CPU procedural render"
        return est

    def _characters(self, req: GenerationRequest) -> list[CharacterIdentity]:
        out: list[CharacterIdentity] = []
        for spec in (req.character_context or {}).get("characters", []) or []:
            if not isinstance(spec, dict):
                continue
            ident = build_identity(
                spec.get("name", "Character"),
                f"{spec.get('description','')} {spec.get('clothing','')} {spec.get('hair','')} {spec.get('age_appearance','')}",
                seed=spec.get("identity_seed"),
            )
            if spec.get("clothing"):
                ident.top = _hexish(spec["clothing"], ident.top)
            if spec.get("hair"):
                ident.hair = _hexish(spec["hair"], ident.hair)
            out.append(ident)
        return out

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        mode = (req.param("mode") or "text_to_image").lower()
        seed = req.seed if req.seed is not None else seed_from_text(req.prompt or mode)
        progress(0.02, f"procedural:{mode}")

        refs = req.references or []
        base_img = None
        mask_img = None
        style_refs = []
        for r in refs:
            if r.role in ("init", "reference", "sketch", "content") and base_img is None:
                base_img = Image.open(r.path).convert("RGBA")
            elif r.role == "mask":
                mask_img = Image.open(r.path).convert("L")
            elif r.role in ("style", "character"):
                style_refs.append(Image.open(r.path).convert("RGB"))

        if mode in ("image_to_image", "sketch_to_image", "reference_to_image",
                    "multi_reference_image", "style_transfer") and base_img is None and not refs:
            mode = "text_to_image"
        if mode in ("image_to_image", "sketch_to_image", "reference_to_image",
                    "multi_reference_image", "style_transfer", "inpainting",
                    "outpainting", "background_replace") and base_img is None:
            raise StudioError(
                f"Mode '{mode}' requires a reference image (role=init).",
                code=ErrorCode.INVALID_REQUEST,
                remediation="Upload an image and attach it as a reference before running this mode.",
            )

        target_w, target_h = req.width, req.height

        if mode == "text_to_image":
            img = render_scene(req.prompt, target_w, target_h, seed=seed,
                               style=req.param("style", ""), mood=req.param("mood", ""),
                               lighting=req.param("lighting", ""), camera=req.param("camera", "static"),
                               characters=self._characters(req),
                               progress=lambda p, m: progress(0.05 + p * 0.85, m))
            path = _out(req, f"procedural_{seed & 0xFFFFFF:06x}.png")
            img.save(path)

        elif mode == "image_to_image":
            strength = float(req.param("strength", req.strength or 0.65))
            base = base_img.convert("RGB").resize((target_w, target_h), Image.Resampling.LANCZOS)
            generated = render_scene(req.prompt, target_w, target_h, seed=seed,
                                     style=req.param("style", ""), mood=req.param("mood", ""),
                                     characters=self._characters(req),
                                     progress=lambda p, m: progress(0.05 + p * 0.8, m))
            progress(0.9, "blending")
            img = Image.blend(base, generated, float(min(1.0, max(0.0, strength))))
            if style_refs:
                img = _palette_transfer(img, _dominant_palette(style_refs[0]))
            path = _out(req, f"img2img_{seed & 0xFFFFFF:06x}.png")
            img.save(path)

        elif mode == "style_transfer":
            strength = float(req.param("strength", 0.8))
            base = base_img.convert("RGB").resize((target_w, target_h), Image.Resampling.LANCZOS)
            preset = str(req.param("filter", "")) or ""
            if preset:
                from app.services import media

                base = media.apply_filter(base, preset, strength)
            target_palette = _dominant_palette(style_refs[0], 6) if style_refs else None
            if target_palette:
                base = _palette_transfer(base, target_palette)
            elif req.prompt:
                guide = render_scene(req.prompt, 256, 256, seed=seed, progress=lambda p, m: None)
                base = _palette_transfer(base, _dominant_palette(guide, 6))
            img = base
            path = _out(req, f"style_{seed & 0xFFFFFF:06x}.png")
            img.save(path)

        elif mode == "sketch_to_image":
            base = base_img.convert("RGB").resize((target_w, target_h), Image.Resampling.LANCZOS)
            lum = np.asarray(base.convert("L"), np.float32) / 255.0
            generated = np.asarray(
                render_scene(req.prompt, target_w, target_h, seed=seed, characters=self._characters(req),
                             progress=lambda p, m: progress(0.05 + p * 0.8, m)), np.float32)
            progress(0.9, "merging structure")
            merged = generated * (0.45 + 0.75 * lum[..., None]) * 0.85
            img = Image.fromarray(np.clip(merged, 0, 255).astype(np.uint8), "RGB")
            path = _out(req, f"sketch_{seed & 0xFFFFFF:06x}.png")
            img.save(path)

        elif mode in ("reference_to_image", "multi_reference_image"):
            base = base_img.convert("RGB").resize((target_w, target_h), Image.Resampling.LANCZOS)
            palettes = []
            for s in style_refs[:3]:
                palettes += _dominant_palette(s, 4)
            generated = render_scene(req.prompt, target_w, target_h, seed=seed,
                                     characters=self._characters(req),
                                     progress=lambda p, m: progress(0.05 + p * 0.75, m))
            progress(0.85, "applying reference palette")
            generated = _palette_transfer(generated, palettes or _dominant_palette(base, 5))
            strength = float(req.param("strength", 0.45))
            img = Image.blend(base, generated, min(1.0, max(0.0, strength)))
            path = _out(req, f"ref2img_{seed & 0xFFFFFF:06x}.png")
            img.save(path)

        elif mode == "inpainting":
            base = base_img.convert("RGB").resize((target_w, target_h), Image.Resampling.LANCZOS)
            if mask_img is None:
                raise StudioError(
                    "Inpainting requires a mask image (role=mask). White = area to regenerate.",
                    code=ErrorCode.INVALID_REQUEST,
                )
            mask = mask_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
            mask = mask.filter(ImageFilter.GaussianBlur(2))
            m = np.asarray(mask, np.float32) / 255.0
            m = np.clip((m - 0.15) / 0.7, 0, 1)[..., None]
            fill = render_scene(req.prompt or "seamless background continuation",
                                target_w, target_h, seed=seed,
                                progress=lambda p, mm: progress(0.1 + p * 0.75, mm))
            progress(0.9, "feathering mask")
            fill = _palette_transfer(fill, _dominant_palette(base, 5))
            arr = np.asarray(base, np.float32) * (1 - m) + np.asarray(fill, np.float32) * m
            img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
            path = _out(req, f"inpaint_{seed & 0xFFFFFF:06x}.png")
            img.save(path)

        elif mode == "outpainting":
            pad = int(req.param("padding", 0.25) * min(target_w, target_h))
            src = base_img.convert("RGB")
            src.thumbnail((target_w - 2 * pad, target_h - 2 * pad), Image.Resampling.LANCZOS)
            canvas_w, canvas_h = int(src.width + 2 * pad), int(src.height + 2 * pad)
            bg = render_scene(req.prompt or "extend the scene naturally", canvas_w, canvas_h, seed=seed,
                              progress=lambda p, m: progress(0.1 + p * 0.7, m))
            progress(0.85, "compositing")
            bg.paste(src, (pad, pad))
            bg = bg.resize((target_w, target_h), Image.Resampling.LANCZOS)
            img = bg
            path = _out(req, f"outpaint_{seed & 0xFFFFFF:06x}.png")
            img.save(path)

        elif mode == "background_replace":
            subject = base_img
            if subject.mode != "RGBA" or "A" not in subject.getbands():
                raise StudioError(
                    "background_replace needs a subject with transparency (PNG with alpha) or a mask.",
                    code=ErrorCode.INVALID_REQUEST,
                    remediation="Remove the background first (Background Removal) or upload a cut-out PNG.",
                )
            subject = subject.resize((target_w, target_h), Image.Resampling.LANCZOS)
            bg = render_scene(req.prompt or "neutral studio backdrop", target_w, target_h, seed=seed,
                              progress=lambda p, m: progress(0.1 + p * 0.75, m))
            progress(0.9, "compositing subject")
            img = Image.alpha_composite(bg.convert("RGBA"), subject).convert("RGB")
            path = _out(req, f"bgreplace_{seed & 0xFFFFFF:06x}.png")
            img.save(path)

        else:
            raise StudioError(f"Unsupported procedural image mode '{mode}'.", code=ErrorCode.INVALID_REQUEST)

        progress(1.0, "complete")
        return GenerationResult(
            files=[GeneratedFile(path=path, kind="image", mime="image/png",
                                 meta={"mode": mode, "renderer": "procedural"})],
            meta={"mode": mode, "seed": seed, "renderer": "procedural",
                  "quality": "draft", "note": "procedural render - not a diffusion model"},
            seed=seed, model_id=self.key,
        )


def _hexish(text: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    """Pull a colour out of a free-text description ('crimson', '#ff0000')."""
    import re

    m = re.search(r"#([0-9a-fA-F]{6})", text or "")
    if m:
        return tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    words = {
        "red": (190, 60, 60), "crimson": (178, 34, 52), "blue": (58, 84, 148),
        "navy": (28, 40, 92), "green": (72, 132, 96), "emerald": (46, 148, 110),
        "yellow": (226, 190, 70), "gold": (212, 175, 55), "orange": (216, 132, 60),
        "purple": (120, 72, 168), "violet": (138, 92, 200), "pink": (224, 140, 180),
        "white": (238, 238, 242), "black": (36, 36, 42), "grey": (128, 128, 132),
        "gray": (128, 128, 132), "brown": (120, 80, 50), "teal": (40, 148, 148),
        "cyan": (86, 190, 210), "maroon": (128, 32, 48), "beige": (216, 200, 172),
    }
    low = (text or "").lower()
    for k, v in words.items():
        if k in low:
            return v
    return default
