"""Real audio DSP: synthesis, envelopes, filtering, mixing and file I/O.

All audio produced here is genuinely synthesised sample-by-sample (numpy) and
encoded with FFmpeg. Voice *intelligibility* depends on a TTS engine — when no
engine is installed we produce a prosody reference track that is clearly
labelled `intelligible: false` rather than pretending to be speech (§42).
"""

from __future__ import annotations

import math
import os
import tempfile
import wave
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

from app.core.errors import StudioError
from app.core.logging import get_logger
from app.media import ffmpeg

log = get_logger("media.audio")

SAMPLE_RATE = 44100


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #

def write_wav(path: str | Path, samples: np.ndarray, sr: int = SAMPLE_RATE) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(np.asarray(samples, dtype=np.float64), -1.0, 1.0)
    if data.ndim == 1:
        data = np.stack([data, data], axis=1)
    pcm = (data * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(pcm.shape[1])
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return str(path)


def read_audio(path: str | Path, *, sr: int = SAMPLE_RATE, mono: bool = True) -> tuple[np.ndarray, int]:
    """Decode any format FFmpeg understands into a float array."""
    # A unique scratch name per call: two decodes of files with the same
    # basename must never race over one path (and `-y` keeps a leftover file
    # from stalling FFmpeg on an overwrite prompt).
    source = Path(path)
    handle, tmp_name = tempfile.mkstemp(prefix=f"{source.stem}.", suffix=".decode.wav",
                                        dir=str(source.parent))
    os.close(handle)
    tmp = Path(tmp_name)
    try:
        ffmpeg.run(["-y", "-i", str(path), "-ar", str(sr), "-ac", "1" if mono else "2",
                    "-c:a", "pcm_s16le", str(tmp), "-loglevel", "error"])
        with wave.open(str(tmp), "rb") as wf:
            channels = wf.getnchannels()
            raw = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")
        audio = raw.astype(np.float64) / 32767.0
        if channels > 1:
            audio = audio.reshape(-1, channels)
        return audio, sr
    finally:
        tmp.unlink(missing_ok=True)


def encode(path: str | Path, out_path: str | Path, *, bitrate: str = "192k") -> str:
    out_path = str(out_path)
    ffmpeg.run(["-i", str(path), "-c:a", "aac" if out_path.endswith((".m4a", ".mp4")) else "libmp3lame",
                "-b:a", bitrate, out_path])
    return out_path


def duration_of(path: str | Path) -> float:
    info = ffmpeg.probe(path)
    return float(info.get("duration") or 0.0)


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #

def _t(seconds: float, sr: int = SAMPLE_RATE) -> np.ndarray:
    return np.arange(int(seconds * sr), dtype=np.float64) / sr


def osc(freq: float, seconds: float, *, wave_type: str = "sine", sr: int = SAMPLE_RATE,
        phase: float = 0.0) -> np.ndarray:
    t = _t(seconds, sr)
    arg = 2 * math.pi * float(freq) * t + phase
    kind = wave_type.lower()
    if kind == "sine":
        return np.sin(arg)
    if kind == "saw":
        return 2 * ((arg / (2 * math.pi)) % 1.0) - 1
    if kind == "square":
        return np.sign(np.sin(arg))
    if kind == "triangle":
        return 2 * np.abs(2 * ((arg / (2 * math.pi)) % 1.0) - 1) - 1
    return np.sin(arg)


def noise(seconds: float, *, sr: int = SAMPLE_RATE, seed: int = 0, color: str = "white") -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    if n <= 0:
        return np.zeros(1)
    white = rng.normal(0, 1, n)
    if color == "pink":
        b = np.array([0.049922035, -0.095993537, 0.050612699, -0.004408786])
        a = np.array([1, -2.494956002, 2.017265875, -0.522189400])
        out = np.zeros(n)
        for i in range(n):
            prev = [out[i - k - 1] if i - k - 1 >= 0 else 0.0 for k in range(len(b))]
            out[i] = b[0] * white[i] + sum(b[k + 1] * prev[k] for k in range(len(b) - 1)) \
                - sum(a[k + 1] * (out[i - k - 1] if i - k - 1 >= 0 else 0.0) for k in range(len(a) - 1))
        return out / (np.max(np.abs(out)) + 1e-6)
    if color == "brown":
        return np.cumsum(white) / (np.max(np.abs(np.cumsum(white))) + 1e-6)
    return white


def adsr(seconds: float, *, attack: float = 0.01, decay: float = 0.1, sustain: float = 0.7,
         release: float = 0.2, sr: int = SAMPLE_RATE) -> np.ndarray:
    total = max(int(seconds * sr), 1)
    a = max(1, int(attack * sr))
    d = max(1, int(decay * sr))
    r = max(1, int(release * sr))
    s = max(0, total - a - d - r)
    env = np.concatenate([
        np.linspace(0, 1, a),
        np.linspace(1, sustain, d),
        np.full(s, sustain),
        np.linspace(sustain, 0, r),
    ])
    if len(env) < total:
        env = np.pad(env, (0, total - len(env)), constant_values=0.0)
    return env[:total]


def lowpass(x: np.ndarray, cutoff: float, sr: int = SAMPLE_RATE, *, q: float = 0.707) -> np.ndarray:
    cutoff = max(20.0, min(cutoff, sr / 2 - 100))
    omega = 2 * math.pi * cutoff / sr
    alpha = math.sin(omega) / (2 * q)
    b0 = (1 - math.cos(omega)) / 2
    b1 = 1 - math.cos(omega)
    b2 = b0
    a0 = 1 + alpha
    a1 = -2 * math.cos(omega)
    a2 = 1 - alpha
    y = np.zeros_like(x)
    for i in range(len(x)):
        x0 = x[i] if i < len(x) else 0.0
        x1 = x[i - 1] if i >= 1 else 0.0
        x2 = x[i - 2] if i >= 2 else 0.0
        y1 = y[i - 1] if i >= 1 else 0.0
        y2 = y[i - 2] if i >= 2 else 0.0
        y[i] = (b0 / a0) * x0 + (b1 / a0) * x1 + (b2 / a0) * x2 - (a1 / a0) * y1 - (a2 / a0) * y2
    return y


def highpass(x: np.ndarray, cutoff: float, sr: int = SAMPLE_RATE) -> np.ndarray:
    return x - lowpass(x, cutoff, sr)


def reverb(x: np.ndarray, *, decay: float = 2.0, mix: float = 0.28, sr: int = SAMPLE_RATE,
           seed: int = 0) -> np.ndarray:
    n = len(x)
    if n == 0:
        return x
    ir_len = max(int(0.05 * sr), int(decay * sr))
    rng = np.random.default_rng(seed)
    ir = rng.normal(0, 1, ir_len) * np.exp(-np.linspace(0, 4.5, ir_len))
    ir /= np.max(np.abs(ir)) + 1e-6
    tail = np.convolve(x, ir, mode="full")[:n]
    tail = tail / (np.max(np.abs(tail)) + 1e-6) * (np.max(np.abs(x)) + 1e-6)
    return x * (1 - mix) + tail * mix


def normalize(x: np.ndarray, *, target: float = 0.89) -> np.ndarray:
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak < 1e-6:
        return x
    return x * (target / peak)


def mix_tracks(tracks: Sequence[np.ndarray]) -> np.ndarray:
    if not tracks:
        return np.zeros(1)
    n = max(len(t) for t in tracks)
    out = np.zeros(n)
    for t in tracks:
        out[: len(t)] += t
    return normalize(out)


def pad_to(x: np.ndarray, seconds: float, sr: int = SAMPLE_RATE) -> np.ndarray:
    target = int(seconds * sr)
    if len(x) >= target:
        return x[:target]
    return np.pad(x, (0, target - len(x)))


def loop_to(x: np.ndarray, seconds: float, *, sr: int = SAMPLE_RATE, crossfade: float = 0.25) -> np.ndarray:
    target = int(seconds * sr)
    if len(x) == 0:
        return np.zeros(max(target, 1))
    if len(x) >= target:
        return x[:target]
    reps = int(math.ceil(target / len(x))) + 1
    out = np.tile(x, reps)[:target]
    fade = max(1, int(crossfade * sr))
    for start in range(len(x), target - fade, len(x)):
        end = min(start + fade, target)
        if end <= start:
            break
        f = np.linspace(0, 1, end - start)
        out[start:end] = out[start:end] * (1 - f) + np.concatenate([x[: end - start]]) * f
    return out


def fade(x: np.ndarray, *, fade_in: float = 0.02, fade_out: float = 0.4, sr: int = SAMPLE_RATE) -> np.ndarray:
    out = np.array(x, dtype=np.float64, copy=True)
    n = len(out)
    fi = min(n, int(fade_in * sr))
    fo = min(n, int(fade_out * sr))
    if fi:
        out[:fi] *= np.linspace(0, 1, fi)
    if fo:
        out[-fo:] *= np.linspace(1, 0, fo)
    return out


# --------------------------------------------------------------------------- #
# Music & SFX synthesis
# --------------------------------------------------------------------------- #

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "pentatonic": [0, 2, 4, 7, 9],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
}

GENRE_PRESETS = {
    "cinematic": {"scale": "minor", "tempo": 84, "pad": 0.5, "arp": 0.22, "bass": 0.45, "perc": 0.35, "reverb": 3.2, "progression": [0, 5, 3, 4]},
    "corporate": {"scale": "major", "tempo": 108, "pad": 0.3, "arp": 0.3, "bass": 0.3, "perc": 0.25, "reverb": 1.2, "progression": [0, 4, 5, 3]},
    "kids": {"scale": "pentatonic", "tempo": 128, "pad": 0.25, "arp": 0.42, "bass": 0.26, "perc": 0.4, "reverb": 0.9, "progression": [0, 4, 2, 3]},
    "ambient": {"scale": "lydian", "tempo": 62, "pad": 0.72, "arp": 0.1, "bass": 0.22, "perc": 0.0, "reverb": 4.0, "progression": [0, 2, 4, 2]},
    "documentary": {"scale": "dorian", "tempo": 92, "pad": 0.42, "arp": 0.24, "bass": 0.38, "perc": 0.2, "reverb": 2.0, "progression": [0, 3, 5, 4]},
    "uplifting": {"scale": "major", "tempo": 118, "pad": 0.35, "arp": 0.36, "bass": 0.34, "perc": 0.32, "reverb": 1.6, "progression": [0, 5, 3, 4]},
    "suspense": {"scale": "minor", "tempo": 70, "pad": 0.6, "arp": 0.12, "bass": 0.5, "perc": 0.18, "reverb": 3.6, "progression": [0, 1, 0, 6]},
}


def _note_freq(root_hz: float, semitones: int) -> float:
    return root_hz * (2 ** (semitones / 12))


def generate_music(*, genre: str = "cinematic", seconds: float = 30.0, key: str = "C",
                   seed: int = 0, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Deterministic multi-track music synthesiser (pad + arp + bass + percussion)."""
    preset = GENRE_PRESETS.get(genre, GENRE_PRESETS["cinematic"])
    scale = SCALES.get(preset["scale"], SCALES["minor"])
    tempo = float(preset["tempo"])
    beat = 60.0 / tempo
    root = {"C": 130.81, "D": 146.83, "E": 164.81, "F": 174.61, "G": 196.0, "A": 220.0, "B": 246.94}.get(
        str(key).upper()[:1], 130.81)
    rng = np.random.default_rng(seed)
    bars = max(1, int(math.ceil(seconds / (beat * 4))))
    progression = preset["progression"]

    tracks: list[np.ndarray] = []
    total = int(seconds * sr)

    # --- pad: sustained triads per bar ---
    pad = np.zeros(total)
    for bar in range(bars):
        start = int(bar * 4 * beat * sr)
        degree = progression[bar % len(progression)]
        chord = [degree, degree + 2, degree + 4]
        chord_len = int(4 * beat * sr)
        for idx in chord:
            semis = scale[idx % len(scale)] + 12 * (idx // len(scale))
            freq = _note_freq(root, semis) * 2
            tone = osc(freq, 4 * beat, wave_type="saw", sr=sr) * 0.34
            tone += osc(freq * 1.005, 4 * beat, wave_type="sine", sr=sr) * 0.3
            tone = lowpass(tone, 1400 + 500 * rng.random(), sr)
            tone *= adsr(4 * beat, attack=beat * 0.6, decay=beat, sustain=0.75, release=beat * 1.2, sr=sr)
            pad[start:start + chord_len] += tone[: max(0, min(chord_len, total - start))]
    tracks.append(normalize(pad) * preset["pad"])

    # --- arpeggio ---
    arp = np.zeros(total)
    step = beat / 2
    i = 0
    for bar in range(bars):
        degree = progression[bar % len(progression)]
        pattern = [0, 2, 4, 2] if rng.random() > 0.5 else [0, 4, 2, 4]
        for p in pattern:
            start = int(i * step * sr)
            if start >= total:
                break
            idx = degree + p
            semis = scale[idx % len(scale)] + 12 * (idx // len(scale))
            freq = _note_freq(root, semis) * 4
            note = osc(freq, step * 1.6, wave_type="triangle", sr=sr)
            note *= adsr(step * 1.6, attack=0.008, decay=0.06, sustain=0.35, release=step, sr=sr)
            end = min(total, start + len(note))
            arp[start:end] += note[: end - start]
            i += 1
        while i * step * sr < (bar + 1) * 4 * beat * sr:
            i += 1
    tracks.append(normalize(arp) * preset["arp"])

    # --- bass ---
    bass = np.zeros(total)
    for bar in range(bars):
        degree = progression[bar % len(progression)]
        start = int(bar * 4 * beat * sr)
        semis = scale[degree % len(scale)]
        freq = _note_freq(root, semis)
        for hit in (0, 2):
            s = start + int(hit * 2 * beat * sr)
            if s >= total:
                continue
            note = osc(freq, beat * 1.8, wave_type="sine", sr=sr) * 0.9
            note += osc(freq * 2, beat * 1.8, wave_type="triangle", sr=sr) * 0.12
            note *= adsr(beat * 1.8, attack=0.01, decay=0.12, sustain=0.6, release=beat * 0.7, sr=sr)
            end = min(total, s + len(note))
            bass[s:end] += note[: end - s]
    tracks.append(normalize(bass) * preset["bass"])

    # --- percussion (soft kick + hat) ---
    if preset["perc"] > 0:
        perc = np.zeros(total)
        for b in range(int(seconds / (beat / 2))):
            s = int(b * (beat / 2) * sr)
            if s >= total:
                break
            if b % 4 == 0:
                kick_t = np.arange(int(0.18 * sr)) / sr
                kick = np.sin(2 * math.pi * (110 - 60 * np.linspace(0, 1, len(kick_t))) * kick_t)
                kick *= np.exp(-kick_t * 22)
                end = min(total, s + len(kick))
                perc[s:end] += kick[: end - s] * 0.9
            else:
                hat = noise(0.05, sr=sr, seed=seed + b) * np.exp(-np.linspace(0, 30, int(0.05 * sr)))
                hat = highpass(hat, 6000, sr)
                end = min(total, s + len(hat))
                perc[s:end] += hat[: end - s] * 0.18
        tracks.append(normalize(perc) * preset["perc"])

    music = mix_tracks(tracks)
    music = reverb(music, decay=preset["reverb"], mix=0.26, sr=sr, seed=seed)
    return fade(normalize(music), fade_in=0.6, fade_out=1.4, sr=sr)


def generate_sfx(kind: str = "whoosh", *, seconds: float = 2.0, seed: int = 0,
                 sr: int = SAMPLE_RATE) -> np.ndarray:
    """Deterministic synthesised sound effects."""
    rng = np.random.default_rng(seed)
    seconds = max(0.15, float(seconds))
    n = int(seconds * sr)
    t = np.arange(n) / sr
    kind = (kind or "whoosh").lower()

    if kind in ("whoosh", "transition", "swish"):
        x = noise(seconds, sr=sr, seed=seed, color="pink")
        cutoff = np.linspace(200, 6500, n)
        out = np.zeros(n)
        for chunk in range(0, n, sr // 20):
            seg = x[chunk: chunk + sr // 20]
            if len(seg):
                out[chunk: chunk + len(seg)] = lowpass(seg, float(cutoff[chunk]), sr)
        out *= np.sin(np.pi * np.linspace(0, 1, n)) ** 1.6
    elif kind in ("impact", "hit", "boom"):
        env = np.exp(-t * 7)
        body = np.sin(2 * math.pi * (90 - 50 * np.clip(t * 2, 0, 1)) * t) * env
        crack = noise(min(seconds, 0.4), sr=sr, seed=seed) * np.exp(-np.linspace(0, 40, min(n, int(0.4 * sr))))
        crack = np.pad(crack, (0, max(0, n - len(crack))))
        out = body * 0.85 + crack * 0.5
    elif kind in ("riser", "tension"):
        x = noise(seconds, sr=sr, seed=seed, color="white")
        out = x * np.linspace(0.05, 1.0, n) ** 2
        out += osc(np.linspace(180, 1400, n), seconds, wave_type="saw", sr=sr) * 0.12 * np.linspace(0, 1, n)
    elif kind in ("pop", "click", "ui"):
        env = np.exp(-np.linspace(0, 60, n))
        out = np.sin(2 * math.pi * (660 + 220 * rng.random()) * t) * env * 0.8
    elif kind in ("nature", "ambient", "wind"):
        base = noise(seconds, sr=sr, seed=seed, color="brown")
        out = lowpass(base, 900)
        out *= 0.7 + 0.3 * np.sin(2 * math.pi * 0.07 * t)
    elif kind in ("water", "rain"):
        out = lowpass(noise(seconds, sr=sr, seed=seed, color="white"), 4200) * 0.7
    elif kind in ("crowd", "city"):
        out = lowpass(noise(seconds, sr=sr, seed=seed, color="pink"), 1800) * 0.6
        out *= 0.8 + 0.2 * np.sin(2 * math.pi * 0.3 * t)
    elif kind in ("magic", "sparkle"):
        out = np.zeros(n)
        for i in range(6):
            f = 1200 + rng.random() * 2600
            start = int(rng.random() * max(1, n - sr // 4))
            tone = osc(f, 0.5, wave_type="sine", sr=sr) * np.exp(-np.linspace(0, 12, int(0.5 * sr)))
            end = min(n, start + len(tone))
            out[start:end] += tone[: end - start]
    elif kind in ("footstep", "step"):
        out = noise(min(seconds, 0.25), sr=sr, seed=seed) * np.exp(-np.linspace(0, 45, min(n, int(0.25 * sr))))
        out = lowpass(np.pad(out, (0, max(0, n - len(out)))), 1200) * 0.8
    else:
        out = lowpass(noise(seconds, sr=sr, seed=seed), 3000) * np.sin(np.pi * np.linspace(0, 1, n))
    return fade(normalize(out), fade_in=0.01, fade_out=0.25, sr=sr)


def speech_envelope(text: str, seconds: float, *, sr: int = SAMPLE_RATE, seed: int = 0) -> np.ndarray:
    """Syllable-rate amplitude envelope derived from the text itself."""
    words = [w for w in "".join(ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in text).split() if w]
    if not words:
        words = ["speech"]
    n = max(int(seconds * sr), 1)
    env = np.zeros(n)
    rng = np.random.default_rng(seed)
    cursor = 0
    for w in words:
        syllables = max(1, sum(1 for ch in w if ch in "aeiouy"))
        word_len = max(int(sr * 0.16), int(n * 0.9 / max(len(words), 1)))
        word_len = min(word_len, max(1, n - cursor))
        if word_len <= 1:
            break
        for s in range(syllables):
            start = cursor + int(s * word_len / syllables)
            end = min(n, cursor + int((s + 1) * word_len / syllables))
            if end <= start:
                continue
            seg = np.sin(np.pi * np.linspace(0, 1, end - start)) ** 0.75
            env[start:end] = np.maximum(env[start:end], seg * (0.75 + 0.25 * rng.random()))
        cursor += word_len
        # Inter-word pause.
        pause = max(1, int(sr * 0.05))
        env[cursor: min(n, cursor + pause)] *= 0.05
        cursor += pause
    return env


def generate_voice_track(text: str, seconds: float, *, voice: str = "narrator", seed: int = 0,
                         pitch: float = 1.0, speed: float = 1.0, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Prosody-shaped vocal-band track.

    IMPORTANT: this is NOT intelligible speech. It is a real audio signal whose
    timing follows the script, used as a stand-in when no TTS engine is
    installed so downstream steps (lip-sync, mixing, editing, export) can run
    end to end. Assets produced this way are flagged `intelligible: false`.
    """
    n = max(int(seconds * sr), 1)
    env = speech_envelope(text, seconds, sr=sr, seed=seed)
    base_hz = {"narrator": 118.0, "female": 196.0, "child": 262.0, "deep": 92.0}.get(voice, 130.0) * float(pitch)
    t = np.arange(n) / sr
    rng = np.random.default_rng(seed + 7)

    # Glottal pulse train with natural-ish pitch drift and vibrato.
    drift = 1 + 0.03 * np.sin(2 * math.pi * 0.6 * t) + 0.012 * np.sin(2 * math.pi * 4.3 * t)
    f0 = base_hz * drift
    phase = 2 * math.pi * np.cumsum(f0) / sr
    source = np.sin(phase) * 0.6
    source += 0.22 * np.sin(2 * phase) + 0.12 * np.sin(3 * phase)
    source += rng.normal(0, 0.035, n)

    # Two formant resonators give a vowel-like timbre that shifts over time.
    out = np.zeros(n)
    for f1, f2, gain in ((620, 1180, 0.9), (420, 2100, 0.7), (760, 1340, 0.55)):
        band = lowpass(source, f2 * 1.6, sr) - lowpass(source, f1 * 0.7, sr)
        out += band * gain
    out = lowpass(out, 3400, sr)
    out *= env
    out = normalize(out, target=0.82)
    if speed and abs(speed - 1.0) > 0.01:
        idx = np.clip((np.arange(n) * float(speed)).astype(int), 0, n - 1)
        out = out[idx]
    return fade(out, fade_in=0.02, fade_out=0.18, sr=sr)
