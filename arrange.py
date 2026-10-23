"""Quantisieren auf Redstone-Ticks, Instrumente zuordnen, Tonumfang anpassen."""
import math
from collections import defaultdict
from dataclasses import dataclass

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


def arrange(song, tpb, max_chord=None, transpose=0, instrument_override=None):
    max_chord = {**DEFAULT_MAX_CHORD, **(max_chord or {})}
    instrument_override = instrument_override or {}

    # Instrument + Oktavlage pro Spur bestimmen
    groups = defaultdict(list)
    for n in song.notes:
        if not n.drum:
            groups[(n.track, n.program)].append(n.pitch + transpose)
    track_cfg = {}
    for (track, program), pitches in groups.items():
        if track in instrument_override:
            cands = [instrument_override[track]]
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
        track_cfg[(track, program)] = (inst, shift)

    raw = []
    for n in song.notes:
        if float(tpb).is_integer():
            tick = round(n.beat * tpb)
        else:   # krummes Tempo: pro Takt runden, damit gleiche Takte gleich bleiben
            bpb = song.beats_per_bar
            beat = round(n.beat * 12) / 12          # Humanisierung raus (16tel + Triolen)
            bar = math.floor(beat / bpb + 1e-9)
            tick = round(bar * bpb * tpb) + round((beat - bar * bpb) * tpb)
        if n.drum:
            inst, clicks = drum_for_gm(n.pitch)
            raw.append(NBNote(tick, "beat", inst, clicks, n.velocity, n.track))
            continue
        inst, shift = track_cfg[(n.track, n.program)]
        if INSTRUMENTS[inst].percussion:
            raw.append(NBNote(tick, "beat", inst, 0, n.velocity, n.track))
            continue
        clicks = _fold(n.pitch + transpose + 12 * shift, inst)
        layer = "bass" if inst in ("bass", "didgeridoo") else "melodie"
        raw.append(NBNote(tick, layer, inst, clicks, n.velocity, n.track))

    # Doppelte entfernen + Akkordgröße begrenzen
    by_slot = defaultdict(dict)
    for n in raw:
        key = (n.inst, n.clicks)
        slot = by_slot[(n.layer, n.tick)]
        if key not in slot or slot[key].velocity < n.velocity:
            slot[key] = n
    out, dropped = [], 0
    for (layer, tick), slot in by_slot.items():
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
