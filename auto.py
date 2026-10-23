"""Auto-Arrangeur: wählt Spuren, Rollen, Instrumente, Tremolo und Drum-Dichte automatisch.

Idee: Notenblöcke sind alle gleich laut und klingen nur kurz nach. Damit ein Song
erkennbar bleibt, braucht es wenige, klar getrennte Stimmen:
  Lead (Gesang/Melodie) · Hooks, die in den Lead-Pausen spielen (z.B. Intro-Riff)
  · eine Akkordspur · Bass · Tom-Fills · ausgedünnte Drums.
"""
from collections import defaultdict

LEAD_HINTS = ("melod", "vocal", "voice", "lead", "sing", "gesang", "stimme", "solo")
PERC_HINTS = ("drum", "tom", "perc", "timp")
# Nachklingzeit (Ticks) -> wie oft lange Töne neu angeschlagen werden
RETRIGGER = {"bit": 1, "flute": 2, "harp": 3, "pling": 3, "bell": 3, "guitar": 3, "bass": 2}

LEAD_INST, LEAD_DOUBLE = "bit", "pling"
CHORD_INST = "harp"
FILL_INST, FILL_DOUBLE = "guitar", "basedrum"


def _stats(notes):
    groups = defaultdict(set)
    for n in notes:
        groups[round(n.beat * 12)].add(n.pitch % 12)
    pitches = sorted(n.pitch for n in notes)
    return {
        "n": len(notes),
        "onsets": len(groups),
        "poly": sum(len(g) for g in groups.values()) / max(1, len(groups)),
        "median": pitches[len(pitches) // 2],
        "vel": sum(n.velocity for n in notes) / len(notes),
    }


def _match(a, b):
    """Anteil der Noten von b, die (fast) zeitgleich in a mit gleicher Tonklasse vorkommen."""
    keys = {(round(n.beat * 8), n.pitch % 12) for n in a}
    hit = sum((round(n.beat * 8), n.pitch % 12) in keys for n in b)
    return hit / max(1, len(b))


def _active_beats(notes):
    beats = set()
    for n in notes:
        for b in range(int(n.beat), int(n.beat + max(n.duration, 0.5)) + 1):
            beats.add(b)
    return beats


def auto_config(song):
    """Liefert {'roles', 'skip', 'override', 'doubles', 'sustain', 'simple_drums'}."""
    tracks = defaultdict(list)
    for n in song.notes:
        if not n.drum:
            tracks[n.track].append(n)
    st = {t: _stats(ns) for t, ns in tracks.items()}
    roles, skip = {}, set()

    # 1) Doppelungen raus (z.B. Piccolo = Gesang, Brass = Trumpet)
    names = sorted(tracks, key=lambda t: (-any(h in t.lower() for h in LEAD_HINTS), -st[t]["n"], -st[t]["vel"]))
    for i, a in enumerate(names):
        if a in skip:
            continue
        for b in names[i + 1:]:
            if b not in skip and _match(tracks[a], tracks[b]) >= 0.7 and _match(tracks[b], tracks[a]) >= 0.5:
                skip.add(b)
                roles[b] = f"doppelt ({a})"

    live = [t for t in tracks if t not in skip]
    perc = [t for t in live if any(h in t.lower() for h in PERC_HINTS)
            or 112 <= tracks[t][0].program <= 119]
    for t in perc:
        roles[t] = "fill"
    rest = [t for t in live if t not in perc]
    mono = [t for t in rest if st[t]["poly"] <= 1.3]
    poly = [t for t in rest if st[t]["poly"] > 1.3]

    # 2) Bass: tiefste einstimmige Spur mit den meisten Noten
    low = [t for t in mono if st[t]["median"] < 50]
    if low:
        bass = max(low, key=lambda t: st[t]["n"])
        roles[bass] = "bass"
        mono.remove(bass)

    # 3) Lead: Namenshinweis, sonst meiste Noten * Lautstärke im mittleren/hohen Bereich
    cand = [t for t in mono if st[t]["median"] >= 50]
    lead = None
    if cand:
        hinted = [t for t in cand if any(h in t.lower() for h in LEAD_HINTS)]
        lead = max(hinted or cand, key=lambda t: st[t]["n"] * st[t]["vel"])
        roles[lead] = "lead"
        cand.remove(lead)

    # 4) Hooks: einstimmige Spuren, die spielen, wo der Lead Pause hat (Intro-Riff, Zwischenspiele)
    covered = _active_beats(tracks[lead]) if lead else set()
    for _ in range(2):
        best = max(cand, key=lambda t: len(_active_beats(tracks[t]) - covered), default=None)
        if not best or len(_active_beats(tracks[best]) - covered) < 8:
            break
        roles[best] = "hook"
        covered |= _active_beats(tracks[best])
        cand.remove(best)

    # 5) Akkorde: mehrstimmige Spur mit den meisten Anschlägen
    if poly:
        chords = max(poly, key=lambda t: st[t]["onsets"])
        roles[chords] = "akkorde"

    for t in tracks:
        if t not in roles:
            roles[t] = "aus"
        if roles[t] in ("aus",) or roles[t].startswith("doppelt"):
            skip.add(t)

    override, doubles, sustain = {}, {}, {}
    for t, r in roles.items():
        if r in ("lead", "hook"):
            override[t] = LEAD_INST
            doubles[t] = LEAD_DOUBLE
            sustain[t] = RETRIGGER[LEAD_INST]
        elif r == "akkorde":
            override[t] = CHORD_INST
        elif r == "bass":
            override[t] = "bass"
        elif r == "fill":
            override[t] = FILL_INST
            doubles[t] = FILL_DOUBLE

    drums = [n for n in song.notes if n.drum]
    span = (max(n.beat for n in drums) - min(n.beat for n in drums) + 1) if drums else 1
    simple = len(drums) / span > 2.5          # mehr als 2,5 Schläge pro Viertel -> ausdünnen
    if drums:
        roles["(Schlagzeug)"] = "drums, ausgedünnt" if simple else "drums"
    return {"roles": roles, "skip": sorted(skip), "override": override, "doubles": doubles,
            "sustain": sustain, "simple_drums": simple}
