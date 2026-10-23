"""Quantisieren auf Redstone-Ticks, Instrumente zuordnen, Tonumfang anpassen."""
import math
import re
from collections import defaultdict
from dataclasses import dataclass, replace

from instruments import INSTRUMENTS, drum_for_gm, instrument_for_program

LAYERS = ("beat", "bass", "melodie")
LAYER_TITLES = {"beat": "Beat", "bass": "Bass", "melodie": "Melodie"}
DEFAULT_MAX_CHORD = {"beat": 3, "bass": 2, "melodie": 4}


@dataclass(frozen=True)
class NBNote:
    tick: int
    layer: str
    inst: str
    clicks: int
    velocity: int = 100
    track: str = ""

    @property
    def pitch(self) -> int:
        return INSTRUMENTS[self.inst].low + self.clicks


def tempo_candidates(song, tpb_range=range(2, 17)):
    """Bewertet mögliche Ticks-pro-Schlag. Ein Redstone-Tick = 0,1 s,
    also Tempo = 600 / tpb BPM. Fehler = mittlere Abweichung vom Raster (in Schlägen)."""
    beats = [n.beat for n in song.notes]
    res = []
    for tpb in tpb_range:
        err = sum(abs(b * tpb - round(b * tpb)) for b in beats) / max(1, len(beats)) / tpb
        bpm = 600 / tpb
        tempo_dev = abs(math.log(bpm / song.bpm))
        res.append({"tpb": tpb, "bpm": bpm, "grid_err": err, "tempo_dev": tempo_dev,
                    "score": err * 6 + tempo_dev})
    # Originaltempo exakt: krumme Ticks pro Schlag, jede Note einzeln gerundet
    exact = 600 / song.bpm
    if all(abs(exact - r["tpb"]) > 0.02 for r in res):
        err = sum(abs(b * exact - round(b * exact)) for b in beats) / max(1, len(beats)) / exact
        res.append({"tpb": round(exact, 4), "bpm": song.bpm, "grid_err": err, "tempo_dev": 0.0,
                    "score": err * 6 + 0.05, "exact": True})
    return sorted(res, key=lambda r: r["score"])


def _fit(pitches, inst_key):
    inst = INSTRUMENTS[inst_key]
    best = None
    for k in range(-4, 5):
        inside = sum(inst.low <= p + 12 * k <= inst.high for p in pitches)
        score = inside / len(pitches) - 0.15 * abs(k)
        if best is None or score > best[0]:
            best = (score, k)
    return best


def _fold(pitch, inst_key):
    inst = INSTRUMENTS[inst_key]
    while pitch < inst.low:
        pitch += 12
    while pitch > inst.high:
        pitch -= 12
    return pitch - inst.low


SIMPLE_DRUMS = {35, 36, 38, 40, 39}   # Kick, Snare, Clap


def _parse_spec(spec):
    """'harp' oder 'harp+1' / 'bell-1' -> (instrument, erzwungene Oktave oder None)"""
    m = re.fullmatch(r"([a-z_]+)\s*([+-]\d+)?", spec.strip())
    if not m or m.group(1) not in INSTRUMENTS:
        raise ValueError(f"Unbekanntes Instrument: {spec!r} (erlaubt: {', '.join(INSTRUMENTS)}, optional +N/-N Oktaven)")
    return m.group(1), int(m.group(2)) if m.group(2) else None


def arrange(song, tpb, max_chord=None, transpose=0, instrument_override=None, doubles=None,
            simple_drums=False, sustain=None):
    """doubles: {Spur: 'bell+2'} -> Spur zusätzlich mit 2. Instrument (lauter/präsenter).
    simple_drums: nur Kick, Snare, Clap (Melodie bleibt hörbar).
    sustain: {Spur: N} -> lange Töne alle N Ticks neu anschlagen (Notenblöcke klingen nicht nach)."""
    sustain = {k: int(v) for k, v in (sustain or {}).items()}
    max_chord = {**DEFAULT_MAX_CHORD, **(max_chord or {})}
    instrument_override = dict(instrument_override or {})
    notes_in = [n for n in song.notes if not (simple_drums and n.drum and n.pitch not in SIMPLE_DRUMS)]
    for track, spec in (doubles or {}).items():
        name = f"{track} (+{spec})"
        instrument_override[name] = spec
        notes_in += [replace(n, track=name) for n in song.notes if n.track == track]

    # Instrument + Oktavlage pro Spur bestimmen
    groups = defaultdict(list)
    for n in notes_in:
        if not n.drum:
            groups[(n.track, n.program)].append(n.pitch + transpose)
    track_cfg = {}
    for (track, program), pitches in groups.items():
        forced = None
        if track in instrument_override:
            inst, forced = _parse_spec(instrument_override[track])
            cands = [inst]
        else:
            base = instrument_for_program(program)
            cands = [base]
            med = sorted(pitches)[len(pitches) // 2]
            if med < 54 and base not in ("bass", "didgeridoo"):
                cands.append("bass")
            if med > 80 and base not in ("bell", "chime", "xylophone"):
                cands.append("bell")
        scored = [(_fit(pitches, c), c) for c in cands]
        (score, shift), inst = max(scored, key=lambda s: s[0][0])
        if forced is not None:
            shift = forced
        track_cfg[(track, program)] = (inst, shift)

    raw = []
    for n in notes_in:
        beat = round(n.beat * 12) / 12              # Humanisierung raus (16tel + Triolen)
        if float(tpb).is_integer():
            tick = math.floor(beat * tpb + 0.5)
        else:   # krummes Tempo: pro Takt runden, damit gleiche Takte gleich bleiben
            bpb = song.beats_per_bar
            bar = math.floor(beat / bpb + 1e-9)
            tick = math.floor(bar * bpb * tpb + 0.5) + math.floor((beat - bar * bpb) * tpb + 0.5)
        if n.drum:
            inst, clicks = drum_for_gm(n.pitch)
            raw.append(NBNote(tick, "beat", inst, clicks, n.velocity, n.track))
            continue
        inst, shift = track_cfg[(n.track, n.program)]
        if INSTRUMENTS[inst].percussion:   # z.B. Tom-Fills: Tonhöhe übernehmen
            clicks = _fold(n.pitch + transpose + 12 * shift, inst)
            raw.append(NBNote(tick, "beat", inst, clicks, n.velocity, n.track))
            continue
        clicks = _fold(n.pitch + transpose + 12 * shift, inst)
        layer = "bass" if inst in ("bass", "didgeridoo") else "melodie"
        raw.append(NBNote(tick, layer, inst, clicks, n.velocity, n.track))
        every = sustain.get(n.track)
        if every and every > 0:
            end = math.floor((beat + n.duration) * tpb + 0.5)
            for t in range(tick + every, end, every):
                raw.append(NBNote(t, layer, inst, clicks, n.velocity - 1, n.track))

    # Doppelte entfernen + Akkordgröße begrenzen
    by_slot = defaultdict(dict)
    for n in raw:
        key = (n.inst, n.clicks)
        # Melodie: Limit pro Instrument (jedes Instrument bekommt eh eine eigene Leitung)
        slot = by_slot[(n.layer, n.tick, n.inst if n.layer == "melodie" else "")]
        if key not in slot or slot[key].velocity < n.velocity:
            slot[key] = n
    out, dropped = [], 0
    for (layer, tick, _), slot in by_slot.items():
        notes = list(slot.values())
        if layer == "melodie":
            top = max(notes, key=lambda x: x.pitch)
            rest = sorted((x for x in notes if x is not top), key=lambda x: -x.velocity)
            notes = [top] + rest
        elif layer == "bass":
            notes.sort(key=lambda x: x.pitch)
        else:
            notes.sort(key=lambda x: -x.velocity)
        keep = notes[:max_chord[layer]]
        dropped += len(notes) - len(keep)
        out.extend(keep)
    out.sort(key=lambda n: (n.tick, LAYERS.index(n.layer), n.inst, n.clicks))
    return out, track_cfg, dropped
