"""
Cloud provider adapters.

Each adapter is a real HTTP client for the vendor's documented API. When the
credential is absent the provider registers as ``available=False`` with an
actionable reason, so the UI can say exactly what to set instead of failing
mysteriously at generation time.

No adapter ever fabricates output: if a request fails, the job fails.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

from ..config import get_settings
from .base import Provider, ProviderError, ProviderInfo, register

TIMEOUT = 300.0


def _httpx():
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover
        raise ProviderError(
            "httpx is required for cloud providers (pip install httpx)"
        ) from exc
    return httpx


def _decode_b64_image(payload: str) -> bytes:
    if payload.startswith("data:"):
        payload = payload.split(",", 1)[-1]
    return base64.b64decode(payload)


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------
def _openai_image(params: dict, ctx: dict) -> dict:
    settings = get_settings()
    httpx = _httpx()
    body: dict[str, Any] = {
        "model": params.get("model") or settings.openai_image_model,
        "prompt": params["prompt"],
        "size": params.get("size") or "1024x1024",
        "n": 1,
    }
    if params.get("quality"):
        body["quality"] = params["quality"]
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    if ctx.get("source_path") and params.get("mode") == "edit":
        files = {"image": ("source.png", open(ctx["source_path"], "rb"), "image/png")}
        response = httpx.post(
            f"{settings.openai_base_url}/images/edits",
            headers=headers,
            data={k: str(v) for k, v in body.items() if k != "n"},
            files=files,
            timeout=TIMEOUT,
        )
    else:
        response = httpx.post(
            f"{settings.openai_base_url}/images/generations",
            headers=headers,
            json=body,
            timeout=TIMEOUT,
        )
    if response.status_code >= 400:
        raise ProviderError(f"OpenAI images failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    items = data.get("data") or []
    if not items:
        raise ProviderError("OpenAI returned no image data")
    entry = items[0]
    raw = _decode_b64_image(entry["b64_json"]) if entry.get("b64_json") else None
    if raw is None and entry.get("url"):
        raw = httpx.get(entry["url"], timeout=TIMEOUT).content
    if raw is None:
        raise ProviderError("OpenAI response contained neither b64_json nor url")
    size = body["size"].split("x")
    return {
        "files": [
            {
                "data": raw,
                "kind": "image",
                "filename": "openai-image.png",
                "mime": "image/png",
                "width": int(size[0]),
                "height": int(size[1]),
            }
        ],
        "meta": {"engine": "openai-images", "quality": "real", "model": body["model"]},
    }


def _stability_image(params: dict, ctx: dict) -> dict:
    settings = get_settings()
    httpx = _httpx()
    engine = params.get("model") or "core"
    width = int(params.get("width") or 1024)
    height = int(params.get("height") or 576)
    form = {
        "prompt": params["prompt"],
        "output_format": "png",
        "aspect_ratio": _closest_aspect(width, height),
    }
    if params.get("negative_prompt"):
        form["negative_prompt"] = params["negative_prompt"]
    if params.get("seed"):
        form["seed"] = str(params["seed"])
    response = httpx.post(
        f"{settings.stability_base_url}/v2beta/stable-image/generate/{engine}",
        headers={
            "Authorization": f"Bearer {settings.stability_api_key}",
            "Accept": "image/*",
        },
        data=form,
        files={"none": ""},
        timeout=TIMEOUT,
    )
    if response.status_code >= 400:
        raise ProviderError(f"Stability failed ({response.status_code}): {response.text[:300]}")
    return {
        "files": [
            {
                "data": response.content,
                "kind": "image",
                "filename": "stability.png",
                "mime": "image/png",
                "width": width,
                "height": height,
            }
        ],
        "meta": {"engine": f"stability-{engine}", "quality": "real"},
    }


def _closest_aspect(width: int, height: int) -> str:
    ratio = width / max(1, height)
    options = {"16:9": 16 / 9, "21:9": 21 / 9, "3:2": 1.5, "1:1": 1.0, "2:3": 2 / 3, "9:16": 9 / 16, "9:21": 9 / 21}
    return min(options.items(), key=lambda kv: abs(kv[1] - ratio))[0]


# --------------------------------------------------------------------------
# replicate (image + video + lipsync)
# --------------------------------------------------------------------------
def _replicate_run(model: str, payload: dict, settings) -> Any:
    httpx = _httpx()
    headers = {
        "Authorization": f"Bearer {settings.replicate_api_token}",
        "Content-Type": "application/json",
    }
    if "/" in model and ":" not in model and model.count("/") == 1:
        endpoint = f"{settings.replicate_base_url}/models/{model}/predictions"
    else:
        endpoint = f"{settings.replicate_base_url}/predictions"
        payload = {**payload, "version": model.split(":")[-1] if ":" in model else model}
    created = httpx.post(endpoint, headers=headers, json=payload, timeout=TIMEOUT)
    if created.status_code >= 400:
        raise ProviderError(f"Replicate rejected the request ({created.status_code}): {created.text[:300]}")
    prediction = created.json()
    url = prediction.get("urls", {}).get("get")
    deadline = time.time() + 1800
    while time.time() < deadline:
        status = prediction.get("status")
        if status in ("succeeded", "failed", "canceled"):
            break
        time.sleep(2.0)
        prediction = httpx.get(url, headers=headers, timeout=60).json()
    if prediction.get("status") != "succeeded":
        raise ProviderError(
            f"Replicate prediction {prediction.get('status')}: {str(prediction.get('error'))[:300]}"
        )
    return prediction.get("output")


def _replicate_image(params: dict, ctx: dict) -> dict:
    settings = get_settings()
    httpx = _httpx()
    model = params.get("model") or "black-forest-labs/flux-schnell"
    output = _replicate_run(
        model,
        {
            "input": {
                "prompt": params["prompt"],
                "aspect_ratio": params.get("aspect_ratio") or "16:9",
                "output_format": "png",
                **({"seed": params["seed"]} if params.get("seed") else {}),
            }
        },
        settings,
    )
    url = output[0] if isinstance(output, list) else output
    if not isinstance(url, str):
        raise ProviderError(f"unexpected Replicate output: {str(output)[:200]}")
    raw = httpx.get(url, timeout=TIMEOUT).content
    return {
        "files": [{"data": raw, "kind": "image", "filename": "replicate.png", "mime": "image/png"}],
        "meta": {"engine": f"replicate:{model}", "quality": "real", "source_url": url},
    }


def _replicate_video(params: dict, ctx: dict) -> dict:
    settings = get_settings()
    httpx = _httpx()
    model = params.get("model") or "wan-video/wan-2.2-t2v-fast"
    duration = int(params.get("duration") or 5)
    payload = {
        "input": {
            "prompt": params["prompt"],
            "aspect_ratio": params.get("aspect_ratio") or "16:9",
            "resolution": params.get("resolution") or "720p",
            "duration": duration,
        }
    }
    if ctx.get("source_path") and params.get("mode") == "image_to_video":
        payload["input"]["image"] = _as_data_uri(ctx["source_path"], httpx)
    output = _replicate_run(model, payload, settings)
    url = output[0] if isinstance(output, list) else output
    if not isinstance(url, str):
        raise ProviderError(f"unexpected Replicate output: {str(output)[:200]}")
    raw = httpx.get(url, timeout=900).content
    return {
        "files": [
            {
                "data": raw,
                "kind": "video",
                "filename": "replicate.mp4",
                "mime": "video/mp4",
                "duration_s": float(duration),
            }
        ],
        "meta": {"engine": f"replicate:{model}", "quality": "real", "source_url": url},
    }


def _as_data_uri(path: str, httpx) -> str:
    from pathlib import Path

    raw = Path(path).read_bytes()
    suffix = Path(path).suffix.lower().lstrip(".") or "png"
    return f"data:image/{suffix};base64,{base64.b64encode(raw).decode()}"


# --------------------------------------------------------------------------
# runway
# --------------------------------------------------------------------------
def _runway_video(params: dict, ctx: dict) -> dict:
    settings = get_settings()
    httpx = _httpx()
    model = params.get("model") or "gen3a_turbo"
    body = {
        "model": model,
        "promptText": params["prompt"],
        "duration": int(params.get("duration") or 5),
        "ratio": params.get("aspect_ratio") or "1280:768",
    }
    if ctx.get("source_path") and params.get("mode") == "image_to_video":
        body["promptImage"] = _as_data_uri(ctx["source_path"], httpx)
    created = httpx.post(
        f"{settings.runway_base_url}/image_to_video",
        headers={
            "Authorization": f"Bearer {settings.runway_api_key}",
            "X-Runway-Version": "2024-11-06",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=TIMEOUT,
    )
    if created.status_code >= 400:
        raise ProviderError(f"Runway rejected the request ({created.status_code}): {created.text[:300]}")
    task_id = created.json().get("id")
    deadline = time.time() + 1800
    output_url = None
    while time.time() < deadline:
        task = httpx.get(
            f"{settings.runway_base_url}/tasks/{task_id}",
            headers={
                "Authorization": f"Bearer {settings.runway_api_key}",
                "X-Runway-Version": "2024-11-06",
            },
            timeout=60,
        ).json()
        if task.get("status") == "SUCCEEDED":
            output_url = (task.get("output") or [None])[0]
            break
        if task.get("status") in ("FAILED", "CANCELLED"):
            raise ProviderError(f"Runway task {task.get('status')}: {task.get('failure')}")
        time.sleep(3.0)
    if not output_url:
        raise ProviderError("Runway task did not produce output in time")
    return {
        "files": [
            {
                "data": httpx.get(output_url, timeout=900).content,
                "kind": "video",
                "filename": "runway.mp4",
                "mime": "video/mp4",
                "duration_s": float(body["duration"]),
            }
        ],
        "meta": {"engine": f"runway:{model}", "quality": "real", "source_url": output_url},
    }


# --------------------------------------------------------------------------
# elevenlabs tts
# --------------------------------------------------------------------------
def _elevenlabs_tts(params: dict, ctx: dict) -> dict:
    settings = get_settings()
    httpx = _httpx()
    voice_id = params.get("voice_id") or settings.elevenlabs_voice_id
    response = httpx.post(
        f"{settings.elevenlabs_base_url}/text-to-speech/{voice_id}",
        headers={
            "xi-api-key": settings.elevenlabs_api_key,
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
        },
        json={
            "text": params["text"],
            "model_id": params.get("model") or "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        },
        timeout=TIMEOUT,
    )
    if response.status_code >= 400:
        raise ProviderError(f"ElevenLabs failed ({response.status_code}): {response.text[:300]}")
    return {
        "files": [
            {"data": response.content, "kind": "audio", "filename": "elevenlabs.mp3", "mime": "audio/mpeg"}
        ],
        "meta": {"engine": "elevenlabs", "quality": "real", "voice_id": voice_id},
    }


def _openai_tts(params: dict, ctx: dict) -> dict:
    settings = get_settings()
    httpx = _httpx()
    response = httpx.post(
        f"{settings.openai_base_url}/audio/speech",
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={
            "model": params.get("model") or settings.openai_tts_model,
            "voice": params.get("voice") or "alloy",
            "input": params["text"],
            "response_format": "mp3",
        },
        timeout=TIMEOUT,
    )
    if response.status_code >= 400:
        raise ProviderError(f"OpenAI TTS failed ({response.status_code}): {response.text[:300]}")
    return {
        "files": [
            {"data": response.content, "kind": "audio", "filename": "openai-speech.mp3", "mime": "audio/mpeg"}
        ],
        "meta": {"engine": "openai-tts", "quality": "real"},
    }


# --------------------------------------------------------------------------
# llm (openai / anthropic)
# --------------------------------------------------------------------------
STORY_SCHEMA_HINT = json.dumps(
    {
        "title": "string",
        "logline": "string",
        "scenes": [{"scene_no": 1, "beat": "string", "purpose": "string", "summary": "string"}],
        "shots": [
            {
                "index_no": 0,
                "scene_no": 1,
                "title": "string",
                "description": "string",
                "prompt": "detailed visual prompt for an image model",
                "duration_s": 3.0,
                "motion": "zoom_in|pan_right|static",
                "transition": "cut|fade|dissolve",
                "caption": "optional on-screen text",
            }
        ],
    },
    indent=None,
)


def _llm_prompt(params: dict) -> str:
    premise = params.get("premise") or params.get("prompt") or ""
    scenes = int(params.get("scenes") or 5)
    shots = int(params.get("shots_per_scene") or 3)
    style = params.get("style") or "cinematic"
    return (
        "You are a film director building a shot list.\n"
        f"Premise: {premise}\n"
        f"Visual style: {style}\n"
        f"Produce exactly {scenes} scenes and {shots} shots per scene.\n"
        "Return ONLY valid minified JSON matching this schema (no markdown, no commentary):\n"
        f"{STORY_SCHEMA_HINT}\n"
        "Each shot prompt must be a vivid, concrete image-generation prompt "
        "describing subject, framing, lighting and mood."
    )


def _parse_story_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.lower().startswith("json") else text
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"model returned unparsable JSON: {exc}") from exc


def _openai_story(params: dict, ctx: dict) -> dict:
    settings = get_settings()
    httpx = _httpx()
    response = httpx.post(
        f"{settings.openai_base_url}/chat/completions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={
            "model": params.get("model") or settings.openai_llm_model,
            "messages": [{"role": "user", "content": _llm_prompt(params)}],
            "response_format": {"type": "json_object"},
            "temperature": 0.8,
        },
        timeout=TIMEOUT,
    )
    if response.status_code >= 400:
        raise ProviderError(f"OpenAI chat failed ({response.status_code}): {response.text[:300]}")
    content = response.json()["choices"][0]["message"]["content"]
    parsed = _parse_story_json(content)
    return {
        "story": {k: parsed.get(k) for k in ("title", "logline", "premise", "scenes") if parsed.get(k) is not None},
        "shots": parsed.get("shots") or [],
        "meta": {"engine": "openai-chat", "quality": "real"},
    }


def _anthropic_story(params: dict, ctx: dict) -> dict:
    settings = get_settings()
    httpx = _httpx()
    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": params.get("model") or "claude-3-5-sonnet-latest",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": _llm_prompt(params) + "\nReturn ONLY the JSON object."}],
        },
        timeout=TIMEOUT,
    )
    if response.status_code >= 400:
        raise ProviderError(f"Anthropic failed ({response.status_code}): {response.text[:300]}")
    blocks = response.json().get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    parsed = _parse_story_json(text)
    return {
        "story": {k: parsed.get(k) for k in ("title", "logline", "premise", "scenes") if parsed.get(k) is not None},
        "shots": parsed.get("shots") or [],
        "meta": {"engine": "anthropic", "quality": "real"},
    }


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------


def register_cloud_providers() -> None:
    settings = get_settings()

    register(
        Provider(
            ProviderInfo(
                id="openai-image",
                label="OpenAI Images",
                capability="image",
                kind="cloud",
                quality="real",
                engine="openai-images",
                requires=("OPENAI_API_KEY",),
                describes="gpt-image-1 generation and image editing.",
                docs_url="https://platform.openai.com/docs/guides/image-generation",
                available=bool(settings.openai_api_key),
                reason="" if settings.openai_api_key else "set OPENAI_API_KEY in .env",
            ),
            _openai_image,
        )
    )
    register(
        Provider(
            ProviderInfo(
                id="stability-image",
                label="Stability AI",
                capability="image",
                kind="cloud",
                quality="real",
                engine="stability",
                requires=("STABILITY_API_KEY",),
                describes="Stable Image Core / Ultra generation.",
                docs_url="https://platform.stability.ai/docs/api-reference",
                available=bool(settings.stability_api_key),
                reason="" if settings.stability_api_key else "set STABILITY_API_KEY in .env",
            ),
            _stability_image,
        )
    )
    register(
        Provider(
            ProviderInfo(
                id="replicate-image",
                label="Replicate (FLUX etc.)",
                capability="image",
                kind="cloud",
                quality="real",
                engine="replicate",
                requires=("REPLICATE_API_TOKEN",),
                describes="Any Replicate image model, FLUX Schnell by default.",
                docs_url="https://replicate.com/docs",
                available=bool(settings.replicate_api_token),
                reason="" if settings.replicate_api_token else "set REPLICATE_API_TOKEN in .env",
            ),
            _replicate_image,
        )
    )
    register(
        Provider(
            ProviderInfo(
                id="replicate-video",
                label="Replicate (Wan, Kling, etc.)",
                capability="video",
                kind="cloud",
                quality="real",
                engine="replicate",
                requires=("REPLICATE_API_TOKEN",),
                describes="Text-to-video and image-to-video models via Replicate.",
                docs_url="https://replicate.com/docs",
                available=bool(settings.replicate_api_token),
                reason="" if settings.replicate_api_token else "set REPLICATE_API_TOKEN in .env",
            ),
            _replicate_video,
        )
    )
    register(
        Provider(
            ProviderInfo(
                id="runway-video",
                label="Runway Gen-3",
                capability="video",
                kind="cloud",
                quality="real",
                engine="runway",
                requires=("RUNWAY_API_KEY",),
                describes="Gen-3 Alpha Turbo text/image-to-video.",
                docs_url="https://docs.dev.runwayml.com",
                available=bool(settings.runway_api_key),
                reason="" if settings.runway_api_key else "set RUNWAY_API_KEY in .env",
            ),
            _runway_video,
        )
    )
    register(
        Provider(
            ProviderInfo(
                id="elevenlabs-tts",
                label="ElevenLabs",
                capability="tts",
                kind="cloud",
                quality="real",
                engine="elevenlabs",
                requires=("ELEVENLABS_API_KEY",),
                describes="High-quality neural narration with voice selection.",
                docs_url="https://elevenlabs.io/docs/api-reference/text-to-speech",
                available=bool(settings.elevenlabs_api_key),
                reason="" if settings.elevenlabs_api_key else "set ELEVENLABS_API_KEY in .env",
            ),
            _elevenlabs_tts,
        )
    )
    register(
        Provider(
            ProviderInfo(
                id="openai-tts",
                label="OpenAI Speech",
                capability="tts",
                kind="cloud",
                quality="real",
                engine="openai-tts",
                requires=("OPENAI_API_KEY",),
                describes="gpt-4o-mini-tts narration.",
                docs_url="https://platform.openai.com/docs/guides/text-to-speech",
                available=bool(settings.openai_api_key),
                reason="" if settings.openai_api_key else "set OPENAI_API_KEY in .env",
            ),
            _openai_tts,
        )
    )
    register(
        Provider(
            ProviderInfo(
                id="openai-llm",
                label="OpenAI (Story Generation)",
                capability="llm",
                kind="cloud",
                quality="real",
                engine="openai-chat",
                requires=("OPENAI_API_KEY",),
                describes="Structured story and shot-list generation as JSON.",
                available=bool(settings.openai_api_key),
                reason="" if settings.openai_api_key else "set OPENAI_API_KEY in .env",
            ),
            _openai_story,
        )
    )
    register(
        Provider(
            ProviderInfo(
                id="anthropic-llm",
                label="Anthropic Claude (Story Generation)",
                capability="llm",
                kind="cloud",
                quality="real",
                engine="anthropic",
                requires=("ANTHROPIC_API_KEY",),
                describes="Structured story and shot-list generation as JSON.",
                available=bool(settings.anthropic_api_key),
                reason="" if settings.anthropic_api_key else "set ANTHROPIC_API_KEY in .env",
            ),
            _anthropic_story,
        )
    )
