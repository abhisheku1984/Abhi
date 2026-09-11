"""Story-to-video (§11, §12): idea → characters → script → scenes → shots.

The planner is a deterministic writing engine (no LLM dependency): it produces
a real title, character sheet, narration, shot list and per-scene video prompts,
then generates each scene independently so a single scene can be regenerated
without touching the rest (§11).
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.core.logging import get_logger
from app.db.models import Project, Scene, Shot
from app.db.session import session_scope
from app.jobs import queue
from app.services import prompt_engine

log = get_logger("services.story")

BEATS = ["setup", "discovery", "conflict", "turning point", "resolution", "ending"]

ANIMAL_NAMES = {
    "lion": "Leo", "rabbit": "Mira", "tiger": "Tara", "elephant": "Ganesh", "bird": "Kiki",
    "fox": "Rusty", "bear": "Bruno", "monkey": "Chintu", "dog": "Rocky", "cat": "Simba",
}


def detect_characters(prompt: str) -> list[dict[str, Any]]:
    text = (prompt or "").lower()
    found: list[dict[str, Any]] = []
    for key, name in ANIMAL_NAMES.items():
        if key in text and len(found) < 4:
            found.append({
                "name": name,
                "kind": key,
                "description": f"a friendly {key}, expressive eyes, soft stylised fur",
                "role": "hero" if not found else "companion",
            })
    if not found:
        found.append({
            "name": "Asha",
            "kind": "person",
            "description": "a curious young protagonist with warm, expressive features",
            "role": "hero",
        })
    return found


def plan_story(prompt: str, *, duration_sec: int = 60, scene_count: Optional[int] = None,
               audience: str = "children", language: str = "en", seed: int = 0) -> dict[str, Any]:
    duration_sec = max(12, min(600, int(duration_sec or 60)))
    scene_count = scene_count or max(3, min(12, int(round(duration_sec / 12)) or 4))
    scene_len = round(duration_sec / scene_count, 2)
    characters = detect_characters(prompt)
    hero = characters[0]
    other = characters[1] if len(characters) > 1 else None
    topic = re.sub(r"^(create|make|generate)\s+(a|an)?\s*", "", (prompt or "").strip(), flags=re.I) or "a gentle adventure"
    title = " ".join(w.capitalize() for w in re.findall(r"[A-Za-z]+", topic)[:5]) or "A Gentle Adventure"

    scenes: list[dict[str, Any]] = []
    for i in range(scene_count):
        beat = BEATS[min(i, len(BEATS) - 1)] if i < len(BEATS) else "continuation"
        if i == 0:
            narration = (f"In a sunlit world, {hero['name']} the {hero['kind']} begins {topic}. "
                         f"Everything feels full of promise.")
            action = f"establishing wide of {hero['name']} entering the scene"
            camera, lens, motion = "drone", "24mm", "slow"
        elif i == scene_count - 1:
            narration = (f"{hero['name']} smiles, and the day ends quietly. "
                         f"{'Together with ' + other['name'] + ', ' if other else ''}the adventure settles into memory.")
            action = f"warm closing shot of {hero['name']}"
            camera, lens, motion = "crane", "50mm", "smooth"
        elif i % 3 == 1:
            narration = (f"Suddenly, something unexpected happens. {hero['name']} pauses, curious and a little unsure.")
            action = f"medium shot, {hero['name']} reacting with surprise"
            camera, lens, motion = "tracking", "35mm", "normal"
        else:
            narration = (f"{hero['name']} takes a brave step forward"
                         f"{', helped by ' + other['name'] if other else ''}. The world opens up.")
            action = f"hero moment, {hero['name']} moving with purpose"
            camera, lens, motion = "dolly", "50mm", "cinematic"

        dialogue = ""
        if other and i % 2 == 1:
            dialogue = f"{hero['name']}: \"Did you hear that?\"\n{other['name']}: \"I did — let's look together.\""
        elif i % 2 == 0 and audience == "children":
            dialogue = f"{hero['name']}: \"Today feels like a good day for a story.\""

        structured = prompt_engine.structure_prompt(
            f"{hero['description']}, {action}, {topic}", style="kids-story" if audience == "children" else "cinematic"
        )
        scenes.append({
            "index": i,
            "title": f"Scene {i + 1} — {beat.title()}",
            "beat": beat,
            "description": action,
            "narration": narration,
            "dialogue": dialogue,
            "script": narration + ("\n" + dialogue if dialogue else ""),
            "duration_sec": scene_len,
            "camera": camera,
            "lens": lens,
            "motion": motion,
            "lighting": "golden-hour" if i in (0, scene_count - 1) else "daylight",
            "music": "kids" if audience == "children" else "cinematic",
            "sfx": ["nature", "wind"][i % 2],
            "transition": "fade" if i == 0 or i == scene_count - 1 else "cut",
            "video_prompt": prompt_engine.compose_prompt(structured),
            "image_prompt": f"{structured['subject']}, {structured['environment']}, {structured['style']}",
            "characters": [c["name"] for c in characters],
        })

    return {
        "title": title,
        "logline": f"{hero['name']} discovers that courage comes in small, quiet steps.",
        "audience": audience,
        "language": language,
        "duration_sec": duration_sec,
        "scene_count": scene_count,
        "characters": characters,
        "scenes": scenes,
        "music_plan": {"genre": "kids" if audience == "children" else "cinematic", "arc": "gentle rise → warm resolve"},
        "sfx_plan": ["nature", "wind", "footstep"],
        "narration_style": "warm storyteller, unhurried pacing",
    }


# --------------------------------------------------------------------------- #
# Persistence helpers
# --------------------------------------------------------------------------- #

def persist_story(db: Session, project_id: str, plan: dict[str, Any]) -> list[Scene]:
    scenes: list[Scene] = []
    for item in plan["scenes"]:
        scene = Scene(
            id=new_id("scn_"),
            project_id=project_id,
            index=item["index"],
            title=item["title"],
            description=item["description"],
            script=item["script"],
            narration=item["narration"],
            dialogue=item["dialogue"],
            prompt=item["image_prompt"],
            video_prompt=item["video_prompt"],
            character_ids=item["characters"],
            duration_sec=item["duration_sec"],
            status="draft",
        )
        db.add(scene)
        db.flush()
        shot = Shot(
            id=new_id("sht_"),
            scene_id=scene.id,
            project_id=project_id,
            index=0,
            description=item["description"],
            camera=item["camera"],
            lens=item["lens"],
            motion=item["motion"],
            lighting=item["lighting"],
            duration_sec=item["duration_sec"],
            transition=item["transition"],
            dialogue=item["dialogue"],
            sfx=item["sfx"],
        )
        db.add(shot)
        scenes.append(scene)
    db.flush()
    return scenes


def run_story_job(job_id: str) -> None:
    """Generate (or regenerate) scene media for a story project."""
    from app.jobs.handlers import run_job_sync

    with session_scope() as db:
        from app.db.models import Job

        job = db.get(Job, job_id)
        if job is None:
            log.error("story_job_missing", job_id=job_id)
            return
        params = dict(job.params or {})
        project_id = params.get("project_id") or job.project_id
        target = params.get("target", "image")  # image | video
        scene_ids = params.get("scene_ids") or []
        plan = params.get("plan")

        if plan and not scene_ids:
            persist_story(db, project_id, plan)

        query = db.query(Scene).filter(Scene.project_id == project_id)
        if scene_ids:
            query = query.filter(Scene.id.in_(scene_ids))
        scenes = query.order_by(Scene.index).all()
        scene_payloads = [
            {
                "id": s.id, "prompt": s.prompt, "video_prompt": s.video_prompt,
                "duration": s.duration_sec, "index": s.index,
            }
            for s in scenes
        ]
        owner_id = job.owner_id
        model_id = job.model_id
        base_params = params.get("params", {})

    if not scene_payloads:
        queue.fail(job_id, {"code": "no_scenes", "message": "No scenes to generate.",
                            "suggested_action": "Create a story plan first."})
        return

    total = len(scene_payloads)
    completed: list[dict[str, Any]] = []
    for i, scene in enumerate(scene_payloads):
        if queue.is_cancelled(job_id):
            return
        child = run_job_sync(
            owner_id=owner_id,
            type="video" if target == "video" else "image",
            mode="text-to-video" if target == "video" else "text-to-image",
            model_id=model_id,
            project_id=project_id,
            params={
                **base_params,
                "prompt": scene["video_prompt"] if target == "video" else scene["prompt"],
                "duration": scene["duration"] if target == "video" else base_params.get("duration", 5),
            },
        )
        status = child.status if child else "failed"
        completed.append({"scene_id": scene["id"], "job_id": child.id if child else None, "status": status,
                          "assets": (child.result or {}).get("asset_ids", []) if child else []})
        queue.progress(job_id, round(100 * (i + 1) / total), f"scene {i + 1}/{total}")

        with session_scope() as db:
            from app.db.models import Scene as _Scene

            s = db.get(_Scene, scene["id"])
            if s and status == "completed":
                ids = (child.result or {}).get("asset_ids", []) if child else []
                if ids:
                    if target == "video":
                        s.video_asset_id = ids[0]
                    else:
                        s.image_asset_id = ids[0]
                    s.status = "ready"

    failed = [c for c in completed if c["status"] != "completed"]
    if failed and len(failed) == len(completed):
        queue.fail(job_id, {"code": "story_failed", "message": "Generation failed.",
                            "suggested_action": "Retry / Change model / Check GPU"})
        return
    queue.complete(job_id, {"scenes": completed, "count": len(completed),
                            "failed": len(failed)})


def _db():
    from app.db.session import SessionLocal

    return SessionLocal()
