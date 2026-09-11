"""Neural lip-sync adapter (Tier B) — Wav2Lip / MuseTalk / SadTalker class."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.core.errors import StudioError
from app.core.logging import get_logger
from app.engines.base import (
    Artifact, BaseModelAdapter, Capabilities, Estimate, GenerateRequest, JobContext, ValidationResult,
)
from app.media import ffmpeg

log = get_logger("engines.lipsync.neural")


class NeuralLipSyncAdapter(BaseModelAdapter):
    id = "neural-lipsync"
    display_name = "Neural Lip Sync (Wav2Lip / MuseTalk)"
    version = "1.0.0"
    family = "lipsync"
    provider = "local"
    license = "depends on selected checkpoint"
    size_mb = 900
    vram_mb = 4096
    speed = "medium"
    is_local = True
    ready_out_of_the_box = False
    requires_extra = "requirements-gpu.txt"
    notes = "Set LIPSYNC_CHECKPOINT and LIPSYNC_RUNNER (path to the model's inference script)."
    capabilities = Capabilities(
        family="lipsync",
        modes=["lip-sync", "avatar-lip-sync"],
        supports_reference_images=True,
        supports_duration=True,
        supports_lipsync=True,
        max_duration_sec=300,
    )

    @property
    def runner(self) -> str:
        return os.getenv("LIPSYNC_RUNNER", "")

    @property
    def checkpoint(self) -> str:
        return os.getenv("LIPSYNC_CHECKPOINT", "")

    def status(self) -> dict[str, Any]:
        if not self.runner or not Path(self.runner).exists():
            return {"status": "not_installed",
                    "reason": "Set LIPSYNC_RUNNER to your lip-sync inference script (e.g. Wav2Lip inference.py)."}
        if not self.checkpoint or not Path(self.checkpoint).exists():
            return {"status": "not_installed", "reason": "Set LIPSYNC_CHECKPOINT to the model weights file."}
        return {"status": "installed", "reason": ""}

    def validate(self, request: GenerateRequest) -> ValidationResult:
        result = ValidationResult()
        if not any(r.kind == "video" for r in request.references):
            result.add_error("Attach the talking-head video.")
        if not any(r.kind == "audio" for r in request.references):
            result.add_error("Attach the speech audio.")
        st = self.status()
        if st["status"] != "installed":
            result.add_error(st["reason"])
        return result

    def estimate(self, request: GenerateRequest) -> Estimate:
        return Estimate(seconds=60.0, vram_mb=self.vram_mb, credits=8.0)

    def generate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        self.ensure_ready()
        video = next(r for r in request.references if r.kind == "video")
        audio = next(r for r in request.references if r.kind == "audio")
        out = ctx.path(f"lipsync_{ctx.job_id}.mp4")
        cmd = [
            "python", self.runner,
            "--face", str(video.path),
            "--audio", str(audio.path),
            "--checkpoint_path", self.checkpoint,
            "--outfile", out,
        ]
        ctx.progress(35, "running neural lip sync")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if proc.returncode != 0 or not Path(out).exists():
            log.error("lipsync_failed", code=proc.returncode, stderr=proc.stderr[:800])
            raise StudioError("Lip sync failed.", detail=proc.stderr[:800],
                              suggested_action="Retry / Check GPU / Verify the checkpoint path")
        info = ffmpeg.probe(out)
        return [Artifact(path=out, kind="video", mime="video/mp4", name=f"lipsync_{ctx.job_id}",
                         meta={"engine": self.id, "mode": request.mode, "lip_sync": True, "method": "neural",
                               "duration": info.get("duration"), "params": request.params})]


ADAPTERS = [NeuralLipSyncAdapter()]
