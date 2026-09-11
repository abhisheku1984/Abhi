"""
Job pipelines.

Each handler turns a queued job into real artifacts on disk. Handlers are
explicit about which layer does the work:

* generative steps resolve a *provider* (cloud / GPU host / local demo engine)
* rendering, editing, mixing steps call the local FFmpeg/Pillow/NumPy engines,
  which are the finished feature and need no GPU.

Nothing here fabricates output. If a generative provider is unavailable the job
fails with the provider's remediation message, which surfaces verbatim in the UI.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import get_settings
from .db import execute, jdumps, jloads, query_all, query_one, update
from .jobs import JobContext, handler
from .media import video as video_engine
from .providers import local as local_providers
from .providers.base import ProviderNotConfigured, get_provider
from .services import (
    asset_path,
    compose_prompt,
    persist_file,
    project_seed_bias,
)
from .storage import tmp_dir_for

log = logging.getLogger("abhi.pipelines")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _persist_result(
    job: dict,
    ctx: JobContext,
    result: dict,
    *,
    provider: str,
    prompt: str = "",
    params: dict | None = None,
    parent_asset_id: str | None = None,
) -> dict:
    meta = result.get("meta") or {}
    engine = meta.get("engine", provider)
    files = result.get("files") or []
    assets = []
    for index, payload in enumerate(files):
        if index > 0:
            ctx.check_cancelled()
        asset = persist_file(
            payload,
            project_id=job.get("project_id"),
            provider=provider,
            engine=engine,
            prompt=prompt,
            params=params or job.get("params") or {},
            meta=meta,
            job_id=job["id"],
            parent_asset_id=parent_asset_id,
        )
        assets.append(asset)
    return {
        "asset_ids": [a["id"] for a in assets],
        "assets": assets,
        "engine": engine,
        "quality": meta.get("quality", "real"),
        "note": meta.get("note", ""),
        "meta": meta,
    }


def _resolve_provider(capability: str, job: dict):
    requested = (job.get("params") or {}).get("provider") or job.get("provider") or "auto"
    provider = get_provider(capability, requested)
    provider.require_available()
    return provider


# --------------------------------------------------------------------------
# image
# --------------------------------------------------------------------------
@handler("image.generate")
def image_generate(job: dict, ctx: JobContext) -> dict:
    params = job.get("params") or {}
    prompt = (params.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("'prompt' is required")
    character_ids = params.get("character_ids") or []
    full_prompt, prompt_meta = compose_prompt(
        prompt,
        project_id=job.get("project_id"),
        character_ids=character_ids,
        style_suffix=params.get("style_suffix") or "",
    )
    if character_ids:
        ctx.log(f"character lock applied: {', '.join(c['name'] for c in prompt_meta['characters']) or 'none'}")

    provider = _resolve_provider("image", job)
    ctx.progress(0.1, f"provider: {provider.info.label} ({provider.info.quality})")
    seed = params.get("seed")
    if not seed:
        seed = project_seed_bias(job.get("project_id"), character_ids)
    width = int(params.get("width") or 1024)
    height = int(params.get("height") or 576)

    ctx.progress(0.25, "generating image")
    ctx.check_cancelled()
    run_params = {
        **params,
        "prompt": full_prompt,
        "seed": seed,
        "width": width,
        "height": height,
        "size": params.get("size") or f"{width}x{height}",
    }
    result = provider.run(run_params, {"job_id": job["id"], "work_dir": str(tmp_dir_for(job["id"]))})
    ctx.progress(0.85, "saving asset")
    out = _persist_result(job, ctx, result, provider=provider.id, prompt=full_prompt, params=run_params)
    out["prompt_used"] = full_prompt
    out["characters"] = prompt_meta["characters"]
    out["seed"] = seed

    # Link the generated image back to its storyboard shot, if it came from one.
    shot_id = params.get("shot_id")
    if shot_id and out["asset_ids"]:
        execute(
            "UPDATE shots SET asset_id = ?, status = 'generated' WHERE id = ?",
            (out["asset_ids"][0], shot_id),
        )
        ctx.log(f"linked image to shot {shot_id}")
    return out


@handler("image.edit")
def image_edit(job: dict, ctx: JobContext) -> dict:
    params = job.get("params") or {}
    asset_id = params.get("asset_id")
    if not asset_id:
        raise ValueError("'asset_id' is required")
    source = asset_path(asset_id)
    if not source:
        raise ValueError(f"asset {asset_id} not found on disk")
    ops = params.get("ops") or []
    if not isinstance(ops, list) or not ops:
        raise ValueError("'ops' must be a non-empty list")
    ctx.progress(0.3, f"applying {len(ops)} edit operation(s)")
    provider = get_provider("image_edit", params.get("provider") or "local-pillow-edit")
    result = provider.run(
        {"ops": ops, "format": params.get("format") or "png", "quality": params.get("quality") or 92},
        {"source_path": str(source), "job_id": job["id"], "work_dir": str(tmp_dir_for(job["id"]))},
    )
    ctx.progress(0.85, "saving edited asset")
    return _persist_result(
        job, ctx, result, provider=provider.id, params=params, parent_asset_id=asset_id
    )


@handler("image.montage")
def image_montage(job: dict, ctx: JobContext) -> dict:
    params = job.get("params") or {}
    asset_ids = params.get("asset_ids") or []
    paths = [asset_path(a) for a in asset_ids]
    paths = [p for p in paths if p]
    if not paths:
        raise ValueError("no usable asset ids provided")
    ctx.progress(0.4, f"composing {len(paths)} tile(s)")
    provider = get_provider("image_util", "local-montage")
    result = provider.run(
        {"columns": params.get("columns") or 4, "cell": params.get("cell") or 320},
        {"source_paths": [str(p) for p in paths], "work_dir": str(tmp_dir_for(job["id"]))},
    )
    return _persist_result(job, ctx, result, provider=provider.id, params=params)


@handler("image.title_card")
def image_title_card(job: dict, ctx: JobContext) -> dict:
    params = job.get("params") or {}
    provider = get_provider("image_util", "local-title-card")
    result = provider.run(
        {
            "text": params.get("text") or params.get("title") or "Untitled",
            "subtitle": params.get("subtitle") or "",
            "width": params.get("width") or 1280,
            "height": params.get("height") or 720,
            "background": params.get("background") or "#101828",
            "color": params.get("color") or "#f2f4f7",
        },
        {"job_id": job["id"], "work_dir": str(tmp_dir_for(job["id"]))},
    )
    return _persist_result(job, ctx, result, provider=provider.id, params=params)


# --------------------------------------------------------------------------
# audio
# --------------------------------------------------------------------------
@handler("audio.tts")
def audio_tts(job: dict, ctx: JobContext) -> dict:
    params = job.get("params") or {}
    text = (params.get("text") or "").strip()
    if not text:
        raise ValueError("'text' is required")
    provider = _resolve_provider("tts", job)
    ctx.progress(0.2, f"provider: {provider.info.label} ({provider.info.quality})")
    result = provider.run(
        {**params, "text": text},
        {"job_id": job["id"], "work_dir": str(tmp_dir_for(job["id"]))},
    )
    ctx.progress(0.8, "saving narration")
    out = _persist_result(job, ctx, result, provider=provider.id, prompt=text, params=params)
    if params.get("shot_id") and out["asset_ids"]:
        update("shots", params["shot_id"], {"audio_asset_id": out["asset_ids"][0]})
        ctx.log(f"linked narration to shot {params['shot_id']}")
    return out


@handler("audio.mix")
def audio_mix(job: dict, ctx: JobContext) -> dict:
    params = job.get("params") or {}
    entries = params.get("tracks") or []
    tracks = []
    for entry in entries:
        path = asset_path(entry.get("asset_id"))
        if path:
            tracks.append({"path": str(path), "gain": float(entry.get("gain", 1.0))})
    if not tracks:
        raise ValueError("no usable audio tracks provided")
    ctx.progress(0.4, f"mixing {len(tracks)} track(s)")
    from .providers.local import _run_audio_mix

    result = _run_audio_mix(
        params, {"tracks": tracks, "job_id": job["id"], "work_dir": str(tmp_dir_for(job["id"]))}
    )
    return _persist_result(job, ctx, result, provider="local-mixer", params=params)


# --------------------------------------------------------------------------
# video
# --------------------------------------------------------------------------
def _resolve_clips(params: dict, ctx: JobContext) -> tuple[list[dict], list[float]]:
    """Turn a clip spec (asset ids) into renderer clip dicts with real paths."""
    clips: list[dict] = []
    durations: list[float] = []
    for index, entry in enumerate(params.get("clips") or []):
        ctx.check_cancelled()
        path = asset_path(entry.get("asset_id") or entry.get("assetId"))
        if not path:
            ctx.log(f"[error] clip {index}: asset not found, skipped")
            continue
        duration = float(entry.get("duration") or entry.get("duration_s") or 3.0)
        clips.append(
            {
                "path": path,
                "image": path,
                "kind": entry.get("kind") or "image",
                "duration": duration,
                "start": float(entry.get("start") or 0.0),
                "motion": entry.get("motion") or "none",
                "caption": entry.get("caption") or "",
                "speed": float(entry.get("speed") or 1.0),
                "transition": entry.get("transition"),
            }
        )
        durations.append(duration)
    return clips, durations


@handler("video.render")
def video_render(job: dict, ctx: JobContext) -> dict:
    params = job.get("params") or {}
    clips, durations = _resolve_clips(params, ctx)
    if not clips:
        raise ValueError("no renderable clips provided (need asset ids of images or videos)")
    ctx.progress(0.15, f"rendering {len(clips)} clip(s) with FFmpeg")
    provider = get_provider("video_render", params.get("provider") or "local-ffmpeg-render")
    ctx.progress(0.35, "building segments")
    result = provider.run(
        params,
        {
            "clips": clips,
            "job_id": job["id"],
            "work_dir": str(tmp_dir_for(job["id"])),
        },
    )
    ctx.progress(0.8, "saving video")
    out = _persist_result(job, ctx, result, provider=provider.id, params=params)

    audio_asset_id = params.get("audio_asset_id")
    if audio_asset_id and out["asset_ids"]:
        ctx.progress(0.88, "muxing narration")
        audio_path = asset_path(audio_asset_id)
        video_path = asset_path(out["asset_ids"][0])
        if audio_path and video_path:
            muxed = tmp_dir_for(job["id"]) / "with_audio.mp4"
            try:
                video_engine.mux_audio(
                    video_path, audio_path, muxed, mode=params.get("audio_mode") or "replace"
                )
                stored = persist_file(
                    {
                        "data": muxed.read_bytes(),
                        "kind": "video",
                        "filename": "render-with-audio.mp4",
                        "mime": "video/mp4",
                        "duration_s": video_engine.media_duration(muxed),
                    },
                    project_id=job.get("project_id"),
                    provider=provider.id,
                    engine="ffmpeg-mux",
                    prompt="",
                    params=params,
                    meta={"engine": "ffmpeg-mux", "quality": "real", "narrated": True},
                    job_id=job["id"],
                    parent_asset_id=out["asset_ids"][0],
                )
                out["asset_ids"].append(stored["id"])
                out["assets"].append(stored)
                out["narrated"] = True
                ctx.log("narration muxed successfully")
            except Exception as exc:
                ctx.log(f"[error] muxing narration failed (video kept): {exc}")
    return out


@handler("video.render_storyboard")
def video_render_storyboard(job: dict, ctx: JobContext) -> dict:
    """
    Orchestrator: render every generated shot of a story into one film.
    Shots without an image get a typographic title card so nothing is silently skipped.
    """
    params = job.get("params") or {}
    story_id = params.get("story_id")
    if not story_id:
        raise ValueError("'story_id' is required")
    shots = query_all("SELECT * FROM shots WHERE story_id = ? ORDER BY index_no", (story_id,))
    if not shots:
        raise ValueError("this story has no shots yet")

    clips: list[dict] = []
    placeholders = 0
    for index, shot in enumerate(shots):
        ctx.check_cancelled()
        ctx.progress(0.05 + 0.3 * (index / len(shots)), f"collecting shot {index + 1}/{len(shots)}")
        path = asset_path(shot["asset_id"]) if shot["asset_id"] else None
        if not path:
            from .providers.local import _run_title_card

            card = _run_title_card(
                {
                    "text": shot["title"] or f"Shot {index + 1}",
                    "subtitle": "image not generated",
                    "width": int(params.get("width") or 1280),
                    "height": int(params.get("height") or 720),
                },
                {"work_dir": str(tmp_dir_for(job["id"]))},
            )
            path = tmp_dir_for(job["id"]) / f"card_{index}.png"
            path.write_bytes(card["files"][0]["data"])
            placeholders += 1
            ctx.log(f"shot {index + 1}: no image, using title card")
        clips.append(
            {
                "path": path,
                "kind": "image",
                "duration": float(shot["duration_s"] or 3.0),
                "motion": shot["motion"] or "none",
                "caption": shot["caption"] or "",
            }
        )

    ctx.progress(0.4, f"rendering {len(clips)} clip(s)")
    provider = get_provider("video_render", "local-ffmpeg-render")
    result = provider.run(
        params,
        {"clips": clips, "job_id": job["id"], "work_dir": str(tmp_dir_for(job["id"]))},
    )
    out = _persist_result(job, ctx, result, provider=provider.id, params=params)
    out["placeholders"] = placeholders
    out["shots"] = len(shots)

    # narration: use the story's per-shot audio if present, otherwise a TTS read of the logline
    audio_asset_id = params.get("audio_asset_id")
    if not audio_asset_id:
        audio_ids = [s["audio_asset_id"] for s in shots if s["audio_asset_id"]]
        if audio_ids:
            audio_asset_id = audio_ids[0]
    if audio_asset_id and out["asset_ids"]:
        audio_path = asset_path(audio_asset_id)
        video_path = asset_path(out["asset_ids"][0])
        if audio_path and video_path:
            ctx.progress(0.88, "muxing narration")
            muxed = tmp_dir_for(job["id"]) / "story_with_audio.mp4"
            try:
                video_engine.mux_audio(video_path, audio_path, muxed, mode="replace")
                stored = persist_file(
                    {
                        "data": muxed.read_bytes(),
                        "kind": "video",
                        "filename": "story-with-narration.mp4",
                        "mime": "video/mp4",
                        "duration_s": video_engine.media_duration(muxed),
                    },
                    project_id=job.get("project_id"),
                    provider=provider.id,
                    engine="ffmpeg-mux",
                    params=params,
                    meta={"engine": "ffmpeg-mux", "quality": "real", "narrated": True},
                    job_id=job["id"],
                    parent_asset_id=out["asset_ids"][0],
                )
                out["asset_ids"].append(stored["id"])
                out["assets"].append(stored)
                out["narrated"] = True
            except Exception as exc:
                ctx.log(f"[error] narration mux failed: {exc}")
    return out


@handler("video.generate")
def video_generate(job: dict, ctx: JobContext) -> dict:
    """Generative video via a cloud or GPU provider. Fails honestly when none exist."""
    params = job.get("params") or {}
    prompt = (params.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("'prompt' is required")
    provider = _resolve_provider("video", job)
    ctx.progress(0.1, f"provider: {provider.info.label}")
    source_path = None
    if params.get("asset_id"):
        source_path = asset_path(params["asset_id"])
        if not source_path:
            raise ValueError("source asset for image-to-video not found")
    ctx.progress(0.25, "submitting generation request (this can take minutes)")
    result = provider.run(
        {**params, "prompt": prompt, "mode": "image_to_video" if source_path else "text_to_video"},
        {
            "source_path": str(source_path) if source_path else None,
            "job_id": job["id"],
            "work_dir": str(tmp_dir_for(job["id"])),
        },
    )
    ctx.progress(0.85, "saving video")
    return _persist_result(
        job, ctx, result, provider=provider.id, prompt=prompt, params=params,
        parent_asset_id=params.get("asset_id"),
    )


@handler("video.edit")
def video_edit(job: dict, ctx: JobContext) -> dict:
    params = job.get("params") or {}
    source = asset_path(params.get("asset_id") or "")
    if not source:
        raise ValueError("'asset_id' of a video is required")
    ops = params.get("ops") or []
    ctx.progress(0.3, f"applying {len(ops)} video operation(s)")
    from .providers.local import _run_video_edit

    result = _run_video_edit(
        {**params, "ops": ops},
        {"source_path": str(source), "job_id": job["id"], "work_dir": str(tmp_dir_for(job["id"]))},
    )
    return _persist_result(
        job, ctx, result, provider="local-ffmpeg-edit", params=params,
        parent_asset_id=params.get("asset_id"),
    )


@handler("video.mux")
def video_mux(job: dict, ctx: JobContext) -> dict:
    params = job.get("params") or {}
    video_path = asset_path(params.get("video_asset_id") or "")
    audio_path = asset_path(params.get("audio_asset_id") or "")
    if not video_path or not audio_path:
        raise ValueError("'video_asset_id' and 'audio_asset_id' are both required")
    ctx.progress(0.4, "muxing audio into video")
    from .providers.local import _run_video_mux

    result = _run_video_mux(
        params,
        {
            "source_path": str(video_path),
            "audio_path": str(audio_path),
            "job_id": job["id"],
            "work_dir": str(tmp_dir_for(job["id"])),
        },
    )
    return _persist_result(
        job, ctx, result, provider="local-ffmpeg-mux", params=params,
        parent_asset_id=params.get("video_asset_id"),
    )


@handler("video.lipsync")
def video_lipsync(job: dict, ctx: JobContext) -> dict:
    params = job.get("params") or {}
    face = asset_path(params.get("asset_id") or "")
    audio = asset_path(params.get("audio_asset_id") or "")
    if not face:
        raise ValueError("'asset_id' (face image or video) is required")
    if not audio and not params.get("text"):
        raise ValueError("provide 'audio_asset_id' or 'text' to drive the lips")
    provider = _resolve_provider("lipsync", job)
    ctx.progress(0.2, f"provider: {provider.info.label}")
    if not audio and params.get("text"):
        ctx.log("no audio asset given; synthesising narration first")
        tts = _resolve_provider("tts", job)
        tts_result = tts.run(
            {"text": params["text"]}, {"job_id": job["id"], "work_dir": str(tmp_dir_for(job["id"]))}
        )
        temp_asset = persist_file(
            tts_result["files"][0],
            project_id=job.get("project_id"),
            provider=tts.id,
            engine=(tts_result.get("meta") or {}).get("engine", tts.id),
            prompt=params["text"],
            params=params,
            meta=tts_result.get("meta") or {},
            job_id=job["id"],
        )
        audio = asset_path(temp_asset["id"])
        ctx.log(f"narration asset created: {temp_asset['id']}")
    ctx.progress(0.45, "running lip sync")
    result = provider.run(
        params,
        {
            "source_path": str(face),
            "audio_path": str(audio) if audio else None,
            "job_id": job["id"],
            "work_dir": str(tmp_dir_for(job["id"])),
        },
    )
    return _persist_result(
        job, ctx, result, provider=provider.id, params=params, parent_asset_id=params.get("asset_id")
    )


# --------------------------------------------------------------------------
# story
# --------------------------------------------------------------------------
@handler("story.generate")
def story_generate(job: dict, ctx: JobContext) -> dict:
    """
    Generate story structure (scenes + shots) via the llm capability and persist
    it as a story with shot rows. Uses the project's characters as the cast when
    none are specified.
    """
    params = job.get("params") or {}
    premise = (params.get("premise") or "").strip()
    if not premise:
        raise ValueError("'premise' is required")
    provider = _resolve_provider("llm", job)
    ctx.progress(0.15, f"provider: {provider.info.label} ({provider.info.quality})")

    character_ids = params.get("character_ids")
    if character_ids is None and job.get("project_id"):
        character_ids = [
            row["id"]
            for row in query_all(
                "SELECT id FROM characters WHERE project_id = ? ORDER BY created_at", (job["project_id"],)
            )
        ]
    cast = []
    for character_id in character_ids or []:
        row = query_one("SELECT * FROM characters WHERE id = ?", (character_id,))
        if row:
            cast.append(f"{row['name']} — {row['appearance'] or row['description'] or row['role']}")

    enriched_premise = premise
    if cast:
        enriched_premise = f"{premise}\nCast (keep consistent): " + "; ".join(cast)

    ctx.progress(0.3, f"structuring story ({params.get('scenes') or 5} scenes)")
    result = provider.run(
        {**params, "premise": enriched_premise, "style": params.get("style") or ""},
        {"job_id": job["id"], "work_dir": str(tmp_dir_for(job["id"]))},
    )
    meta = result.get("meta") or {}
    story_data = result.get("story") or {}
    shots = result.get("shots") or []
    ctx.progress(0.7, f"persisting {len(shots)} shot(s)")

    from .db import insert, utcnow

    story_row = insert(
        "stories",
        {
            "project_id": job.get("project_id"),
            "title": story_data.get("title") or params.get("title") or "Untitled Story",
            "logline": story_data.get("logline") or premise[:200],
            "premise": premise,
            "genre": params.get("genre") or "",
            "tone": params.get("tone") or "",
            "status": "drafted",
            "scenes_json": jdumps(story_data.get("scenes") or []),
            "engine": meta.get("engine", provider.id),
        },
    )
    created = []
    for index, shot in enumerate(shots):
        ctx.check_cancelled()
        row = insert(
            "shots",
            {
                "story_id": story_row["id"],
                "project_id": job.get("project_id"),
                "index_no": int(shot.get("index_no", index)),
                "scene_no": int(shot.get("scene_no", 1)),
                "title": str(shot.get("title") or f"Shot {index + 1}")[:200],
                "description": str(shot.get("description") or ""),
                "prompt": str(shot.get("prompt") or ""),
                "character_ids": jdumps(character_ids or []),
                "duration_s": float(shot.get("duration_s") or 3.0),
                "transition": str(shot.get("transition") or "cut"),
                "motion": str(shot.get("motion") or "static"),
                "caption": str(shot.get("caption") or ""),
                "status": "draft",
            },
        )
        created.append(row["id"])
    ctx.progress(0.95, "story saved")
    return {
        "story_id": story_row["id"],
        "shot_ids": created,
        "shots": len(created),
        "engine": meta.get("engine", provider.id),
        "quality": meta.get("quality", "demo"),
        "note": meta.get("note", ""),
        "asset_ids": [],
    }


@handler("character.sheet")
def character_sheet(job: dict, ctx: JobContext) -> dict:
    """Compose a character's reference images into a single contact sheet."""
    params = job.get("params") or {}
    character_id = params.get("character_id")
    if not character_id:
        raise ValueError("'character_id' is required")
    character = query_one("SELECT * FROM characters WHERE id = ?", (character_id,))
    if not character:
        raise ValueError("character not found")
    refs = jloads(character["ref_asset_ids"], [])
    if not refs:
        raise ValueError("this character has no reference images yet")
    paths = [p for p in (asset_path(a) for a in refs) if p]
    if not paths:
        raise ValueError("character reference assets are missing from storage")
    ctx.progress(0.4, f"building sheet from {len(paths)} reference(s)")
    provider = get_provider("image_util", "local-montage")
    result = provider.run(
        {"columns": params.get("columns") or 3, "cell": params.get("cell") or 384},
        {"source_paths": [str(p) for p in paths], "work_dir": str(tmp_dir_for(job["id"]))},
    )
    return _persist_result(job, ctx, result, provider=provider.id, params=params)


# --------------------------------------------------------------------------
# maintenance
# --------------------------------------------------------------------------
@handler("asset.thumbnail")
def asset_thumbnail(job: dict, ctx: JobContext) -> dict:
    """(Re)build a thumbnail for one asset or backfill every missing one."""
    from .storage import make_thumbnail, resolve

    params = job.get("params") or {}
    if params.get("asset_id"):
        rows = query_all("SELECT * FROM assets WHERE id = ?", (params["asset_id"],))
    else:
        rows = query_all(
            "SELECT * FROM assets WHERE deleted_at IS NULL AND (thumb_path IS NULL OR thumb_path = '') LIMIT 500"
        )
    if not rows:
        return {"asset_ids": [], "updated": 0, "message": "nothing to do"}
    updated = 0
    for index, row in enumerate(rows):
        ctx.check_cancelled()
        ctx.progress(index / len(rows), f"thumbnail {index + 1}/{len(rows)}")
        try:
            path = resolve(row["rel_path"])
            thumb = make_thumbnail(path, row["id"], row["kind"])
            if thumb:
                execute("UPDATE assets SET thumb_path = ? WHERE id = ?", (thumb, row["id"]))
                updated += 1
        except Exception as exc:
            ctx.log(f"[error] thumbnail failed for {row['id']}: {exc}")
    return {"asset_ids": [], "updated": updated}


@handler("demo.render")
def demo_render(job: dict, ctx: JobContext) -> dict:
    """End-to-end self test: generate art, narrate it, render a film. Proves the stack."""
    from .providers.local import _run_image_generate, _run_tts, _run_video_render

    params = job.get("params") or {}
    prompt = params.get("prompt") or "an abstract study in light and motion"
    scopes = params.get("scenes") or ["a beginning", "a turning point", "a resolution"]
    width = int(params.get("width") or 1280)
    height = int(params.get("height") or 720)
    assets: list[dict] = []
    clips: list[dict] = []
    work = tmp_dir_for(job["id"])

    for index, scope in enumerate(scopes):
        ctx.check_cancelled()
        ctx.progress(0.1 + 0.4 * (index / len(scopes)), f"image {index + 1}/{len(scopes)}: {scope}")
        generated = _run_image_generate(
            {
                "prompt": f"{prompt} — {scope}",
                "width": width,
                "height": height,
                "seed": 1000 + index,
                "style": params.get("style") or "auto",
            },
            {},
        )
        asset = persist_file(
            generated["files"][0],
            project_id=job.get("project_id"),
            provider="local-procedural",
            engine="procedural-local",
            prompt=f"{prompt} — {scope}",
            params=params,
            meta={**generated["meta"], "demo": True},
            job_id=job["id"],
        )
        assets.append(asset)
        clips.append(
            {
                "path": asset_path(asset["id"]),
                "kind": "image",
                "duration": float(params.get("clip_seconds") or 3.0),
                "motion": ["zoom_in", "pan_right", "zoom_out"][index % 3],
                "caption": scope.title(),
            }
        )

    narration_text = params.get("narration") or (
        f"{prompt}. " + " ".join(f"{scope.capitalize()}." for scope in scopes)
    )
    ctx.progress(0.55, "synthesising narration")
    speech = _run_tts({"text": narration_text, "voice": params.get("voice") or "aria"}, {})
    narration = persist_file(
        speech["files"][0],
        project_id=job.get("project_id"),
        provider="local-formant-tts",
        engine="formant-demo",
        prompt=narration_text,
        params=params,
        meta={**speech["meta"], "demo": True},
        job_id=job["id"],
    )
    assets.append(narration)

    ctx.progress(0.7, "rendering film")
    rendered = _run_video_render(
        {
            "width": width,
            "height": height,
            "fps": int(params.get("fps") or 30),
            "transition": params.get("transition") or "fade",
            "transition_duration": float(params.get("transition_duration") or 0.6),
        },
        {"clips": clips, "work_dir": work, "job_id": job["id"]},
    )
    film = persist_file(
        rendered["files"][0],
        project_id=job.get("project_id"),
        provider="local-ffmpeg-render",
        engine="ffmpeg-render",
        params=params,
        meta={**rendered["meta"], "demo": True},
        job_id=job["id"],
    )
    assets.append(film)

    ctx.progress(0.92, "muxing narration")
    muxed_path = work / "final.mp4"
    video_engine.mux_audio(asset_path(film["id"]), asset_path(narration["id"]), muxed_path, mode="replace")
    final = persist_file(
        {
            "data": muxed_path.read_bytes(),
            "kind": "video",
            "filename": "demo-film-narrated.mp4",
            "mime": "video/mp4",
            "duration_s": video_engine.media_duration(muxed_path),
        },
        project_id=job.get("project_id"),
        provider="local-ffmpeg-mux",
        engine="ffmpeg-mux",
        params=params,
        meta={"engine": "ffmpeg-mux", "quality": "real", "narrated": True, "demo": True},
        job_id=job["id"],
        parent_asset_id=film["id"],
    )
    assets.append(final)
    return {
        "asset_ids": [a["id"] for a in assets],
        "assets": assets,
        "quality": "demo",
        "note": (
            "Full pipeline proof: procedural images + formant narration + real FFmpeg "
            "render with motion, transitions and muxed audio."
        ),
    }
