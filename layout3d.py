"""3D-Layout MIT Wiederverwendung: jedes Muster (Pattern) wird nur einmal gebaut.

Aufbau
------
* Schicht 0: Dirigent – Repeater-Leitung als Schlange (Bahnen ±X, Abstand 8 in Z).
  Für jeden Einsatz eines Musters ein Abgriff (Block T in der Leitung). Vom Abgriff geht
  ein Repeater nach Norden und eine Redstone-Treppe hoch in Schicht 1.
* Schichten 1..L-1: Muster-Blöcke + Verdrahtung. Jeder Muster-Block:
      Eingangskette  M_m → R → … → M_0 → R → Verteiler (Redstone-Spalte) → Teilleitungen
  Leitungen münden mit einem Repeater seitlich in einen M-Block (ODER-Verknüpfung).
* Verdrahtung per A*: Repeater geradeaus, Ecke = Block, Schicht hoch = Redstone-Treppe.
  Zwischen verschiedenen Leitungen/Blöcken immer ≥1 freies Feld (kein Übersprechen).
* Timing: Leitungs- und Dirigenten-Repeater haben Verzögerung 1–4 → per Intervall-Rechnung
  so eingestellt, dass jedes Muster exakt startet. Geprüft mit redstone_sim.py.

Koordinaten: Schicht k: Boden y=3k, Technik y=3k+1, Luft y=3k+2.
"""
import heapq
import math
from collections import defaultdict

from layout import FLOOR, INST_BLOCK, S_BLOCK, _note_block, _repeater, _wire
from planner import split_delay

LH = 3
DV = {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0)}
FACE = {"E": "west", "W": "east", "N": "south", "S": "north"}     # Repeater-facing = Eingangsseite
PERP = {"N": "EW", "S": "EW", "E": "NS", "W": "NS"}
ROW_C = 16         # Bahnabstand Dirigent (Lücken = Platz für Muster auf Schicht 1)
ROW_P = 4          # Bahnabstand in Mustern
GAP = 6            # Mindestabstand zwischen Muster-Blöcken (Platz für Leitungen)
MAX_USES = 4       # mehr Einsätze -> Muster wird zusätzlich kopiert (kurze Leitungen)


class LogDict(dict):
    """dict, das (optional) alte Werte protokolliert – für billiges Rückgängigmachen."""
    log = None

    def __setitem__(self, k, v):
        if self.log is not None and k not in self.log:
            self.log[k] = dict.get(self, k)
        dict.__setitem__(self, k, v)

    def pop(self, k, *a):
        if self.log is not None and k not in self.log:
            self.log[k] = dict.get(self, k)
        return dict.pop(self, k, *a)


class World:
    def __init__(self):
        self.blocks = LogDict()
        self.occ = LogDict()           # (layer, x, z) -> net
        self.wire_reps = defaultdict(list)   # net -> [(x,y,z,dir)] einstellbare Repeater

    def y(self, layer, part=1):
        return LH * layer + part

    def _floor(self, L, x, z):
        self.blocks[(x, self.y(L, 0), z)] = FLOOR

    def R(self, L, x, z, d, net, delay=1, adjustable=None):
        self.blocks[(x, self.y(L), z)] = _repeater(delay, FACE[d])
        self._floor(L, x, z)
        self.occ[(L, x, z)] = net
        if adjustable is not None:
            self.wire_reps[adjustable].append((x, self.y(L), z, d))

    def B(self, L, x, z, net):
        self.blocks[(x, self.y(L), z)] = S_BLOCK
        self.occ[(L, x, z)] = net

    def D(self, L, x, z, net):
        self.blocks[(x, self.y(L), z)] = _wire()
        self._floor(L, x, z)
        self.occ[(L, x, z)] = net

    def NB(self, L, x, z, inst, clicks, net):
        self.blocks[(x, self.y(L), z)] = _note_block(inst, clicks)
        self.blocks[(x, self.y(L, 0), z)] = INST_BLOCK[inst]
        self.occ[(L, x, z)] = net

    def reserve(self, L, x, z, net):
        self.occ[(L, x, z)] = net

    def lift(self, L, x, z, d, net):
        """Treppe von Schicht L nach L+1. (x,z) = Feld des Treppen-Blocks B. Endet mit Redstone
        bei (x+2d, z+2d) in Schicht L+1; dahinter muss ein Repeater folgen."""
        dx, dz = DV[d]
        x1, z1, x2, z2 = x + dx, z + dz, x + 2 * dx, z + 2 * dz
        self.B(L, x, z, net)
        self.blocks[(x, self.y(L, 2), z)] = _wire()                      # D1 auf B
        self.blocks[(x1, self.y(L, 2), z1)] = "minecraft:stone"         # Stütze
        self.blocks[(x1, self.y(L + 1, 0), z1)] = _wire()                # D2 (Bodenebene L+1)
        self.reserve(L, x1, z1, net)
        self.reserve(L + 1, x, z, net)
        self.reserve(L + 1, x1, z1, net)
        self.D(L + 1, x2, z2, net)                                       # D3


def drop(self, U, x, z, d, net):
    """Treppe von Schicht U nach U-1. (x,z) = erstes Feld (Redstone auf Schicht U).
    Endet mit einem Block in Schicht U-1 bei (x+2d); dahinter (x+3d) folgt ein Repeater."""
    dx, dz = DV[d]
    k = U - 1
    x1, z1, x2, z2 = x + dx, z + dz, x + 2 * dx, z + 2 * dz
    self.D(U, x, z, net)                                                  # D_a
    self.blocks[(x1, self.y(U, 0), z1)] = _wire()                          # D_b (Bodenebene U)
    self.blocks[(x1, self.y(k, 2), z1)] = "minecraft:stone"               # Stütze
    self.blocks[(x2, self.y(k, 1), z2)] = "minecraft:stone"               # S_c (Technikebene k)
    self.blocks[(x2, self.y(k, 2), z2)] = _wire()                          # D_c
    for cell in ((U, x1, z1), (U, x2, z2), (k, x1, z1), (k, x2, z2)):
        self.reserve(*cell, net)


World.drop = drop


def drop_cells(U, x, z, d):
    dx, dz = DV[d]
    return [(U, x, z), (U, x + dx, z + dz), (U, x + 2 * dx, z + 2 * dz),
            (U - 1, x + dx, z + dz), (U - 1, x + 2 * dx, z + 2 * dz)]


def lift_cells(L, x, z, d):
    dx, dz = DV[d]
    return [(L, x, z), (L, x + dx, z + dz), (L + 1, x, z), (L + 1, x + dx, z + dz),
            (L + 1, x + 2 * dx, z + 2 * dz)]


# ----------------------------------------------------------------------------------------
# Muster-Block (relativ aufgebaut, später verschoben)
# ----------------------------------------------------------------------------------------
def _sub_tokens(steps, start):
    toks, prev = [], start
    for tick in sorted(steps):
        for d in split_delay(tick - prev):
            toks.append(("R", d))
        toks.append(("S", tick, steps[tick]))
        prev = tick
    return toks


def build_pattern(notes, uses, width):
    """notes: [(offset, inst, clicks)]. Liefert Zellen relativ zu (0,0):
    cells: [(x, z, kind, data)], inputs: [(q, x, z, side_dir)], lead, Größe."""
    by_off = defaultdict(list)
    for off, inst, clicks in notes:
        by_off[off].append((inst, clicks))
    nsub = max(1, math.ceil(max(len(v) for v in by_off.values()) / 2))
    subs = [{o: sorted(v)[2 * i:2 * i + 2] for o, v in by_off.items() if len(v) > 2 * i} for i in range(nsub)]

    m = max(0, math.ceil((uses - 3) / 2))       # Eingangskette M_m … M_0
    cells, inputs = [], []
    x0 = 2 * m                                  # M_0 bei x0, M_q bei x0-2q
    for q in range(m + 1):
        xm = x0 - 2 * q
        cells.append((xm, 0, "B", None))
        inputs.append((q, xm, -1, "S"))          # von Norden kommend (Repeater zeigt nach Süden)
        inputs.append((q, xm, 1, "N"))
        if q < m:
            cells.append((xm - 1, 0, "R", "E"))
    inputs.append((m, x0 - 2 * m - 1, 0, "E"))   # Westseite des letzten M
    cells.append((x0 + 1, 0, "R", "E"))         # Eingangs-Repeater
    xf = x0 + 2                                 # Verteiler-Spalte

    # Teilleitungen falten, Bahnen untereinander
    feed = []
    z = 0
    sub_rows = []
    for st in subs:
        sub_rows.append(z)
        z += ROW_P * _rows(st, width)
    zlast = sub_rows[-1]
    rep_z = {zz for zz in range(0, zlast + 1) if zz % 16 == 14}
    for zz in range(0, zlast + 1):
        cells.append((xf, zz, "R", "S") if zz in rep_z else (xf, zz, "D", None))
    for zs in sub_rows:
        feed.append(sum(1 for r in rep_z if r < zs))
    lead = max(feed) + 3
    xs = xf + 1
    for st, zs, fd in zip(subs, sub_rows, feed):
        cells.append((xs, zs, "R", "E"))               # Teilleitungs-Eingang (+1 Tick)
        cells.append((xs + 1, zs, "D", None))
        toks = _sub_tokens({o + lead: v for o, v in st.items()}, 2 + fd)
        _lay_serpentine(cells, toks, xs + 2, zs, width)
    return cells, inputs, lead, m


def _rows(steps, width):
    toks = _sub_tokens({o + 40: v for o, v in steps.items()}, 0)
    rows, x, d = 1, 0, 1
    for i in range(len(toks)):
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        rem = (width - 1 - x) if d == 1 else x
        if nxt and nxt[0] == "R" and rem <= 1:
            rows, d = rows + 1, -d
        else:
            x += d
    return rows + 1          # Reserve, falls der echte Start etwas mehr Repeater braucht


def _lay_serpentine(cells, toks, xstart, z, width):
    x, d = xstart, 1
    for i, tok in enumerate(toks):
        if tok[0] == "R":
            cells.append((x, z, "R", "E" if d == 1 else "W", tok[1]))
        else:
            cells.append((x, z, "S", None))
            for side, (inst, clicks) in zip((1, -1), tok[2]):
                cells.append((x, z + side, "N", (inst, clicks), tok[1]))
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        rem = (xstart + width - 1 - x) if d == 1 else (x - xstart)
        if nxt and nxt[0] == "R" and rem <= 1:
            xt = x + d
            for dz in range(ROW_P + 1):
                cells.append((xt, z + dz, "D", None))
            z += ROW_P
            d = -d
        else:
            x += d


# ----------------------------------------------------------------------------------------
class TimingError(RuntimeError):
    def __init__(self, msg, delays):
        super().__init__(msg)
        self.delays = delays


def build_layout3d(plan, max_layers=4, pattern_width=24, verbose=False):
    """Zwei Durchgänge: ist das Timing mit der Zeit-Reihenfolge der Abgriffe nicht lösbar,
    wird der Dirigent nach (Start − gemessene Leitungsverzögerung) sortiert neu gebaut."""
    est = None
    for attempt in range(4):
        try:
            return _build3d(plan, max_layers, pattern_width, verbose, est)
        except TimingError as e:
            if verbose:
                print(f"  Timing-Durchgang {attempt + 1} nicht lösbar – Dirigent wird umsortiert", flush=True)
            est = {**(est or {}), **e.delays}
    raise RuntimeError("Timing nicht lösbar")


def _build3d(plan, max_layers, pattern_width, verbose, delay_est):
    """plan: planner.Plan. Liefert dasselbe Format wie layout.build_layout."""
    import time as _t
    _t0 = _t.time()

    def log(msg):
        if verbose:
            print(f"  [{_t.time() - _t0:6.1f}s] {msg}", flush=True)
    L = max(2, max_layers)
    uses = []                                   # (A, pid)
    for t0, pid in plan.plays:
        uses.append((t0, pid))
    for c in plan.clocks:
        for k in range(c.runs):
            uses.append((c.first_start + k * c.period, c.pattern))
    uses.sort()
    # Viel genutzte Muster mehrfach bauen (je Kopie max. MAX_USES zeitlich benachbarte Einsätze)
    by_pid = defaultdict(list)
    for A, pid in uses:
        by_pid[pid].append(A)
    inst_of = {}
    pats = {}
    for pid, As in by_pid.items():
        for k in range(0, len(As), MAX_USES):
            iid = pid if k == 0 else f"{pid}#{k // MAX_USES + 1}"
            pats[iid] = plan.patterns[pid]
            for A in As[k:k + MAX_USES]:
                inst_of[(A, pid)] = iid
    uses = sorted((A, inst_of[(A, pid)]) for A, pid in uses)
    key = {u: u[0] - (delay_est or {}).get(u, 0) for u in uses}   # gewünschte Abgriffszeit (ungefähr)
    uses.sort(key=lambda u: (key[u], u[0]))
    use_count = defaultdict(int)
    for _, pid in uses:
        use_count[pid] += 1

    w = World()
    # --- Dirigent (Schicht 0) -----------------------------------------------------------
    n_taps = len(uses)
    seg = []                                    # Anzahl R vor jedem Abgriff
    prev = min(key.values()) - 4
    for u in uses:
        seg.append(max(2, round((key[u] - prev) / 2.5)))   # ≥2 Repeater: Platz für Abzweige
        prev = key[u]
    total = sum(seg) + n_taps
    est_area = sum(len(p.notes) * 6 + 40 for p in pats.values()) / max(1, L - 1)
    side = max(24, int(math.sqrt(est_area)) + 8)
    wc = max(16, min(side, total))              # Breite der Dirigenten-Bahnen
    tokens = []
    for n in seg:
        tokens += [("R",)] * n + [("T",)]
    w.blocks[(-1, 0, 0)] = FLOOR
    w.blocks[(-1, 1, 0)] = "minecraft:stone"
    w.blocks[(-1, 2, 0)] = "minecraft:stone_button[face=floor,facing=north,powered=false]"
    w.reserve(0, -1, 0, "start")
    x, z, d = 0, 0, 1
    cond_reps = []                              # (Position, Index im Segment-Plan)
    taps = []                                   # (x, z) je Abgriff
    seg_of_rep = []
    seg_i = 0
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        pos = x if d == 1 else (wc - 1 - x)
        if tok[0] == "T" and (pos < 2 or pos > wc - 4):
            tokens.insert(i, ("R",))            # Abgriff nicht an Bahnenden
            seg[seg_i] += 1
            continue
        if tok[0] == "R":
            w.R(0, x, z, "E" if d == 1 else "W", "cond")
            cond_reps.append((x, 1, z))
            seg_of_rep.append(seg_i)
        else:
            w.B(0, x, z, "cond")
            taps.append((x, z))
            seg_i += 1
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        rem = (wc - 1 - x) if d == 1 else x
        if nxt and rem <= 1:
            if nxt[0] == "T":                   # nach der Kurve muss ein Repeater kommen
                tokens.insert(i + 1, ("R",))
                seg[seg_i] += 1
            xt = x + d
            for dz in range(ROW_C + 1):
                if dz == ROW_C // 2:                    # Redstone trägt nur 15 Blöcke -> Repeater
                    w.R(0, xt, z + dz, "S", "cond")
                    cond_reps.append((xt, 1, z + dz))
                    seg_of_rep.append(seg_i)
                else:
                    w.D(0, xt, z + dz, "cond")
            z += ROW_C
            d = -d
        else:
            x += d
        i += 1

    # Abzweige: Repeater nach Norden + Treppe in Schicht 1
    starts = []
    for j, (tx, tz) in enumerate(taps):
        net = ("w", j)
        w.R(0, tx, tz - 1, "N", net, adjustable=net)
        w.lift(0, tx, tz - 2, "N", net)
        w.R(1, tx, tz - 5, "N", net, adjustable=net)                    # Ausgang vorab belegen
        for dz in (6, 7, 8):                                             # Ausgang freihalten
            w.reserve(1, tx, tz - dz, ("res", net))
        starts.append((1, tx, tz - 5, "N"))

    log(f"Dirigent: {len(taps)} Abgriffe, Breite {wc}")
    # --- Muster platzieren ------------------------------------------------------------------
    built = {}
    for pid, p in pats.items():
        built[pid] = build_pattern(p.notes, use_count[pid], pattern_width)
    tap_of_use = defaultdict(list)
    for j, (A, pid) in enumerate(uses):
        tap_of_use[pid].append(j)

    blocked = defaultdict(set)                  # Schicht -> belegte Felder inkl. Abstand
    for (lay, cx, cz) in list(w.occ):
        for ddx in range(-1, 2):
            for ddz in range(-1, 2):
                blocked[lay].add((cx + ddx, cz + ddz))
    for (_, sx, sz, _) in starts:                # Korridor vor jedem Abgriff (alle Schichten) frei halten
        for lay in range(1, L):
            for ddz in range(-8, 3):
                for ddx in range(-2, 3):
                    blocked[lay].add((sx + ddx, sz + ddz))
    place = {}
    order = sorted(pats, key=lambda p: -len(built[p][0]))
    for pid in order:
        cells = built[pid][0]
        xs_ = [c[0] for c in cells]; zs_ = [c[1] for c in cells]
        bx0, bx1, bz0, bz1 = min(xs_) - 1, max(xs_) + 1, min(zs_) - 1, max(zs_) + 1
        js = tap_of_use[pid]
        tx = sum(starts[j][1] for j in js) / len(js)
        tz = sum(starts[j][2] for j in js) / len(js)
        best = None
        for lay in range(1, L):
            for r in range(0, 400, 2):
                found = None
                for ox in range(-r, r + 1, 2):
                    for oz in (-r, r) if abs(ox) != r else range(-r, r + 1, 2):
                        px, pz = int(tx + ox) - (bx0 + bx1) // 2, int(tz + oz) - (bz0 + bz1) // 2
                        ok = True
                        for cx in range(bx0 - GAP + 1, bx1 + GAP):
                            for cz in range(bz0 - GAP + 1, bz1 + GAP):
                                if (cx + px, cz + pz) in blocked[lay]:
                                    ok = False
                                    break
                            if not ok:
                                break
                        if ok:
                            found = (abs(ox) + abs(oz) + 400 * (lay - 1), lay, px, pz)
                            break
                    if found:
                        break
                if found:
                    if best is None or found[0] < best[0]:
                        best = found
                    break
        _, lay, px, pz = best
        place[pid] = (lay, px, pz)
        for cx in range(bx0, bx1 + 1):
            for cz in range(bz0, bz1 + 1):
                blocked[lay].add((cx + px, cz + pz))
        # Zellen setzen
        net = ("p", pid)
        for c in cells:
            cx, cz, kind = c[0] + px, c[1] + pz, c[2]
            if kind == "R":
                w.R(lay, cx, cz, c[3], net, delay=c[4] if len(c) > 4 else 1)
            elif kind == "B" or kind == "S":
                w.B(lay, cx, cz, net)
            elif kind == "D":
                w.D(lay, cx, cz, net)
            elif kind == "N":
                w.NB(lay, cx, cz, c[3][0], c[3][1], net)
        # Eingangs-Korridore (Nachbarn der Einmündungen) frei halten
        for q, ix, iz, sd in built[pid][1]:
            for ddx, ddz in ((0, 0), (1, 0), (-1, 0)):
                blocked[lay].discard((ix + px + ddx, iz + pz))

    log(f"{len(place)} Muster platziert")
    # --- Verdrahtung (A* mit Rip-up & Reroute) ---------------------------------------------
    from collections import deque
    taken_inputs = {}
    wire_info, wire_added = {}, {}
    queue = deque(sorted(range(n_taps), key=lambda j: _dist(starts[j], place[uses[j][1]])))
    tries = defaultdict(int)
    steps_left = 6 * n_taps
    while queue:
        steps_left -= 1
        if steps_left < 0:
            raise RuntimeError("Verdrahtung gibt auf – mehr Schichten erlauben")
        j = queue.popleft()
        pid = uses[j][1]
        lay, px, pz = place[pid]
        targets = {}
        for q, ix, iz, sd in built[pid][1]:
            key = (pid, q, ix, iz)
            if key not in taken_inputs:
                targets[(lay, ix + px, iz + pz)] = (sd, q, key)
        net = ("w", j)
        path = _astar(w, starts[j], targets, net, L) if targets else None
        if path is None:
            tries[j] += 1
            if tries[j] > 6:
                raise RuntimeError(f"Keine Leitung für Einsatz {j} ({pid}) ({_astar.fail}) – mehr Schichten erlauben")
            # störende Leitungen in der Nähe des Ausgangs und des Ziels abreißen
            sl, sx, sz, _ = starts[j]
            spots = [(sl, sx, sz)] + [(t[0], t[1], t[2]) for t in targets] + \
                    [(lay, ix + px, iz + pz) for q, ix, iz, sd in built[pid][1]]
            victims = set()
            for (vl, vx, vz) in spots:
                for ddx in range(-4, 5):
                    for ddz in range(-6, 5):
                        o = w.occ.get((vl, vx + ddx, vz + ddz))
                        if isinstance(o, tuple) and o[0] == "w" and o != net and o[1] in wire_info:
                            victims.add(o[1])
            for v in victims:
                _rip(w, v, wire_added.pop(v), wire_info.pop(v))
                for k2, owner in list(taken_inputs.items()):
                    if owner == v:
                        del taken_inputs[k2]
            queue.appendleft(j)
            for v in victims:
                queue.append(v)
            continue
        (tl, tx2, tz2), (sd, q, key) = path[-1]
        taken_inputs[key] = j
        w.blocks.log, w.occ.log, before_r = {}, {}, len(w.wire_reps[net])
        reps = 2                                    # Abzweig-Repeater (Schicht 0) + Ausgang (Schicht 1)
        for st in path[:-1]:
            kind, lay2, cx, cz, dd = st
            if kind == "R":
                w.R(lay2, cx, cz, dd, net, adjustable=net); reps += 1
            elif kind == "B":
                w.B(lay2, cx, cz, net)
            elif kind == "LIFT":
                w.lift(lay2, cx, cz, dd, net)
            elif kind == "DROP":
                w.drop(lay2, cx, cz, dd, net)
        w.R(tl, tx2, tz2, sd, net, adjustable=net); reps += 1
        wire_info[j] = (reps, q)
        wire_added[j] = (w.blocks.log, w.occ.log, before_r)
        w.paths = getattr(w, "paths", {})
        w.paths[j] = (starts[j], path)
        w.blocks.log = w.occ.log = None
        if verbose and len(wire_info) % 25 == 0:
            log(f"{len(wire_info)}/{n_taps} Leitungen")

    # --- Timing -----------------------------------------------------------------------------
    lead = {pid: built[pid][2] for pid in pats}
    nseg = defaultdict(int)
    for s in seg_of_rep:
        nseg[s] += 1
    try:
        G, T = _solve_timing(uses, wire_info, lead, nseg)
    except RuntimeError:
        raise TimingError("Timing", {u: lead[u[1]] + wire_info[j][1] + 1.6 * wire_info[j][0]
                                     for j, u in enumerate(uses)})
    # Dirigent-Verzögerungen
    by_seg = defaultdict(list)
    for (pos3, s) in zip(cond_reps, seg_of_rep):
        by_seg[s].append(pos3)
    prevT = 0
    for j in range(n_taps):
        _set_delays(w, by_seg[j], T[j] - prevT)
        prevT = T[j]
    for j in range(n_taps):
        reps, q = wire_info[j]
        A, pid = uses[j]
        need = A + G - lead[pid] - q - T[j]          # Abgriff -> M_q (danach q Ketten-Repeater)
        _set_delays(w, [(x_, y_, z_) for x_, y_, z_, _ in w.wire_reps[("w", j)]], need)

    schedule = []
    for j, (A, pid) in enumerate(uses):
        lay, px, pz = place[pid]
        for c in built[pid][0]:
            if c[2] == "N":
                schedule.append([A + G + c[4] - lead[pid], c[0] + px, w.y(lay), c[1] + pz,
                                 c[3][0], c[3][1]])
    res = _finish(w, schedule, L, len(pats), G)
    res["debug"] = {"paths": w.paths, "uses": uses, "place": place}
    return res


def _rip(w, j, added, info):
    """Leitung j wieder entfernen (nur den per A* verlegten Teil)."""
    blocks, occ, nrep = added
    for k, old in blocks.items():
        if old is None:
            w.blocks.pop(k, None)
        else:
            w.blocks[k] = old
    for k, old in occ.items():
        if old is None:
            w.occ.pop(k, None)
        else:
            w.occ[k] = old
    net = ("w", j)
    del w.wire_reps[net][nrep:]


def _dist(start, plc):
    return abs(start[1] - plc[1]) + abs(start[2] - plc[2]) + 4 * (plc[0] - start[0])


def _set_delays(w, reps, total):
    """Verteilt `total` Ticks auf die Repeater (je 1–4)."""
    n = len(reps)
    if not (n <= total <= 4 * n):
        raise RuntimeError(f"Timing nicht lösbar: {total} Ticks auf {n} Repeater")
    base, extra = divmod(total, n)
    for k, (x, y, z) in enumerate(reps):
        dl = base + (1 if k < extra else 0)
        st = w.blocks[(x, y, z)]
        w.blocks[(x, y, z)] = st.replace(st[st.index("delay="):st.index(",", st.index("delay="))], f"delay={dl}")


def _solve_timing(uses, wire_info, lead, nseg):
    """Abgriffszeiten T_j: Dirigent-Segment j hat nseg[j] Repeater (Δ ∈ [n,4n]),
    Leitung j hat reps Repeater (Verzögerung ∈ [reps, 4·reps]) plus q feste Ketten-Repeater."""
    J = len(uses)
    for G in range(0, 2000):
        lo_prev = hi_prev = 0
        ivs = []
        ok = True
        for j, (A, pid) in enumerate(uses):
            reps, q = wire_info[j]
            arrive = A + G - lead[pid]           # M_0 muss dann an sein
            wl, wh = arrive - q - 4 * reps, arrive - q - reps
            n = nseg[j]
            lo, hi = max(lo_prev + n, wl), min(hi_prev + 4 * n, wh)
            if lo > hi:
                ok = False
                break
            ivs.append((lo, hi))
            lo_prev, hi_prev = lo, hi
        if not ok:
            continue
        T = [0] * J
        T[-1] = ivs[-1][1]
        for j in range(J - 2, -1, -1):
            n = nseg[j + 1]
            lo, hi = max(ivs[j][0], T[j + 1] - 4 * n), min(ivs[j][1], T[j + 1] - n)
            T[j] = hi
            assert lo <= hi
        return G, T
    raise RuntimeError("Timing nicht lösbar")


def _astar(w, start, targets, net, L):
    """start=(layer,x,z,dir) – letzte gesetzte Zelle ist Redstone (D3). Ziele: {(layer,x,z): (dir, …)}
    = Feld, auf dem der letzte Repeater mit Richtung dir in den M-Block zeigt."""
    tl = {t[0] for t in targets}
    tlist = list(targets)

    def h(lay, x, z):
        return min(abs(x - tx) + abs(z - tz) + 5 * abs(tlay - lay) for tlay, tx, tz in tlist)

    mine = ("res", net)

    def free(lay, x, z, allow=()):
        o0 = w.occ.get((lay, x, z))
        if o0 is not None and o0 != mine:
            return False
        for dx, dz in DV.values():
            o = w.occ.get((lay, x + dx, z + dz))
            if o is not None and o != mine and (lay, x + dx, z + dz) not in allow:
                return False
        return True

    sl, sx, sz, sd = start
    bx = [sx] + [t[1] for t in tlist]; bz = [sz] + [t[2] for t in tlist]
    box = (min(bx) - 30, max(bx) + 30, min(bz) - 30, max(bz) + 30)
    open_ = [(h(sl, sx, sz), 0, (sl, sx, sz, sd, "R"))]
    came = {(sl, sx, sz, sd, "R"): None}
    gbest = {(sl, sx, sz, sd, "R"): 0}
    own = defaultdict(set)
    expanded = 0
    while open_:
        f, g, st = heapq.heappop(open_)
        expanded += 1
        if expanded > 250000:
            _astar.fail = ("limit", expanded)
            return None
        lay, x, z, d, prev = st
        dx, dz = DV[d]
        nx, nz = x + dx, z + dz
        # Ziel?
        if (lay, nx, nz) in targets and targets[(lay, nx, nz)][0] == d:
            tgt = (lay, nx, nz)
            sd_, q, key = targets[tgt]
            mx, mz = nx + DV[sd_][0], nz + DV[sd_][1]
            if free(lay, nx, nz, allow={(lay, mx, mz), (lay, x, z)}):
                path = []
                s = st
                while came[s] is not None:
                    path.append(came[s][1])
                    s = came[s][0]
                path.reverse()
                return path + [(tgt, targets[tgt])]
        moves = []
        allow_prev = {(lay, x, z)}
        # geradeaus: Repeater
        if free(lay, nx, nz, allow_prev):
            moves.append(((lay, nx, nz, d, "R"), ("R", lay, nx, nz, d), 1))
        if prev == "R":
            # Ecke: Block, danach neue Richtung
            if free(lay, nx, nz, allow_prev):
                for nd in PERP[d]:
                    ex, ez = nx + DV[nd][0], nz + DV[nd][1]
                    if (lay, ex, ez) not in w.occ:
                        moves.append(((lay, nx, nz, nd, "B"), ("B", lay, nx, nz, d), 1.3))
            # Treppe hoch
            if lay + 1 < L:
                cells = lift_cells(lay, nx, nz, d)
                ok = all(free(cl, cx, cz, allow={(lay, x, z)} | set(cells)) for cl, cx, cz in cells)
                if ok:
                    ex, ez = nx + 2 * dx, nz + 2 * dz
                    moves.append(((lay + 1, ex, ez, d, "D"), ("LIFT", lay, nx, nz, d), 6))
            # Treppe runter (nie in Schicht 0)
            if lay - 1 >= 1:
                cells = drop_cells(lay, nx, nz, d)
                ok = all(free(cl, cx, cz, allow={(lay, x, z)} | set(cells)) for cl, cx, cz in cells)
                if ok:
                    ex, ez = nx + 2 * dx, nz + 2 * dz
                    moves.append(((lay - 1, ex, ez, d, "D"), ("DROP", lay, nx, nz, d), 6))
        for nst, step, cost in moves:
            if not (box[0] <= nst[1] <= box[1] and box[2] <= nst[2] <= box[3]):
                continue
            ng = g + cost
            if ng < gbest.get(nst, 1e18):
                gbest[nst] = ng
                came[nst] = (st, step)
                heapq.heappush(open_, (ng + h(nst[0], nst[1], nst[2]), ng, nst))
    _astar.fail = ("exhausted", expanded)
    return None


def _finish(w, schedule, L, n_patterns, G):
    xs = [p[0] for p in w.blocks]; ys = [p[1] for p in w.blocks]; zs = [p[2] for p in w.blocks]
    mn = (min(xs), min(ys), min(zs))
    norm = {(x - mn[0], y - mn[1], z - mn[2]): s for (x, y, z), s in w.blocks.items()}
    for s in schedule:
        s[1] -= mn[0]; s[2] -= mn[1]; s[3] -= mn[2]
    size = (max(xs) - mn[0] + 1, max(ys) - mn[1] + 1, max(zs) - mn[2] + 1)
    start = (-1 - mn[0], 2 - mn[1], 0 - mn[2])
    layers = (max(ys) + 1) // LH + (1 if (max(ys) + 1) % LH else 0)
    return {"blocks": norm, "size": size, "schedule": sorted(schedule), "start_button": start,
            "lines": n_patterns, "layers": min(L, layers), "width": None, "offset": G}
