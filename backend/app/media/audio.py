"""
Audio engine.

* ``write_wav`` — real 16-bit PCM WAV writing (stdlib ``wave``).
* ``synth_voice`` — a genuine *formant synthesiser* that turns text into
  audible, word-shaped speech-like audio (per-syllable vowel formants, consonant
  bursts, pitch contour, prosody). It is honest DSP, not a neural TTS model, and
  is always reported as ``engine=formant-demo``. It exists so the narration
  pipeline, timing, waveform and video muxing are all really functional before a
  cloud TTS provider is configured.
* ``waveform_png`` — real waveform rendering for the UI timeline.
* ``decode_pcm`` — FFmpeg-backed decode used for analysis and rendering.
"""

from __future__ import annotations

import io
import math
import subprocess
import wave
from array import array
from pathlib import Path

import numpy as np

from .fonts import font_for

try:  # Pillow is a hard dependency of the image engine; keep import local-safe
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]

DEFAULT_SR = 22050

VOICE_PROFILES: dict[str, dict] = {
    "aria":   {"f0": 196.0, "f0_range": 62.0, "brightness": 1.12, "rate": 1.0,  "formant_shift": 1.10},
    "nova":   {"f0": 214.0, "f0_range": 70.0, "brightness": 1.05, "rate": 1.02, "formant_shift": 1.16},
    "kai":    {"f0": 118.0, "f0_range": 44.0, "brightness": 0.98, "rate": 0.98, "formant_shift": 0.96},
    "orion":  {"f0": 132.0, "f0_range": 52.0, "brightness": 0.92, "rate": 0.95, "formant_shift": 0.90},
    "sage":   {"f0": 156.0, "f0_range": 38.0, "brightness": 1.0,  "rate": 0.9,  "formant_shift": 1.0},
    "neutral": {"f0": 165.0, "f0_range": 50.0, "brightness": 1.0, "rate": 1.0,  "formant_shift": 1.0},
}

VOWELS = {
    "a": (730, 1090), "e": (530, 1840), "i": (270, 2290),
    "o": (570, 840), "u": (300, 870), "y": (300, 2100),
}
CONSONANTS = {"s": 5200, "f": 4000, "sh": 3200, "t": 3000, "k": 2600, "p": 900, "b": 700, "d": 1700, "g": 1500, "m": 260, "n": 300, "r": 1200, "l": 1100, "v": 900, "z": 4400, "h": 1500, "w": 600}


# --------------------------------------------------------------------------
# low level
# --------------------------------------------------------------------------
def write_wav(path: Path, samples: np.ndarray, sample_rate: int = DEFAULT_SR) -> Path:
    """Write float samples in [-1, 1] to a 16-bit mono PCM WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    data = array("h", pcm.tobytes().decode("latin-1") if False else pcm.tolist())
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(data.tobytes())
    return path


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
        width = handle.getsampwidth()
        channels = handle.getnchannels()
        rate = handle.getframerate()
    if width != 2:
        raise ValueError(f"unsupported sample width {width}")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def decode_pcm(path: Path, sample_rate: int = 8000) -> np.ndarray:
    """Decode any media file to mono float samples via FFmpeg."""
    from ..config import detect_ffmpeg

    ffmpeg = detect_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg unavailable; cannot decode audio")
    proc = subprocess.run(
        [ffmpeg, "-v", "quiet", "-i", str(path), "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(sample_rate), "-"],
        capture_output=True,
        timeout=600,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"ffmpeg decode failed for {path.name}")
    return np.frombuffer(proc.stdout, dtype="<i2").astype(np.float32) / 32768.0


def silence(seconds: float, sample_rate: int = DEFAULT_SR) -> np.ndarray:
    return np.zeros(int(max(0.0, seconds) * sample_rate), dtype=np.float32)


def normalise(samples: np.ndarray, target_peak: float = 0.92) -> np.ndarray:
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak < 1e-6:
        return samples
    return (samples * (target_peak / peak)).astype(np.float32)


def fade(samples: np.ndarray, ms: int = 12, sample_rate: int = DEFAULT_SR) -> np.ndarray:
    length = min(int(sample_rate * ms / 1000), samples.size // 2)
    if length <= 1:
        return samples
    ramp = np.linspace(0.0, 1.0, length, dtype=np.float32)
    out = samples.copy()
    out[:length] *= ramp
    out[-length:] *= ramp[::-1]
    return out


# --------------------------------------------------------------------------
# formant synthesiser
# --------------------------------------------------------------------------
def _syllabify(word: str) -> list[tuple[str, str]]:
    """Split a word into rough (consonant, vowel) syllable clusters."""
    vowels = set("aeiouy")
    clusters: list[tuple[str, str]] = []
    buffer = ""
    vowel = ""
    for index, char in enumerate(word):
        if char in vowels:
            vowel = char
            next_char = word[index + 1] if index + 1 < len(word) else ""
            if next_char and next_char not in vowels:
                clusters.append((buffer, vowel))
                buffer = ""
                vowel = ""
        else:
            buffer += char
    if vowel or buffer:
        clusters.append((buffer, vowel or "a"))
    return clusters or [("", "a")]


def _formant_tone(f1: float, f2: float, f0: float, duration: float, rate: int, brightness: float) -> np.ndarray:
    n = max(1, int(duration * rate))
    t = np.arange(n, dtype=np.float32) / rate
    # glottal-ish source: fundamental + harmonics, decaying amplitude
    source = np.zeros(n, dtype=np.float32)
    for harmonic in range(1, 13):
        amplitude = 1.0 / (harmonic**1.35)
        if harmonic * f0 > rate / 2 - 100:
            break
        source += amplitude * np.sin(2 * math.pi * f0 * harmonic * t + harmonic * 0.3)
    source /= max(1e-6, float(np.max(np.abs(source))))
    # crude formant emphasis by modulating with resonant envelopes
    envelope = 0.55 + 0.45 * np.sin(2 * math.pi * (f1 / max(f0, 1)) * t) * np.sin(
        2 * math.pi * (f2 / max(f0, 1)) * t
    )
    voiced = source * envelope * brightness
    # gentle low-pass via cumulative smoothing to reduce harshness
    kernel = np.hanning(9)
    kernel /= kernel.sum()
    voiced = np.convolve(voiced, kernel, mode="same").astype(np.float32)
    return voiced


def _noise_burst(centre: float, duration: float, rate: int, rng: np.random.Generator) -> np.ndarray:
    n = max(1, int(duration * rate))
    noise = rng.standard_normal(n).astype(np.float32)
    # simple band-pass approximation around the consonant centre frequency
    cutoff = float(np.clip(centre / (rate / 2), 0.02, 0.95))
    kernel_len = max(3, int(rate / max(200.0, centre) * 2))
    kernel = np.hanning(kernel_len).astype(np.float32)
    kernel /= max(1e-6, float(kernel.sum()))
    shaped = np.convolve(noise, kernel, mode="same").astype(np.float32)
    shaped *= cutoff * 3.0
    return shaped


def synth_voice(
    text: str,
    *,
    voice: str = "aria",
    speed: float = 1.0,
    sample_rate: int = DEFAULT_SR,
    pitch_shift: float = 0.0,
    pause_ms: int = 140,
) -> np.ndarray:
    """Render text to speech-like audio using formant synthesis (demo engine)."""
    profile = VOICE_PROFILES.get((voice or "neutral").lower(), VOICE_PROFILES["neutral"])
    rng = np.random.default_rng(abs(hash(text)) % (2**32))
    rate = max(0.4, min(2.5, float(speed or 1.0)))
    f0_base = profile["f0"] * (2 ** (pitch_shift / 12.0))
    pieces: list[np.ndarray] = []
    words = [w for w in (text or "").split() if w.strip()]

    for word_index, word in enumerate(words):
        clean = "".join(ch for ch in word.lower() if ch.isalpha())
        if not clean:
            continue
        clusters = _syllabify(clean)
        for syl_index, (consonant, vowel) in enumerate(clusters):
            # pitch contour: declination across the sentence + small intra-word wiggle
            progress = word_index / max(1, len(words))
            contour = profile["f0_range"] * (0.5 - progress) * 0.5
            wobble = profile["f0_range"] * 0.12 * math.sin(syl_index * 1.7 + word_index)
            f0 = max(70.0, f0_base + contour + wobble + rng.normal(0, 4.0))

            if consonant:
                centre = CONSONANTS.get(consonant[0], 2500)
                burst = _noise_burst(centre, 0.045 / rate, sample_rate, rng)
                burst = fade(burst * 0.55, 6, sample_rate)
                pieces.append(burst)

            f1, f2 = VOWELS.get(vowel, VOWELS["a"])
            shift = profile["formant_shift"]
            syllable_len = (0.145 / rate) * (1.0 + (len(vowel) > 0) * 0.1)
            tone = _formant_tone(
                f1 * shift, f2 * shift, f0, syllable_len, sample_rate, profile["brightness"]
            )
            # amplitude envelope with syllable stress
            attack = int(0.18 * tone.size)
            decay = max(1, tone.size - attack)
            env = np.concatenate(
                [
                    np.linspace(0.0, 1.0, attack, dtype=np.float32),
                    0.85 * np.exp(-np.linspace(0, 2.0, decay, dtype=np.float32)),
                ]
            )[: tone.size]
            pieces.append(fade(tone * env, 8, sample_rate))

        pieces.append(silence(pause_ms / 1000.0, sample_rate))

    if not pieces:
        return silence(0.35, sample_rate)
    audio = np.concatenate(pieces).astype(np.float32)
    audio = np.convolve(audio, np.hanning(5) / np.hanning(5).sum(), mode="same").astype(np.float32)
    return normalise(audio, 0.88)


def estimate_speech_seconds(text: str, words_per_minute: float = 150.0) -> float:
    words = max(1, len((text or "").split()))
    return round(words / max(30.0, words_per_minute) * 60.0, 2)


# --------------------------------------------------------------------------
# waveform rendering
# --------------------------------------------------------------------------
def waveform_png(
    source: Path,
    target: Path,
    width: int = 640,
    height: int = 120,
    color: str = "#4f9cf9",
    background: str = "#0b1220",
    sample_rate: int = 8000,
) -> Path:
    """Render a real waveform (peak envelope) for an audio file."""
    if Image is None:  # pragma: no cover
        return target
    try:
        samples = decode_pcm(source, sample_rate)
    except Exception:
        samples = np.zeros(1, dtype=np.float32)
    columns = max(32, int(width))
    if samples.size >= columns:
        # split into columns and take peak absolute amplitude
        trimmed = samples[: (samples.size // columns) * columns]
        blocks = trimmed.reshape(columns, -1)
        peaks = np.max(np.abs(blocks), axis=1)
    else:
        peaks = np.abs(samples) if samples.size else np.zeros(1, dtype=np.float32)
        peaks = np.interp(
            np.linspace(0, peaks.size - 1, columns), np.arange(peaks.size), peaks
        )
    peaks = np.clip(peaks, 0.0, 1.0)

    image = Image.new("RGB", (int(width), int(height)), background)
    draw = ImageDraw.Draw(image)
    mid = height / 2
    fill = tuple(int(color.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    for x in range(columns):
        amplitude = max(1.0, peaks[x] * (height - 6) / 2)
        draw.line([(x, mid - amplitude), (x, mid + amplitude)], fill=fill, width=1)
    draw.line([(0, mid), (columns, mid)], fill=(255, 255, 255, 40), width=1)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, "PNG")
    return target


def duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as handle:
            return round(handle.getnframes() / float(handle.getframerate()), 3)
    except Exception:
        try:
            from ..storage import probe_video

            return float(probe_video(path).get("duration_s") or 0.0)
        except Exception:
            return 0.0


def mix_tracks(tracks: list[tuple[np.ndarray, float]], sample_rate: int = DEFAULT_SR) -> np.ndarray:
    """Mix (samples, gain) pairs by overlaying from t=0, padding to the longest."""
    if not tracks:
        return silence(0.1, sample_rate)
    length = max(int(track.size) for track, _ in tracks)
    output = np.zeros(length, dtype=np.float32)
    for track, gain in tracks:
        output[: track.size] += track * float(gain)
    return normalise(output, 0.94)


def wav_bytes(samples: np.ndarray, sample_rate: int = DEFAULT_SR) -> bytes:
    """In-memory WAV encoding (used when we must not touch disk)."""
    buffer = io.BytesIO()
    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()
