"""Wiederverwendung für das 3D-Layout: Muster pro Spur (Basis 1 Takt) -> Abschnitte.

1. Jede Spur wird in Takte geschnitten; gleiche Takte = gleiches 1-Takt-Muster.
2. Wiederholte Taktfolgen einer Spur werden gierig zu Abschnitten zusammengefasst
   (wie LZ-Kompression): ein Abschnitt wird EINMAL gebaut und an jedem Vorkommen vom
   Dirigenten gestartet. Jeder Start kostet eine Leitung (`tap_cost` Blöcke), deshalb lohnt
   sich ein Abschnitt nur, wenn er genug Notenblöcke spart.
3. Abschnitte verschiedener Spuren mit exakt denselben Startzeiten werden zu einem
   Muster verschmolzen (z.B. Bass + Oktav-Doppelung, Drums + Bass im Refrain).
4. Was übrig bleibt, wird pro Spur am Stück ausgerollt (ein Muster je zusammenhängendem Rest).

Ergebnis ist ein normaler planner.Plan (ohne Clocks) – passt also zu simulate() und layout3d.
"""
import math
from collections import defaultdict

from planner import Pattern, Plan, _build_conductor, _chunkify, chunk_start

TAP_COST = 30          # geschätzte Blöcke für eine Dirigent-Leitung zu einem Muster
MAX_SECTION_BARS = 64


def _track_key(track):
    return track.replace(" (+harp)", "").split(" (+")[0]


def _segment(seq, weight, tap_cost, max_len):
    """seq: Takt-Signaturen einer Spur. Liefert [(start, länge)] je Abschnitt-Gruppe:
    {abschnitt_id: [startindizes]} und die Länge."""
    n = len(seq)
    covered = [False] * n
    groups = []
    while True:
        best = None
        for L in range(1, min(max_len, n) + 1):
            occ = defaultdict(list)
            for i in range(n - L + 1):
                if covered[i] or covered[i + L - 1] or any(covered[i:i + L]):
                    continue
                w = sum(weight[i:i + L])
                if not w:
                    continue
                occ[tuple(seq[i:i + L])].append(i)
            for key, starts in occ.items():
                sel, last = [], -10 ** 9
                for s in starts:
                    if s >= last + L:
                        sel.append(s)
                        last = s
                if len(sel) < 2:
                    continue
                notes = sum(weight[sel[0]:sel[0] + L])
                save = (len(sel) - 1) * notes - (len(sel) - 1) * tap_cost
                if save > 0 and (best is None or save > best[0]):
                    best = (save, L, sel)
        if not best:
            break
        _, L, sel = best
        for s in sel:
            for i in range(s, s + L):
                covered[i] = True
        groups.append((L, sel))
    # Reste: zusammenhängende, nicht abgedeckte Takte mit Noten
    i = 0
    while i < n:
        if not covered[i] and weight[i]:
            j = i
            while j < n and not covered[j]:
                j += 1
            while not weight[j - 1]:
                j -= 1
            groups.append((j - i, [i]))
            i = j
        else:
            i += 1
    return groups


def build_section_plan(nbnotes, tpb, beats_per_bar, tap_cost=TAP_COST, max_len=MAX_SECTION_BARS):
    """nbnotes: arrangierte NBNotes. Liefert (plan, nbnotes_verschoben) wie planner.build_plan."""
    from arrange import NBNote
    length = beats_per_bar * tpb
    bar_ticks = max(1, round(length))
    first = min(n.tick for n in nbnotes)
    lead = (first // bar_ticks) * bar_ticks if float(tpb).is_integer() else 0
    nbnotes = [NBNote(n.tick - lead, n.layer, n.inst, n.clicks, n.velocity, n.track) for n in nbnotes]
    end = max(n.tick for n in nbnotes)
    nbars = math.floor(end / length) + 2

    by_track = defaultdict(list)
    for n in nbnotes:
        by_track[_track_key(n.track)].append(n)

    # (startzeit-tupel) -> [(track, notes relativ zum Abschnittsstart, layer)]
    sections = defaultdict(list)
    bar_patterns = 0
    for track in sorted(by_track):
        notes = by_track[track]
        chunks = _chunkify(notes, 0, length)
        seq = [chunks.get(k, ()) for k in range(nbars)]
        bar_patterns += len({s for s in seq if s})
        weight = [len(s) for s in seq]
        layer = max({n.layer for n in notes}, key=lambda l: sum(1 for n in notes if n.layer == l))
        for L, starts in _segment(seq, weight, tap_cost, max_len):
            s0 = starts[0]
            t0 = chunk_start(0, s0, length)
            rel = []
            for k in range(s0, s0 + L):
                tk = chunk_start(0, k, length) - t0
                rel += [(off + tk, inst, clicks) for off, inst, clicks in seq[k]]
            times = tuple(chunk_start(0, s, length) for s in starts)
            sections[times].append((track, rel, layer))

    patterns, plays = {}, []
    for times, parts in sorted(sections.items()):
        notes = sorted({x for _, rel, _ in parts for x in rel})
        # Startzeit = erste Note (spart Repeater am Anfang des Musters)
        first_off = min(o for o, _, _ in notes)
        notes = [(o - first_off, i, c) for o, i, c in notes]
        name = "+".join(sorted({t for t, _, _ in parts}))
        pid = f"{name[:24]}-{len(patterns) + 1}"
        span = max(o for o, _, _ in notes) + 1
        p = Pattern(pid, parts[0][2], span, notes)
        p.uses = len(times)
        patterns[pid] = p
        plays += [(t + first_off, pid) for t in times]
    for p in patterns.values():
        p.build_steps()
    info = {"Abschnitte": {"layer": "mix", "bars": None, "chunk_ticks": None, "phase": 0,
                           "patterns": len(patterns), "notes": len(nbnotes),
                           "bar_patterns": bar_patterns}}
    plan = Plan(tpb, bar_ticks, lead, patterns, sorted(plays), [], layer_info=info)
    plan.beats_per_bar = beats_per_bar
    _build_conductor(plan)
    return plan, nbnotes
