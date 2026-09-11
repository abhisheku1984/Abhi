"""Real audio DSP.

Everything here produces genuine PCM samples with NumPy - no samples, no stubs.
Used by the Audio Studio (music/SFX), the Voice engine (post-processing) and the
talking-avatar pipeline (audio envelope -> mouth movement).
"""
from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

DEFAULT_SR = 44100


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------
def write_wav(path: str | Path, samples: np.ndarray, sr: int = DEFAULT_SR) -> str:
    path = str(path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(np.asarray(samples, dtype=np.float64), -1.0, 1.0)
    pcm = (data * 32767.0).astype("<i2")
    if pcm.ndim == 1:
        pcm = pcm.reshape(-1, 1)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(pcm.shape[1])
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return path


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data, sr


def duration_of(path: str | Path) -> float:
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate() or 1)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def normalize(x: np.ndarray, target: float = 0.89) -> np.ndarray:
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    return x * (target / peak) if peak > 1e-9 else x


def soft_limit(x: np.ndarray, drive: float = 1.0) -> np.ndarray:
    return np.tanh(x * drive) / np.tanh(drive)


def fade(x: np.ndarray, fade_in: float = 0.0, fade_out: float = 0.0, sr: int = DEFAULT_SR) -> np.ndarray:
    n = x.size
    out = x.copy()
    if fade_in > 0:
        k = min(n, int(fade_in * sr))
        out[:k] *= np.linspace(0.0, 1.0, k)
    if fade_out > 0:
        k = min(n, int(fade_out * sr))
        out[-k:] *= np.linspace(1.0, 0.0, k)
    return out


def mix(tracks: Sequence[np.ndarray], gains: Sequence[float] | None = None) -> np.ndarray:
    if not tracks:
        return np.zeros(0, dtype=np.float64)
    length = max(t.size for t in tracks)
    out = np.zeros(length, dtype=np.float64)
    gains = list(gains) if gains else [1.0] * len(tracks)
    for t, g in zip(tracks, gains):
        pad = np.zeros(length)
        pad[: t.size] = t
        out += pad * float(g)
    return out


def concat(parts: Sequence[np.ndarray]) -> np.ndarray:
    return np.concatenate([p for p in parts if p.size]) if parts else np.zeros(0)


def silence(duration: float, sr: int = DEFAULT_SR) -> np.ndarray:
    return np.zeros(int(duration * sr), dtype=np.float64)


def rms_envelope(x: np.ndarray, sr: int = DEFAULT_SR, fps: float = 24.0) -> np.ndarray:
    """Per-frame RMS in [0,1] - drives mouth animation for talking avatars."""
    if x.size == 0:
        return np.zeros(1)
    hop = max(1, int(sr / max(fps, 1)))
    frames = []
    for i in range(0, x.size, hop):
        chunk = x[i: i + hop]
        frames.append(float(np.sqrt(np.mean(chunk ** 2))) if chunk.size else 0.0)
    env = np.asarray(frames, dtype=np.float64)
    if env.size == 0:
        return np.zeros(1)
    peak = float(np.max(env)) or 1.0
    env = np.clip(env / peak, 0, 1)
    # Smooth so the mouth never jitters frame to frame.
    kernel = np.ones(3) / 3.0
    env = np.convolve(env, kernel, mode="same")
    return env


def resample_linear(x: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr or x.size == 0:
        return x
    n = int(round(x.size * dst_sr / src_sr))
    return np.interp(np.linspace(0, x.size - 1, n), np.arange(x.size), x).astype(np.float64)


def change_speed(x: np.ndarray, factor: float) -> np.ndarray:
    if abs(factor - 1.0) < 1e-6:
        return x
    n = max(1, int(x.size / max(factor, 0.05)))
    return np.interp(np.linspace(0, x.size - 1, n), np.arange(x.size), x).astype(np.float64)


def pitch_shift(x: np.ndarray, semitones: float) -> np.ndarray:
    """Naive resampling pitch shift (keeps duration, changes pitch)."""
    if abs(semitones) < 1e-6:
        return x
    factor = 2.0 ** (semitones / 12.0)
    stretched = change_speed(x, factor)
    n = x.size
    return np.interp(np.linspace(0, stretched.size - 1, n), np.arange(stretched.size), stretched).astype(np.float64)


def simple_reverb(x: np.ndarray, sr: int = DEFAULT_SR, room: float = 0.35, mix_amount: float = 0.28) -> np.ndarray:
    """Real feedback-delay reverb (cheap, deterministic, no IR file needed)."""
    if mix_amount <= 0:
        return x
    out = x.copy()
    for delay_ms, decay in ((37, 0.6), (53, 0.5), (79, 0.42), (97, 0.34)):
        d = int(sr * delay_ms / 1000.0 * (0.6 + room * 2.2))
        buf = np.zeros(x.size + d)
        buf[: x.size] = x
        y = x.copy()
        amp = decay
        for _ in range(4):
            y = y + amp * buf[d: d + x.size] if False else y + amp * np.concatenate([np.zeros(d), x])[: x.size]
            amp *= 0.5
        out = out + mix_amount * 0.25 * (y - x)
    return out


# ---------------------------------------------------------------------------
# Synthesis primitives
# ---------------------------------------------------------------------------
def _adsr(n: int, sr: int, attack: float, decay: float, sustain: float, release: float) -> np.ndarray:
    a = max(1, int(attack * sr))
    d = max(1, int(decay * sr))
    r = max(1, int(release * sr))
    s_len = max(0, n - a - d - r)
    env = np.concatenate([
        np.linspace(0, 1, a),
        np.linspace(1, sustain, d),
        np.full(s_len, sustain),
        np.linspace(sustain, 0, r),
    ])
    if env.size < n:
        env = np.pad(env, (0, n - env.size), constant_values=0.0)
    return env[:n]


def tone(freq: float, duration: float, sr: int = DEFAULT_SR, wave: str = "sine",
         attack: float = 0.01, decay: float = 0.05, sustain: float = 0.7,
         release: float = 0.15, harmonics: int = 6, detune: float = 0.0) -> np.ndarray:
    n = int(duration * sr)
    if n <= 0:
        return np.zeros(0)
    t = np.arange(n) / sr
    f = float(freq) * (1.0 + detune)
    if wave == "sine":
        y = np.sin(2 * np.pi * f * t)
    elif wave == "square":
        y = sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, harmonics * 2, 2))
        y = np.tanh(y * 1.2) * 0.6
    elif wave == "saw":
        y = sum((1.0 / k) * np.sin(2 * np.pi * f * k * t) for k in range(1, harmonics + 1))
        y = y * 0.35
    elif wave == "triangle":
        y = sum(((-1) ** ((k - 1) // 2)) / (k * k) * np.sin(2 * np.pi * f * k * t) for k in range(1, harmonics * 2, 2))
        y = y * 0.7
    elif wave == "noise":
        y = np.random.default_rng(int(f * 1000) % (2 ** 32)).standard_normal(n)
    else:
        y = np.sin(2 * np.pi * f * t)
    return y * _adsr(n, sr, attack, decay, sustain, release)


def noise(duration: float, sr: int = DEFAULT_SR, seed: int = 0) -> np.ndarray:
    n = int(duration * sr)
    return np.random.default_rng(seed & 0xFFFFFFFF).standard_normal(n)


def lowpass(x: np.ndarray, cutoff, sr: int = DEFAULT_SR, q: float = 0.707,
            numtaps: int = 257) -> np.ndarray:
    """Windowed-sinc FIR low-pass, applied via FFT convolution (fast, no scipy).

    `cutoff` may be a scalar (Hz) or a per-sample array - a per-sample array
    produces a swept filter, used by whooshes and risers.
    """
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return x
    cut = np.asarray(cutoff, dtype=np.float64)
    if cut.ndim > 0:
        return lowpass_sweep(x, cut, sr, numtaps=numtaps)

    fc = float(cut)
    if fc >= sr / 2:
        return x.copy()
    if fc <= 0:
        return np.zeros_like(x)

    nyq = sr / 2.0
    m = max(2, int(numtaps) // 2)
    n = np.arange(-m, m + 1, dtype=np.float64)
    h = np.sinc(2.0 * (fc / nyq) * n) * np.hamming(2 * m + 1)
    h /= h.sum()
    size = x.size + h.size - 1
    N = 1 << int(np.ceil(np.log2(max(size, 1))))
    y = np.fft.irfft(np.fft.rfft(x, N) * np.fft.rfft(h, N), N)
    return y[: x.size]


def lowpass_sweep(x: np.ndarray, cutoffs: np.ndarray, sr: int = DEFAULT_SR,
                  numtaps: int = 129, chunks: int = 96) -> np.ndarray:
    """Time-varying low-pass: processes the signal in chunks, each filtered with
    its own cutoff. Vectorised (no per-sample Python loop)."""
    n = x.size
    cut = np.interp(np.arange(n), np.arange(cutoffs.size), cutoffs) if cutoffs.size != n else cutoffs
    step = max(1, n // max(1, chunks))
    pad = numtaps
    out = np.zeros(n, dtype=np.float64)
    for start in range(0, n, step):
        end = min(n, start + step)
        s = max(0, start - pad)
        e = min(n, end + pad)
        seg = x[s:e]
        fc = float(np.clip(np.mean(cut[start:end]), 1.0, sr / 2 - 1.0))
        filtered = lowpass(seg, fc, sr, numtaps=numtaps)
        out[start:end] = filtered[start - s: start - s + (end - start)]
    return out


def highpass(x: np.ndarray, cutoff: float, sr: int = DEFAULT_SR) -> np.ndarray:
    return x - lowpass(x, cutoff, sr)


# ---------------------------------------------------------------------------
# Music generation
# ---------------------------------------------------------------------------
SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "pentatonic": [0, 2, 4, 7, 9],
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
}

GENRES = {
    "cinematic": {"tempo": 84, "scale": "minor", "progression": [0, 5, 3, 4], "pad": 0.55, "arp": 0.22, "drums": 0.18, "bass": 0.5, "reverb": 0.45},
    "epic": {"tempo": 96, "scale": "minor", "progression": [0, 3, 4, 3], "pad": 0.5, "arp": 0.3, "drums": 0.5, "bass": 0.6, "reverb": 0.4},
    "corporate": {"tempo": 110, "scale": "major", "progression": [0, 4, 5, 3], "pad": 0.32, "arp": 0.42, "drums": 0.28, "bass": 0.4, "reverb": 0.2},
    "children": {"tempo": 124, "scale": "pentatonic", "progression": [0, 4, 5, 4], "pad": 0.28, "arp": 0.55, "drums": 0.38, "bass": 0.32, "reverb": 0.18},
    "ambient": {"tempo": 62, "scale": "dorian", "progression": [0, 3, 0, 4], "pad": 0.7, "arp": 0.12, "drums": 0.0, "bass": 0.3, "reverb": 0.6},
    "documentary": {"tempo": 92, "scale": "major", "progression": [0, 5, 3, 4], "pad": 0.45, "arp": 0.3, "drums": 0.14, "bass": 0.42, "reverb": 0.3},
    "lofi": {"tempo": 78, "scale": "dorian", "progression": [1, 4, 0, 5], "pad": 0.45, "arp": 0.3, "drums": 0.42, "bass": 0.5, "reverb": 0.3},
    "sad": {"tempo": 68, "scale": "minor", "progression": [0, 5, 3, 0], "pad": 0.6, "arp": 0.18, "drums": 0.0, "bass": 0.35, "reverb": 0.5},
    "happy": {"tempo": 118, "scale": "major", "progression": [0, 3, 4, 5], "pad": 0.34, "arp": 0.48, "drums": 0.4, "bass": 0.4, "reverb": 0.2},
    "suspense": {"tempo": 74, "scale": "harmonic_minor", "progression": [0, 0, 4, 3], "pad": 0.5, "arp": 0.2, "drums": 0.16, "bass": 0.55, "reverb": 0.5},
    "technology": {"tempo": 116, "scale": "major", "progression": [0, 5, 4, 3], "pad": 0.3, "arp": 0.5, "drums": 0.32, "bass": 0.45, "reverb": 0.22},
}


def midi_to_freq(note: float) -> float:
    return 440.0 * (2.0 ** ((note - 69) / 12.0))


def _kick(sr: int) -> np.ndarray:
    n = int(0.28 * sr)
    t = np.arange(n) / sr
    f = 130 * np.exp(-t * 28) + 46
    y = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t * 12)
    return y * 0.9


def _snare(sr: int, seed: int = 0) -> np.ndarray:
    n = int(0.20 * sr)
    t = np.arange(n) / sr
    body = np.sin(2 * np.pi * 190 * t) * np.exp(-t * 26) * 0.5
    air = noise(n / sr, sr, seed) * np.exp(-t * 18) * 0.5
    return (body + air) * 0.7


def _hat(sr: int, seed: int = 0, open_hat: bool = False) -> np.ndarray:
    n = int((0.18 if open_hat else 0.05) * sr)
    t = np.arange(n) / sr
    y = noise(n / sr, sr, seed) * np.exp(-t * (18 if open_hat else 60))
    return highpass(y, 6500, sr) * 0.35


def generate_music(genre: str = "cinematic", duration: float = 30.0, sr: int = DEFAULT_SR,
                   key: str = "C", mood: str = "", tempo: float | None = None,
                   seed: int | None = None, instruments: Sequence[str] | None = None,
                   progress=None) -> np.ndarray:
    """Compose a real music bed: chords, arpeggio, bass and drums."""
    cfg = GENRES.get((genre or "cinematic").lower(), GENRES["cinematic"])
    bpm = float(tempo or cfg["tempo"])
    beat = 60.0 / bpm
    bars = max(1, int(round(duration / (beat * 4))))
    scale = SCALES.get(cfg["scale"], SCALES["minor"])
    root = 48 + {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}.get((key or "C").upper()[:1], 0)
    rng = np.random.default_rng((seed if seed is not None else hash(genre + key) & 0xFFFF) & 0xFFFFFFFF)
    total = int(duration * sr)
    tracks: list[np.ndarray] = []
    gains: list[float] = []
    instruments = list(instruments or ["pad", "arp", "bass", "drums"])

    def place(buf: np.ndarray, at: float) -> np.ndarray:
        out = np.zeros(total)
        i = int(at * sr)
        end = min(total, i + buf.size)
        if i < total:
            out[i:end] = buf[: end - i]
        return out

    def deg_to_midi(degree: int, octave: int = 0) -> int:
        idx = degree % len(scale)
        oct_add = degree // len(scale)
        return root + scale[idx] + 12 * (octave + oct_add)

    for bar in range(bars):
        if progress:
            progress(min(0.95, (bar + 1) / max(bars, 1)), f"composing bar {bar + 1}/{bars}")
        chord_deg = cfg["progression"][bar % len(cfg["progression"])]
        chord_notes = [deg_to_midi(chord_deg, 0), deg_to_midi(chord_deg + 2, 0), deg_to_midi(chord_deg + 4, 0)]
        bar_t = bar * 4 * beat

        if "pad" in instruments and cfg["pad"] > 0:
            for note in chord_notes:
                y = tone(midi_to_freq(note), beat * 4 * 0.98, sr, wave="triangle",
                         attack=0.35, decay=0.4, sustain=0.8, release=0.9)
                y = lowpass(y, 2200, sr)
                tracks.append(place(y, bar_t))
                gains.append(cfg["pad"] * 0.22)

        if "bass" in instruments and cfg["bass"] > 0:
            for b in range(4):
                note = chord_notes[0] - 24 if b % 2 == 0 else chord_notes[0] - 17
                y = tone(midi_to_freq(note), beat * 0.85, sr, wave="saw",
                         attack=0.01, decay=0.12, sustain=0.6, release=0.12, harmonics=4)
                y = lowpass(y, 420, sr)
                tracks.append(place(y, bar_t + b * beat))
                gains.append(cfg["bass"] * 0.34)

        if "arp" in instruments and cfg["arp"] > 0:
            steps = int(4 * beat / (beat / 2))
            for s in range(steps):
                note = chord_notes[s % len(chord_notes)] + (12 if (s // 4) % 2 else 0)
                if rng.random() < 0.12:
                    continue
                y = tone(midi_to_freq(note), beat * 0.42, sr, wave="sine",
                         attack=0.005, decay=0.08, sustain=0.35, release=0.2)
                tracks.append(place(y, bar_t + s * beat / 2))
                gains.append(cfg["arp"] * 0.3)

        if "drums" in instruments and cfg["drums"] > 0:
            for b in range(4):
                if b in (0, 2):
                    tracks.append(place(_kick(sr), bar_t + b * beat))
                    gains.append(cfg["drums"] * 0.85)
                if b in (1, 3):
                    tracks.append(place(_snare(sr, seed=int(rng.integers(0, 9999))), bar_t + b * beat))
                    gains.append(cfg["drums"] * 0.5)
                for sub in range(2):
                    tracks.append(place(_hat(sr, seed=int(rng.integers(0, 9999)), open_hat=(sub == 1 and b == 3)),
                                       bar_t + b * beat + sub * beat / 2))
                    gains.append(cfg["drums"] * 0.22)

    bed = mix(tracks, gains) if tracks else np.zeros(total)
    bed = simple_reverb(bed, sr, room=cfg["reverb"], mix_amount=cfg["reverb"] * 0.5)
    bed = fade(bed, fade_in=min(1.5, duration * 0.1), fade_out=min(2.5, duration * 0.2), sr=sr)
    bed = soft_limit(bed, 1.15)
    return normalize(bed, 0.85)


# ---------------------------------------------------------------------------
# Sound effects & ambience
# ---------------------------------------------------------------------------
def generate_sfx(kind: str = "whoosh", duration: float = 2.0, sr: int = DEFAULT_SR,
                 seed: int = 0, progress=None) -> np.ndarray:
    """Deterministic synthesised sound effects."""
    kind = (kind or "whoosh").lower()
    n = int(max(0.05, duration) * sr)
    rng = np.random.default_rng((seed & 0xFFFFFFFF) or 1)
    t = np.arange(n) / sr

    if progress:
        progress(0.3, f"synth {kind}")

    if kind in ("whoosh", "swoosh", "transition"):
        y = noise(duration, sr, seed)
        sweep = np.linspace(1.0, 0.15, n) ** 1.5
        y = lowpass(y * sweep, 900 + 2600 * (1 - t / duration), sr)
        y *= np.sin(np.pi * np.clip(t / duration, 0, 1)) ** 0.7

    elif kind in ("impact", "hit", "boom"):
        env = np.exp(-t * 6.5)
        sub = np.sin(2 * np.pi * (70 * np.exp(-t * 9) + 34) * t) * env
        crack = noise(duration, sr, seed + 1) * np.exp(-t * 42) * 0.6
        y = sub * 0.9 + crack
        y = lowpass(y, 3200, sr)

    elif kind in ("riser", "tension"):
        f = np.linspace(120, 1900, n)
        y = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.linspace(0.05, 1.0, n)
        y += noise(duration, sr, seed) * np.linspace(0.02, 0.45, n)
        y = highpass(y, 220, sr) * 0.8
        y *= np.clip(t / duration * 3, 0, 1)

    elif kind in ("sparkle", "magic", "chime"):
        y = np.zeros(n)
        for i in range(6):
            f = 880 * (2 ** (rng.integers(0, 5) / 12.0)) * (1 + i * 0.02)
            start = int(rng.uniform(0, max(1, n * 0.5)))
            dur = min(duration * 0.6, 1.2)
            tone_n = int(dur * sr)
            bell = np.sin(2 * np.pi * f * np.arange(tone_n) / sr) * np.exp(-np.arange(tone_n) / sr * 3.2)
            end = min(n, start + tone_n)
            y[start:end] += bell[: end - start] * 0.35

    elif kind in ("pop", "click", "ui_click"):
        y = noise(duration, sr, seed) * np.exp(-t * 220)
        y = highpass(y, 1800, sr) * 0.9

    elif kind in ("camera_shutter", "shutter"):
        y = np.zeros(n)
        for at in (0.0, 0.09):
            i = int(at * sr)
            burst_n = int(0.05 * sr)
            if i + burst_n <= n:
                y[i:i + burst_n] += noise(burst_n / sr, sr, seed + i) * np.exp(-np.arange(burst_n) / sr * 90) * 0.8
        y = highpass(y, 900, sr)

    elif kind in ("footsteps", "steps", "walk"):
        y = np.zeros(n)
        step_every = max(0.18, duration / max(1, int(duration / 0.5)))
        at = 0.0
        while at < duration:
            i = int(at * sr)
            bn = int(0.16 * sr)
            if i + bn <= n:
                burst = noise(bn / sr, sr, seed + i) * np.exp(-np.arange(bn) / sr * 34)
                y[i:i + bn] += lowpass(burst, 1100, sr) * 0.65
            at += step_every

    elif kind in ("wind", "ambient_wind"):
        base = noise(duration, sr, seed)
        lfo = 0.55 + 0.45 * np.sin(2 * np.pi * 0.08 * t) * np.sin(2 * np.pi * 0.031 * t + 1.1)
        y = lowpass(base, 520, sr) * lfo * 0.8

    elif kind in ("rain", "ambient_rain"):
        base = noise(duration, sr, seed)
        y = lowpass(base, 2400, sr) * 0.55
        for _ in range(int(duration * 22)):
            i = int(rng.uniform(0, max(1, n - 2000)))
            drop = np.exp(-np.arange(1200) / 90.0) * rng.uniform(0.15, 0.5)
            y[i:i + 1200] += drop * (rng.uniform(-1, 1))

    elif kind in ("waves", "ocean", "sea"):
        base = noise(duration, sr, seed)
        swell = (0.5 + 0.5 * np.sin(2 * np.pi * 0.09 * t)) ** 2
        y = lowpass(base, 900, sr) * swell * 0.85

    elif kind in ("birds", "forest", "nature"):
        y = lowpass(noise(duration, sr, seed), 800, sr) * 0.22
        at = 0.0
        while at < duration:
            at += float(rng.uniform(0.7, 2.4))
            i = int(at * sr)
            chirp_n = int(0.16 * sr)
            if i + chirp_n > n:
                break
            f0 = float(rng.uniform(1800, 3200))
            f = f0 * (1 + 0.35 * np.sin(2 * np.pi * 22 * np.arange(chirp_n) / sr))
            env = np.exp(-np.arange(chirp_n) / sr * 9)
            y[i:i + chirp_n] += np.sin(2 * np.pi * np.cumsum(f) / sr) * env * 0.32

    elif kind in ("fire", "campfire"):
        y = lowpass(noise(duration, sr, seed), 700, sr) * 0.5
        for _ in range(int(duration * 30)):
            i = int(rng.uniform(0, max(1, n - 900)))
            crackle = np.exp(-np.arange(700) / 40.0) * rng.uniform(0.1, 0.6)
            y[i:i + 700] += crackle

    elif kind in ("crowd", "ambient_crowd"):
        y = lowpass(noise(duration, sr, seed), 1500, sr) * 0.4
        y *= (0.6 + 0.4 * np.sin(2 * np.pi * 0.13 * t))

    elif kind in ("typing", "keyboard"):
        y = np.zeros(n)
        at = 0.0
        while at < duration:
            at += float(rng.uniform(0.08, 0.18))
            i = int(at * sr)
            bn = int(0.035 * sr)
            if i + bn <= n:
                y[i:i + bn] += highpass(noise(bn / sr, sr, seed + i), 2200, sr) * np.exp(-np.arange(bn) / sr * 160) * 0.7

    elif kind in ("notification", "ping"):
        y = np.zeros(n)
        for f, delay in ((1318.5, 0.0), (1760.0, 0.12)):
            i = int(delay * sr)
            tone_n = int(min(duration - delay, 0.7) * sr)
            if tone_n > 0:
                y[i:i + tone_n] += np.sin(2 * np.pi * f * np.arange(tone_n) / sr) * np.exp(-np.arange(tone_n) / sr * 4.5) * 0.4

    elif kind in ("heartbeat", "pulse"):
        y = np.zeros(n)
        at = 0.0
        while at < duration:
            for off, amp in ((0.0, 1.0), (0.22, 0.65)):
                i = int((at + off) * sr)
                bn = int(0.22 * sr)
                if i + bn <= n:
                    tt = np.arange(bn) / sr
                    y[i:i + bn] += np.sin(2 * np.pi * (58 * np.exp(-tt * 16) + 30) * tt) * np.exp(-tt * 9) * amp * 0.8
            at += 0.95

    elif kind in ("drone", "ambient_drone"):
        y = np.zeros(n)
        for f in (55.0, 82.5, 110.0, 164.8):
            y += np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28)) * 0.18
        y *= (0.7 + 0.3 * np.sin(2 * np.pi * 0.06 * t))

    else:  # generic whoosh fallback
        y = noise(duration, sr, seed) * np.sin(np.pi * np.clip(t / duration, 0, 1))
        y = lowpass(y, 1800, sr)

    y = fade(y, fade_in=min(0.05, duration * 0.1), fade_out=min(0.4, duration * 0.25), sr=sr)
    y = soft_limit(y, 1.2)
    return normalize(y, 0.82)


SFX_KINDS = (
    "whoosh", "impact", "riser", "sparkle", "pop", "camera_shutter", "footsteps",
    "wind", "rain", "waves", "birds", "fire", "crowd", "typing", "notification",
    "heartbeat", "drone",
)


def beat_grid(duration: float, bpm: float, sr: int = DEFAULT_SR) -> np.ndarray:
    """Timestamps of beats - used to align cuts to music in the editor."""
    beat = 60.0 / max(bpm, 1)
    return np.arange(0, duration, beat)
