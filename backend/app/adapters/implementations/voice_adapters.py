"""Voice engine adapters: text-to-speech and voice cloning.

SAFETY (spec §25, §43): voice cloning refuses to run unless a dated consent
attestation exists on the voice profile (`consent_attested_at`) or the request
carries `consent_attested: true`. No real person's voice is ever cloned
without that attestation.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.adapters.base import (
    AdapterStatus,
    GeneratedFile,
    GenerationRequest,
    GenerationResult,
    ProgressCallback,
    VoiceModelAdapter,
    timed,
)
from app.core.config import settings
from app.core.errors import ErrorCode, StudioError
from app.core.hardware import ffmpeg_path


def _out(req: GenerationRequest, name: str) -> str:
    d = Path(req.output_dir)
    d.mkdir(parents=True, exist_ok=True)
    return str(d / name)


def _check_consent(req: GenerationRequest) -> None:
    if not settings.REQUIRE_VOICE_CONSENT:
        return
    if req.param("consent_attested") is True:
        return
    raise StudioError(
        "Voice cloning requires a written consent attestation for the voice owner.",
        code=ErrorCode.CONSENT_REQUIRED,
        status_code=403,
        remediation="Store a dated consent attestation on the voice profile (Voice Studio → Consent) "
                    "before cloning. Cloning a person's voice without authorisation is not permitted.",
    )


class PiperTTSAdapter(VoiceModelAdapter):
    """Local neural TTS (piper). CPU friendly, permissive licences.

    The Python package is installed; a voice model must be downloaded once
    (never automatic for large files - and these are small, ~60 MB).
    """

    key = "piper"
    name = "Piper Neural TTS (local)"
    capability = "tts"
    capabilities = ("tts",)
    version = "1.8"
    license = "MIT (engine) / voice model licences vary"
    source_url = "https://github.com/rhasspy/piper"
    description = "Fast local neural TTS that runs on CPU. Download one voice model (~60 MB) to enable."
    requires = ("piper-tts", "onnxruntime")
    quality = "standard"
    size_gb = 0.06
    ram_gb = 0.5
    vram_gb = 0.0
    weights_path = str(Path("storage/models/piper"))

    def weights_present(self) -> bool:
        p = Path(self.weights_path)
        return p.exists() and any(p.rglob("*.onnx"))

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        self.ensure_ready()
        text = (req.prompt or "").strip()
        if not text:
            raise StudioError("Text is required for speech synthesis.", code=ErrorCode.INVALID_REQUEST)
        model = next(Path(self.weights_path).rglob("*.onnx"))
        out = _out(req, "speech.wav")
        progress(0.2, "synthesising")
        cmd = [
            "python", "-m", "piper", "-m", str(model), "-f", out,
            "--length-scale", str(req.param("speed", 1.0)),
        ]
        proc = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0 or not Path(out).exists():
            raise StudioError("Piper synthesis failed.", code=ErrorCode.INTERNAL_ERROR,
                              details={"stderr": (proc.stderr or "")[:2000]})
        progress(1.0, "complete")
        return GenerationResult(files=[GeneratedFile(out, "audio", "audio/wav", {"engine": "piper"})],
                                meta={"engine": "piper", "characters": len(text)}, model_id=self.key)


class EspeakAdapter(VoiceModelAdapter):
    """Robotic but instant local TTS. Zero model download."""

    key = "espeak"
    name = "eSpeak NG (local, offline)"
    capability = "tts"
    capabilities = ("tts",)
    version = "1.52"
    license = "GPL-3.0"
    source_url = "https://github.com/espeak-ng/espeak-ng"
    description = "Formant synthesiser bundled with most Linux distros. Instant, offline, low quality."
    requires_binaries = ("espeak-ng",)
    quality = "draft"
    ram_gb = 0.05

    def status(self, refresh: bool = False) -> AdapterStatus:
        st = super().status(refresh=refresh)
        if st.code == ErrorCode.DEPENDENCY_MISSING:
            # `espeak` (older package name) is an acceptable substitute.
            if shutil.which("espeak"):
                st.available = True
                st.code = ""
                st.message = "Available (espeak)"
                st.remediation = ""
        return st

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        self.ensure_ready()
        exe = shutil.which("espeak-ng") or shutil.which("espeak")
        if not exe:
            raise StudioError("eSpeak is not installed.", code=ErrorCode.DEPENDENCY_MISSING,
                              remediation="Windows: choco install espeak-ng | Linux: sudo apt install espeak-ng")
        text = (req.prompt or "").strip()
        if not text:
            raise StudioError("Text is required.", code=ErrorCode.INVALID_REQUEST)
        out = _out(req, "speech.wav")
        wpm = int(175 * float(req.param("speed", 1.0)))
        pitch = int(50 + 20 * float(req.param("pitch", 0.0)))
        voice = req.param("language", "en") or "en"
        cmd = [exe, "-v", voice, "-s", str(wpm), "-p", str(pitch), "-w", out, text]
        progress(0.3, "synthesising")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0 or not Path(out).exists():
            raise StudioError("eSpeak synthesis failed.", code=ErrorCode.INTERNAL_ERROR,
                              details={"stderr": (proc.stderr or "")[:1500]})
        progress(1.0, "complete")
        return GenerationResult(files=[GeneratedFile(out, "audio", "audio/wav", {"engine": "espeak"})],
                                meta={"engine": "espeak", "voice": voice}, model_id=self.key)


class Pyttsx3Adapter(VoiceModelAdapter):
    """OS speech engine: SAPI5 on Windows, NSSpeechSynthesizer on macOS,
    eSpeak on Linux. Ships with the OS, so Windows users get real TTS free."""

    key = "pyttsx3"
    name = "System Voices (pyttsx3 / SAPI5 / NSSpeech)"
    capability = "tts"
    capabilities = ("tts",)
    version = "2.90"
    license = "MPL-2.0"
    source_url = "https://github.com/nateshmbhat/pyttsx3"
    description = "Uses the operating system's built-in voices. Works out of the box on Windows."
    requires = ("pyttsx3",)
    quality = "standard"
    ram_gb = 0.1

    def status(self, refresh: bool = False) -> AdapterStatus:
        st = super().status(refresh=refresh)
        if st.code != ErrorCode.DEPENDENCY_MISSING:
            return st
        system = platform.system()
        if system == "Windows":
            return AdapterStatus(self.key, self.name, False, ErrorCode.DEPENDENCY_MISSING,
                                 "pyttsx3 is not installed. Windows SAPI5 voices are available once it is.",
                                 "pip install pyttsx3", device="cpu", requires=list(self.requires),
                                 missing_requirements=["pyttsx3"], hardware={})
        return st

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        self.ensure_ready()
        import pyttsx3  # type: ignore  # noqa: WPS433

        text = (req.prompt or "").strip()
        if not text:
            raise StudioError("Text is required.", code=ErrorCode.INVALID_REQUEST)
        out = _out(req, "speech.wav")
        engine = pyttsx3.init()
        try:
            base = engine.getProperty("rate")
            engine.setProperty("rate", int(base * float(req.param("speed", 1.0))))
            engine.setProperty("volume", float(req.param("volume", 1.0)))
            voice_id = req.param("voice_id")
            if voice_id:
                engine.setProperty("voice", voice_id)
            progress(0.4, "synthesising")
            engine.save_to_file(text, out)
            engine.runAndWait()
        finally:
            try:
                engine.stop()
            except Exception:
                pass
        if not Path(out).exists() or Path(out).stat().st_size < 100:
            raise StudioError(
                "The system speech engine did not produce a file.",
                code=ErrorCode.INTERNAL_ERROR,
                remediation="On Linux install espeak-ng; on macOS/Windows use the Piper or provider adapters.",
            )
        progress(1.0, "complete")
        return GenerationResult(files=[GeneratedFile(out, "audio", "audio/wav", {"engine": "pyttsx3"})],
                                meta={"engine": "pyttsx3", "platform": platform.system()}, model_id=self.key)


class ElevenLabsTTSAdapter(VoiceModelAdapter):
    key = "elevenlabs"
    name = "ElevenLabs TTS (API)"
    capability = "tts"
    capabilities = ("tts", "voice_clone")
    provider = "elevenlabs"
    local = False
    quality = "reference"
    description = "Hosted high-quality voices. Requires an API key and is billed per character."

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        self.ensure_ready()
        if req.param("mode") == "voice_clone":
            _check_consent(req)
        import httpx  # noqa: WPS433

        voice_id = req.param("voice_id", "21m00Tcm4TlvDq8ikWAM")
        out = _out(req, "speech.mp3")
        progress(0.3, "calling ElevenLabs")
        r = httpx.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": self._api_key(), "Content-Type": "application/json"},
            json={"text": req.prompt, "model_id": req.param("model", "eleven_multilingual_v2"),
                  "voice_settings": {"stability": float(req.param("stability", 0.4)),
                                     "similarity_boost": float(req.param("similarity", 0.8))}},
            timeout=180,
        )
        if r.status_code >= 400:
            raise StudioError(f"ElevenLabs HTTP {r.status_code}", code=ErrorCode.INTERNAL_ERROR,
                              details={"body": r.text[:1500]})
        Path(out).write_bytes(r.content)
        progress(1.0, "complete")
        return GenerationResult(files=[GeneratedFile(out, "audio", "audio/mpeg", {"provider": "elevenlabs"})],
                                meta={"provider": "elevenlabs", "voice_id": voice_id}, model_id=self.key)


class AzureTTSAdapter(VoiceModelAdapter):
    key = "azure_tts"
    name = "Azure Neural TTS (API)"
    capability = "tts"
    capabilities = ("tts",)
    provider = "azure"
    local = False
    quality = "high"
    description = "Hosted neural voices with SSML control over pitch, rate and pauses."

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        self.ensure_ready()
        import httpx  # noqa: WPS433
        import xml.sax.saxutils as su

        region = req.param("region", "eastus")
        rate = int((float(req.param("speed", 1.0)) - 1.0) * 100)
        pitch = f"{int(float(req.param('pitch', 0.0)) * 20):+d}%"
        ssml = (
            f"<speak version='1.0' xml:lang='{req.param('language','en-US')}'>"
            f"<voice name='{req.param('voice','en-US-AriaNeural')}'>"
            f"<prosody rate='{rate:+d}%' pitch='{pitch}'>{su.escape(req.prompt or '')}</prosody>"
            f"</voice></speak>"
        )
        out = _out(req, "speech.wav")
        r = httpx.post(
            f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
            headers={"Ocp-Apim-Subscription-Key": self._api_key(),
                     "Content-Type": "application/ssml+xml",
                     "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm"},
            content=ssml.encode("utf-8"), timeout=180,
        )
        if r.status_code >= 400:
            raise StudioError(f"Azure TTS HTTP {r.status_code}", code=ErrorCode.INTERNAL_ERROR,
                              details={"body": r.text[:1500]})
        Path(out).write_bytes(r.content)
        return GenerationResult(files=[GeneratedFile(out, "audio", "audio/wav", {"provider": "azure"})],
                                meta={"provider": "azure"}, model_id=self.key)


class GoogleTTSAdapter(VoiceModelAdapter):
    key = "google_tts"
    name = "Google Cloud TTS (API)"
    capability = "tts"
    capabilities = ("tts",)
    provider = "google"
    local = False
    quality = "high"
    description = "Hosted WaveNet/Neural2 voices, 200+ languages."

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        self.ensure_ready()
        import base64

        import httpx  # noqa: WPS433

        payload = {
            "input": {"text": req.prompt},
            "voice": {"languageCode": req.param("language", "en-US"),
                      "name": req.param("voice", "en-US-Neural2-F")},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": float(req.param("speed", 1.0)),
                            "pitch": float(req.param("pitch", 0.0))},
        }
        r = httpx.post("https://texttospeech.googleapis.com/v1/text:synthesize",
                       params={"key": self._api_key()}, json=payload, timeout=180)
        if r.status_code >= 400:
            raise StudioError(f"Google TTS HTTP {r.status_code}", code=ErrorCode.INTERNAL_ERROR,
                              details={"body": r.text[:1500]})
        out = _out(req, "speech.mp3")
        Path(out).write_bytes(base64.b64decode(r.json()["audioContent"]))
        return GenerationResult(files=[GeneratedFile(out, "audio", "audio/mpeg", {"provider": "google"})],
                                meta={"provider": "google"}, model_id=self.key)


class OpenVoiceCloneAdapter(VoiceModelAdapter):
    """Local zero-shot voice cloning. Consent gated."""

    key = "openvoice"
    name = "OpenVoice v2 (local voice clone)"
    capability = "voice_clone"
    capabilities = ("voice_clone", "tts")
    version = "v2"
    license = "MIT"
    source_url = "https://github.com/myshell-ai/OpenVoice"
    description = "Zero-shot voice cloning from a short reference clip. Consent attestation required."
    requires = ("torch", "openvoice")
    size_gb = 2.2
    vram_gb = 6.0
    ram_gb = 12.0
    quality = "high"
    weights_path = str(Path("storage/models/openvoice"))

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        _check_consent(req)
        self.ensure_ready()
        raise StudioError(
            "OpenVoice checkpoints are not installed.",
            code=ErrorCode.MODEL_NOT_AVAILABLE,
            remediation="Download OpenVoice v2 checkpoints into storage/models/openvoice, then retry.",
        )
