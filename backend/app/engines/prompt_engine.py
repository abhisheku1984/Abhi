"""AI Prompt Engine (spec §29).

Structures a raw idea into SUBJECT / ENVIRONMENT / ACTION / CAMERA / LIGHTING /
STYLE / MOOD / COMPOSITION / MOTION / AUDIO, then renders it back into a rich
prompt plus a matched negative prompt and suggested generation parameters.

Runs fully offline (deterministic linguistic rules). If an LLM adapter is
available it is used to refine the result; if not, the deterministic path is
used and reported as such - never faked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.services.procedural_render import (
    MOOD_KEYWORDS,
    STYLE_KEYWORDS,
    THEME_KEYWORDS,
    _match,
    seed_from_text,
)

STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "with", "and", "or", "is", "are",
    "create", "generate", "make", "show", "image", "video", "picture", "shot", "scene",
    "cinematic", "highly", "detailed", "please", "very", "some", "this", "that", "into",
}

CAMERA_HINTS: dict[str, tuple[str, ...]] = {
    "wide": ("landscape", "vast", "panorama", "wide shot", "cityscape"),
    "close-up": ("portrait", "face", "close up", "closeup", "headshot", "eyes"),
    "aerial": ("aerial", "drone", "from above", "bird's eye", "overhead"),
    "low angle": ("towering", "hero shot", "looking up", "monument"),
    "macro": ("insect", "dew", "texture", "macro", "tiny"),
    "tracking": ("walking through", "following", "runs through", "chase"),
    "orbit": ("surrounded", "circle", "orbit"),
}

LIGHTING_HINTS: dict[str, tuple[str, ...]] = {
    "golden hour": ("sunset", "golden hour", "dusk", "sundown"),
    "blue hour": ("blue hour", "twilight", "dawn"),
    "daylight": ("morning", "afternoon", "midday", "daylight", "sunny"),
    "neon": ("neon", "nightlife", "club", "synthwave"),
    "studio": ("studio", "product shot", "headshot", "indoor", "office"),
    "low key": ("dark", "moody", "noir", "shadow"),
    "high key": ("bright", "airy", "soft light", "white background"),
}

COMPOSITION_HINTS: dict[str, tuple[str, ...]] = {
    "rule of thirds": ("standing", "walking", "horizon", "field"),
    "centered": ("portrait", "product", "symmetry", "temple", "throne"),
    "leading lines": ("road", "path", "bridge", "corridor", "stairs", "railway"),
    "foreground framing": ("through", "behind", "frame", "window", "archway"),
    "symmetrical": ("symmetry", "reflection", "palace", "mandala"),
}

AUDIO_HINTS: dict[str, tuple[str, ...]] = {
    "wind ambience": ("desert", "mountain", "open", "field", "sky"),
    "city ambience": ("city", "street", "traffic", "urban", "market"),
    "ocean waves": ("ocean", "sea", "beach", "island", "coast"),
    "forest ambience": ("forest", "jungle", "woods", "garden", "trees"),
    "rain": ("rain", "storm", "monsoon"),
    "birdsong": ("garden", "meadow", "morning", "spring"),
    "soft drone": ("space", "future", "abstract", "dream"),
}


@dataclass
class StructuredPrompt:
    subject: str = ""
    environment: str = ""
    action: str = ""
    camera: str = ""
    lens: str = ""
    lighting: str = ""
    style: str = ""
    mood: str = ""
    composition: str = ""
    motion: str = ""
    audio: str = ""
    details: list[str] = field(default_factory=list)
    negatives: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    source: str = "deterministic"

    def to_dict(self) -> dict:
        return {
            "subject": self.subject, "environment": self.environment, "action": self.action,
            "camera": self.camera, "lens": self.lens, "lighting": self.lighting,
            "style": self.style, "mood": self.mood, "composition": self.composition,
            "motion": self.motion, "audio": self.audio, "details": self.details,
            "negatives": self.negatives, "parameters": self.parameters, "source": self.source,
        }

    def render(self) -> str:
        parts = []
        if self.subject:
            parts.append(self.subject)
        if self.action:
            parts.append(self.action)
        if self.environment:
            parts.append(f"in {self.environment}")
        if self.details:
            parts.append(", ".join(self.details))
        if self.camera:
            parts.append(f"{self.camera} camera")
        if self.lens:
            parts.append(f"{self.lens} lens")
        if self.lighting:
            parts.append(f"{self.lighting} lighting")
        if self.composition:
            parts.append(f"{self.composition} composition")
        if self.style:
            parts.append(f"{self.style} style")
        if self.mood:
            parts.append(f"{self.mood} mood")
        return ", ".join(p for p in parts if p)


MODE_PRESETS: dict[str, dict[str, Any]] = {
    "enhance": {"lens": "35mm", "style": "cinematic", "add": ["highly detailed", "professional color grading"]},
    "cinematic": {"lens": "50mm", "style": "cinematic", "camera": "wide", "lighting": "golden hour",
                  "add": ["anamorphic look", "shallow depth of field", "film grain"]},
    "advertisement": {"lens": "85mm", "style": "photoreal", "lighting": "studio",
                      "add": ["premium product photography", "clean studio backdrop", "softbox lighting"]},
    "product": {"lens": "85mm", "style": "photoreal", "lighting": "studio", "composition": "centered",
                "add": ["seamless white background", "crisp reflections", "commercial lighting"]},
    "character": {"lens": "85mm", "style": "cinematic", "camera": "close-up",
                  "add": ["consistent character design", "expressive face", "detailed costume"]},
    "kids_story": {"lens": "35mm", "style": "anime", "mood": "joyful",
                   "add": ["colorful children's book illustration", "friendly characters", "soft rounded shapes"]},
    "social_media": {"lens": "35mm", "style": "vivid", "camera": "close-up",
                     "add": ["vertical composition", "eye-catching", "scroll-stopping"]},
    "documentary": {"lens": "24mm", "style": "photoreal", "camera": "wide",
                    "add": ["natural light", "handheld documentary feel", "authentic"]},
    "fashion": {"lens": "135mm", "style": "photoreal", "lighting": "studio",
                "add": ["editorial fashion photography", "dramatic rim light"]},
    "architecture": {"lens": "18mm", "style": "photoreal", "camera": "wide",
                     "add": ["architectural photography", "perspective correction", "clean lines"]},
}

NEGATIVE_BASE = [
    "blurry", "low quality", "jpeg artifacts", "watermark", "text overlay",
    "deformed", "extra limbs", "distorted face", "oversaturated",
]


def _first_sentence(text: str) -> str:
    m = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return m[0] if m else (text or "")


def _extract_subject(text: str) -> str:
    low = (text or "").lower()
    m = re.search(r"\b(?:of|showing|featuring|with)\s+(?:a|an|the)?\s*([a-z0-9' -]{3,60})", low)
    if m:
        return m.group(1).strip()
    words = [w for w in re.findall(r"[A-Za-z0-9']+", text or "") if w.lower() not in STOPWORDS]
    if not words:
        return (text or "").strip()[:60]
    # subject = first meaningful noun phrase (up to 5 words)
    return " ".join(words[:5])


def _extract_action(text: str) -> str:
    verbs = ("walking", "running", "flying", "standing", "sitting", "dancing", "driving",
             "fighting", "exploring", "speaking", "presenting", "cooking", "reading",
             "playing", "climbing", "swimming", "riding", "building", "demonstrating")
    low = (text or "").lower()
    for v in verbs:
        if v in low:
            idx = low.find(v)
            frag = (text or "")[idx: idx + 60]
            frag = re.split(r"[,.;]", frag)[0]
            return frag.strip()
    return ""


def _hint_lookup(text: str, table: dict[str, tuple[str, ...]], default: str) -> str:
    low = (text or "").lower()
    best, best_pos = default, 10 ** 9
    for key, words in table.items():
        for w in words:
            pos = low.find(w)
            if pos >= 0 and pos < best_pos:
                best, best_pos = key, pos
    return best


def structure_prompt(text: str, mode: str = "enhance", llm: Any = None) -> StructuredPrompt:
    """Break a raw idea into the ten canonical fields."""
    raw = _first_sentence(text)
    low = (text or "").lower()
    preset = MODE_PRESETS.get((mode or "enhance").lower(), MODE_PRESETS["enhance"])

    theme = _match(text, THEME_KEYWORDS, "")
    sp = StructuredPrompt(
        subject=_extract_subject(raw),
        environment=THEME_KEYWORDS and (theme or "") or "",
        action=_extract_action(text),
        camera=_hint_lookup(text, CAMERA_HINTS, preset.get("camera", "wide")),
        lens=preset.get("lens", "35mm"),
        lighting=_hint_lookup(text, LIGHTING_HINTS, preset.get("lighting", "cinematic")),
        style=_match(text, STYLE_KEYWORDS, preset.get("style", "cinematic")),
        mood=_match(text, MOOD_KEYWORDS, preset.get("mood", "calm")),
        composition=_hint_lookup(text, COMPOSITION_HINTS, preset.get("composition", "rule of thirds")),
        motion="slow cinematic motion" if "video" in low or mode == "cinematic" else "static",
        audio=_hint_lookup(text, AUDIO_HINTS, "soft ambience"),
        details=list(preset.get("add", [])),
        negatives=list(NEGATIVE_BASE),
        source="deterministic",
    )
    sp.environment = sp.environment or "a carefully composed environment"

    if llm is not None:
        try:
            refined = llm_json_struct(raw, mode, llm)
            if refined:
                for k, v in refined.items():
                    if hasattr(sp, k) and isinstance(v, str) and v:
                        setattr(sp, k, v)
                sp.source = "llm"
        except Exception:
            pass
    return sp


def llm_json_struct(text: str, mode: str, llm: Any) -> dict | None:
    from app.adapters.implementations.llm_adapters import llm_json

    prompt = (
        "Structure this creative brief into strict JSON with keys: subject, environment, "
        "action, camera, lens, lighting, style, mood, composition, motion, audio. "
        f"Mode: {mode}. Brief: {text}"
    )
    return llm_json(prompt)


def enhance_prompt(text: str, mode: str = "enhance", use_llm: bool = True) -> dict:
    """Public API used by /api/prompts/enhance and the creation workspace."""
    llm = None
    if use_llm:
        from app.adapters.implementations.llm_adapters import best_llm

        llm = best_llm()
    sp = structure_prompt(text, mode, llm=llm)
    params = {
        "guidance": 7.5,
        "steps": 30,
        "motion": sp.motion,
        "lens": sp.lens,
        "camera": sp.camera,
        "lighting": sp.lighting,
    }
    if mode in ("product", "advertisement"):
        params.update({"steps": 40, "guidance": 8.5, "aspect_ratio": "1:1"})
    if mode == "social_media":
        params.update({"aspect_ratio": "9:16"})
    if mode == "cinematic":
        params.update({"aspect_ratio": "16:9", "steps": 35})
    sp.parameters = params
    return {
        "original": text,
        "structured": sp.to_dict(),
        "enhanced_prompt": sp.render(),
        "negative_prompt": ", ".join(sp.negatives),
        "parameters": params,
        "mode": mode,
        "source": sp.source,
        "seed_suggestion": seed_from_text(text) % 1000000,
    }


def suggest_camera_for(action: str, environment: str) -> str:
    return _hint_lookup(f"{action} {environment}", CAMERA_HINTS, "wide")
