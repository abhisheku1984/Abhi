"""Deterministic CPU renderer.

This is a REAL renderer: it composes gradients, fractal-noise terrain,
silhouettes, lighting, atmosphere and grain into genuine pixels. It is NOT a
diffusion model and never claims to be — every asset it produces is stamped
`engine: local-cpu-renderer, deterministic: true, diffusion: false`.

Its purpose: the platform stays end-to-end usable, testable and demoable on a
CPU-only machine, while diffusion adapters slot in via the same interface.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# --------------------------------------------------------------------------- #
# Noise & palettes
# --------------------------------------------------------------------------- #


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed & 0xFFFFFFFF)


def fbm(width: int, height: int, seed: int, *, octaves: int = 5, persistence: float = 0.5, base: int = 3) -> np.ndarray:
    rng = _rng(seed)
    total = np.zeros((height, width), dtype=np.float32)
    amp, freq, norm = 1.0, base, 0.0
    for _ in range(octaves):
        gw, gh = max(2, freq), max(2, int(freq * height / max(width, 1)))
        grid = rng.random((gh, gw)).astype(np.float32)
        layer = Image.fromarray((grid * 255).astype(np.uint8), "L").resize((width, height), Image.BICUBIC)
        total += (np.asarray(layer, dtype=np.float32) / 255.0) * amp
        norm += amp
        amp *= persistence
        freq *= 2
    return total / max(norm, 1e-6)


def hash_seed(text: str, salt: int = 0) -> int:
    return int(hashlib.sha256(f"{text}::{salt}".encode("utf-8")).hexdigest()[:12], 16)


PALETTES = {
    "golden-hour": {
        "sky_top": (38, 32, 74), "sky_bottom": (255, 168, 92),
        "sun": (255, 226, 158), "far": (96, 74, 106), "mid": (52, 38, 66), "near": (22, 15, 28),
        "accent": (255, 196, 110), "haze": (255, 176, 116),
    },
    "blue-hour": {
        "sky_top": (10, 14, 40), "sky_bottom": (78, 108, 178),
        "sun": (176, 206, 255), "far": (38, 52, 92), "mid": (24, 32, 62), "near": (10, 14, 30),
        "accent": (120, 176, 255), "haze": (86, 116, 180),
    },
    "daylight": {
        "sky_top": (86, 154, 232), "sky_bottom": (206, 232, 250),
        "sun": (255, 250, 226), "far": (126, 156, 176), "mid": (72, 100, 112), "near": (34, 52, 58),
        "accent": (255, 255, 255), "haze": (200, 224, 240),
    },
    "studio": {
        "sky_top": (238, 240, 246), "sky_bottom": (206, 210, 222),
        "sun": (255, 255, 255), "far": (176, 180, 192), "mid": (126, 130, 142), "near": (42, 44, 52),
        "accent": (255, 255, 255), "haze": (228, 230, 238),
    },
    "neon": {
        "sky_top": (12, 6, 28), "sky_bottom": (72, 22, 96),
        "sun": (255, 92, 196), "far": (64, 24, 96), "mid": (36, 14, 62), "near": (10, 4, 20),
        "accent": (64, 232, 255), "haze": (128, 40, 160),
    },
    "cinematic": {
        "sky_top": (16, 18, 30), "sky_bottom": (86, 78, 96),
        "sun": (255, 214, 170), "far": (58, 56, 72), "mid": (34, 32, 44), "near": (14, 13, 20),
        "accent": (255, 176, 120), "haze": (96, 92, 104),
    },
    "low-key": {
        "sky_top": (8, 8, 12), "sky_bottom": (36, 34, 42),
        "sun": (214, 190, 168), "far": (30, 28, 34), "mid": (20, 19, 24), "near": (6, 6, 8),
        "accent": (198, 160, 120), "haze": (40, 38, 46),
    },
    "high-key": {
        "sky_top": (248, 250, 255), "sky_bottom": (232, 238, 248),
        "sun": (255, 255, 255), "far": (206, 214, 228), "mid": (166, 176, 194), "near": (84, 92, 108),
        "accent": (255, 255, 255), "haze": (244, 248, 255),
    },
    "night": {
        "sky_top": (4, 6, 18), "sky_bottom": (26, 34, 66),
        "sun": (220, 230, 255), "far": (22, 28, 54), "mid": (14, 18, 36), "near": (4, 6, 14),
        "accent": (140, 200, 255), "haze": (30, 40, 74),
    },
}


def _mix(a: Sequence[int], b: Sequence[int], t: float) -> tuple[int, int, int]:
    t = float(np.clip(t, 0, 1))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# --------------------------------------------------------------------------- #
# Layer painters
# --------------------------------------------------------------------------- #

def _gradient(width: int, height: int, top: Sequence[int], bottom: Sequence[int], *, curve: float = 1.6) -> np.ndarray:
    t = (np.linspace(0, 1, height, dtype=np.float32) ** curve)[:, None, None]
    top_arr = np.asarray(top, dtype=np.float32)[None, None, :]
    bottom_arr = np.asarray(bottom, dtype=np.float32)[None, None, :]
    arr = top_arr * (1 - t) + bottom_arr * t
    return np.repeat(arr, width, axis=1)


def _radial_glow(width: int, height: int, cx: float, cy: float, radius: float, color: Sequence[int],
                 intensity: float = 1.0) -> np.ndarray:
    yy, xx = np.mgrid[0:height, 0:width]
    d = np.sqrt((xx - cx * width) ** 2 + (yy - cy * height) ** 2) / (radius * max(width, height))
    glow = np.clip(1 - d, 0, 1) ** 2.4 * intensity
    return glow[..., None] * np.asarray(color, dtype=np.float32)[None, None, :]


def _clouds(width: int, height: int, seed: int, *, amount: float = 0.5, color: Sequence[int]) -> np.ndarray:
    n = fbm(width, height, seed, octaves=6, persistence=0.55, base=3)
    n = np.clip((n - 0.48) * 3.2, 0, 1) * amount
    return n[..., None] * np.asarray(color, dtype=np.float32)[None, None, :]


def _ridge(width: int, height: int, seed: int, *, roughness: float = 0.5, baseline: float = 0.62,
           amp: float = 0.22) -> np.ndarray:
    """Per-column ridge heights in normalised image space (1-D, length = width)."""
    del height  # ridge is a 1-D profile; callers map it onto the canvas height
    n = fbm(width, 8, seed, octaves=4, persistence=roughness, base=2)
    profile = np.asarray(n, dtype=np.float32).mean(axis=0)
    profile = (profile - profile.min()) / (profile.max() - profile.min() + 1e-6)
    return baseline - profile * amp


def _silhouette_layer(width: int, height: int, ridge: np.ndarray, color: Sequence[int],
                      *, alpha: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    yy = np.arange(height)[:, None]
    mask = (yy >= ridge[None, :] * height).astype(np.float32)
    mask = np.repeat(mask[:, :, None], 3, axis=2)
    return mask * alpha, (mask * np.asarray(color, dtype=np.float32)[None, None, :])


def _humanoid(draw: ImageDraw.ImageDraw, x: int, y: int, h: int, color: tuple[int, int, int],
              *, coat: Optional[tuple[int, int, int]] = None, tie: bool = False) -> None:
    """Proportional human silhouette (≈7.5 heads tall)."""
    head = h / 7.5
    body_top = y
    head_cx = x
    draw.ellipse([head_cx - head * 0.32, body_top - head * 0.7, head_cx + head * 0.32, body_top + head * 0.05],
                 fill=color)
    draw.rectangle([head_cx - head * 0.14, body_top + head * 0.02, head_cx + head * 0.14, body_top + head * 0.22],
                   fill=color)
    shoulder_w = head * 1.15
    torso_h = h * 0.34
    draw.polygon([
        (head_cx - shoulder_w, body_top + head * 0.2),
        (head_cx + shoulder_w, body_top + head * 0.2),
        (head_cx + shoulder_w * 0.78, body_top + torso_h),
        (head_cx - shoulder_w * 0.78, body_top + torso_h),
    ], fill=coat or color)
    leg_h = h * 0.42
    leg_top = body_top + torso_h
    draw.polygon([
        (head_cx - shoulder_w * 0.8, leg_top), (head_cx - shoulder_w * 0.12, leg_top),
        (head_cx - shoulder_w * 0.16, leg_top + leg_h), (head_cx - shoulder_w * 0.5, leg_top + leg_h),
    ], fill=color)
    draw.polygon([
        (head_cx + shoulder_w * 0.12, leg_top), (head_cx + shoulder_w * 0.8, leg_top),
        (head_cx + shoulder_w * 0.5, leg_top + leg_h), (head_cx + shoulder_w * 0.16, leg_top + leg_h),
    ], fill=color)
    arm_w = head * 0.24
    draw.polygon([
        (head_cx - shoulder_w * 1.02, body_top + head * 0.24),
        (head_cx - shoulder_w * 0.62, body_top + head * 0.24),
        (head_cx - shoulder_w * 0.7, body_top + torso_h * 0.92),
        (head_cx - shoulder_w * 1.0, body_top + torso_h * 0.92),
    ], fill=coat or color)
    draw.polygon([
        (head_cx + shoulder_w * 0.62, body_top + head * 0.24),
        (head_cx + shoulder_w * 1.02, body_top + head * 0.24),
        (head_cx + shoulder_w * 1.0, body_top + torso_h * 0.92),
        (head_cx + shoulder_w * 0.7, body_top + torso_h * 0.92),
    ], fill=coat or color)
    if tie:
        draw.polygon([
            (head_cx - head * 0.07, body_top + head * 0.24),
            (head_cx + head * 0.07, body_top + head * 0.24),
            (head_cx + head * 0.04, body_top + torso_h * 0.72),
            (head_cx - head * 0.04, body_top + torso_h * 0.72),
        ], fill=(196, 72, 72))


def _animal(draw: ImageDraw.ImageDraw, x: int, y: int, size: float, kind: str,
            color: tuple[int, int, int]) -> None:
    if kind == "lion":
        draw.ellipse([x - size * 0.55, y - size * 0.5, x + size * 0.45, y + size * 0.45], fill=color)  # body
        draw.ellipse([x + size * 0.25, y - size * 0.75, x + size * 0.95, y - size * 0.05], fill=color)  # head
        draw.ellipse([x + size * 0.2, y - size * 0.92, x + size * 1.02, y - size * 0.28],
                     outline=color, width=max(2, int(size * 0.09)))  # mane
        for dx in (-0.4, -0.1, 0.18):
            draw.rectangle([x + size * dx, y + size * 0.4, x + size * (dx + 0.12), y + size * 0.85], fill=color)
        draw.line([x - size * 0.55, y - size * 0.1, x - size * 1.0, y - size * 0.55], fill=color,
                  width=max(2, int(size * 0.08)))
    elif kind == "rabbit":
        draw.ellipse([x - size * 0.4, y - size * 0.35, x + size * 0.4, y + size * 0.4], fill=color)
        draw.ellipse([x + size * 0.15, y - size * 0.85, x + size * 0.7, y - size * 0.25], fill=color)
        draw.ellipse([x + size * 0.25, y - size * 1.5, x + size * 0.4, y - size * 0.7], fill=color)
        draw.ellipse([x + size * 0.48, y - size * 1.5, x + size * 0.63, y - size * 0.7], fill=color)
        draw.ellipse([x - size * 0.58, y - size * 0.1, x - size * 0.28, y + size * 0.2], fill=color)
    else:
        draw.ellipse([x - size * 0.5, y - size * 0.35, x + size * 0.5, y + size * 0.35], fill=color)
        draw.ellipse([x + size * 0.35, y - size * 0.75, x + size * 0.85, y - size * 0.25], fill=color)
        for dx in (-0.35, 0.0, 0.3):
            draw.rectangle([x + size * dx, y + size * 0.3, x + size * (dx + 0.12), y + size * 0.8], fill=color)


# --------------------------------------------------------------------------- #
# Prompt understanding (shared with the Prompt Engine)
# --------------------------------------------------------------------------- #

SCENE_HINTS = {
    "city": ("city", "urban", "skyline", "hyderabad", "downtown", "street", "building", "tower", "metropolis"),
    "mountain": ("mountain", "hill", "peak", "valley", "cliff", "himalaya"),
    "water": ("ocean", "sea", "beach", "lake", "river", "water", "harbour", "harbor", "boat"),
    "forest": ("forest", "tree", "trees", "jungle", "woods", "park"),
    "desert": ("desert", "dune", "sand", "sahara"),
    "space": ("space", "galaxy", "nebula", "planet", "cosmos", "astronaut", "orbit"),
    "interior": ("office", "room", "interior", "studio", "kitchen", "lobby"),
    "futuristic": ("futuristic", "sci-fi", "scifi", "cyber", "neon", "future", "robot", "android"),
    "village": ("village", "farm", "rural", "countryside", "field"),
    "road": ("road", "highway", "street", "path", "bridge"),
}

SUBJECT_HINTS = {
    "person": ("person", "man", "woman", "businessman", "businesswoman", "people", "human", "boy", "girl",
               "walk", "walking", "standing", "presenter", "teacher", "worker", "crowd"),
    "lion": ("lion",),
    "rabbit": ("rabbit", "bunny", "hare"),
    "bird": ("bird", "eagle", "parrot"),
    "car": ("car", "vehicle", "truck", "bike"),
    "product": ("bottle", "product", "watch", "shoe", "phone", "box", "perfume"),
    "animal": ("animal", "dog", "cat", "horse", "elephant", "tiger"),
}


def analyze_prompt(prompt: str) -> dict:
    p = (prompt or "").lower()
    scenes = [name for name, keys in SCENE_HINTS.items() if any(k in p for k in keys)]
    subjects = [name for name, keys in SUBJECT_HINTS.items() if any(k in p for k in keys)]
    lighting = "cinematic"
    for key in PALETTES:
        if key.replace("-", " ") in p or key.replace("-", "") in p:
            lighting = key
            break
    if "sunset" in p or "golden" in p or "dusk" in p:
        lighting = "golden-hour"
    if "night" in p or "midnight" in p:
        lighting = "night"
    if "neon" in p or "cyberpunk" in p:
        lighting = "neon"
    weather = "clear"
    for w in ("rain", "snow", "fog", "mist", "storm", "cloud"):
        if w in p:
            weather = w
            break
    camera = "wide"
    for c in ("close-up", "closeup", "close up", "portrait", "macro", "wide", "aerial", "drone", "pov"):
        if c in p:
            camera = {"closeup": "close-up", "close up": "close-up"}.get(c, c)
            break
    return {
        "scenes": scenes or ["mountain" if "mountain" in p else "city"],
        "subjects": subjects,
        "lighting": lighting,
        "weather": weather,
        "camera": camera,
        "words": [w for w in "".join(ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in p).split() if w],
    }


# --------------------------------------------------------------------------- #
# Main scene renderer
# --------------------------------------------------------------------------- #

def render_scene(
    prompt: str,
    *,
    width: int = 1024,
    height: int = 1024,
    seed: int = 0,
    lighting: str = "cinematic",
    camera: str = "wide",
    style: str = "cinematic",
    negative_prompt: str = "",
    reference_images: Optional[Sequence[str]] = None,
    character: Optional[dict] = None,
    progress: Optional[object] = None,
) -> Image.Image:
    info = analyze_prompt(prompt)
    if lighting == "cinematic" and info["lighting"] != "cinematic":
        lighting = info["lighting"]
    if camera == "wide" and info["camera"] != "wide":
        camera = info["camera"]

    pal = PALETTES.get(lighting, PALETTES["cinematic"])
    scenes = info["scenes"]
    subjects = info["subjects"]
    rng = _rng(seed)
    W, H = int(width), int(height)

    canvas = _gradient(W, H, pal["sky_top"], pal["sky_bottom"], curve=1.5)

    # Sun / key light
    sun_x = 0.24 + 0.5 * float(rng.random())
    sun_y = 0.18 + 0.22 * float(rng.random())
    canvas += _radial_glow(W, H, sun_x, sun_y, 0.42, pal["sun"], intensity=0.95)
    canvas += _radial_glow(W, H, sun_x, sun_y, 0.09, (255, 255, 255), intensity=0.85)

    if "space" in scenes:
        stars = (rng.random((H, W)) > 0.9985).astype(np.float32)
        canvas += stars[..., None] * np.asarray([255, 255, 255], dtype=np.float32)[None, None, :] * 0.9
        neb = fbm(W, H, seed + 11, octaves=6, persistence=0.6, base=3)
        canvas += (neb ** 2.2)[..., None] * np.asarray(pal["accent"], dtype=np.float32)[None, None, :] * 0.55

    if info["weather"] in ("cloud", "storm", "rain", "snow", "fog", "mist") or "space" not in scenes:
        canvas += _clouds(W, H, seed + 3, amount=0.55 if info["weather"] in ("storm", "cloud") else 0.28,
                          color=pal["haze"])

    horizon = 0.52 + 0.08 * (float(rng.random()) - 0.5)

    # Far layer
    far_ridge = _ridge(W, H, seed + 21, roughness=0.45, baseline=horizon + 0.06, amp=0.16)
    mask, layer = _silhouette_layer(W, H, far_ridge, pal["far"], alpha=0.92)
    canvas = canvas * (1 - mask) + layer

    # Mid layer — scene specific
    mid = Image.new("RGB", (W, H), (0, 0, 0))
    md = ImageDraw.Draw(mid)
    mid_mask = np.zeros((H, W), dtype=np.float32)

    if "city" in scenes or "futuristic" in scenes:
        base_y = int(H * (horizon + 0.16))
        x = -int(0.05 * W)
        while x < W:
            bw = int(W * (0.045 + 0.075 * rng.random()))
            bh = int(H * (0.10 + 0.34 * rng.random()))
            top = base_y - bh
            md.rectangle([x, top, x + bw, H], fill=tuple(pal["mid"]))
            if "futuristic" in scenes or lighting == "neon":
                for wy in range(top + 8, H, max(8, bh // 12)):
                    for wx in range(x + 5, x + bw - 5, max(6, bw // 4)):
                        if rng.random() > 0.55:
                            md.rectangle([wx, wy, wx + max(2, bw // 12), wy + max(2, bh // 26)],
                                         fill=tuple(_mix(pal["accent"], (255, 255, 255), rng.random())))
            x += bw + max(2, int(W * 0.008))
        mid_mask[int(H * (horizon + 0.16)):, :] = 1.0
    elif "mountain" in scenes or "village" in scenes:
        ridge = _ridge(W, H, seed + 33, roughness=0.55, baseline=horizon + 0.12, amp=0.2)
        for x_px in range(W):
            top = int(ridge[x_px] * H)
            md.line([(x_px, top), (x_px, H)], fill=tuple(pal["mid"]))
        mid_mask[np.arange(H)[:, None] >= (ridge[None, :] * H).astype(int)] = 1.0
    elif "forest" in scenes:
        for i in range(int(W / 26)):
            tx = int(rng.random() * W)
            th = int(H * (0.12 + 0.22 * rng.random()))
            top = int(H * (horizon + 0.22)) - th
            md.polygon([(tx, top), (tx - th * 0.18, H), (tx + th * 0.18, H)],
                       fill=tuple(_mix(pal["mid"], (10, 30, 24), rng.random() * 0.4)))
        mid_mask[int(H * (horizon + 0.24)):, :] = 1.0
    elif "desert" in scenes:
        for k in range(3):
            ridge = _ridge(W, H, seed + 40 + k * 7, roughness=0.4, baseline=horizon + 0.12 + k * 0.1, amp=0.07)
            for x_px in range(W):
                md.line([(x_px, int(ridge[x_px] * H)), (x_px, H)],
                        fill=tuple(_mix(pal["mid"], (214, 168, 108), 0.25 + k * 0.25)))
            mid_mask[np.arange(H)[:, None] >= (ridge[None, :] * H).astype(int)] = 1.0
    else:
        ridge = _ridge(W, H, seed + 51, roughness=0.5, baseline=horizon + 0.12, amp=0.15)
        for x_px in range(W):
            md.line([(x_px, int(ridge[x_px] * H)), (x_px, H)], fill=tuple(pal["mid"]))
        mid_mask[np.arange(H)[:, None] >= (ridge[None, :] * H).astype(int)] = 1.0

    # Water reflection
    if "water" in scenes:
        water_top = int(H * (horizon + 0.18))
        sky_band = np.asarray(Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), "RGB")
                              .crop((0, max(0, water_top - (H - water_top)), W, water_top))
                              .transpose(Image.FLIP_TOP_BOTTOM).resize((W, H - water_top), Image.BILINEAR),
                              dtype=np.float32)
        ripples = fbm(W, H - water_top, seed + 61, octaves=4, persistence=0.5, base=6)
        reflected = sky_band * (0.42 + 0.35 * ripples[..., None])
        canvas[water_top:, :, :] = canvas[water_top:, :, :] * 0.25 + reflected * 0.8
        canvas[water_top:, :, :] += _radial_glow(W, H - water_top, sun_x, 0.0, 0.5, pal["sun"], intensity=0.35)

    mid_arr = np.asarray(mid, dtype=np.float32) * mid_mask[..., None]
    canvas = canvas * (1 - mid_mask[..., None]) + mid_arr

    # Foreground ground plane
    ground_top = int(H * (horizon + 0.34)) if "water" not in scenes else int(H * (horizon + 0.30))
    if "water" not in scenes:
        gmask = np.zeros((H, W), dtype=np.float32)
        gmask[ground_top:, :] = 1.0
        gnoise = fbm(W, H, seed + 71, octaves=3, persistence=0.5, base=8)[..., None]
        ground_col = np.asarray(pal["near"], dtype=np.float32)[None, None, :] * (0.85 + 0.3 * gnoise)
        canvas = canvas * (1 - gmask[..., None]) + ground_col * gmask[..., None]

    # Subjects
    fig_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fig_layer)
    placed = False
    figure_h = H * (0.62 if camera in ("close-up", "portrait", "macro") else 0.34)
    if "person" in subjects or character:
        n_figs = 1 if camera in ("close-up", "portrait", "macro") else max(1, int(rng.integers(1, 4)))
        for i in range(n_figs):
            fx = int(W * (0.5 if n_figs == 1 else 0.18 + 0.62 * rng.random()))
            fy = int(H * (horizon + 0.42)) if n_figs == 1 else int(H * (horizon + 0.36 + 0.06 * rng.random()))
            _humanoid(fd, fx, fy, int(figure_h * (1.0 if n_figs == 1 else 0.82 + 0.3 * rng.random())),
                      tuple(pal["near"]), coat=tuple(_mix(pal["near"], pal["accent"], 0.18)),
                      tie=("businessman" in prompt.lower() or "businesswoman" in prompt.lower() or "office" in prompt.lower()))
        placed = True
    for animal_kind in ("lion", "rabbit", "bird", "animal"):
        if animal_kind in subjects:
            ax = int(W * (0.36 + 0.28 * rng.random()))
            ay = int(H * (horizon + 0.40))
            _animal(fd, ax, ay, H * 0.20, animal_kind if animal_kind != "animal" else "generic", tuple(pal["near"]))
            placed = True
    if "car" in subjects:
        cx, cy = int(W * 0.5), int(H * (horizon + 0.40))
        cw, ch = int(W * 0.24), int(H * 0.09)
        fd.rounded_rectangle([cx - cw // 2, cy - ch, cx + cw // 2, cy], radius=int(ch * 0.35), fill=tuple(pal["near"]))
        fd.ellipse([cx - cw * 0.36, cy - ch * 0.25, cx - cw * 0.12, cy + ch * 0.1], fill=(12, 12, 16))
        fd.ellipse([cx + cw * 0.12, cy - ch * 0.25, cx + cw * 0.36, cy + ch * 0.1], fill=(12, 12, 16))
        placed = True
    if "product" in subjects and not placed:
        px, py = int(W * 0.5), int(H * (horizon + 0.34))
        pw, ph = int(W * 0.12), int(H * 0.26)
        fd.rounded_rectangle([px - pw // 2, py - ph, px + pw // 2, py], radius=int(pw * 0.25),
                             fill=tuple(_mix(pal["accent"], (255, 255, 255), 0.35)))
        fd.rectangle([px - pw * 0.16, py - ph - int(H * 0.05), px + pw * 0.16, py - ph], fill=tuple(pal["mid"]))
        placed = True

    fig_layer = fig_layer.filter(ImageFilter.GaussianBlur(max(0.4, W / 2400)))
    canvas = canvas * (1 - (np.asarray(fig_layer, dtype=np.float32)[..., 3:4] / 255.0)) + \
        np.asarray(fig_layer, dtype=np.float32)[..., :3] * (np.asarray(fig_layer, dtype=np.float32)[..., 3:4] / 255.0)

    # Atmosphere
    if info["weather"] in ("fog", "mist") or style in ("cinematic", "documentary"):
        haze = fbm(W, H, seed + 91, octaves=4, persistence=0.5, base=2)[..., None]
        depth = np.clip((np.arange(H)[:, None, None] / H), 0, 1)
        fog = np.clip(haze * 0.55 * (1 - depth) + 0.12, 0, 0.6)
        canvas = canvas * (1 - fog) + np.asarray(pal["haze"], dtype=np.float32)[None, None, :] * fog

    # God rays
    if lighting in ("golden-hour", "cinematic", "blue-hour"):
        rays = np.abs(np.sin(np.linspace(0, 6.0, W) + fbm(W, 32, seed + 5, base=2).mean(axis=0) * 4))[None, :, None]
        rays = np.repeat(rays, H, axis=0) * np.clip(1 - np.arange(H)[:, None, None] / (H * 0.8), 0, 1)
        canvas += rays * np.asarray(pal["sun"], dtype=np.float32)[None, None, :] * 0.06

    # Weather particles
    if info["weather"] == "rain":
        drops = (rng.random((H, W)) > 0.9975).astype(np.float32)
        canvas += drops[..., None] * np.asarray([210, 220, 240], dtype=np.float32)[None, None, :] * 0.8
    if info["weather"] == "snow":
        flakes = (rng.random((H, W)) > 0.9982).astype(np.float32)
        canvas += flakes[..., None] * np.asarray([255, 255, 255], dtype=np.float32)[None, None, :] * 0.95

    # Grade: bloom + vignette + grain
    img = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), "RGB")
    bloom = img.filter(ImageFilter.GaussianBlur(W / 42)).filter(ImageFilter.GaussianBlur(W / 90))
    img = Image.blend(img, Image.fromarray(np.clip(np.asarray(img, dtype=np.float32) * 0.82 +
                                                   np.asarray(bloom, dtype=np.float32) * 0.32, 0, 255).astype(np.uint8)), 0.55)
    arr = np.asarray(img, dtype=np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / math.sqrt(2)
    arr *= (1 - 0.34 * (r ** 2.1))[..., None]
    grain = _rng(seed + 999).normal(0, 3.2, (H, W, 3)).astype(np.float32)
    arr += grain
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


# --------------------------------------------------------------------------- #
# Avatar rig (talking avatar frames)
# --------------------------------------------------------------------------- #

def render_avatar_frame(
    *,
    width: int = 720,
    height: int = 1280,
    seed: int = 0,
    skin: tuple[int, int, int] = (226, 178, 148),
    hair: tuple[int, int, int] = (42, 32, 28),
    clothing: tuple[int, int, int] = (38, 52, 96),
    background: tuple[int, int, int] = (24, 26, 40),
    expression: str = "neutral",
    mouth_open: float = 0.15,
    blink: float = 0.0,
    head_tilt: float = 0.0,
    gesture: str = "none",
    style: str = "photorealistic",
    lighting: str = "studio",
) -> Image.Image:
    """Composited presenter rig.

    `mouth_open`, `blink` and `head_tilt` are driven per-frame by the
    lip-sync/viseme engine, which is what makes talking-avatar output actually
    animate. Deterministic for a given (seed, params) pair.
    """
    W, H = int(width), int(height)
    pal = PALETTES.get(lighting, PALETTES["studio"])
    bg = _gradient(W, H, _mix(background, pal["sky_top"], 0.25), _mix(background, pal["sky_bottom"], 0.35), curve=1.1)
    img = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8), "RGB")
    img = Image.blend(img, img.filter(ImageFilter.GaussianBlur(W / 12)), 0.5)
    draw = ImageDraw.Draw(img, "RGBA")

    cx = W / 2 + head_tilt * W * 0.02
    head_r = W * 0.23
    head_cy = H * 0.34

    # shoulders / clothing
    draw.rounded_rectangle([cx - W * 0.46, H * 0.62, cx + W * 0.46, H * 1.02], radius=int(W * 0.18), fill=tuple(clothing))
    draw.ellipse([cx - W * 0.13, H * 0.585, cx + W * 0.13, H * 0.70], fill=tuple(skin))
    if gesture in ("open-palm", "talking", "presenting"):
        draw.ellipse([cx + W * 0.28, H * 0.74, cx + W * 0.46, H * 0.90], fill=tuple(_mix(skin, (0, 0, 0), 0.06)))
        draw.ellipse([cx - W * 0.46, H * 0.76, cx - W * 0.28, H * 0.92], fill=tuple(_mix(skin, (0, 0, 0), 0.10)))

    # neck + head
    draw.ellipse([cx - head_r, head_cy - head_r * 1.25, cx + head_r, head_cy + head_r * 1.18], fill=tuple(skin))
    # hair
    draw.ellipse([cx - head_r * 1.04, head_cy - head_r * 1.34, cx + head_r * 1.04, head_cy - head_r * 0.30],
                 fill=tuple(hair))
    draw.rectangle([cx - head_r * 1.02, head_cy - head_r * 1.2, cx - head_r * 0.72, head_cy - head_r * 0.1],
                   fill=tuple(hair))
    draw.rectangle([cx + head_r * 0.72, head_cy - head_r * 1.2, cx + head_r * 1.02, head_cy - head_r * 0.1],
                   fill=tuple(hair))

    # brows
    brow_y = head_cy - head_r * 0.42
    brow_drop = 0.10 if expression in ("serious", "angry") else (-0.05 if expression == "surprised" else 0.0)
    for side in (-1, 1):
        x0 = cx + side * head_r * 0.18
        x1 = cx + side * head_r * 0.72
        draw.line([(x0, brow_y + head_r * brow_drop), (x1, brow_y - head_r * 0.06 + head_r * brow_drop)],
                  fill=tuple(hair), width=max(2, int(head_r * 0.07)))

    # eyes (blink closes them)
    eye_y = head_cy - head_r * 0.18
    openness = 1.0 - float(np.clip(blink, 0, 1))
    for side in (-1, 1):
        ex = cx + side * head_r * 0.42
        eh = max(1.0, head_r * 0.14 * openness)
        draw.ellipse([ex - head_r * 0.17, eye_y - eh, ex + head_r * 0.17, eye_y + eh], fill=(250, 250, 252))
        if openness > 0.25:
            draw.ellipse([ex - head_r * 0.085, eye_y - eh * 0.85, ex + head_r * 0.085, eye_y + eh * 0.85],
                         fill=(58, 46, 44))
            draw.ellipse([ex - head_r * 0.03, eye_y - eh * 0.85, ex + head_r * 0.02, eye_y - eh * 0.35],
                         fill=(255, 255, 255))

    # nose + mouth driven by mouth_open
    draw.line([(cx, eye_y + head_r * 0.12), (cx - head_r * 0.02, eye_y + head_r * 0.34)],
              fill=tuple(_mix(skin, (0, 0, 0), 0.18)), width=max(2, int(head_r * 0.05)))
    mouth_y = head_cy + head_r * 0.52
    openness = float(np.clip(mouth_open, 0.0, 1.0))
    mouth_w = head_r * (0.52 if expression == "smile" else 0.46)
    mouth_h = max(2.0, head_r * (0.05 + 0.36 * openness))
    draw.rounded_rectangle([cx - mouth_w / 2, mouth_y - mouth_h / 2, cx + mouth_w / 2, mouth_y + mouth_h / 2],
                           radius=max(2, int(mouth_h / 2)), fill=(128, 62, 62))
    if openness < 0.08:
        draw.line([(cx - mouth_w / 2, mouth_y), (cx + mouth_w / 2, mouth_y)],
                  fill=tuple(_mix(skin, (0, 0, 0), 0.35)), width=max(1, int(head_r * 0.035)))

    # cheeks / blush for cartoon style
    if style in ("cartoon", "3d-character"):
        for side in (-1, 1):
            draw.ellipse([cx + side * head_r * 0.55 - head_r * 0.12, head_cy + head_r * 0.16,
                          cx + side * head_r * 0.55 + head_r * 0.12, head_cy + head_r * 0.34],
                         fill=(*skin, 60))

    # key + rim light
    arr = np.asarray(img, dtype=np.float32)
    arr += _radial_glow(W, H, 0.28, 0.18, 0.55, pal["sun"], intensity=0.20)
    arr += _radial_glow(W, H, 0.82, 0.30, 0.40, pal["accent"], intensity=0.12)
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / math.sqrt(2)
    arr *= (1 - 0.30 * (r ** 2.0))[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
