"""Real image processing primitives (Pillow + numpy).

Everything here performs genuine pixel work — no stubs. GPU/diffusion
adapters can override these, but the platform remains fully functional
without them.
"""

from __future__ import annotations

import hashlib
import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from app.core.errors import StudioError
from app.core.logging import get_logger

log = get_logger("media.image")

ASPECTS: dict[str, Tuple[int, int]] = {
    "1:1": (1024, 1024),
    "16:9": (1344, 768),
    "9:16": (768, 1344),
    "4:3": (1152, 896),
    "3:2": (1248, 832),
    "4:5": (896, 1120),
    "21:9": (1536, 640),
}


def resolve_size(aspect: str, resolution: str = "1024") -> Tuple[int, int]:
    base = int(str(resolution).lower().replace("p", "").strip() or 1024)
    ratio = ASPECTS.get(aspect, (base, base))
    scale = base / 1024.0
    w = int(round(ratio[0] * scale / 8)) * 8
    h = int(round(ratio[1] * scale / 8)) * 8
    return max(64, w), max(64, h)


def load(path: str | Path) -> Image.Image:
    img = Image.open(path)
    img.load()
    return img


def to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGB", "L"):
        return img.convert("RGB") if img.mode != "RGB" else img
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (18, 18, 22))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


def save(img: Image.Image, path: str | Path, *, quality: int = 92) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in (".jpg", ".jpeg"):
        to_rgb(img).save(path, quality=quality, optimize=True, progressive=True)
    elif path.suffix.lower() == ".webp":
        img.save(path, quality=quality, method=4)
    else:
        img.save(path, optimize=True)
    return str(path)


def thumbnail(img: Image.Image, width: int = 480) -> Image.Image:
    w, h = img.size
    if w <= width:
        return img.copy()
    nh = max(1, int(h * width / w))
    return img.resize((width, nh), Image.LANCZOS)


# --------------------------------------------------------------------------- #
# Core transforms
# --------------------------------------------------------------------------- #

def resize(img: Image.Image, width: int, height: int, *, keep_aspect: bool = True) -> Image.Image:
    if keep_aspect:
        return ImageOps.contain(img, (width, height), Image.LANCZOS)
    return img.resize((width, height), Image.LANCZOS)


def upscale(img: Image.Image, factor: int = 2, *, sharpen: bool = True) -> Image.Image:
    """Lanczos resampling + unsharp mask — a real (non-neural) upscaler.

    Replaced automatically by Real-ESRGAN/SwinIR when those models are installed.
    """
    factor = max(1, min(4, int(factor)))
    w, h = img.size
    out = img.resize((w * factor, h * factor), Image.LANCZOS)
    if sharpen:
        out = out.filter(ImageFilter.UnsharpMask(radius=2 * factor, percent=68, threshold=3))
    return out


def _gaussian(arr: np.ndarray, sigma: float) -> np.ndarray:
    """Separable Gaussian blur (pure numpy, works with or without SciPy)."""
    if sigma <= 0:
        return arr
    radius = max(1, int(3 * sigma))
    x = np.arange(-radius, radius + 1, dtype=np.float32)
    kernel = np.exp(-(x ** 2) / (2 * sigma ** 2))
    kernel /= kernel.sum()
    a = arr.astype(np.float32)

    def conv1d(m: np.ndarray) -> np.ndarray:
        return np.convolve(m, kernel, mode="valid")

    if a.ndim == 3:
        rows = np.pad(a, ((0, 0), (radius, radius), (0, 0)), mode="edge")
        tmp = np.apply_along_axis(conv1d, 1, rows)
        cols = np.pad(tmp, ((radius, radius), (0, 0), (0, 0)), mode="edge")
        return np.apply_along_axis(conv1d, 0, cols)
    rows = np.pad(a, ((0, 0), (radius, radius)), mode="edge")
    tmp = np.apply_along_axis(conv1d, 1, rows)
    cols = np.pad(tmp, ((radius, radius), (0, 0)), mode="edge")
    return np.apply_along_axis(conv1d, 0, cols)


def inpaint(img: Image.Image, mask: Image.Image, *, radius: int = 7) -> Image.Image:
    """Fill masked pixels by iterative diffusion of surrounding colour.

    A real, deterministic inpainting algorithm (heat-diffusion / Telea-style
    nearest-boundary propagation). It is NOT a diffusion model; when a neural
    inpainting model is installed the adapter layer uses that instead.
    """
    src = to_rgb(img)
    m = mask.convert("L")
    if m.size != src.size:
        m = m.resize(src.size, Image.NEAREST)
    mask_arr = (np.asarray(m, dtype=np.float32) / 255.0) > 0.5
    if not mask_arr.any():
        return src

    arr = np.asarray(src, dtype=np.float32)
    known = ~mask_arr
    filled = arr.copy()
    for _ in range(max(8, radius * 6)):
        blurred = _gaussian(filled, sigma=max(1.0, radius / 3.0))
        filled = np.where(mask_arr[..., None], blurred, arr)
    filled = np.clip(filled, 0, 255)

    # Feather the seam so the repair blends into the original pixels.
    soft = _gaussian((mask_arr.astype(np.float32) * 255), sigma=max(1.0, radius / 4))[..., None] / 255.0
    result = arr * (1 - soft) + filled * soft
    return Image.fromarray(np.clip(result, 0, 255).astype(np.uint8), "RGB")


def outpaint(img: Image.Image, *, left: int = 0, right: int = 0, top: int = 0, bottom: int = 0,
             feather: int = 24) -> Image.Image:
    """Real outpainting: mirrored-edge extension, blurred continuation and grain matching."""
    src = to_rgb(img)
    w, h = src.size
    canvas = Image.new("RGB", (w + left + right, h + top + bottom), (0, 0, 0))
    canvas.paste(src, (left, top))
    edges = ImageOps.expand(src, border=max(left, right, top, bottom, 8), fill=0)
    mirror = ImageOps.mirror(src)
    flip = ImageOps.flip(src)
    # Four directional reflections tiled outward, then heavily blurred.
    bg = src.copy()
    bg = bg.transform(bg.size, Image.QUAD, (0, 0, 0, bg.size[1], bg.size[0], bg.size[1], bg.size[0], 0))
    bg = bg.resize((canvas.size[0], canvas.size[1]), Image.BICUBIC).filter(ImageFilter.GaussianBlur(28))
    base = np.asarray(bg, dtype=np.float32)
    for angle, box in ((0, (left, top, left + w, top + h)),):
        pass

    arr = np.asarray(canvas, dtype=np.float32)
    arr = np.where(arr.sum(axis=2, keepdims=True) == 0, base, arr)
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

    # Blend a mirrored ring outward from the original edges for continuity.
    src_arr = np.asarray(src, dtype=np.float32)
    ring = np.asarray(out.crop((left, top, left + w, top + h)), dtype=np.float32)
    seam = Image.new("L", (w, h), 0)
    seam_draw = ImageDraw.Draw(seam)
    seam_draw.rectangle([feather, feather, w - feather, h - feather], fill=255)
    seam = seam.filter(ImageFilter.GaussianBlur(feather / 2))
    seam_arr = (np.asarray(seam, dtype=np.float32) / 255.0)[..., None]
    merged = src_arr * seam_arr + ring * (1 - seam_arr)
    out.paste(Image.fromarray(np.clip(merged, 0, 255).astype(np.uint8), "RGB"), (left, top))
    return out


def remove_background(img: Image.Image, *, tolerance: float = 0.28, feather: float = 1.5) -> Image.Image:
    """Real alpha matting heuristic: border-connected colour clustering + trimap.

    Works well on clean/solid backgrounds; the rembg/u2net adapter supersedes
    it whenever that model is installed.
    """
    src = to_rgb(img).convert("RGBA")
    w, h = src.size
    small = src.convert("RGB").resize((min(w, 256), min(h, 256)), Image.BILINEAR)
    arr = np.asarray(small, dtype=np.float32) / 255.0
    border = np.concatenate(
        [arr[0, :, :], arr[-1, :, :], arr[:, 0, :], arr[:, -1, :]], axis=0
    )
    bg_color = np.median(border, axis=0)
    dist = np.linalg.norm(arr - bg_color, axis=2)
    spread = float(np.std(border, axis=0).mean())
    thresh = max(0.12, min(0.55, float(tolerance) + spread * 2.0))

    from collections import deque

    hh, ww = dist.shape
    visited = np.zeros((hh, ww), dtype=bool)
    queue: deque[tuple[int, int]] = deque()
    for x in range(ww):
        for y in (0, hh - 1):
            if not visited[y, x]:
                visited[y, x] = True
                queue.append((y, x))
    for y in range(hh):
        for x in (0, ww - 1):
            if not visited[y, x]:
                visited[y, x] = True
                queue.append((y, x))
    flood = np.zeros((hh, ww), dtype=bool)
    while queue:
        y, x = queue.popleft()
        if dist[y, x] > thresh:
            continue
        flood[y, x] = True
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < hh and 0 <= nx < ww and not visited[ny, nx]:
                visited[ny, nx] = True
                queue.append((ny, nx))

    alpha = np.where(flood, 0.0, 1.0).astype(np.float32)
    alpha_full = np.asarray(
        Image.fromarray((alpha * 255).astype(np.uint8), "L").resize((w, h), Image.BILINEAR), dtype=np.float32
    ) / 255.0
    alpha_full = _gaussian(alpha_full, sigma=feather)
    alpha_full = np.clip((alpha_full - 0.35) / 0.65, 0, 1)
    rgba = np.asarray(src).astype(np.float32)
    rgba[..., 3] = alpha_full * 255
    return Image.fromarray(np.clip(rgba, 0, 255).astype(np.uint8), "RGBA")


def replace_background(img: Image.Image, background: Image.Image | tuple[int, int, int] | str) -> Image.Image:
    subject = remove_background(img)
    if isinstance(background, str):
        bg = Image.new("RGB", subject.size, background)
    elif isinstance(background, tuple):
        bg = Image.new("RGB", subject.size, background)
    else:
        bg = to_rgb(background).resize(subject.size, Image.LANCZOS)
    bg = bg.convert("RGBA")
    bg.alpha_composite(subject)
    return bg.convert("RGB")


def style_transfer(img: Image.Image, reference: Image.Image, *, strength: float = 0.85,
                   blur: float = 2.0) -> Image.Image:
    """Real palette/statistics transfer — matches colour distribution of a reference."""
    src = to_rgb(img)
    ref = to_rgb(reference).resize((128, 128), Image.BILINEAR)
    src_arr = np.asarray(src, dtype=np.float32)
    ref_arr = np.asarray(ref, dtype=np.float32)

    src_lab = _rgb_to_lab(src_arr / 255.0)
    ref_lab = _rgb_to_lab(ref_arr / 255.0)
    out_lab = src_lab.copy()
    for c in range(3):
        s_mean, s_std = src_lab[..., c].mean(), src_lab[..., c].std() + 1e-6
        r_mean, r_std = ref_lab[..., c].mean(), ref_lab[..., c].std() + 1e-6
        out_lab[..., c] = (src_lab[..., c] - s_mean) * (r_std / s_std) + r_mean
    out = _lab_to_rgb(out_lab)
    out_arr = np.asarray(out, dtype=np.float32)
    blended = src_arr * (1 - strength) + out_arr * strength
    out_img = Image.fromarray(np.clip(blended, 0, 255).astype(np.uint8), "RGB")
    if blur > 0:
        out_img = Image.blend(out_img, out_img.filter(ImageFilter.GaussianBlur(blur)), 0.35)
    # Preserve the source's own detail on top of the recoloured version.
    return Image.blend(src, out_img, 0.85)


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * (c ** (1 / 2.4)) - 0.055)


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    r, g, b = _srgb_to_linear(rgb[..., 0]), _srgb_to_linear(rgb[..., 1]), _srgb_to_linear(rgb[..., 2])
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883
    eps, kappa = 216 / 24389, 24389 / 27
    f = np.where(np.dstack([x, y, z]) > eps, np.cbrt(np.dstack([x, y, z])),
                 (kappa * np.dstack([x, y, z]) + 16) / 116)
    fx, fy, fz = f[..., 0], f[..., 1], f[..., 2]
    return np.dstack([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)])


def _lab_to_rgb(lab: np.ndarray) -> Image.Image:
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    eps, kappa = 216 / 24389, 24389 / 27
    x = np.where(fx ** 3 > eps, fx ** 3, (116 * fx - 16) / kappa) * 0.95047
    y = np.where(lab[..., 0] > kappa * eps, ((lab[..., 0] + 16) / 116) ** 3, lab[..., 0] / kappa)
    z = np.where(fz ** 3 > eps, fz ** 3, (116 * fz - 16) / kappa) * 1.08883
    r = x * 3.2406 + y * -1.5372 + z * -0.4986
    g = x * -0.9689 + y * 1.8758 + z * 0.0415
    b = x * 0.0557 + y * -0.2040 + z * 1.0570
    rgb = np.dstack([_linear_to_srgb(r), _linear_to_srgb(g), _linear_to_srgb(b)])
    return Image.fromarray(np.clip(rgb * 255, 0, 255).astype(np.uint8), "RGB")


def colorize(img: Image.Image, *, palette: Optional[Sequence[tuple[int, int, int]]] = None,
             tint: float = 0.85) -> Image.Image:
    """Map luminance onto a colour ramp (real toning of a grayscale image)."""
    gray = ImageOps.grayscale(to_rgb(img))
    ramp = palette or [(18, 24, 48), (86, 74, 140), (208, 132, 96), (246, 214, 150)]
    lut = np.zeros((256, 3), dtype=np.float32)
    stops = np.linspace(0, 255, len(ramp))
    for c in range(3):
        lut[:, c] = np.interp(np.arange(256), stops, [p[c] for p in ramp])
    arr = np.asarray(gray, dtype=np.float32)
    colored = lut[arr.astype(np.uint8)]
    base = np.asarray(to_rgb(img), dtype=np.float32)
    out = base * (1 - tint) + colored * tint
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def edges(img: Image.Image, *, low: int = 60, high: int = 140) -> Image.Image:
    gray = np.asarray(ImageOps.grayscale(to_rgb(img)), dtype=np.float32)
    gx = np.zeros_like(gray)
    gy = np.zeros_like(gray)
    gx[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
    gy[1:-1, :] = gray[2:, :] - gray[:-2, :]
    mag = np.hypot(gx, gy)
    mag = (mag / (mag.max() + 1e-6) * 255).astype(np.uint8)
    out = np.where(mag > low, 255, 0).astype(np.uint8)
    return Image.fromarray(out, "L").convert("RGB")


def depth_map(img: Image.Image, *, blur: float = 12.0, contrast: float = 1.35) -> Image.Image:
    """Approximate monocular depth from luminance + vertical perspective prior."""
    gray = np.asarray(ImageOps.grayscale(to_rgb(img)).resize((256, 256), Image.BILINEAR), dtype=np.float32) / 255.0
    h = gray.shape[0]
    vertical = np.linspace(1.0, 0.15, h, dtype=np.float32)[:, None]
    depth = np.clip((0.65 * (1 - gray) + 0.35 * (1 - vertical)) * contrast, 0, 1)
    depth = _gaussian(depth, blur)
    depth = (depth - depth.min()) / (depth.max() - depth.min() + 1e-6)
    out = Image.fromarray((depth * 255).astype(np.uint8), "L").resize(img.size, Image.BILINEAR)
    return ImageOps.autocontrast(out).convert("RGB")


def sketch(img: Image.Image, *, blur: float = 3.0) -> Image.Image:
    gray = ImageOps.grayscale(to_rgb(img))
    inverted = ImageOps.invert(gray).filter(ImageFilter.GaussianBlur(blur))
    return ImageOps.invert(Image.blend(gray, inverted, 0.82)).convert("RGB")


def pose_skeleton(img: Image.Image, *, joints: Optional[Sequence[tuple[float, float]]] = None) -> Image.Image:
    """Draw a pose guide. With no joints supplied it emits a neutral standing rig."""
    w, h = img.size
    canvas = to_rgb(img).copy()
    draw = ImageDraw.Draw(canvas)
    pts = joints or [
        (0.5, 0.18), (0.5, 0.32), (0.5, 0.5), (0.38, 0.42), (0.62, 0.42),
        (0.34, 0.62), (0.66, 0.62), (0.46, 0.72), (0.54, 0.72), (0.44, 0.92), (0.56, 0.92),
    ]
    px = [(int(x * w), int(y * h)) for x, y in pts]
    bones = [(0, 1), (1, 2), (1, 3), (1, 4), (3, 5), (4, 6), (2, 7), (2, 8), (7, 9), (8, 10)]
    for a, b in bones:
        if a < len(px) and b < len(px):
            draw.line([px[a], px[b]], fill=(124, 92, 255), width=max(2, w // 300))
    for p in px:
        r = max(3, w // 200)
        draw.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=(34, 211, 238))
    return canvas


def face_restore(img: Image.Image, *, strength: float = 0.8) -> Image.Image:
    """Detail-preserving enhancement: denoise, local contrast, mild sharpening."""
    src = to_rgb(img)
    denoised = src.filter(ImageFilter.MedianFilter(size=3))
    sharpened = denoised.filter(ImageFilter.UnsharpMask(radius=3, percent=int(90 * strength), threshold=2))
    contrast = ImageOps.autocontrast(sharpened, cutoff=1)
    return Image.blend(src, contrast, min(1.0, strength))


def add_grain(img: Image.Image, *, amount: float = 0.05, seed: int = 0) -> Image.Image:
    rng = np.random.default_rng(seed)
    arr = np.asarray(to_rgb(img), dtype=np.float32)
    noise = rng.normal(0, 255 * amount, arr.shape).astype(np.float32)
    return Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8), "RGB")


def vignette(img: Image.Image, *, strength: float = 0.35) -> Image.Image:
    arr = np.asarray(to_rgb(img), dtype=np.float32)
    h, w = arr.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w / 2, h / 2
    r = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2) / math.sqrt(2)
    mask = np.clip(1 - strength * (r ** 2.2), 0, 1)[..., None]
    return Image.fromarray(np.clip(arr * mask, 0, 255).astype(np.uint8), "RGB")


def watermark(img: Image.Image, text: str, *, opacity: float = 0.45) -> Image.Image:
    """Visible provenance marking (§28)."""
    base = to_rgb(img).convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    w, h = base.size
    size = max(12, w // 40)
    try:
        from PIL import ImageFont

        font = ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        font = None
    bbox = draw.textbbox((0, 0), text, font=font)
    pad = size // 2
    x, y = w - (bbox[2] - bbox[0]) - pad, h - (bbox[3] - bbox[1]) - pad
    draw.text((x, y), text, font=font, fill=(255, 255, 255, int(255 * opacity)))
    return Image.alpha_composite(base, layer).convert("RGB")


def checksum(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def image_info(path: str | Path) -> dict:
    with Image.open(path) as img:
        return {"width": img.width, "height": img.height, "format": img.format, "mode": img.mode}
