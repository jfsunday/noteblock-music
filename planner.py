"""Struktur erkennen (Wiederholungen -> Pattern + Clocks) und Bauplan erzeugen.

Schaltungsmodell (alle Zeiten in Redstone-Ticks, 1 Tick = 0,1 s):

* Dirigent: lange Repeater-Kette ab dem Startknopf. An jedem "Abgriff" zweigt
  Redstone zur Seite ab.
* Jeder Abzweig endet mit einem Dioden-Repeater (1 Tick), damit Signale nicht
  zurücklaufen. Mehrere Abzweige dürfen in denselben Pattern-Eingang münden.
* Pattern-Leitung: Eingangs-Repeater (1 Tick), dann Schritte aus
  Repeatern -> Notenblock -> Repeater -> Notenblock ...
  => Direkter Abgriff bei Tick c startet das Pattern bei c + 2.
* Clock: Schleife aus Repeatern mit Gesamtverzögerung P (= Pattern-Länge).
  Ein Sperr-Repeater in der Schleife wird von einem RS-Latch gesteuert.
  Start-Abgriff s: setzt den Latch (entsperrt) und schickt über 6 Ticks
  Verzögerung einen Puls in die Schleife (Punkt S bei s + 6).
  Von S geht's über Diode in den Pattern-Eingang => Pattern-Start s + 8 + k*P.
  Stopp-Abgriff u: setzt den Latch zurück, Sperre greift bei u + 3.
"""
import math
from collections import defaultdict
from dataclasses import dataclass, field

from arrange import LAYERS, LAYER_TITLES
from instruments import INSTRUMENTS

PLAY_DELAY = 2          # Abgriff -> Pattern-Start (Diode + Eingangs-Repeater)
CLOCK_INJECT = 6        # Start-Abgriff -> Puls am Schleifenpunkt S
CLOCK_TO_PATTERN = 2    # S -> Pattern-Start (Diode + Eingangs-Repeater)
LOCK_DELAY = 3          # Stopp-Abgriff -> Sperre aktiv
MIN_CLOCK_PERIOD = 12
MIN_CLOCK_RUNS = 3


def split_delay(d):
    """Verzögerung in Repeater zerlegen (max. 4 Ticks pro Repeater)."""
    return [4] * (d // 4) + ([d % 4] if d % 4 else [])


@dataclass
class Pattern:
    pid: str
    layer: str
    length: int
    notes: list                   # [(offset, inst, clicks)]
    steps: list = field(default_factory=list)   # [(delay, [ (inst, clicks) ])]
    uses: int = 0

    def build_steps(self):
        by_off = defaultdict(list)
        for off, inst, clicks in self.notes:
            by_off[off].append((inst, clicks))
        prev = 0
        self.steps = []
        for off in sorted(by_off):
            self.steps.append((off - prev, off, sorted(by_off[off])))
            prev = off

    @property
    def repeaters(self):
        return 1 + sum(len(split_delay(d)) for d, _, _ in self.steps)


@dataclass
class Clock:
    cid: str
    pattern: str
    period: int
    first_start: int      # Pattern-Start des ersten Durchlaufs (Song-Tick)
    runs: int
    start_tap: int = 0
    stop_tap: int = 0


@dataclass
class Plan:
    tpb: float
    bar_ticks: int
    lead_ticks: int
    patterns: dict
    plays: list           # [(pattern_start_tick, pid)] direkte Starts
    clocks: list
    taps: list = field(default_factory=list)    # [(tick, [Aktionen])]
    layer_info: dict = field(default_factory=dict)
    offset: int = 0       # Song-Tick 0 = Dirigent-Tick `offset`


def chunk_start(start, k, length):
    """Start-Tick von Chunk k. Bei krummer Länge (Originaltempo exakt) gerundet."""
    return start + round(k * length)


def _chunkify(notes, start, length):
    chunks = defaultdict(list)
    for n in notes:
        k = math.floor((n.tick - start) / length)
        while chunk_start(start, k + 1, length) <= n.tick:
            k += 1
        while chunk_start(start, k, length) > n.tick:
            k -= 1
        chunks[k].append((n.tick - chunk_start(start, k, length), n.inst, n.clicks))
    return {k: tuple(sorted(v)) for k, v in chunks.items()}


def _line_cost(key):
    """Notenblöcke + Repeater einer Pattern-Leitung (inkl. leerer Strecken)."""
    offs = sorted({o for o, _, _ in key})
    reps = sum(len(split_delay(b - a)) for a, b in zip([0] + offs, offs))
    return len(key) + reps


def _layer_options(notes, bar_ticks, tpb, end, beats_per_bar=4):
    opts = []
    if float(tpb).is_integer():
        for beats in (2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 32):
            length = beats * tpb
            if length > end:
                break
            for start in range(0, min(length, bar_ticks), tpb):
                opts.append((length, start, beats / beats_per_bar))
    else:   # krummes Tempo: nur ganze Takte, passend zur taktweisen Rundung in arrange()
        for bars in (1, 2, 4, 8):
            length = bars * beats_per_bar * tpb
            if length <= end:
                opts.append((length, 0, bars))
    best = None
    for length, start, bars in opts:
        chunks = _chunkify(notes, start, length)
        types = {}
        events = []        # ("play", k, key) | ("clock", k, runs, key)
        ks = sorted(chunks)
        i = 0
        while i < len(ks):
            k, key = ks[i], chunks[ks[i]]
            j = i
            while j + 1 < len(ks) and ks[j + 1] == ks[j] + 1 and chunks[ks[j + 1]] == key:
                j += 1
            run = j - i + 1
            types[key] = True
            if run >= MIN_CLOCK_RUNS and length >= MIN_CLOCK_PERIOD and float(length).is_integer():
                events.append(("clock", k, run, key))
            else:
                events.extend(("play", ks[x], key) for x in range(i, j + 1))
            i = j + 1
        cost = sum(_line_cost(key) for key in types)
        cost += 8 * len(types)
        cost += sum(3 if e[0] == "play" else 14 for e in events)
        cand = (cost, length, start, bars, events, types)
        if best is None or cand[0] < best[0]:
            best = cand
    # Alternative: ganzer Layer als eine einzige Leitung
    whole_key = tuple(sorted((n.tick, n.inst, n.clicks) for n in notes))
    whole_cost = _line_cost(whole_key) + 8 + 3
    if best is None or whole_cost <= best[0]:
        return (whole_cost, end + 1, 0, None, [("play", 0, whole_key)], {whole_key: True})
    return best


def _short(inst):
    return INSTRUMENTS[inst].name.split("/")[0].split(" ")[0]


def build_plan(nbnotes, tpb, beats_per_bar, allow_repeats=True):
    bar_ticks = max(1, round(beats_per_bar * tpb))
    first = min(n.tick for n in nbnotes)
    lead = (first // bar_ticks) * bar_ticks if float(tpb).is_integer() else 0
    from arrange import NBNote
    nbnotes = [NBNote(n.tick - lead, n.layer, n.inst, n.clicks, n.velocity, n.track) for n in nbnotes]
    end = max(n.tick for n in nbnotes)

    # Layer ggf. nach Instrument aufteilen, wenn sich die Teile besser wiederholen
    groups = []
    for layer in LAYERS:
        notes = [n for n in nbnotes if n.layer == layer]
        if not notes:
            continue
        if not allow_repeats:
            groups.append((layer, LAYER_TITLES[layer], notes, None))
            continue
        title = LAYER_TITLES[layer]
        best = (_layer_options(notes, bar_ticks, tpb, end, beats_per_bar)[0],
                [(title, notes, _layer_options(notes, bar_ticks, tpb, end, beats_per_bar))])
        for keyf, namef in ((lambda n: n.inst, lambda k: _short(k)),
                            (lambda n: (n.inst, n.clicks), lambda k: f"{_short(k[0])}{k[1]}")):
            keys = sorted({keyf(n) for n in notes})
            if len(keys) < 2:
                continue
            parts = [(f"{title}-{namef(k)}", ns, _layer_options(ns, bar_ticks, tpb, end, beats_per_bar))
                     for k in keys for ns in [[n for n in notes if keyf(n) == k]]]
            cost = sum(o[0] for _, _, o in parts)
            if cost < best[0]:
                best = (cost, parts)
        groups.extend((layer, t, ns, o) for t, ns, o in best[1])

    patterns, plays, clocks, layer_info = {}, [], [], {}
    for layer, title, notes, opt in groups:
        if allow_repeats:
            cost, length, start, bars, events, types = opt
        else:
            key = tuple(sorted((n.tick, n.inst, n.clicks) for n in notes))
            length, start, bars, events = end + 1, 0, None, [("play", 0, key)]
        names = {}
        for ev in events:
            key = ev[-1]
            if key not in names:
                letter = chr(ord("A") + len(names)) if len(names) < 26 else str(len(names))
                pid = f"{title}-{letter}"
                names[key] = pid
                patterns[pid] = Pattern(pid, layer, math.ceil(length), list(key))
            pid = names[key]
            t0 = chunk_start(start, ev[1], length)
            if ev[0] == "play":
                plays.append((t0, pid))
                patterns[pid].uses += 1
            else:
                cid = f"Clock-{len(clocks) + 1}"
                clocks.append(Clock(cid, pid, int(length), t0, ev[2]))
                patterns[pid].uses += ev[2]
        layer_info[title] = {"layer": layer, "bars": bars, "chunk_ticks": length, "phase": start,
                             "patterns": len(names), "notes": len(notes)}
    for p in patterns.values():
        p.build_steps()

    plan = Plan(tpb, bar_ticks, lead, patterns, sorted(plays), clocks, layer_info=layer_info)
    plan.beats_per_bar = beats_per_bar
    _build_conductor(plan)
    return plan, nbnotes


def _build_conductor(plan):
    actions = []   # (tap_tick, text, kind, ref)
    for t0, pid in plan.plays:
        actions.append((t0 - PLAY_DELAY, "play", pid))
    for c in plan.clocks:
        c.start_tap = c.first_start - CLOCK_TO_PATTERN - CLOCK_INJECT
        last_s = c.start_tap + CLOCK_INJECT + (c.runs - 1) * c.period
        c.stop_tap = last_s - 2 - LOCK_DELAY
        actions.append((c.start_tap, "clock_start", c.cid))
        actions.append((c.stop_tap, "clock_stop", c.cid))
    offset = -min(a[0] for a in actions)
    plan.offset = offset
    taps = defaultdict(list)
    for tick, kind, ref in actions:
        taps[tick + offset].append((kind, ref))
    for c in plan.clocks:
        c.start_tap += offset
        c.stop_tap += offset
    plan.taps = sorted(taps.items())


def simulate(plan):
    """Spielt den Bauplan so ab, wie die Schaltung es tun würde.
    Ergebnis: Liste (song_tick, inst, clicks, pattern)."""
    starts = []
    clocks = {c.cid: c for c in plan.clocks}
    stop_at = {c.cid: c.stop_tap for c in plan.clocks}
    for tap, acts in plan.taps:
        for kind, ref in acts:
            if kind == "play":
                pid = ref
                starts.append((tap + PLAY_DELAY, pid))
            elif kind == "clock_start":
                c = clocks[ref]
                t_s = tap + CLOCK_INJECT
                lock = stop_at[ref] + LOCK_DELAY
                while True:
                    starts.append((t_s + CLOCK_TO_PATTERN, c.pattern))
                    if lock <= t_s:          # Sperre aktiv -> Puls läuft nicht weiter
                        break
                    t_s += c.period
    played = []
    for t0, pid in starts:
        for off, inst, clicks in plan.patterns[pid].notes:
            played.append((t0 + off - plan.offset, inst, clicks, pid))
    return sorted(played)
