"""
Real image engine (Pillow).

Two responsibilities, both producing genuine pixel output:

1. ``generate_procedural`` — a deterministic *procedural* image generator. This is
   explicitly NOT a neural model: it is honest generative art driven by the
   prompt hash (palette, composition, shapes, lighting, grain). It exists so the
   product has a working, testable image pipeline before any GPU/API provider is
   configured, and it is always labelled ``engine=procedural-local`` in the API.

2. An edit engine covering resize/crop/rotate/flip, tone adjustments, filters,
   vignette, borders, text/watermarks, compositing and contact sheets. This is
   fully production-grade — it is plain image processing, not AI.
"""

from __future__ import annotations

import io
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .fonts import font_for

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = (value or "#ffffff").lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (255, 255, 255)


def _colorize_gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int], angle: str) -> Image.Image:
    """Cheap gradient: build a small gradient strip, rotate by mode, resize up."""
    width, height = size
    strip = Image.new("RGB", (1, 256))
    for y in range(256):
        ratio = y / 255
        strip.putpixel(
            (0, y),
            tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3)),
        )
    if angle == "horizontal":
        strip = strip.transpose(Image.Transpose.ROTATE_90)
    elif angle in ("diagonal", "diagonal-rev"):
        base = Image.new("RGB", (512, 512))
        big = strip.resize((1, 1024)).rotate(-45, expand=True, resample=Image.Resampling.BILINEAR)
        base.paste(big.resize((1024, 1024)), (-256, -256))
        out = base.resize(size, Image.Resampling.BICUBIC)
        return out.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if angle == "diagonal-rev" else out
    return strip.resize(size, Image.Resampling.BICUBIC).transpose(Image.Transpose.ROTATE_270)


def _palette(rng: random.Random, style: str) -> list[tuple[int, int, int]]:
    """Derive a coherent palette from a seeded RNG, biased by style."""
    style_profiles = {
        "cinematic": (0.55, 0.28, 0.45),   # saturation, value, hue spread
        "noir": (0.12, 0.30, 0.20),
        "neon": (0.85, 0.62, 0.16),
        "pastel": (0.42, 0.88, 0.30),
        "vintage": (0.38, 0.55, 0.55),
        "auto": (0.62, 0.55, 0.4),
    }
    sat, val, spread = style_profiles.get(style, style_profiles["auto"])
    import colorsys

    base_hue = rng.random()
    palette = []
    for i in range(6):
        hue = (base_hue + spread * i / 5 + rng.uniform(-0.03, 0.03)) % 1.0
        s = min(1.0, max(0.12, sat + rng.uniform(-0.1, 0.1)))
        # bright, saturated family: these are the visible colours of the piece
        v = min(1.0, max(0.42, val + 0.22 + rng.uniform(-0.1, 0.1)))
        palette.append(tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, s, v)))
    return palette


def _rng_from(prompt: str, seed: int) -> random.Random:
    return random.Random(f"{prompt.strip().lower()}|{int(seed)}")


def _vignette(image: Image.Image, strength: float = 0.45) -> Image.Image:
    width, height = image.size
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    steps = 24
    for i in range(steps):
        ratio = i / steps
        inset_x = int(width * 0.5 * ratio * 0.9)
        inset_y = int(height * 0.5 * ratio * 0.9)
        value = int(255 * (1 - strength * (ratio**1.6)))
        draw.ellipse(
            (inset_x, inset_y, width - inset_x, height - inset_y), fill=value
        )
    mask = mask.filter(ImageFilter.GaussianBlur(width / 12))
    black = Image.new("RGB", (width, height), (0, 0, 0))
    return Image.composite(image, black, mask)


def _grain(image: Image.Image, amount: float = 0.05, rng: random.Random | None = None) -> Image.Image:
    rng = rng or random.Random(7)
    width, height = image.size
    small = Image.new("L", (max(2, width // 2), max(2, height // 2)))
    small.putdata([rng.randint(112, 143) for _ in range(small.width * small.height)])
    noise = small.resize((width, height), Image.Resampling.BILINEAR)
    noise_rgb = Image.merge("RGB", (noise, noise, noise))
    return Image.blend(image, ImageChops.overlay(image, noise_rgb), amount)


def _draw_soft_shape(
    layer: Image.Image, rng: random.Random, color: tuple[int, int, int], alpha: int
) -> None:
    width, height = layer.size
    shape = rng.choice(["ellipse", "polygon", "triangle", "capsule", "arc"])
    overlay = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    cx = rng.uniform(0.05, 0.95) * width
    cy = rng.uniform(0.05, 0.95) * height
    rx = rng.uniform(0.08, 0.45) * width
    ry = rng.uniform(0.08, 0.45) * height
    fill = (*color, alpha)
    if shape == "ellipse":
        draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=fill)
    elif shape == "triangle":
        draw.polygon(
            [(cx, cy - ry), (cx - rx, cy + ry), (cx + rx, cy + ry)], fill=fill
        )
    elif shape == "capsule":
        draw.rounded_rectangle(
            (cx - rx, cy - ry * 0.4, cx + rx, cy + ry * 0.4),
            radius=min(rx, ry) * 0.4,
            fill=fill,
        )
    elif shape == "arc":
        draw.arc(
            (cx - rx, cy - ry, cx + rx, cy + ry),
            start=rng.uniform(0, 180),
            end=rng.uniform(200, 360),
            fill=fill,
            width=max(2, int(min(width, height) * 0.02)),
        )
    else:  # polygon
        points = []
        sides = rng.randint(3, 7)
        for i in range(sides):
            angle = 2 * math.pi * i / sides + rng.uniform(-0.3, 0.3)
            points.append(
                (
                    cx + math.cos(angle) * rx * rng.uniform(0.6, 1.2),
                    cy + math.sin(angle) * ry * rng.uniform(0.6, 1.2),
                )
            )
        draw.polygon(points, fill=fill)
    overlay = overlay.filter(ImageFilter.GaussianBlur(rng.uniform(1.5, 14)))
    layer.alpha_composite(overlay)


def _light_streaks(image: Image.Image, rng: random.Random, color: tuple[int, int, int]) -> Image.Image:
    width, height = image.size
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for _ in range(rng.randint(1, 2)):
        x = rng.uniform(0.05, 0.95) * width
        width_px = rng.uniform(0.04, 0.18) * width
        draw.polygon(
            [
                (x, -10),
                (x + width_px, -10),
                (x + width_px * rng.uniform(1.2, 2.4), height + 10),
                (x + rng.uniform(0, width_px), height + 10),
            ],
            fill=(*color, rng.randint(10, 22)),
        )
    overlay = overlay.filter(ImageFilter.GaussianBlur(width / 18))
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


# --------------------------------------------------------------------------
# procedural generation
# --------------------------------------------------------------------------


def generate_procedural(
    prompt: str,
    width: int = 1024,
    height: int = 576,
    seed: int = 0,
    style: str = "auto",
    quality: int = 92,
) -> bytes:
    """
    Create a deterministic procedural artwork (PNG bytes) from a prompt + seed.

    Same prompt+seed+size always yields byte-identical output, which makes the
    pipeline testable and reproducible.
    """
    width = max(64, min(4096, int(width)))
    height = max(64, min(4096, int(height)))
    rng = _rng_from(prompt, seed)
    palette = _palette(rng, style)

    angle = rng.choice(["vertical", "horizontal", "diagonal", "diagonal-rev"])
    import colorsys

    # a deep base colour with the same hue family keeps gradients rich rather than muddy
    top_rgb = palette[0]
    hue, sat, _ = colorsys.rgb_to_hsv(*[c / 255 for c in palette[1]])
    deep = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, min(1.0, sat * 0.9), 0.16))
    canvas = _colorize_gradient((width, height), top_rgb, deep, angle).convert("RGBA")

    for i in range(rng.randint(4, 8)):
        _draw_soft_shape(canvas, rng, palette[1 + (i % 5)], rng.randint(90, 190))

    canvas = canvas.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 1.6)))

    # focal light bloom
    bloom = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(bloom)
    bx, by = rng.uniform(0.2, 0.8) * width, rng.uniform(0.15, 0.7) * height
    radius = rng.uniform(0.18, 0.42) * min(width, height)
    for step in range(8, 0, -1):
        r = radius * step / 8
        bdraw.ellipse(
            (bx - r, by - r, bx + r, by + r),
            fill=(*palette[4], int(12 + (8 - step) * 4)),
        )
    bloom = bloom.filter(ImageFilter.GaussianBlur(radius / 3))
    canvas = Image.alpha_composite(canvas, bloom)

    rgb = canvas.convert("RGB")
    rgb = _light_streaks(rgb, rng, palette[3])
    rgb = ImageOps.autocontrast(rgb, cutoff=1)
    rgb = ImageEnhance.Color(rgb).enhance(1.12)
    rgb = ImageEnhance.Brightness(rgb).enhance(1.06)
    rgb = ImageEnhance.Contrast(rgb).enhance(1.05)
    rgb = _vignette(rgb, rng.uniform(0.16, 0.32))
    rgb = _grain(rgb, 0.03, rng)
    rgb = rgb.filter(ImageFilter.UnsharpMask(radius=2, percent=55, threshold=3))

    buffer = io.BytesIO()
    rgb.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def make_card(
    text: str,
    width: int = 1024,
    height: int = 576,
    background: str = "#101828",
    color: str = "#f2f4f7",
    subtitle: str = "",
) -> bytes:
    """Solid title card with wrapped text — used as the placeholder for shots."""
    image = Image.new("RGB", (width, height), _hex_to_rgb(background))
    draw = ImageDraw.Draw(image)
    title_size = max(18, int(height * 0.09))
    font = font_for(title_size, bold=True)
    sub_font = font_for(max(12, int(height * 0.045)))
    lines: list[str] = []
    words = (text or "Untitled").split()
    line = ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) > width * 0.8 and line:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    total = len(lines) * title_size * 1.25 + (sub_font.size * 2 if subtitle else 0)
    y = (height - total) / 2
    for entry in lines[:5]:
        text_width = draw.textlength(entry, font=font)
        draw.text(((width - text_width) / 2, y), entry, font=font, fill=_hex_to_rgb(color))
        y += title_size * 1.25
    if subtitle:
        text_width = draw.textlength(subtitle, font=sub_font)
        draw.text(
            ((width - text_width) / 2, y + 8),
            subtitle,
            font=sub_font,
            fill=_hex_to_rgb(color),
        )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------
# edit engine
# --------------------------------------------------------------------------


def _fit_cover(image: Image.Image, width: int, height: int) -> Image.Image:
    return ImageOps.fit(image, (width, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def _fit_contain(image: Image.Image, width: int, height: int, background: str) -> Image.Image:
    ratio = min(width / image.width, height / image.height)
    resized = image.resize(
        (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGB", (width, height), _hex_to_rgb(background))
    canvas.paste(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
    return canvas


def apply_ops(image: Image.Image, ops: list[dict]) -> Image.Image:
    """Apply an ordered list of edit operations. Unknown ops are ignored, not fatal."""
    current = image
    for op in ops or []:
        name = (op.get("op") or op.get("type") or "").lower()
        try:
            if name == "resize":
                width = int(op.get("width") or current.width)
                height = int(op.get("height") or current.height)
                fit = (op.get("fit") or "cover").lower()
                if fit == "contain":
                    current = _fit_contain(current, width, height, op.get("background", "#000000"))
                elif fit == "stretch":
                    current = current.resize((width, height), Image.Resampling.LANCZOS)
                else:
                    current = _fit_cover(current, width, height)
            elif name == "scale":
                factor = float(op.get("factor") or 1.0)
                factor = max(0.05, min(8.0, factor))
                current = current.resize(
                    (max(1, int(current.width * factor)), max(1, int(current.height * factor))),
                    Image.Resampling.LANCZOS,
                )
            elif name == "crop":
                left = int(op.get("x") or 0)
                top = int(op.get("y") or 0)
                width = int(op.get("width") or current.width)
                height = int(op.get("height") or current.height)
                box = (max(0, left), max(0, top), min(current.width, left + width), min(current.height, top + height))
                if box[2] > box[0] and box[3] > box[1]:
                    current = current.crop(box)
            elif name == "rotate":
                degrees = float(op.get("degrees") or 0)
                expand = bool(op.get("expand", True))
                background = op.get("background")
                current = current.rotate(
                    -degrees,  # PIL is counter-clockwise; users expect clockwise
                    resample=Image.Resampling.BICUBIC,
                    expand=expand,
                    fillcolor=_hex_to_rgb(background) if background else None,
                )
            elif name == "flip":
                axis = (op.get("axis") or "horizontal").lower()
                current = current.transpose(
                    Image.Transpose.FLIP_LEFT_RIGHT if axis.startswith("h") else Image.Transpose.FLIP_TOP_BOTTOM
                )
            elif name == "adjust":
                if "brightness" in op:
                    current = ImageEnhance.Brightness(current).enhance(float(op["brightness"]))
                if "contrast" in op:
                    current = ImageEnhance.Contrast(current).enhance(float(op["contrast"]))
                if "saturation" in op:
                    current = ImageEnhance.Color(current).enhance(float(op["saturation"]))
                if "sharpness" in op:
                    current = ImageEnhance.Sharpness(current).enhance(float(op["sharpness"]))
            elif name == "grayscale":
                current = ImageOps.grayscale(current).convert("RGB")
            elif name == "sepia":
                gray = ImageOps.grayscale(current).convert("RGB")
                sepia = Image.new("RGB", gray.size)
                pixels = gray.getdata()
                sepia.putdata([(min(255, int(r * 1.07 + 20)), int(g * 0.95 + 8), int(b * 0.78)) for r, g, b in pixels])
                current = sepia
            elif name == "invert":
                current = ImageOps.invert(current.convert("RGB"))
            elif name == "autocontrast":
                current = ImageOps.autocontrast(current.convert("RGB"), cutoff=int(op.get("cutoff", 1)))
            elif name == "equalize":
                current = ImageOps.equalize(current.convert("RGB"))
            elif name == "posterize":
                current = ImageOps.posterize(current.convert("RGB"), bits=int(op.get("bits", 4)))
            elif name == "blur":
                radius = float(op.get("radius") or 2.0)
                current = current.filter(ImageFilter.GaussianBlur(radius))
            elif name == "sharpen":
                current = current.filter(ImageFilter.UnsharpMask(radius=2, percent=int(op.get("percent", 150)), threshold=3))
            elif name == "edge":
                current = ImageOps.grayscale(current).filter(ImageFilter.FIND_EDGES).convert("RGB")
            elif name == "emboss":
                current = current.filter(ImageFilter.EMBOSS).convert("RGB")
            elif name == "pixelate":
                factor = max(2, int(op.get("factor", 12)))
                small = current.resize(
                    (max(1, current.width // factor), max(1, current.height // factor)),
                    Image.Resampling.BILINEAR,
                )
                current = small.resize(current.size, Image.Resampling.NEAREST)
            elif name == "vignette":
                current = _vignette(current.convert("RGB"), float(op.get("strength", 0.45)))
            elif name == "grain":
                current = _grain(current.convert("RGB"), float(op.get("amount", 0.05)), random.Random(int(op.get("seed", 3))))
            elif name == "border":
                width = int(op.get("width", 12))
                color = _hex_to_rgb(op.get("color", "#ffffff"))
                current = ImageOps.expand(current.convert("RGB"), border=width, fill=color)
            elif name in ("text", "watermark", "caption"):
                current = draw_text(
                    current.convert("RGB"),
                    text=str(op.get("text", "")),
                    position=str(op.get("position", "bottom-right")),
                    size=int(op.get("size", 32)),
                    color=str(op.get("color", "#ffffff")),
                    opacity=float(op.get("opacity", 0.9)),
                    background=str(op.get("background", "")) or None,
                    padding=int(op.get("padding", 16)),
                    x=op.get("x"),
                    y=op.get("y"),
                    stroke=int(op.get("stroke", 0)),
                )
            elif name == "composite":
                path = op.get("path")
                if path:
                    overlay = Image.open(path).convert("RGBA")
                    scale = float(op.get("scale", 1.0))
                    if scale != 1.0:
                        overlay = overlay.resize(
                            (max(1, int(overlay.width * scale)), max(1, int(overlay.height * scale))),
                            Image.Resampling.LANCZOS,
                        )
                    base = current.convert("RGBA")
                    pos = (int(op.get("x", 0)), int(op.get("y", 0)))
                    base.alpha_composite(overlay, dest=pos)
                    current = base.convert("RGB")
            elif name == "round":
                radius = int(op.get("radius", 32))
                mask = Image.new("L", current.size, 0)
                ImageDraw.Draw(mask).rounded_rectangle((0, 0, current.width - 1, current.height - 1), radius=radius, fill=255)
                out = Image.new("RGB", current.size, _hex_to_rgb(op.get("background", "#000000")))
                out.paste(current.convert("RGB"), (0, 0), mask)
                current = out
        except Exception:
            # one bad op must not destroy the whole render
            continue
    return current


def draw_text(
    image: Image.Image,
    *,
    text: str,
    position: str = "bottom-right",
    size: int = 32,
    color: str = "#ffffff",
    opacity: float = 0.9,
    background: str | None = None,
    padding: int = 16,
    x: int | None = None,
    y: int | None = None,
    stroke: int = 0,
) -> Image.Image:
    if not text:
        return image
    base = image.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = font_for(size, bold=position.startswith("title"))
    box = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    text_width, text_height = box[2] - box[0], box[3] - box[1]
    if x is not None and y is not None:
        px, py = int(x), int(y)
    else:
        position = position.replace("title-", "")
        if position in ("bottom-right", "bottom-left", "bottom-center"):
            py = base.height - text_height - padding * 3
        elif position in ("top-right", "top-left", "top-center"):
            py = padding * 2
        else:
            py = (base.height - text_height) // 2
        if position.endswith("right"):
            px = base.width - text_width - padding * 2
        elif position.endswith("left"):
            px = padding * 2
        else:
            px = (base.width - text_width) // 2
    rgb = _hex_to_rgb(color)
    if background:
        bg = _hex_to_rgb(background)
        draw.rounded_rectangle(
            (px - padding // 2, py - padding // 2, px + text_width + padding // 2, py + text_height + padding // 2),
            radius=max(4, padding // 2),
            fill=(*bg, int(255 * min(0.95, opacity + 0.15))),
        )
    draw.text(
        (px, py),
        text,
        font=font,
        fill=(*rgb, int(255 * max(0.0, min(1.0, opacity)))),
        stroke_width=stroke,
        stroke_fill=(0, 0, 0, int(200 * opacity)),
    )
    return Image.alpha_composite(base, layer).convert("RGB")


def apply_edits(source: Path, ops: list[dict], output_format: str = "png", quality: int = 92) -> bytes:
    with Image.open(source) as img:
        img.load()
        result = apply_ops(img.convert("RGB"), ops)
    buffer = io.BytesIO()
    fmt = (output_format or "png").upper()
    if fmt in ("JPG", "JPEG"):
        result.save(buffer, format="JPEG", quality=quality, optimize=True)
    elif fmt == "WEBP":
        result.save(buffer, format="WEBP", quality=quality)
    else:
        result.save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------
# composition utilities
# --------------------------------------------------------------------------


def contact_sheet(
    paths: list[Path], columns: int = 4, cell: int = 320, background: str = "#0b1220"
) -> bytes:
    """Grid montage of images (used for character sheets / storyboard previews)."""
    if not paths:
        raise ValueError("contact_sheet requires at least one image")
    columns = max(1, min(8, int(columns)))
    rows = math.ceil(len(paths) / columns)
    sheet = Image.new("RGB", (columns * cell, rows * cell), _hex_to_rgb(background))
    for index, path in enumerate(paths):
        try:
            with Image.open(path) as img:
                tile = _fit_cover(img.convert("RGB"), cell, cell)
        except Exception:
            tile = Image.new("RGB", (cell, cell), _hex_to_rgb(background))
        sheet.paste(tile, ((index % columns) * cell, (index // columns) * cell))
    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG")
    return buffer.getvalue()


def ensure_rgb(image: Image.Image) -> Image.Image:
    return image.convert("RGB") if image.mode != "RGB" else image


def prep_for_video(
    source: Path,
    width: int,
    height: int,
    caption: str = "",
    caption_size: int = 34,
    supersample: float = 1.5,
) -> Image.Image:
    """
    Prepare a still for the video renderer: cover-fit at ``supersample`` scale so
    the FFmpeg zoompan filter can pan/zoom without softening, with the caption
    baked in (avoids depending on ffmpeg drawtext font config).
    """
    with Image.open(source) as img:
        img.load()
        base = _fit_cover(
            img.convert("RGB"),
            max(width, int(width * supersample)),
            max(height, int(height * supersample)),
        )
    if caption:
        base = draw_text(
            base,
            text=caption,
            position="bottom-center",
            size=max(14, int(caption_size * supersample)),
            color="#ffffff",
            opacity=0.95,
            background="#0b1220",
            padding=int(14 * supersample),
        )
    return base
