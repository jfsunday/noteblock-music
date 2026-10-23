"""Grobe Hörprobe (WAV) des simulierten Bauplans – zum Prüfen, bevor man baut."""
import wave

import numpy as np

from instruments import INSTRUMENTS

SR = 22050


def _tone(freq, inst, dur=0.6):
    t = np.arange(int(SR * dur)) / SR
    if inst == "basedrum":
        f = 60 * (1 + 2 * np.exp(-t * 30)) * (freq / 370) ** 0.3
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 18)
    if inst == "snare":
        return (np.random.uniform(-1, 1, t.size) * 0.7 + np.sin(2 * np.pi * 180 * t) * 0.3) * np.exp(-t * 25)
    if inst == "hat":
        n = np.random.uniform(-1, 1, t.size)
        n = np.diff(n, prepend=0)
        return n * 0.6 * np.exp(-t * 60)
    w = 2 * np.pi * freq * t
    if inst in ("bass", "didgeridoo"):
        sig = np.sin(w) + 0.5 * np.sin(2 * w) + 0.25 * np.sin(3 * w)
        env = np.exp(-t * 5)
    elif inst == "bit":
        sig = np.sign(np.sin(w)) * 0.5
        env = np.exp(-t * 4)
    elif inst in ("flute",):
        sig = np.sin(w + 0.3 * np.sin(2 * np.pi * 5 * t))
        env = np.minimum(1, t * 30) * np.exp(-t * 3)
    elif inst in ("bell", "chime", "iron_xylophone", "xylophone", "cow_bell"):
        sig = np.sin(w) + 0.4 * np.sin(2.76 * w)
        env = np.exp(-t * (12 if inst == "xylophone" else 4))
    else:   # harp, guitar, banjo, pling
        sig = np.sin(w) + 0.3 * np.sin(2 * w) + 0.15 * np.sin(3 * w)
        env = np.exp(-t * 6)
    return sig * env


def render(played, path, tick_seconds=0.1):
    if not played:
        return
    end = max(p[0] for p in played) * tick_seconds + 1.0
    out = np.zeros(int(end * SR) + SR)
    cache = {}
    for tick, inst, clicks, *_ in played:
        midi = INSTRUMENTS[inst].low + clicks
        key = (inst, midi)
        if key not in cache:
            cache[key] = _tone(440 * 2 ** ((midi - 69) / 12), inst)
        s = cache[key]
        a = int(tick * tick_seconds * SR)
        out[a:a + s.size] += s * 0.25
    out /= max(1.0, np.abs(out).max())
    data = (out * 32000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
