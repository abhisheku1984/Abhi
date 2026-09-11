"""Prompt engine (§5).

Structures a free-form idea into SUBJECT / ENVIRONMENT / ACTION / CAMERA /
LIGHTING / STYLE / MOOD / COMPOSITION / MOTION / AUDIO, and offers the
/enhance-prompt family of transforms.

Rule: it never silently changes the user's intended subject.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.engines.image.render import analyze_prompt

STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "with", "and", "or", "is", "are", "be",
    "this", "that", "create", "make", "generate", "show", "give", "me", "please", "image", "video",
    "cinematic", "shot", "scene", "very", "some", "into", "from", "about",
}

STYLE_PACKS: dict[str, dict[str, Any]] = {
    "cinematic": {
        "style": "cinematic film still, anamorphic texture, film grain, shallow depth of field",
        "camera": "slow dolly with subtle parallax",
        "lighting": "dramatic key light with soft fill",
        "mood": "expansive and atmospheric",
        "composition": "rule of thirds, layered foreground and background",
    },
    "advertisement": {
        "style": "premium commercial look, crisp product rendering, high-key polish",
        "camera": "smooth hero push-in",
        "lighting": "bright studio lighting with soft specular highlights",
        "mood": "aspirational and clean",
        "composition": "centred hero subject with negative space for copy",
    },
    "product": {
        "style": "studio product photography, seamless backdrop, macro detail",
        "camera": "slow orbit around the product",
        "lighting": "three-point studio lighting with softbox reflections",
        "mood": "precise and premium",
        "composition": "centred hero framing on a clean background",
    },
    "character": {
        "style": "consistent character design sheet, turnaround-friendly, clean linework",
        "camera": "neutral eye-level framing",
        "lighting": "even soft lighting that reads the silhouette clearly",
        "mood": "expressive and character-led",
        "composition": "full-body centred with clear proportions",
    },
    "kids-story": {
        "style": "warm storybook illustration, soft edges, friendly colour palette",
        "camera": "gentle push-in",
        "lighting": "warm daylight with soft shadows",
        "mood": "playful, safe and heartwarming",
        "composition": "simple centred staging, easy to read at a glance",
    },
    "social-media": {
        "style": "scroll-stopping vertical content, punchy colour grade, bold subject",
        "camera": "handheld energy with quick moves",
        "lighting": "bright, even and flattering",
        "mood": "upbeat and immediate",
        "composition": "centred subject with room for captions",
    },
    "documentary": {
        "style": "observational documentary look, natural texture, available light",
        "camera": "handheld with slow reframing",
        "lighting": "natural available light",
        "mood": "authentic and grounded",
        "composition": "wide establishing frames with human scale",
    },
}

LANGUAGE_NAMES = {
    "en": "English", "hi": "Hindi", "te": "Telugu", "ta": "Tamil", "kn": "Kannada",
    "ml": "Malayalam", "mr": "Marathi", "bn": "Bengali", "gu": "Gujarati", "pa": "Punjabi",
    "or": "Odia", "as": "Assamese", "ur": "Urdu",
}


def _subject_phrase(prompt: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9'-]+", prompt) if w.lower() not in STOPWORDS]
    if not words:
        return prompt.strip() or "an unnamed subject"
    # The leading noun phrase is treated as the subject and kept verbatim.
    return " ".join(words[:6])


def structure_prompt(prompt: str, *, style: str = "cinematic") -> dict[str, Any]:
    text = (prompt or "").strip()
    info = analyze_prompt(text)
    pack = STYLE_PACKS.get(style, STYLE_PACKS["cinematic"])
    subject = _subject_phrase(text)
    env_words = [w for w in info["words"] if w not in STOPWORDS]
    environment = ", ".join(env_words[:8]) if env_words else "an evocative location"
    action = "in motion" if any(w in text.lower() for w in ("walking", "running", "flying", "driving", "dancing")) else "present in frame"
    return {
        "subject": subject,
        "environment": environment,
        "action": action,
        "camera": pack["camera"],
        "lighting": info["lighting"].replace("-", " ") if info["lighting"] != "cinematic" else pack["lighting"],
        "style": pack["style"],
        "mood": pack["mood"],
        "composition": pack["composition"],
        "motion": info["camera"],
        "audio": "ambient bed with subtle foley",
        "negative": "low quality, blurry, distorted anatomy, watermark, text artefacts",
    }


def compose_prompt(structured: dict[str, Any]) -> str:
    order = ["subject", "environment", "action", "camera", "lighting", "style", "mood", "composition"]
    parts = [str(structured.get(k, "")).strip() for k in order]
    return ", ".join([p for p in parts if p])


def enhance(prompt: str, *, style: str = "cinematic") -> dict[str, Any]:
    structured = structure_prompt(prompt, style=style)
    return {
        "original": prompt,
        "enhanced": compose_prompt(structured),
        "structured": structured,
        "style": style,
    }


def shorten(prompt: str, *, max_words: int = 18) -> str:
    words = [w for w in (prompt or "").split() if w.lower() not in STOPWORDS]
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words])


def for_style(prompt: str, style: str) -> dict[str, Any]:
    return enhance(prompt, style=style)


def cinematic(prompt: str) -> dict[str, Any]:
    return enhance(prompt, style="cinematic")


def advertisement(prompt: str) -> dict[str, Any]:
    return enhance(prompt, style="advertisement")


def product(prompt: str) -> dict[str, Any]:
    return enhance(prompt, style="product")


def character_prompt(prompt: str) -> dict[str, Any]:
    return enhance(prompt, style="character")


def kids_story(prompt: str) -> dict[str, Any]:
    return enhance(prompt, style="kids-story")


def social_media(prompt: str) -> dict[str, Any]:
    return enhance(prompt, style="social-media")


TRANSFORMS = {
    "enhance": enhance,
    "shorten": shorten,
    "cinematic": cinematic,
    "advertisement": advertisement,
    "product": product,
    "character": character_prompt,
    "kids-story": kids_story,
    "social-media": social_media,
}


def apply_transform(kind: str, prompt: str) -> dict[str, Any]:
    fn = TRANSFORMS.get(kind, enhance)
    result = fn(prompt)
    if isinstance(result, str):
        return {"original": prompt, "enhanced": result, "structured": structure_prompt(prompt)}
    return result
