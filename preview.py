"""Hörprobe (WAV) des simulierten Bauplans – mit den echten Minecraft-Notenblock-Sounds
aus einer lokalen Minecraft-Installation, sonst mit Synth-Ersatzklängen."""
import json
import wave
from pathlib import Path

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


SOUND_FILES = {"harp": "harp2", "bass": "bass", "basedrum": "bd", "snare": "snare", "hat": "hat",
               "bell": "bell", "flute": "flute", "chime": "icechime", "guitar": "guitar",
               "xylophone": "xylobone", "iron_xylophone": "iron_xylophone", "cow_bell": "cow_bell",
               "didgeridoo": "didgeridoo", "bit": "bit", "banjo": "banjo", "pling": "pling"}
ASSET_DIRS = ["~/.minecraft/assets", "~/.local/share/PrismLauncher/assets", "~/.local/share/multimc/assets",
              "~/Games/Curseforge/Install/assets", "~/.local/share/noriskclientv3/meta/assets",
              "~/.var/app/org.prismlauncher.PrismLauncher/data/PrismLauncher/assets"]


def find_mc_sounds(assets=None):
    """Sucht die Notenblock-.ogg-Dateien im Hash-Speicher (assets/objects) über den neuesten Index."""
    dirs = [assets] if assets else ASSET_DIRS
    for d in dirs:
        root = Path(d).expanduser()
        for idx in sorted(root.glob("indexes/*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            objs = json.loads(idx.read_text()).get("objects", {})
            found = {}
            for inst, name in SOUND_FILES.items():
                o = objs.get(f"minecraft/sounds/note/{name}.ogg")
                f = o and root / "objects" / o["hash"][:2] / o["hash"]
                if f and f.exists():
                    found[inst] = f
            if len(found) == len(SOUND_FILES):
                return found
    return None


def _load_ogg(path):
    import soundfile as sf
    y, sr = sf.read(str(path), dtype="float32", always_2d=True)
    y = y.mean(axis=1)
    if sr != SR:
        y = np.interp(np.arange(0, len(y), sr / SR), np.arange(len(y)), y)
    return y


def _mc_tone(base, clicks):
    """Wie Minecraft: Sample mit Faktor 2^((klicks-12)/12) schneller/langsamer abspielen."""
    f = 2 ** ((clicks - 12) / 12)
    return np.interp(np.arange(0, len(base) - 1, f), np.arange(len(base)), base)


def render(played, path, tick_seconds=0.1, assets=None):
    """Gibt True zurück, wenn echte Minecraft-Sounds verwendet wurden."""
    if not played:
        return False
    try:
        files = find_mc_sounds(assets)
        samples = {k: _load_ogg(v) for k, v in files.items()} if files else None
    except Exception:
        samples = None
    end = max(p[0] for p in played) * tick_seconds + 1.0
    out = np.zeros(int(end * SR) + SR)
    cache = {}
    for tick, inst, clicks, *_ in played:
        midi = INSTRUMENTS[inst].low + clicks
        key = (inst, midi)
        if key not in cache:
            cache[key] = (_mc_tone(samples[inst], clicks) * 0.8 if samples
                          else _tone(440 * 2 ** ((midi - 69) / 12), inst))
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
    return samples is not None
