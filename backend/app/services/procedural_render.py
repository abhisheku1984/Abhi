"""Deterministic CPU renderer.

IMPORTANT - HONEST POSITIONING
------------------------------
This module produces *real* pixels: seeded, deterministic, prompt-conditioned
raster art. It exists so the entire pipeline (prompt -> character -> scene ->
storyboard -> video -> export) is genuinely executable and testable on a
CPU-only machine with no model weights.

It is NOT a diffusion model and does not claim photorealism. Every asset it
produces is labelled `renderer: procedural` in provenance metadata so nothing
is ever mistaken for model output. Installing a real image model later
replaces it with one click in Model Manager.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageColor

# ---------------------------------------------------------------------------
# Noise
# ---------------------------------------------------------------------------


def _value_noise(h: int, w: int, res: int, rng: np.random.Generator) -> np.ndarray:
    grid = rng.random((max(2, res), max(2, res))).astype(np.float32)
    im = Image.fromarray((grid * 255).astype(np.uint8), mode="L").resize((w, h), Image.Resampling.BICUBIC)
    return np.asarray(im).astype(np.float32) / 255.0


def fbm(h: int, w: int, seed: int, octaves: int = 5, base: int = 3, persistence: float = 0.5) -> np.ndarray:
    """Fractal value noise in [0,1]."""
    rng = np.random.default_rng(seed & 0xFFFFFFFF)
    total = np.zeros((h, w), np.float32)
    amp, norm = 1.0, 0.0
    for o in range(max(1, octaves)):
        total += _value_noise(h, w, base * (2 ** o), rng) * amp
        norm += amp
        amp *= persistence
    return total / max(norm, 1e-6)


# ---------------------------------------------------------------------------
# Palettes / theme detection
# ---------------------------------------------------------------------------
@dataclass
class Palette:
    sky_top: tuple[int, int, int]
    sky_bottom: tuple[int, int, int]
    sun: tuple[int, int, int]
    far: tuple[int, int, int]
    mid: tuple[int, int, int]
    near: tuple[int, int, int]
    accent: tuple[int, int, int]
    fog: tuple[int, int, int]


THEMES: dict[str, Palette] = {
    "sunset": Palette((42, 30, 92), (255, 138, 84), (255, 226, 148), (86, 62, 96), (52, 36, 68), (26, 18, 38), (255, 176, 94), (255, 150, 110)),
    "sunrise": Palette((60, 78, 140), (255, 196, 150), (255, 240, 200), (96, 94, 122), (60, 60, 84), (32, 32, 50), (255, 210, 150), (255, 200, 170)),
    "day": Palette((86, 156, 224), (198, 226, 246), (255, 250, 224), (140, 168, 190), (86, 122, 96), (52, 82, 56), (255, 236, 170), (206, 224, 236)),
    "night": Palette((8, 10, 28), (30, 38, 74), (214, 226, 255), (24, 28, 52), (16, 20, 38), (8, 10, 22), (150, 180, 255), (40, 48, 84)),
    "ocean": Palette((64, 132, 200), (168, 214, 236), (255, 244, 214), (72, 120, 168), (26, 84, 128), (12, 48, 84), (120, 214, 226), (180, 216, 232)),
    "forest": Palette((120, 168, 132), (196, 216, 176), (255, 244, 190), (78, 110, 84), (44, 76, 52), (24, 46, 32), (150, 200, 120), (186, 206, 176)),
    "mountain": Palette((96, 136, 186), (206, 220, 236), (255, 248, 224), (110, 126, 150), (74, 88, 108), (46, 56, 70), (255, 230, 180), (206, 218, 232)),
    "desert": Palette((112, 148, 200), (246, 214, 158), (255, 236, 180), (206, 160, 104), (186, 130, 78), (140, 92, 54), (255, 196, 110), (232, 200, 154)),
    "snow": Palette((130, 158, 196), (222, 234, 246), (255, 255, 255), (196, 208, 224), (168, 184, 204), (140, 158, 180), (200, 226, 255), (226, 236, 246)),
    "city": Palette((40, 52, 92), (128, 148, 196), (255, 226, 170), (52, 62, 96), (34, 42, 70), (20, 26, 44), (255, 196, 110), (96, 112, 152)),
    "neon": Palette((16, 8, 40), (86, 24, 96), (255, 96, 200), (40, 16, 72), (26, 10, 52), (12, 6, 28), (86, 240, 255), (72, 32, 96)),
    "cyberpunk": Palette((10, 12, 40), (108, 24, 96), (86, 240, 255), (36, 20, 68), (22, 12, 46), (10, 8, 26), (255, 96, 200), (60, 28, 96)),
    "space": Palette((4, 4, 14), (16, 12, 40), (255, 240, 220), (28, 20, 56), (16, 12, 36), (8, 6, 20), (150, 120, 255), (24, 20, 48)),
    "studio": Palette((46, 48, 58), (86, 88, 100), (255, 250, 240), (60, 62, 72), (44, 46, 56), (28, 30, 38), (255, 240, 210), (90, 92, 104)),
    "fantasy": Palette((54, 34, 96), (172, 132, 200), (255, 226, 150), (72, 48, 110), (46, 30, 78), (26, 18, 46), (150, 240, 200), (140, 110, 180)),
    "garden": Palette((110, 168, 216), (216, 238, 216), (255, 248, 200), (96, 140, 104), (60, 106, 72), (36, 72, 48), (255, 176, 200), (206, 230, 206)),
    "rain": Palette((58, 66, 88), (124, 136, 158), (200, 214, 236), (60, 70, 92), (44, 52, 70), (28, 34, 48), (150, 180, 210), (120, 132, 154)),
    "fire": Palette((40, 12, 12), (180, 60, 24), (255, 214, 120), (76, 24, 20), (48, 16, 14), (26, 8, 8), (255, 140, 60), (120, 50, 30)),
}

THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "sunset": ("sunset", "dusk", "golden hour", "sundown", "evening"),
    "sunrise": ("sunrise", "dawn", "daybreak", "morning"),
    "night": ("night", "midnight", "dark", "moonlit", "moonlight"),
    "ocean": ("ocean", "sea", "beach", "waves", "coast", "shore", "island"),
    "forest": ("forest", "jungle", "woods", "trees", "woodland", "rainforest"),
    "mountain": ("mountain", "mountains", "peak", "hills", "valley", "cliff", "alps"),
    "desert": ("desert", "sand", "dunes", "sahara", "arid"),
    "snow": ("snow", "winter", "frozen", "ice", "arctic", "blizzard"),
    "city": ("city", "urban", "street", "skyline", "downtown", "hyderabad", "tokyo", "new york", "london", "metropolis"),
    "neon": ("neon", "nightclub", "synthwave", "laser"),
    "cyberpunk": ("cyberpunk", "futuristic city", "sci-fi city", "cyber", "blade runner"),
    "space": ("space", "galaxy", "planet", "cosmos", "stars", "astronaut", "nebula", "cosmic"),
    "studio": ("studio", "portrait", "indoor", "office", "room", "headshot", "product"),
    "fantasy": ("fantasy", "magic", "dragon", "castle", "kingdom", "enchanted", "mythical"),
    "garden": ("garden", "flowers", "meadow", "park", "blossom"),
    "rain": ("rain", "storm", "monsoon", "rainy"),
    "fire": ("fire", "lava", "volcano", "flames", "burning"),
}

STYLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "cinematic": ("cinematic", "film", "movie", "anamorphic", "widescreen"),
    "anime": ("anime", "manga", "cartoon", "illustration", "comic"),
    "photoreal": ("photoreal", "photorealistic", "photo", "realistic", "dslr", "35mm"),
    "painting": ("painting", "oil", "watercolor", "canvas", "artistic"),
    "sketch": ("sketch", "pencil", "line art", "drawing", "charcoal"),
    "3d": ("3d", "render", "unreal", "blender", "octane", "cgi"),
    "vintage": ("vintage", "retro", "film grain", "analog", "80s"),
}

MOOD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "epic": ("epic", "grand", "majestic", "heroic"),
    "calm": ("calm", "serene", "peaceful", "quiet", "tranquil"),
    "dark": ("dark", "moody", "grim", "ominous", "horror"),
    "joyful": ("happy", "joyful", "cheerful", "bright", "playful"),
    "mysterious": ("mysterious", "mystery", "eerie", "enigmatic"),
}


def _match(text: str, table: dict[str, tuple[str, ...]], default: str) -> str:
    low = (text or "").lower()
    best, best_pos = default, 10 ** 9
    for key, words in table.items():
        for w in words:
            pos = low.find(w)
            if pos >= 0 and pos < best_pos:
                best, best_pos = key, pos
    return best


def seed_from_text(text: str, salt: int = 0) -> int:
    """Stable 32-bit hash so the same prompt+seed always renders identically."""
    h = 2166136261 ^ (salt & 0xFFFFFFFF)
    for ch in (text or ""):
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


# ---------------------------------------------------------------------------
# Character identity (character consistency, §20)
# ---------------------------------------------------------------------------
@dataclass
class CharacterIdentity:
    name: str
    seed: int
    skin: tuple[int, int, int]
    hair: tuple[int, int, int]
    top: tuple[int, int, int]
    bottom: tuple[int, int, int]
    accent: tuple[int, int, int]
    hair_style: str = "short"
    height: float = 0.42          # fraction of frame height
    build: float = 1.0
    age: str = "adult"
    accessory: str = ""
    palette: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "name": self.name, "seed": self.seed,
            "skin": self.skin, "hair": self.hair, "top": self.top,
            "bottom": self.bottom, "accent": self.accent,
            "hair_style": self.hair_style, "height": self.height, "build": self.build,
            "age": self.age, "accessory": self.accessory, "palette": self.palette,
        }


_HAIR_COLORS = [(32, 26, 24), (72, 48, 30), (140, 92, 48), (196, 152, 84), (226, 206, 160),
                (120, 60, 40), (186, 96, 60), (84, 84, 92), (220, 220, 226)]
_SKIN_COLORS = [(250, 224, 200), (238, 200, 168), (216, 172, 138), (180, 132, 96),
                (140, 96, 66), (96, 64, 44), (246, 216, 196)]
_CLOTH_COLORS = [(58, 84, 148), (176, 64, 64), (72, 132, 96), (216, 168, 72), (96, 72, 148),
                 (40, 44, 60), (206, 206, 214), (214, 132, 84), (64, 148, 160), (150, 92, 132)]


def build_identity(name: str, description: str = "", seed: int | None = None, **overrides) -> CharacterIdentity:
    """Derive a stable visual identity from a character's name/spec.

    Same name + description => same face/hair/clothing colours forever, which is
    what makes a character recognisable across shots without a diffusion model.
    """
    base = seed if seed is not None else seed_from_text(f"{name}::{description}")
    rng = np.random.default_rng(base & 0xFFFFFFFF)

    def pick(table):
        return tuple(int(v) for v in table[int(rng.integers(0, len(table)))])

    hair_style = ["short", "long", "ponytail", "bun", "curly", "bald", "braided"][int(rng.integers(0, 7))]
    age = "adult"
    low = (description or "").lower()
    if "child" in low or "kid" in low or "young" in low:
        age = "child"
    elif "old" in low or "elder" in low or "aged" in low:
        age = "elder"
    if "long hair" in low:
        hair_style = "long"
    if "bald" in low:
        hair_style = "bald"

    height = 0.42 if age == "adult" else (0.30 if age == "child" else 0.38)
    ident = CharacterIdentity(
        name=name,
        seed=int(base),
        skin=pick(_SKIN_COLORS),
        hair=pick(_HAIR_COLORS),
        top=pick(_CLOTH_COLORS),
        bottom=pick(_CLOTH_COLORS),
        accent=pick(_CLOTH_COLORS),
        hair_style=hair_style,
        height=height,
        build=1.0 + float(rng.uniform(-0.08, 0.12)),
        age=age,
    )
    ident.palette = ["#%02x%02x%02x" % c for c in (ident.skin, ident.hair, ident.top, ident.bottom, ident.accent)]
    for k, v in overrides.items():
        if hasattr(ident, k) and v not in (None, ""):
            setattr(ident, k, v)
    return ident


def draw_character(draw: ImageDraw.ImageDraw, ident: CharacterIdentity, x: int, ground_y: int,
                   height_px: int, facing: int = 1, pose: str = "stand",
                   mouth_open: float = 0.0, arm_angle: float = 0.0) -> dict:
    """Stylised humanoid. Returns the bounding box so callers can place effects.

    `mouth_open` (0..1) is driven by audio RMS for talking avatars - this is a
    real, audio-driven mouth animation, not a canned loop.
    """
    h = max(24, int(height_px))
    w = int(h * 0.30 * ident.build)
    head_r = max(6, int(h * 0.115))
    cx = int(x)
    top_y = int(ground_y - h)

    skin, hair, top, bottom, accent = ident.skin, ident.hair, ident.top, ident.bottom, ident.accent

    # legs
    leg_w = max(3, int(w * 0.22))
    hip_y = int(top_y + h * 0.52)
    draw.line([(cx - int(w * 0.16), hip_y), (cx - int(w * 0.20), ground_y)], fill=bottom, width=leg_w)
    draw.line([(cx + int(w * 0.16), hip_y), (cx + int(w * 0.20), ground_y)], fill=bottom, width=leg_w)
    # shoes
    draw.ellipse([cx - int(w * 0.26) - 2, ground_y - leg_w, cx - int(w * 0.06), ground_y + leg_w // 2], fill=(40, 40, 48))
    draw.ellipse([cx + int(w * 0.06), ground_y - leg_w, cx + int(w * 0.26) + 2, ground_y + leg_w // 2], fill=(40, 40, 48))

    # torso
    shoulder_y = int(top_y + h * 0.20)
    draw.polygon(
        [(cx - int(w * 0.30), shoulder_y), (cx + int(w * 0.30), shoulder_y),
         (cx + int(w * 0.24), hip_y + int(h * 0.02)), (cx - int(w * 0.24), hip_y + int(h * 0.02))],
        fill=top,
    )
    draw.rectangle([cx - int(w * 0.30), shoulder_y, cx + int(w * 0.30), shoulder_y + max(2, int(h * 0.03))], fill=accent)

    # arms
    arm_w = max(3, int(w * 0.16))
    swing = math.sin(math.radians(arm_angle)) * h * 0.10
    draw.line([(cx - int(w * 0.30), shoulder_y + int(h * 0.02)),
               (cx - int(w * 0.40), shoulder_y + int(h * 0.26) + swing)], fill=top, width=arm_w)
    draw.line([(cx + int(w * 0.30), shoulder_y + int(h * 0.02)),
               (cx + int(w * 0.40), shoulder_y + int(h * 0.26) - swing)], fill=top, width=arm_w)
    draw.ellipse([cx - int(w * 0.44), shoulder_y + int(h * 0.26) + swing - arm_w,
                  cx - int(w * 0.36), shoulder_y + int(h * 0.26) + swing + arm_w], fill=ident.skin)
    draw.ellipse([cx + int(w * 0.36), shoulder_y + int(h * 0.26) - swing - arm_w,
                  cx + int(w * 0.44), shoulder_y + int(h * 0.26) - swing + arm_w], fill=ident.skin)

    # neck + head
    neck_y = int(shoulder_y - h * 0.02)
    draw.line([(cx, neck_y), (cx, top_y + head_r * 2)], fill=skin, width=max(3, int(head_r * 0.55)))
    head_cy = top_y + head_r
    draw.ellipse([cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r], fill=skin)

    # hair
    if ident.hair_style != "bald":
        if ident.hair_style in ("long", "braided"):
            draw.polygon(
                [(cx - head_r - 2, head_cy + int(head_r * 0.2)),
                 (cx - head_r - 1, head_cy - head_r - 1),
                 (cx + head_r + 1, head_cy - head_r - 1),
                 (cx + head_r + 2, head_cy + int(head_r * 0.2)),
                 (cx + int(head_r * 0.75), head_cy + int(head_r * 1.5)),
                 (cx - int(head_r * 0.75), head_cy + int(head_r * 1.5))],
                fill=hair,
            )
        elif ident.hair_style == "ponytail":
            draw.arc([cx - head_r - 1, head_cy - head_r - 1, cx + head_r + 1, head_cy + head_r + 1], 180, 360, fill=hair, width=max(3, head_r // 2))
            draw.ellipse([cx + int(head_r * 0.7) * facing, head_cy, cx + int(head_r * 1.5) * facing, head_cy + int(head_r * 1.2)], fill=hair)
        else:
            draw.arc([cx - head_r - 1, head_cy - head_r - 1, cx + head_r + 1, head_cy + head_r + 1], 180, 360, fill=hair, width=max(3, head_r // 2))

    # eyes
    eye_dx = int(head_r * 0.40)
    eye_y = head_cy - int(head_r * 0.10)
    eye_r = max(1, head_r // 7)
    draw.ellipse([cx - eye_dx - eye_r, eye_y - eye_r, cx - eye_dx + eye_r, eye_y + eye_r], fill=(30, 30, 36))
    draw.ellipse([cx + eye_dx - eye_r, eye_y - eye_r, cx + eye_dx + eye_r, eye_y + eye_r], fill=(30, 30, 36))

    # mouth (audio driven when used for talking avatars)
    mouth_w = int(head_r * 0.5)
    mouth_h = max(1, int(head_r * 0.10 + mouth_open * head_r * 0.42))
    mouth_y = head_cy + int(head_r * 0.42)
    draw.ellipse([cx - mouth_w // 2, mouth_y - mouth_h, cx + mouth_w // 2, mouth_y + mouth_h],
                 fill=(120, 62, 62))

    return {"x": cx, "top": top_y, "bottom": ground_y, "head_center": head_cy, "head_radius": head_r}


# ---------------------------------------------------------------------------
# Scene composition
# ---------------------------------------------------------------------------
def _vgrad(h: int, w: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> np.ndarray:
    t = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    out = np.zeros((h, w, 3), np.float32)
    for c in range(3):
        out[..., c] = top[c] * (1 - t) + bottom[c] * t
    return out


def _haze(arr: np.ndarray, color: tuple[int, int, int], strength: np.ndarray) -> np.ndarray:
    s = np.clip(strength, 0, 1)[..., None]
    return arr * (1 - s) + np.array(color, np.float32) * s


def render_scene(prompt: str, width: int = 1024, height: int = 1024, seed: int | None = None,
                 style: str = "", mood: str = "", theme_override: str = "",
                 characters: list[CharacterIdentity] | None = None,
                 lighting: str = "", camera: str = "static", progress=None) -> Image.Image:
    """Compose a full scene as a PIL image. Deterministic for (prompt, seed)."""
    w, h = int(width), int(height)
    if progress:
        progress(0.05, "analysing prompt")

    text = f"{prompt} {style} {mood}"
    theme = theme_override or _match(text, THEME_KEYWORDS, "day")
    pal = THEMES.get(theme, THEMES["day"])
    style_key = _match(text, STYLE_KEYWORDS, "cinematic")
    mood_key = _match(text, MOOD_KEYWORDS, "calm")
    base_seed = seed if seed is not None else seed_from_text(prompt)

    rng = np.random.default_rng(base_seed & 0xFFFFFFFF)
    arr = _vgrad(h, w, pal.sky_top, pal.sky_bottom)

    horizon = int(h * (0.52 + float(rng.uniform(-0.06, 0.10))))
    if theme in ("space", "studio", "city", "neon", "cyberpunk"):
        horizon = int(h * (0.62 + float(rng.uniform(-0.05, 0.08))))

    # sun / moon
    if progress:
        progress(0.15, "placing light source")
    sun_x = int(w * float(rng.uniform(0.18, 0.82)))
    sun_y = int(horizon * float(rng.uniform(0.25, 0.75)))
    sun_r = int(min(w, h) * (0.055 if theme != "space" else 0.035))
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.sqrt((xx - sun_x) ** 2 + (yy - sun_y) ** 2)
    glow = np.clip(1.0 - dist / (sun_r * 7.0), 0, 1) ** 2.2
    arr += glow[..., None] * np.array(pal.sun, np.float32) * 0.55
    disc = (dist < sun_r).astype(np.float32)
    arr = arr * (1 - disc[..., None]) + np.array(pal.sun, np.float32) * disc[..., None]

    # stars
    if theme in ("night", "space", "neon", "cyberpunk"):
        star_mask = (rng.random((h, w)) > 0.9975).astype(np.float32)
        star_mask = np.maximum(star_mask, np.roll(star_mask, 1, axis=1) * 0.5)
        arr += star_mask[..., None] * 235

    # clouds / nebula
    if progress:
        progress(0.3, "generating atmosphere")
    cloud = fbm(h, w, base_seed + 11, octaves=6, base=3, persistence=0.55)
    cloud = np.clip((cloud - 0.45) * 2.4, 0, 1)
    if theme == "space":
        neb = fbm(h, w, base_seed + 31, octaves=5, base=2, persistence=0.6)
        tinted = np.stack([neb * pal.accent[0], neb * pal.fog[0], neb * pal.accent[2]], axis=-1)
        arr = arr + tinted * 0.5
        cloud *= 0.35
    sky_mask = np.clip((horizon - yy) / max(horizon, 1), 0, 1) ** 0.6
    arr = _haze(arr, pal.fog, cloud * sky_mask * 0.7)

    # distant ridge line
    if progress:
        progress(0.45, "building terrain")
    layers = 3 if theme not in ("studio", "space") else 1
    for li in range(layers):
        ridge = fbm(1, w, base_seed + 100 + li * 17, octaves=4, base=2, persistence=0.5)[0]
        amp = (0.10 - li * 0.02) * h
        base_y = horizon - int(h * (0.14 - li * 0.05))
        ys = base_y - (ridge * amp).astype(int)
        col = [pal.far, pal.mid, pal.near][li]
        depth = np.clip((yy - np.asarray(ys)[None, :]) / max(1, h), -1, 1)
        mask = (np.arange(h)[:, None] >= np.asarray(ys)[None, :]).astype(np.float32)
        shade = 1.0 - li * 0.06
        block = np.array([col[0] * shade, col[1] * shade, col[2] * shade], np.float32)
        arr = arr * (1 - mask[..., None]) + block * mask[..., None]
        # atmospheric depth
        fog_strength = mask * np.clip(1.0 - (np.arange(h)[:, None] - horizon) / (h * 0.7), 0, 1) * (0.35 - li * 0.08)
        arr = _haze(arr, pal.fog, fog_strength)

    # ground plane
    ground_mask = (np.arange(h)[:, None] >= horizon).astype(np.float32)
    gnoise = fbm(h, w, base_seed + 555, octaves=5, base=4, persistence=0.5)
    ground_col = np.array(pal.near, np.float32)
    # gnoise/ripple are (h, w) height maps -> add a channel axis to colour the
    # (h, w, 3) ground plane.
    ground = ground_col * (0.72 + gnoise[..., None] * 0.55)
    if theme in ("ocean", "rain"):
        ripple = np.abs(np.sin((yy * 0.35) + fbm(h, w, base_seed + 77, octaves=3, base=6) * 12.0))
        ground = ground * (0.75 + ripple[..., None] * 0.45)
        # sun glitter path
        glitter = np.clip(1.0 - np.abs(xx - sun_x) / (w * 0.16), 0, 1) ** 2 * (yy > horizon)
        ground += glitter[..., None] * np.array(pal.sun, np.float32) * 0.35
    arr = arr * (1 - ground_mask[..., None]) + ground * ground_mask[..., None]

    # city lights
    if theme in ("city", "neon", "cyberpunk"):
        if progress:
            progress(0.6, "adding city lights")
        lights = (rng.random((h, w)) > 0.9955).astype(np.float32) * ground_mask
        lights *= (np.abs(xx - sun_x) < w * 0.75)
        arr += lights[..., None] * np.array(pal.accent, np.float32) * 0.9

    # rain streaks
    if theme == "rain":
        streaks = (rng.random((h, w)) > 0.9985).astype(np.float32)
        streaks = np.maximum.reduce([np.roll(streaks, k, axis=0) for k in range(6)])
        arr += streaks[..., None] * 90

    arr = np.clip(arr, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr, "RGB")

    # ---- characters -------------------------------------------------------
    if characters:
        if progress:
            progress(0.72, "placing characters")
        draw = ImageDraw.Draw(img, "RGBA")
        n = len(characters)
        ground_y = int(min(h - h * 0.02, horizon + (h - horizon) * 0.72))
        for i, ident in enumerate(characters):
            span = w * 0.6
            x = int(w * 0.2 + span * ((i + 0.5) / max(1, n)))
            jitter = int(rng.integers(-int(w * 0.03), int(w * 0.03)))
            draw_character(draw, ident, x + jitter, ground_y - int(rng.integers(0, h * 0.03)),
                           int(h * ident.height), facing=1 if i % 2 == 0 else -1)

    # ---- style grading ----------------------------------------------------
    if progress:
        progress(0.85, "applying style")
    img = _grade(img, style_key, mood_key, theme, pal)

    if camera in ("tilt up", "tilt down", "dolly", "orbit"):
        img = img.filter(ImageFilter.GaussianBlur(0.4))

    if progress:
        progress(1.0, "done")
    return img.convert("RGB")


def _grade(img: Image.Image, style_key: str, mood_key: str, theme: str, pal: Palette) -> Image.Image:
    from PIL import ImageEnhance

    if style_key == "anime":
        img = ImageEnhance.Color(img).enhance(1.45)
        img = ImageEnhance.Contrast(img).enhance(1.12)
    elif style_key == "painting":
        img = img.filter(ImageFilter.SMOOTH_MORE)
        img = ImageEnhance.Color(img).enhance(1.12)
    elif style_key == "sketch":
        gray = img.convert("L")
        edges = gray.filter(ImageFilter.FIND_EDGES)
        img = Image.merge("RGB", (gray, gray, gray))
        img = Image.blend(img, Image.merge("RGB", (edges, edges, edges)), 0.35)
    elif style_key == "3d":
        img = ImageEnhance.Contrast(img).enhance(1.15)
        img = ImageEnhance.Sharpness(img).enhance(1.25)
    elif style_key == "vintage":
        img = ImageEnhance.Color(img).enhance(0.82)
        img = ImageEnhance.Contrast(img).enhance(0.92)
        img = _sepia_tint(img, 0.35)
    elif style_key == "photoreal":
        img = ImageEnhance.Sharpness(img).enhance(1.15)
    else:  # cinematic
        img = ImageEnhance.Contrast(img).enhance(1.1)
        img = ImageEnhance.Color(img).enhance(0.95)

    if mood_key == "dark":
        img = ImageEnhance.Brightness(img).enhance(0.82)
    elif mood_key == "joyful":
        img = ImageEnhance.Brightness(img).enhance(1.08)
        img = ImageEnhance.Color(img).enhance(1.12)
    elif mood_key == "epic":
        img = ImageEnhance.Contrast(img).enhance(1.15)
    elif mood_key == "mysterious":
        img = ImageEnhance.Brightness(img).enhance(0.9)
        img = ImageEnhance.Color(img).enhance(0.9)

    img = _vignette(img, 0.35)
    return img


def _sepia_tint(img: Image.Image, amount: float) -> Image.Image:
    r, g, b = img.convert("RGB").split()
    lut_r = [min(255, int(0.393 * i + 0.769 * i + 0.189 * i)) for i in range(256)]
    lut_g = [min(255, int(0.349 * i + 0.686 * i + 0.168 * i)) for i in range(256)]
    lut_b = [min(255, int(0.272 * i + 0.534 * i + 0.131 * i)) for i in range(256)]
    out = Image.merge("RGB", (r.point(lut_r), g.point(lut_g), b.point(lut_b)))
    return Image.blend(img.convert("RGB"), out, amount)


def _vignette(img: Image.Image, strength: float) -> Image.Image:
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    h, w, _ = arr.shape
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w / 2, h / 2
    dist = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2) / np.sqrt(2)
    arr *= (1 - strength * np.clip(dist ** 1.7, 0, 1))[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


# ---------------------------------------------------------------------------
# Avatar portrait renderer (used by the talking-avatar pipeline)
# ---------------------------------------------------------------------------
def render_avatar_portrait(ident: CharacterIdentity, width: int = 768, height: int = 768,
                           background: str = "studio", mouth_open: float = 0.0,
                           eye_blink: float = 0.0, head_tilt: float = 0.0,
                           expression: str = "neutral", avatar_type: str = "photorealistic_human") -> Image.Image:
    """Render a portrait frame. Parameterised so the talking-avatar pipeline can
    animate mouth/eyes/head across frames."""
    w, h = int(width), int(height)
    pal = THEMES.get(background if background in THEMES else "studio", THEMES["studio"])
    arr = _vgrad(h, w, pal.sky_top, pal.sky_bottom)
    yy, xx = np.mgrid[0:h, 0:w]
    glow = np.clip(1.0 - np.sqrt((xx - w * 0.5) ** 2 + (yy - h * 0.35) ** 2) / (w * 0.9), 0, 1) ** 2
    arr += glow[..., None] * np.array(pal.sun, np.float32) * 0.18
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")

    cx = int(w * 0.5 + math.sin(math.radians(head_tilt)) * w * 0.02)
    head_r = int(min(w, h) * 0.20)
    head_cy = int(h * 0.40)
    skin, hair, top, accent = ident.skin, ident.hair, ident.top, ident.accent

    # shoulders / clothing
    draw.polygon([(cx - w * 0.30, h), (cx - w * 0.22, head_cy + head_r * 1.9),
                  (cx + w * 0.22, head_cy + head_r * 1.9), (cx + w * 0.30, h)], fill=top)
    draw.polygon([(cx - w * 0.09, head_cy + head_r * 1.85), (cx, head_cy + head_r * 1.35),
                  (cx + w * 0.09, head_cy + head_r * 1.85)], fill=accent)

    # neck + head + ears
    draw.rectangle([cx - head_r * 0.30, head_cy + head_r * 0.6, cx + head_r * 0.30, head_cy + head_r * 1.5], fill=skin)
    draw.ellipse([cx - head_r * 1.06, head_cy - head_r * 0.15, cx - head_r * 0.82, head_cy + head_r * 0.35], fill=skin)
    draw.ellipse([cx + head_r * 0.82, head_cy - head_r * 0.15, cx + head_r * 1.06, head_cy + head_r * 0.35], fill=skin)
    draw.ellipse([cx - head_r, head_cy - head_r * 1.12, cx + head_r, head_cy + head_r * 1.12], fill=skin)

    # subtle shading
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    sd.ellipse([cx - head_r, head_cy - head_r * 1.12, cx + head_r * 0.1, head_cy + head_r * 1.12], fill=(0, 0, 0, 26))
    img = Image.alpha_composite(img.convert("RGBA"), shade).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")

    # hair
    if ident.hair_style != "bald":
        if ident.hair_style in ("long", "braided"):
            draw.polygon([(cx - head_r * 1.08, head_cy + head_r * 0.6), (cx - head_r * 1.1, head_cy - head_r * 1.15),
                          (cx + head_r * 1.1, head_cy - head_r * 1.15), (cx + head_r * 1.08, head_cy + head_r * 0.6),
                          (cx + head_r * 0.86, head_cy + head_r * 2.1), (cx - head_r * 0.86, head_cy + head_r * 2.1)], fill=hair)
        elif ident.hair_style == "ponytail":
            draw.arc([cx - head_r * 1.05, head_cy - head_r * 1.18, cx + head_r * 1.05, head_cy + head_r * 0.6], 180, 360, fill=hair, width=int(head_r * 0.42))
            draw.ellipse([cx + head_r * 0.95, head_cy - head_r * 0.4, cx + head_r * 1.5, head_cy + head_r * 1.5], fill=hair)
        elif ident.hair_style == "bun":
            draw.arc([cx - head_r * 1.05, head_cy - head_r * 1.18, cx + head_r * 1.05, head_cy + head_r * 0.2], 180, 360, fill=hair, width=int(head_r * 0.4))
            draw.ellipse([cx - head_r * 0.36, head_cy - head_r * 1.8, cx + head_r * 0.36, head_cy - head_r * 1.05], fill=hair)
        else:
            draw.arc([cx - head_r * 1.05, head_cy - head_r * 1.18, cx + head_r * 1.05, head_cy + head_r * 0.55], 180, 360, fill=hair, width=int(head_r * 0.44))

    # brows
    brow_y = head_cy - head_r * 0.42
    brow_w = head_r * 0.34
    lift = -head_r * 0.06 if expression in ("happy", "surprised") else 0
    draw.line([(cx - head_r * 0.62, brow_y + lift), (cx - head_r * 0.62 + brow_w, brow_y + lift - head_r * 0.04)], fill=hair, width=max(2, int(head_r * 0.06)))
    draw.line([(cx + head_r * 0.62, brow_y + lift), (cx + head_r * 0.62 - brow_w, brow_y + lift - head_r * 0.04)], fill=hair, width=max(2, int(head_r * 0.06)))

    # eyes (blink closes them)
    eye_dx = head_r * 0.40
    eye_y = head_cy - head_r * 0.14
    open_h = head_r * 0.14 * (1 - eye_blink)
    for sgn in (-1, 1):
        ex = cx + sgn * eye_dx
        if open_h < 1.0:
            draw.line([(ex - head_r * 0.17, eye_y), (ex + head_r * 0.17, eye_y)], fill=(40, 34, 34), width=max(2, int(head_r * 0.05)))
        else:
            draw.ellipse([ex - head_r * 0.19, eye_y - head_r * 0.15, ex + head_r * 0.19, eye_y + head_r * 0.15], fill=(250, 250, 252))
            draw.ellipse([ex - head_r * 0.10, eye_y - head_r * 0.11, ex + head_r * 0.10, eye_y + head_r * 0.11], fill=(58, 46, 40))
            draw.ellipse([ex - head_r * 0.035, eye_y - head_r * 0.10, ex + head_r * 0.035, eye_y + head_r * 0.10], fill=(16, 12, 12))
            draw.ellipse([ex - head_r * 0.06, eye_y - head_r * 0.13, ex + head_r * 0.01, eye_y - head_r * 0.05], fill=(255, 255, 255, 200))

    # nose + mouth (mouth_open drives lip sync)
    draw.arc([cx - head_r * 0.10, head_cy + head_r * 0.02, cx + head_r * 0.10, head_cy + head_r * 0.34], 200, 340, fill=(0, 0, 0, 60), width=max(1, int(head_r * 0.04)))
    mw = head_r * 0.44
    mh = max(1.0, head_r * 0.05 + mouth_open * head_r * 0.36)
    my = head_cy + head_r * 0.55
    curve = head_r * 0.10 if expression == "happy" else (-head_r * 0.06 if expression == "sad" else 0)
    draw.ellipse([cx - mw, my - mh - curve, cx + mw, my + mh - curve], fill=(150, 66, 66))
    if mouth_open > 0.12:
        draw.ellipse([cx - mw * 0.7, my - mh * 0.4 - curve, cx + mw * 0.7, my + mh * 0.9 - curve], fill=(88, 32, 38))

    img = _vignette(img, 0.28)
    return img


def parse_hex_color(value: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    try:
        return ImageColor.getrgb(value)[:3]
    except Exception:
        return default
