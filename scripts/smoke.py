"""End-to-end smoke test for AI Creative Studio.

Standard library only, so it runs on Windows, macOS and Linux with no extra
dependencies. Exercises the real pipeline: auth → project → image → video →
voice → music → avatar → story → timeline export.

Usage:
    python scripts/smoke.py [--base http://127.0.0.1:8000/api/v1]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Optional

CHECK_ICON = {"pass": "PASS", "fail": "FAIL", "warn": "WARN"}


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.token: Optional[str] = None

    def _request(self, method: str, path: str, body: Optional[dict] = None, timeout: int = 60) -> Any:
        url = f"{self.base}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = {"error": {"message": raw[:300]}}
            raise RuntimeError(f"{method} {path} → {exc.code}: {json.dumps(parsed)[:400]}") from None

    def get(self, path: str, **kw) -> Any:
        return self._request("GET", path, **kw)

    def post(self, path: str, body: Optional[dict] = None, **kw) -> Any:
        return self._request("POST", path, body, **kw)

    def patch(self, path: str, body: Optional[dict] = None, **kw) -> Any:
        return self._request("PATCH", path, body, **kw)

    def wait_for_job(self, job_id: str, *, timeout: int = 600, poll: float = 1.0) -> dict:
        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            last = self.get(f"/jobs/{job_id}")
            if last.get("status") in ("completed", "failed", "cancelled"):
                return last
            time.sleep(poll)
        return {**last, "status": "timeout"}


RESULTS: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    state = "pass" if ok else "fail"
    RESULTS.append((state, name, detail))
    print(f"[{CHECK_ICON[state]}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--email", default="admin@studio.ai")
    parser.add_argument("--password", default="Admin@12345")
    args = parser.parse_args()

    c = Client(args.base)

    # --- Health --------------------------------------------------------- #
    try:
        health = c.get("/health")
        check("health endpoint", health.get("status") == "ok", f"db={health.get('database')} ffmpeg={health.get('ffmpeg')}")
    except Exception as exc:
        check("health endpoint", False, str(exc))
        return 1

    # --- Auth ----------------------------------------------------------- #
    try:
        tokens = c.post("/auth/login", {"email": args.email, "password": args.password})
        c.token = tokens["access_token"]
        check("login", bool(c.token), f"user={tokens['user']['email']} role={tokens['user']['role']}")
    except Exception as exc:
        print(f"[FAIL] login — {exc}")
        print("       Create the admin account first (see backend/.env BOOTSTRAP_ADMIN_*).")
        return 1

    try:
        c.get("/auth/login")  # missing body → validation error path
    except Exception:
        pass
    check("unauthenticated request is rejected", _rejects_unauth(Client(args.base)))

    # --- Projects ------------------------------------------------------- #
    project = c.post("/projects", {"name": "Smoke test project", "kind": "story"})
    check("create project", bool(project.get("id")), project.get("id", ""))

    # --- Prompt engine --------------------------------------------------- #
    enhanced = c.post("/prompts/transform", {"prompt": "a lion and a rabbit in a forest", "kind": "enhance"})
    check("prompt enhancement", bool(enhanced.get("enhanced")) and enhanced["enhanced"] != enhanced["original"])

    # --- Image ------------------------------------------------------------ #
    img_job = c.post("/generate/image", {
        "prompt": "cinematic wide shot of a businessman walking through Hyderabad at sunset",
        "project_id": project["id"],
        "params": {"aspect": "16:9", "resolution": "768", "lighting": "golden-hour"},
    })
    check("image job enqueued", bool(img_job.get("job_id")), f"engine={img_job.get('engine')}")
    img = c.wait_for_job(img_job["job_id"], timeout=300)
    check("image generation completed", img.get("status") == "completed",
          f"status={img.get('status')} {(img.get('error') or {}).get('message','')}")

    assets = c.get("/assets?limit=5")
    image_asset = next((a for a in assets["items"] if a["kind"] == "image"), None)
    check("image asset stored", image_asset is not None,
          f"{image_asset['width']}x{image_asset['height']} {image_asset['size_bytes']}B" if image_asset else "none")

    # --- Upscale ---------------------------------------------------------- #
    if image_asset:
        up = c.post("/generate/upscale", {
            "prompt": "", "project_id": project["id"],
            "references": [{"asset_id": image_asset["id"], "kind": "image", "role": "reference"}],
            "params": {"upscale_factor": 2},
        })
        up_job = c.wait_for_job(up["job_id"], timeout=300)
        check("image upscale completed", up_job.get("status") == "completed", f"status={up_job.get('status')}")

    # --- Video ------------------------------------------------------------ #
    vid = c.post("/generate/video", {
        "prompt": "slow aerial drone shot over a futuristic city at blue hour",
        "project_id": project["id"],
        "params": {"duration": 3, "fps": 16, "camera": "drone", "aspect": "16:9", "resolution": "512"},
    })
    vid_job = c.wait_for_job(vid["job_id"], timeout=600)
    check("video generation completed", vid_job.get("status") == "completed", f"status={vid_job.get('status')}")

    assets = c.get("/assets?limit=10")
    video_asset = next((a for a in assets["items"] if a["kind"] == "video"), None)
    check("video asset stored", video_asset is not None,
          f"{video_asset.get('duration_sec')}s {video_asset.get('width')}x{video_asset.get('height')}" if video_asset else "none")

    # --- Voice + music ------------------------------------------------------ #
    voice = c.post("/generate/voice", {"params": {"text": "Welcome to AI Creative Studio.", "language": "en"}})
    voice_job = c.wait_for_job(voice["job_id"], timeout=300)
    check("voice generation completed", voice_job.get("status") == "completed", f"status={voice_job.get('status')}")

    music = c.post("/generate/audio", {"prompt": "gentle cinematic bed", "params": {"genre": "cinematic", "duration": 8}})
    music_job = c.wait_for_job(music["job_id"], timeout=300)
    check("music generation completed", music_job.get("status") == "completed", f"status={music_job.get('status')}")

    # --- Avatar ------------------------------------------------------------- #
    avatar = c.post("/generate/avatar", {"params": {"script": "Hello and welcome to the studio.", "aspect": "9:16"}})
    avatar_job = c.wait_for_job(avatar["job_id"], timeout=600)
    check("talking avatar completed", avatar_job.get("status") == "completed", f"status={avatar_job.get('status')}")

    # --- Story --------------------------------------------------------------- #
    story = c.post("/story/generate", {"prompt": "a short animated story about a lion and a rabbit",
                                       "duration_sec": 24, "project_id": project["id"], "target": "image"})
    story_job = c.wait_for_job(story["job_id"], timeout=900) if story.get("job_id") else {"status": "none"}
    check("story scenes generated", story_job.get("status") == "completed", f"status={story_job.get('status')}")

    scenes = c.get(f"/story/{project['id']}")
    check("story persisted scenes", len(scenes.get("scenes", [])) > 0, f"{len(scenes.get('scenes', []))} scenes")

    # --- Timeline export ------------------------------------------------------ #
    if video_asset:
        render = c.post("/editor/render", {
            "preset": "youtube", "project_id": project["id"],
            "timeline": {"clips": [{"track": "video", "asset_id": video_asset["id"], "start": 0}],
                         "captions": [{"start": 0, "end": 2, "text": "Smoke test caption"}]},
        })
        render_job = c.wait_for_job(render["job_id"], timeout=600)
        check("timeline export completed", render_job.get("status") == "completed", f"status={render_job.get('status')}")

    # --- Assistant ------------------------------------------------------------- #
    cmd = c.post("/assistant/command", {"command": "Create a cinematic image of a futuristic Hyderabad", "dry_run": True})
    check("assistant planning", cmd.get("planned", {}).get("intent") == "create_image",
          f"intent={cmd.get('planned', {}).get('intent')}")

    # --- Model manager ---------------------------------------------------------- #
    models = c.get("/models")
    ready = [m for m in models["items"] if m.get("status") in ("installed", "available")]
    check("model registry", len(models["items"]) > 0, f"{len(ready)}/{len(models['items'])} ready")

    # --- Missing model reports honestly ------------------------------------------ #
    try:
        c.post("/generate/image", {"prompt": "test", "model_id": "diffusers-image"})
        check("uninstalled model is reported", False, "request unexpectedly succeeded")
    except RuntimeError as exc:
        # §42.3 — an absent model is a 409 with an explicit, actionable message.
        check("uninstalled model is reported",
              "409" in str(exc) and "not installed" in str(exc).lower(),
              str(exc)[:120])

    # --- Summary --------------------------------------------------------------- #
    failed = [r for r in RESULTS if r[0] != "pass"]
    print()
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("Failed checks:")
        for _, name, detail in failed:
            print(f"  - {name}: {detail}")
    return 1 if failed else 0


def _rejects_unauth(client: Client) -> bool:
    try:
        client.get("/projects")
        return False
    except RuntimeError as exc:
        return "401" in str(exc) or "unauthorized" in str(exc).lower()


if __name__ == "__main__":
    sys.exit(main())
