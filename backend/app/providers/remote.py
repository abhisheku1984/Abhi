"""
Remote GPU host adapters — your own machine, your own models.

These are the adapters that unlock *real* diffusion-grade generation without a
cloud bill, provided the user points the app at a GPU host. All of them are
implemented against the tools people actually run:

* ``comfyui``  — ComfyUI ``/prompt`` + ``/history`` + ``/view`` (image, video)
* ``a1111``    — AUTOMATIC1111 / Forge ``/sdapi/v1/txt2img`` (image)
* ``ollama``   — local LLM ``/api/chat`` (story generation)
* ``wav2lip``  — local Wav2Lip / SadTalker checkout invoked with FFmpeg for
  audio extraction (lip sync), reporting clearly when the checkout is missing.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from ..config import detect_ffmpeg, get_settings
from .base import Provider, ProviderError, ProviderInfo, register

TIMEOUT = 600.0


def _httpx():
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover
        raise ProviderError("httpx is required for remote providers") from exc
    return httpx


def _reachable(url: str, timeout: float = 4.0) -> tuple[bool, str]:
    """Cheap liveness probe so availability reflects reality at boot."""
    if not url:
        return False, "no URL configured"
    try:
        httpx = _httpx()
        response = httpx.get(url, timeout=timeout)
        return response.status_code < 500, f"HTTP {response.status_code}"
    except Exception as exc:
        return False, f"unreachable ({type(exc).__name__})"


# --------------------------------------------------------------------------
# ComfyUI
# --------------------------------------------------------------------------
DEFAULT_WORKFLOW = {
    "3": {
        "class_type": "KSampler",
        "inputs": {"seed": 0, "steps": 20, "cfg": 7, "sampler_name": "euler",
                   "scheduler": "normal", "denoise": 1.0, "model": ["4", 0],
                   "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]},
    },
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 576, "batch_size": 1}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}},
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "abhi", "images": ["8", 0]}},
}


def _comfy_generate(params: dict, ctx: dict) -> dict:
    settings = get_settings()
    httpx = _httpx()
    base = settings.comfyui_url.rstrip("/")
    headers = {}
    if settings.comfyui_token:
        headers["Authorization"] = f"Bearer {settings.comfyui_token}"

    workflow = params.get("workflow")
    if not workflow:
        workflow = json.loads(json.dumps(DEFAULT_WORKFLOW))  # deep copy
        workflow["5"]["inputs"]["width"] = int(params.get("width") or 1024)
        workflow["5"]["inputs"]["height"] = int(params.get("height") or 576)
        workflow["6"]["inputs"]["text"] = params.get("prompt") or ""
        workflow["7"]["inputs"]["text"] = params.get("negative_prompt") or ""
        if params.get("steps"):
            workflow["3"]["inputs"]["steps"] = int(params["steps"])
        if params.get("cfg"):
            workflow["3"]["inputs"]["cfg"] = float(params["cfg"])
        if params.get("seed"):
            workflow["3"]["inputs"]["seed"] = int(params["seed"])
    else:
        workflow = _inject_workflow_prompt(workflow, params)

    response = httpx.post(
        f"{base}/prompt", headers=headers, json={"prompt": workflow}, timeout=TIMEOUT
    )
    if response.status_code >= 400:
        raise ProviderError(f"ComfyUI rejected the workflow ({response.status_code}): {response.text[:400]}")
    prompt_id = response.json().get("prompt_id")
    if not prompt_id:
        raise ProviderError("ComfyUI returned no prompt_id")

    deadline = time.time() + 1800
    images: list[dict] = []
    while time.time() < deadline:
        history = httpx.get(f"{base}/history/{prompt_id}", headers=headers, timeout=60).json()
        entry = history.get(prompt_id)
        if entry:
            for node_output in (entry.get("outputs") or {}).values():
                images.extend(node_output.get("images") or [])
                for key in ("gifs", "videos"):
                    images.extend(node_output.get(key) or [])
            if images:
                break
            if entry.get("status", {}).get("status_str") == "error":
                raise ProviderError(f"ComfyUI workflow error: {str(entry.get('status'))[:300]}")
        time.sleep(2.0)
    if not images:
        raise ProviderError("ComfyUI produced no output within the timeout")

    source = images[0]
    view = httpx.get(
        f"{base}/view",
        params={
            "filename": source.get("filename"),
            "subfolder": source.get("subfolder", ""),
            "type": source.get("type", "output"),
        },
        headers=headers,
        timeout=TIMEOUT,
    )
    if view.status_code >= 400:
        raise ProviderError(f"ComfyUI could not return the generated file ({view.status_code})")
    filename = source.get("filename") or "comfy.png"
    suffix = Path(filename).suffix.lower()
    kind = "video" if suffix in (".mp4", ".webm", ".gif") else "image"
    mime = "video/mp4" if kind == "video" else ("image/gif" if suffix == ".gif" else "image/png")
    return {
        "files": [
            {
                "data": view.content,
                "kind": kind,
                "filename": filename,
                "mime": mime,
                "width": int(params.get("width") or 0) or None,
                "height": int(params.get("height") or 0) or None,
            }
        ],
        "meta": {"engine": "comfyui", "quality": "real", "prompt_id": prompt_id},
    }


def _inject_workflow_prompt(workflow: dict, params: dict) -> dict:
    """Replace obvious placeholder tokens in a user-supplied workflow."""
    blob = json.dumps(workflow)
    blob = blob.replace("__PROMPT__", json.dumps(params.get("prompt") or "")[1:-1])
    blob = blob.replace("__NEGATIVE__", json.dumps(params.get("negative_prompt") or "")[1:-1])
    blob = blob.replace("__SEED__", str(int(params.get("seed") or 0)))
    blob = blob.replace("__WIDTH__", str(int(params.get("width") or 1024)))
    blob = blob.replace("__HEIGHT__", str(int(params.get("height") or 576)))
    return json.loads(blob)


# --------------------------------------------------------------------------
# AUTOMATIC1111 / Forge
# --------------------------------------------------------------------------
def _a1111_generate(params: dict, ctx: dict) -> dict:
    settings = get_settings()
    httpx = _httpx()
    base = settings.a1111_url.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.a1111_token}"} if settings.a1111_token else {}
    body = {
        "prompt": params.get("prompt") or "",
        "negative_prompt": params.get("negative_prompt") or "",
        "width": int(params.get("width") or 1024),
        "height": int(params.get("height") or 576),
        "steps": int(params.get("steps") or 25),
        "cfg_scale": float(params.get("cfg") or 7.0),
        "sampler_name": params.get("sampler") or "DPM++ 2M Karras",
        "seed": int(params.get("seed") or -1),
    }
    response = httpx.post(f"{base}/sdapi/v1/txt2img", headers=headers, json=body, timeout=1800)
    if response.status_code >= 400:
        raise ProviderError(f"A1111 failed ({response.status_code}): {response.text[:300]}")
    images = response.json().get("images") or []
    if not images:
        raise ProviderError("A1111 returned no images")
    payload = images[0]
    if payload.startswith("data:"):
        payload = payload.split(",", 1)[-1]
    raw = base64.b64decode(payload)
    return {
        "files": [
            {
                "data": raw,
                "kind": "image",
                "filename": "a1111.png",
                "mime": "image/png",
                "width": body["width"],
                "height": body["height"],
            }
        ],
        "meta": {"engine": "a1111", "quality": "real"},
    }


# --------------------------------------------------------------------------
# Ollama
# --------------------------------------------------------------------------
OLLAMA_SCHEMA = """Return ONLY minified JSON with this shape:
{"title":str,"logline":str,"scenes":[{"scene_no":int,"beat":str,"purpose":str,"summary":str}],
 "shots":[{"index_no":int,"scene_no":int,"title":str,"description":str,"prompt":str,
           "duration_s":float,"motion":str,"transition":str,"caption":str}]}"""


def _ollama_story(params: dict, ctx: dict) -> dict:
    settings = get_settings()
    httpx = _httpx()
    base = settings.ollama_url.rstrip("/")
    scenes = int(params.get("scenes") or 5)
    shots = int(params.get("shots_per_scene") or 3)
    prompt = (
        "You are a film director building a shot list.\n"
        f"Premise: {params.get('premise') or params.get('prompt') or ''}\n"
        f"Style: {params.get('style') or 'cinematic'}\n"
        f"Produce {scenes} scenes and {shots} shots per scene.\n{OLLAMA_SCHEMA}"
    )
    response = httpx.post(
        f"{base}/api/chat",
        json={
            "model": params.get("model") or settings.ollama_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
        },
        timeout=1800,
    )
    if response.status_code >= 400:
        raise ProviderError(f"Ollama failed ({response.status_code}): {response.text[:300]}")
    content = response.json().get("message", {}).get("content", "")
    text = content.strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Ollama returned unparsable JSON: {exc}") from exc
    return {
        "story": {k: parsed.get(k) for k in ("title", "logline", "premise", "scenes") if parsed.get(k) is not None},
        "shots": parsed.get("shots") or [],
        "meta": {"engine": "ollama", "quality": "real"},
    }


# --------------------------------------------------------------------------
# Wav2Lip / SadTalker (lip sync)
# --------------------------------------------------------------------------
def _lipsync_local(params: dict, ctx: dict) -> dict:
    """
    Run a local Wav2Lip or SadTalker checkout.

    Expected layout (both are the standard upstream repos):
      Wav2Lip:    <dir>/inference.py + <dir>/checkpoints/wav2lip_gan.pth
      SadTalker:  <dir>/inference.py + <dir>/checkpoints/
    The adapter extracts audio with FFmpeg, invokes the repo's own CLI, and
    returns the produced video. Heavy lifting stays in the GPU environment.
    """
    settings = get_settings()
    face_path: str | None = ctx.get("source_path")
    audio_path: str | None = ctx.get("audio_path")
    if not face_path or not audio_path:
        raise ProviderError("lip sync needs a face image/video asset and an audio asset")

    work_dir = Path(ctx.get("work_dir") or tempfile.mkdtemp(prefix="abhi-lipsync-"))
    work_dir.mkdir(parents=True, exist_ok=True)
    output = work_dir / "lipsync.mp4"

    candidates: list[tuple[str, Path]] = []
    if settings.wav2lip_dir:
        candidates.append(("wav2lip", Path(settings.wav2lip_dir)))
    if settings.sadtalker_dir:
        candidates.append(("sadtalker", Path(settings.sadtalker_dir)))
    if not candidates:
        raise ProviderError("no Wav2Lip or SadTalker directory configured")

    last_error = ""
    for engine_name, directory in candidates:
        inference = directory / "inference.py"
        if not inference.is_file():
            last_error = f"{engine_name}: {inference} not found"
            continue
        if engine_name == "wav2lip":
            command = [
                "python", str(inference), "--checkpoint_path",
                str(directory / "checkpoints" / "wav2lip_gan.pth"),
                "--face", face_path, "--audio", audio_path, "--outfile", str(output),
            ]
        else:
            command = [
                "python", str(inference), "--source_image", face_path,
                "--driven_audio", audio_path, "--result_dir", str(work_dir),
            ]
        try:
            proc = subprocess.run(
                command, cwd=str(directory), capture_output=True, text=True, timeout=3600
            )
        except subprocess.TimeoutExpired as exc:
            last_error = f"{engine_name} timed out"
            raise ProviderError(last_error) from exc
        if proc.returncode != 0:
            last_error = f"{engine_name} exited {proc.returncode}: {(proc.stderr or '')[-300:]}"
            continue
        if not output.exists():
            produced = sorted(work_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
            if produced:
                output = produced[0]
        if output.exists():
            return {
                "files": [
                    {
                        "data": output.read_bytes(),
                        "kind": "video",
                        "filename": "lipsync.mp4",
                        "mime": "video/mp4",
                    }
                ],
                "meta": {"engine": engine_name, "quality": "real"},
            }
        last_error = f"{engine_name} produced no output file"
    raise ProviderError(f"lip sync failed: {last_error or 'unknown error'}")


def _replicate_lipsync(params: dict, ctx: dict) -> dict:
    from .cloud import _replicate_run

    settings = get_settings()
    httpx = _httpx()
    model = params.get("model") or "sync/lipsync-2"
    payload: dict = {"input": {"sync_mode": "cut_off"}}
    if ctx.get("source_path"):
        payload["input"]["video"] = _file_data_uri(ctx["source_path"], httpx)
    if ctx.get("audio_path"):
        payload["input"]["audio"] = _file_data_uri(ctx["audio_path"], httpx)
    if params.get("text"):
        payload["input"].pop("audio", None)
        payload["input"]["text"] = params["text"]
    output = _replicate_run(model, payload, settings)
    url = output[0] if isinstance(output, list) else output
    if not isinstance(url, str):
        raise ProviderError(f"unexpected Replicate output: {str(output)[:200]}")
    return {
        "files": [
            {
                "data": httpx.get(url, timeout=900).content,
                "kind": "video",
                "filename": "lipsync.mp4",
                "mime": "video/mp4",
            }
        ],
        "meta": {"engine": f"replicate:{model}", "quality": "real", "source_url": url},
    }


def _file_data_uri(path: str, httpx) -> str:
    raw = Path(path).read_bytes()
    suffix = Path(path).suffix.lower().lstrip(".") or "bin"
    mime = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
        "mp4": "video/mp4", "mov": "video/quicktime", "wav": "audio/wav", "mp3": "audio/mpeg",
        "m4a": "audio/mp4", "ogg": "audio/ogg",
    }.get(suffix, "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------


def register_remote_providers() -> None:
    settings = get_settings()

    comfy_ok, comfy_reason = (
        _reachable(settings.comfyui_url.rstrip("/") + "/system_stats")
        if settings.comfyui_url
        else (False, "set ABHI_COMFYUI_URL to your GPU host (e.g. http://192.168.1.50:8188)")
    )
    register(
        Provider(
            ProviderInfo(
                id="comfyui-image",
                label="ComfyUI (your GPU)",
                capability="image",
                kind="remote",
                quality="real",
                engine="comfyui",
                requires=("ABHI_COMFYUI_URL",),
                describes=(
                    "Runs your own diffusion checkpoints on your GPU host. Supply a custom "
                    "workflow JSON or use the built-in SDXL text-to-image graph."
                ),
                docs_url="https://github.com/comfyanonymous/ComfyUI",
                available=comfy_ok,
                reason="" if comfy_ok else comfy_reason,
            ),
            _comfy_generate,
        )
    )
    register(
        Provider(
            ProviderInfo(
                id="comfyui-video",
                label="ComfyUI Video (Wan / AnimateDiff)",
                capability="video",
                kind="remote",
                quality="real",
                engine="comfyui",
                requires=("ABHI_COMFYUI_URL",),
                describes=(
                    "Video generation on your GPU host. Pass a workflow JSON whose output node "
                    "is SaveImage/VHS_VideoCombine; tokens __PROMPT__/__SEED__/__WIDTH__/"
                    "__HEIGHT__ are substituted."
                ),
                available=comfy_ok,
                reason="" if comfy_ok else comfy_reason,
            ),
            _comfy_generate,
        )
    )
    a1111_ok, a1111_reason = (
        _reachable(settings.a1111_url.rstrip("/") + "/sdapi/v1/sd-models")
        if settings.a1111_url
        else (False, "set ABHI_A1111_URL to your GPU host (e.g. http://192.168.1.50:7860)")
    )
    register(
        Provider(
            ProviderInfo(
                id="a1111-image",
                label="AUTOMATIC1111 / Forge (your GPU)",
                capability="image",
                kind="remote",
                quality="real",
                engine="a1111",
                requires=("ABHI_A1111_URL",),
                describes="txt2img against your running WebUI instance.",
                docs_url="https://github.com/AUTOMATIC1111/stable-diffusion-webui",
                available=a1111_ok,
                reason="" if a1111_ok else a1111_reason,
            ),
            _a1111_generate,
        )
    )
    ollama_ok, ollama_reason = (
        _reachable(settings.ollama_url.rstrip("/") + "/api/tags")
        if settings.ollama_url
        else (False, "set ABHI_OLLAMA_URL (e.g. http://localhost:11434)")
    )
    register(
        Provider(
            ProviderInfo(
                id="ollama-llm",
                label="Ollama (local LLM)",
                capability="llm",
                kind="remote",
                quality="real",
                engine="ollama",
                requires=("ABHI_OLLAMA_URL",),
                describes="Story/shot-list generation with a local model such as llama3.1.",
                docs_url="https://ollama.com",
                available=ollama_ok,
                reason="" if ollama_ok else ollama_reason,
            ),
            _ollama_story,
        )
    )

    wav2lip_ready = bool(settings.wav2lip_dir) or bool(settings.sadtalker_dir)
    register(
        Provider(
            ProviderInfo(
                id="local-wav2lip",
                label="Wav2Lip / SadTalker (your GPU)",
                capability="lipsync",
                kind="remote",
                quality="real",
                engine="wav2lip",
                requires=("ABHI_WAV2LIP_DIR", "ABHI_SADTALKER_DIR"),
                describes=(
                    "Lip-sync a face asset to any narration track using a local Wav2Lip or "
                    "SadTalker checkout."
                ),
                docs_url="https://github.com/Rudrabha/Wav2Lip",
                available=wav2lip_ready and bool(detect_ffmpeg()),
                reason=(
                    ""
                    if (wav2lip_ready and detect_ffmpeg())
                    else "set ABHI_WAV2LIP_DIR (or ABHI_SADTALKER_DIR) to a checkout with checkpoints"
                ),
            ),
            _lipsync_local,
        )
    )
    register(
        Provider(
            ProviderInfo(
                id="replicate-lipsync",
                label="Replicate (Sync / SadTalker)",
                capability="lipsync",
                kind="cloud",
                quality="real",
                engine="replicate-lipsync",
                requires=("REPLICATE_API_TOKEN",),
                describes="Cloud lip-sync when no local GPU checkout exists.",
                available=bool(settings.replicate_api_token),
                reason="" if settings.replicate_api_token else "set REPLICATE_API_TOKEN in .env",
            ),
            _replicate_lipsync,
        )
    )
