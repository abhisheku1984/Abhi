"""Command-based AI assistant (§27).

User command → intent detection → action plan → tool execution → validation.
Destructive actions always require explicit confirmation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.ids import new_id
from app.core.logging import get_logger
from app.db.models import Asset, Project, Scene
from app.db.session import session_scope
from app.engines.registry import registry
from app.jobs import queue
from app.services import prompt_engine

log = get_logger("assistant")


@dataclass
class Plan:
    intent: str
    confidence: float = 0.7
    params: dict[str, Any] = field(default_factory=dict)
    action: Optional[dict[str, Any]] = None
    requires_confirmation: bool = False
    message: str = ""
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent, "confidence": round(self.confidence, 2), "params": self.params,
            "action": self.action, "requires_confirmation": self.requires_confirmation,
            "message": self.message, "suggestions": self.suggestions,
        }


KEYWORD_RULES: list[tuple[str, re.Pattern, float]] = [
    ("create_image", re.compile(r"\b(create|make|generate|draw|render)\b.*\b(image|picture|photo|poster|thumbnail)\b", re.I), 0.9),
    ("create_video", re.compile(r"\b(create|make|generate|render|animate)\b.*\b(video|clip|animation|ad|advertisement|reel|short)\b", re.I), 0.9),
    ("create_avatar", re.compile(r"\b(create|make|generate)\b.*\b(avatar|presenter|talking head|spokesperson)\b", re.I), 0.9),
    ("create_story", re.compile(r"\b(create|make|write|generate)\b.*\b(story|storyboard|scenes?)\b", re.I), 0.9),
    ("create_character", re.compile(r"\b(create|make|design)\b.*\b(character|persona)\b", re.I), 0.85),
    ("create_voice", re.compile(r"\b(generate|create|make)\b.*\b(voice|narration|speech|tts)\b", re.I), 0.85),
    ("create_audio", re.compile(r"\b(generate|create|make|compose)\b.*\b(music|sound|sfx|audio|ambience)\b", re.I), 0.85),
    ("enhance_prompt", re.compile(r"\b(enhance|improve|expand)\b.*\b(prompt)\b", re.I), 0.9),
    ("shorten_prompt", re.compile(r"\b(shorten|condense|simplify)\b.*\b(prompt)\b", re.I), 0.9),
    ("change_background", re.compile(r"\b(change|replace|swap)\b.*\b(background|backdrop|bg)\b", re.I), 0.9),
    ("upscale", re.compile(r"\b(upscale|increase resolution|make it bigger|enhance resolution)\b", re.I), 0.9),
    ("remove_background", re.compile(r"\b(remove|cut out|erase)\b.*\b(background|bg)\b", re.I), 0.9),
    ("make_cinematic", re.compile(r"\b(make|more)\b.*\b(cinematic|dramatic|epic)\b", re.I), 0.8),
    ("regenerate_scene", re.compile(r"\b(regenerate|redo|remake)\b.*\b(scene\s*\d+|scene)\b", re.I), 0.85),
    ("lip_sync", re.compile(r"\b(lip[\s-]?sync|sync (the )?lips)\b", re.I), 0.9),
    ("delete_project", re.compile(r"\b(delete|remove|destroy)\b.*\b(project)\b", re.I), 0.9),
    ("delete_asset", re.compile(r"\b(delete|remove|destroy)\b.*\b(asset|image|video|file)\b", re.I), 0.85),
    ("export", re.compile(r"\b(export|render|download)\b.*\b(mp4|webm|mov|youtube|instagram|reels|tiktok)\b", re.I), 0.85),
    ("help", re.compile(r"^\s*(help|what can you do|commands)\s*[?.!]?\s*$", re.I), 1.0),
]

DESTRUCTIVE = {"delete_project", "delete_asset"}


def detect_intent(command: str) -> tuple[str, float]:
    for intent, pattern, confidence in KEYWORD_RULES:
        if pattern.search(command or ""):
            return intent, confidence
    # Fall back on modality keywords.
    text = (command or "").lower()
    if "video" in text:
        return "create_video", 0.55
    if "image" in text or "picture" in text:
        return "create_image", 0.55
    if "avatar" in text:
        return "create_avatar", 0.55
    return "unknown", 0.2


def _extract_quoted(command: str) -> Optional[str]:
    m = re.search(r"[\"'“”]([^\"'“”]{3,})[\"'“”]", command or "")
    return m.group(1) if m else None


def _extract_language(command: str) -> str:
    names = {"hindi": "hi", "telugu": "te", "tamil": "ta", "kannada": "kn", "malayalam": "ml",
             "marathi": "mr", "bengali": "bn", "gujarati": "gu", "punjabi": "pa", "odia": "or",
             "assamese": "as", "urdu": "ur", "english": "en"}
    for name, code in names.items():
        if re.search(rf"\b{name}\b", command or "", re.I):
            return code
    return "en"


def _extract_seconds(command: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(second|sec|s)\b", command or "", re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+)\s*(minute|min)\b", command or "", re.I)
    if m:
        return float(m.group(1)) * 60
    return None


def _extract_scene(command: str) -> Optional[int]:
    m = re.search(r"scene\s*(\d+)", command or "", re.I)
    return int(m.group(1)) if m else None


def plan(command: str, *, context: Optional[dict[str, Any]] = None) -> Plan:
    """Turn a natural-language command into a concrete, inspectable plan."""
    context = context or {}
    intent, confidence = detect_intent(command)
    text = (command or "").strip()
    prompt_text = _extract_quoted(command) or text

    if intent == "help":
        return Plan("help", 1.0, message=(
            "Try: \"Create a cinematic image of a futuristic Hyderabad\", "
            "\"Turn this image into a 10 second video\", \"Generate Hindi narration\", "
            "\"Make scene 3 more cinematic\", \"Delete project\" (I'll ask first)."))

    if intent == "unknown":
        return Plan("unknown", confidence, message="I could not map that to an action yet.",
                    suggestions=["Create a cinematic image of a businessman in Hyderabad at sunset",
                                 "Turn this image into a 10 second video",
                                 "Generate Hindi narration for my script"])

    params: dict[str, Any] = {"prompt": prompt_text}
    action: dict[str, Any] = {"type": "noop"}

    if intent == "create_image":
        action = {"type": "generate", "kind": "image", "mode": "text-to-image"}
    elif intent == "create_video":
        params["duration"] = _extract_seconds(command) or 5
        action = {"type": "generate", "kind": "video", "mode": "text-to-video"}
    elif intent == "create_avatar":
        action = {"type": "generate", "kind": "avatar", "mode": "avatar-from-script", "script": prompt_text}
    elif intent == "create_story":
        params["duration"] = _extract_seconds(command) or 60
        action = {"type": "story", "kind": "story"}
    elif intent == "create_character":
        action = {"type": "create", "kind": "character", "name": prompt_text[:60]}
    elif intent == "create_voice":
        params["language"] = _extract_language(command)
        action = {"type": "generate", "kind": "voice", "mode": "text-to-speech", "text": prompt_text}
    elif intent == "create_audio":
        action = {"type": "generate", "kind": "audio", "mode": "background-music"}
    elif intent == "enhance_prompt":
        enhanced = prompt_engine.enhance(prompt_text)
        return Plan(intent, confidence, {"prompt": prompt_text}, {"type": "prompt", "result": enhanced},
                    message="Here is an enhanced version of your prompt:")
    elif intent == "shorten_prompt":
        return Plan(intent, confidence, {"prompt": prompt_text},
                    {"type": "prompt", "result": {"shortened": prompt_engine.shorten(prompt_text)}},
                    message="Shortened prompt:")
    elif intent == "change_background":
        action = {"type": "generate", "kind": "image", "mode": "background-replace",
                  "needs_asset": True}
    elif intent == "remove_background":
        action = {"type": "generate", "kind": "image", "mode": "background-remove", "needs_asset": True}
    elif intent == "upscale":
        action = {"type": "generate", "kind": "image", "mode": "upscale", "needs_asset": True}
    elif intent == "make_cinematic":
        params["style"] = "cinematic"
        action = {"type": "prompt", "result": prompt_engine.cinematic(prompt_text)}
    elif intent == "regenerate_scene":
        params["scene"] = _extract_scene(command)
        action = {"type": "regenerate_scene", "scene": _extract_scene(command)}
    elif intent == "lip_sync":
        action = {"type": "generate", "kind": "lipsync", "mode": "lip-sync", "needs_asset": True}
    elif intent == "export":
        action = {"type": "render", "preset": _detect_preset(command)}
    elif intent in DESTRUCTIVE:
        return Plan(intent, confidence, {"target": _extract_quoted(command) or context.get("selected_name")},
                    {"type": "delete", "target": intent.replace("delete_", "")},
                    requires_confirmation=True,
                    message="This will permanently delete it. Confirm to continue.")

    return Plan(intent, confidence, params, action,
                message=f"Planned: {intent.replace('_', ' ')}.")


def _detect_preset(command: str) -> str:
    text = (command or "").lower()
    for key in ("shorts", "reels", "tiktok", "instagram", "youtube", "linkedin", "facebook"):
        if key in text:
            return {"shorts": "youtube-shorts", "reels": "instagram-reels", "tiktok": "tiktok",
                    "instagram": "instagram-post", "youtube": "youtube", "linkedin": "linkedin",
                    "facebook": "facebook"}[key]
    return "youtube"


def execute(plan: Plan, *, user_id: str, project_id: Optional[str] = None,
            context: Optional[dict[str, Any]] = None, confirmed: bool = False) -> dict[str, Any]:
    context = context or {}
    if plan.requires_confirmation and not confirmed:
        return {"executed": False, "needs_confirmation": True, "plan": plan.to_dict()}

    action = plan.action or {}
    kind = action.get("type")

    if kind == "noop" or plan.intent in ("help", "unknown"):
        return {"executed": False, "plan": plan.to_dict(), "message": plan.message, "suggestions": plan.suggestions}

    if kind == "prompt":
        return {"executed": True, "result": action.get("result"), "message": plan.message}

    if kind == "delete":
        return _execute_delete(action, plan, user_id, context)

    if kind == "regenerate_scene":
        return _regenerate_scene(plan, user_id, project_id, context)

    if kind == "story":
        from app.services.story import plan_story

        story_plan = plan_story(plan.params.get("prompt", ""), duration_sec=plan.params.get("duration", 60))
        job = queue.enqueue(None, owner_id=user_id, type="story", mode="story-to-video",
                            project_id=project_id,
                            params={"project_id": project_id, "plan": story_plan,
                                    "target": "image",
                                    "params": {"aspect": "16:9", "resolution": "768"}})
        return {"executed": True, "job_id": job.id, "plan": story_plan,
                "message": f"Story '{story_plan['title']}' queued with {story_plan['scene_count']} scenes."}

    if kind == "generate":
        return _execute_generate(plan, action, user_id, project_id, context)

    if kind == "render":
        job = queue.enqueue(None, owner_id=user_id, type="render", mode="export", project_id=project_id,
                            params={"preset": action.get("preset", "youtube"),
                                    "timeline": context.get("timeline", {}),
                                    "name": context.get("timeline_name")})
        return {"executed": True, "job_id": job.id, "message": f"Export queued ({action.get('preset')})."}

    return {"executed": False, "message": "That action is not wired up yet.", "plan": plan.to_dict()}


def _execute_generate(plan: Plan, action: dict, user_id: str, project_id: Optional[str],
                      context: dict) -> dict[str, Any]:
    kind = action.get("kind")
    mode = action.get("mode", "text-to-image")
    params = dict(plan.params)
    references = list(context.get("references") or [])
    if action.get("needs_asset") and context.get("asset_id"):
        with session_scope() as db:
            asset = db.get(Asset, context["asset_id"])
            if asset:
                references.append({"asset_id": asset.id, "kind": asset.kind})
    if action.get("needs_asset") and not references:
        return {"executed": False, "message": "Select an asset first, then run this command again.",
                "plan": plan.to_dict()}

    if kind == "voice":
        params["text"] = action.get("text") or params.get("prompt")
    if kind == "avatar":
        params["script"] = action.get("script") or params.get("prompt")
    params["references"] = references
    job_type = {"image": "image", "video": "video", "avatar": "avatar", "voice": "voice",
                "audio": "audio", "lipsync": "lipsync"}.get(kind, "image")
    job = queue.enqueue(None, owner_id=user_id, type=job_type, mode=mode, project_id=project_id, params=params)
    return {"executed": True, "job_id": job.id, "message": f"{mode.replace('-', ' ').title()} queued.",
            "job_status_url": f"/api/v1/jobs/{job.id}"}


def _regenerate_scene(plan: Plan, user_id: str, project_id: Optional[str], context: dict) -> dict[str, Any]:
    scene_index = plan.params.get("scene") or context.get("scene_index")
    if scene_index is None or not project_id:
        return {"executed": False, "message": "Open a project scene first, then ask me to regenerate it.",
                "plan": plan.to_dict()}
    with session_scope() as db:
        scene = (
            db.query(Scene)
            .filter(Scene.project_id == project_id, Scene.index == int(scene_index) - 1)
            .one_or_none()
        )
        if scene is None:
            return {"executed": False, "message": f"Scene {scene_index} was not found.", "plan": plan.to_dict()}
        scene_id = scene.id
        prompt = scene.video_prompt or scene.prompt
        scene_prompt = f"{prompt}, cinematic, dramatic lighting, film grain"
        scene.video_prompt = scene_prompt
    job = queue.enqueue(None, owner_id=user_id, type="story", mode="regenerate-scene",
                        project_id=project_id, params={"project_id": project_id, "scene_ids": [scene_id],
                                                       "target": "image", "params": {}})
    return {"executed": True, "job_id": job.id, "message": f"Regenerating scene {scene_index}."}


def _execute_delete(action: dict, plan: Plan, user_id: str, context: dict) -> dict[str, Any]:
    target = action.get("target")
    from app.db.models import Project as _Project

    if target == "project":
        project_id = context.get("project_id")
        if not project_id:
            return {"executed": False, "message": "Open the project you want to delete first."}
        with session_scope() as db:
            project = db.get(_Project, project_id)
            if project and project.owner_id == user_id:
                db.delete(project)
                return {"executed": True, "message": f"Project '{project.name}' deleted."}
        return {"executed": False, "message": "Project not found or not owned by you."}

    if target == "asset":
        asset_id = context.get("asset_id")
        if not asset_id:
            return {"executed": False, "message": "Select the asset you want to delete first."}
        with session_scope() as db:
            asset = db.get(Asset, asset_id)
            if asset and asset.owner_id == user_id:
                db.delete(asset)
                return {"executed": True, "message": "Asset deleted."}
        return {"executed": False, "message": "Asset not found or not owned by you."}

    return {"executed": False, "message": "Nothing to delete."}
