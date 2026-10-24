"""3D-Layout MIT Wiederverwendung: „Kreuzschiene“ (feste Geometrie, keine Wegsuche).

Jeder Abschnitt (siehe sections.py) wird genau EINMAL als Leitung gebaut („Körper“).
Die Körper liegen untereinander in Z (Bank, Westseite). Pro Körper läuft auf Höhe y=1 eine
„Brücke“ nach Osten (Repeater, Zellen abwechselnd Kreuzung/Repeater). Darüber (y=2) laufen
die „Durchgänge“ des Dirigenten in Z hin und her (Schlange, Abstand 2 in X).

  Kreuzung, wo der Dirigent einen Körper starten soll:  Durchgang-Zelle = Block S
      (vom Repeater davor stark gepowert) über Redstone der Brücke  -> Brücke feuert.
  Kreuzung ohne Start: Durchgang-Repeater steht auf einem Block der Brücke -> keine Verbindung.

Timing: Durchgänge (Repeater 1–4 / Redstone 0), Brücken-Repeater (1–4) und Vorlauf je Körper
werden über ein Differenz-Ungleichungssystem (Bellman-Ford) exakt eingestellt.
Geprüft mit redstone_sim.py.
"""
from collections import defaultdict

from layout import INST_BLOCK, S_BLOCK, FLOOR, _conductive, _note_block, _repeater, _wire, _tokens

ROW = 3
SUPPORT = "minecraft:stone"
MAX_DUST = 14


def _face(dx, dz):
    """Laufrichtung -> Repeater-facing (= Eingangsseite)."""
    return {(1, 0): "west", (-1, 0): "east", (0, 1): "north", (0, -1): "south"}[(dx, dz)]


# ----------------------------------------------------------------------------------------
# Körper (Muster-Leitungen)
# ----------------------------------------------------------------------------------------
def _sublines(notes):
    """Noten eines Musters auf Teilleitungen verteilen (je Schritt max. 3 Notenblöcke)."""
    by = defaultdict(list)
    for off, inst, clicks in notes:
        by[off].append((inst, clicks))
    subs = defaultdict(dict)
    for off, v in by.items():
        cond = sorted(n for n in v if _conductive(n[0]))
        nonc = sorted(n for n in v if not _conductive(n[0]))
        k = 0
        while cond or nonc:
            top = cond.pop(0) if cond else None
            sides = []
            while len(sides) < 2 and (nonc or cond):
                sides.append(nonc.pop(0) if nonc else cond.pop(0))
            subs[k][off] = {"sides": sides, "top": top}
            k += 1
    return [subs[k] for k in sorted(subs)]


def _lane_walk(toks, x_start, width):
    """Positionen der Tokens einer Leitung, die bei x_start nach Westen startet und in Bahnen
    (+Z, Abstand ROW) zwischen x_start-width+1 und x_start pendelt.
    Liefert [(x, dz, tok)], [(x_turn, dz)] und Bahnenzahl."""
    xmin, xmax = x_start - width + 1, x_start
    x, d, dz = x_start, -1, 0
    out, turns = [], []
    for i, tok in enumerate(toks):
        out.append((x, dz, tok, d))
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        remaining = (x - xmin) if d == -1 else (xmax - x)
        if nxt is not None and nxt[0] == "R" and remaining <= 1:
            turns.append((x + d, dz))
            dz += ROW
            d = -d
        else:
            x += d
    return out, turns, dz // ROW + 1


def _body_rows(sub, lead, width):
    toks = _tokens({o + lead: v for o, v in sub.items()}, 0)
    return _lane_walk(toks, 0, width)[2]


# ----------------------------------------------------------------------------------------
# Bellman-Ford für Differenz-Ungleichungen  x_v - x_u <= w
# ----------------------------------------------------------------------------------------
def _bf(nodes, edges, explain=False):
    dist = {n: 0 for n in nodes}
    pred = {}
    for _ in range(len(nodes) + 1):
        changed = None
        for e in edges:
            u, v, w = e[:3]
            if dist[u] + w < dist[v]:
                dist[v] = dist[u] + w
                pred[v] = e
                changed = v
        if changed is None:
            return dist
    if explain:                       # negativen Kreis ausgeben
        v = changed
        for _ in range(len(nodes)):
            v = pred[v][0]
        cyc, x = [], v
        while True:
            e = pred[x]
            cyc.append(e)
            x = e[0]
            if x == v:
                break
        for e in reversed(cyc):
            print("   ", e)
    return None


# ----------------------------------------------------------------------------------------
class Crossbar:
    def __init__(self, plan, width):
        self.plan = plan
        self.width = width
        pats = plan.patterns
        uses = defaultdict(list)
        for t, pid in plan.plays:
            uses[pid].append(t)
        # Körper nach erstem Einsatz sortieren
        self.pids = sorted(pats, key=lambda p: (min(uses[p]), p))
        self.uses = sorted((t, pid) for pid in self.pids for t in uses[pid])
        self.subs = {pid: _sublines(pats[pid].notes) for pid in self.pids}
        self.lead_slack = 40
        self.window = 12
        self.G_fixed = None

    # ---------- Bank-Geometrie ----------
    def place_bank(self, lead_guess):
        """Z-Start je Körper und je Teilleitung; lead_guess: {pid: Vorlauf} (für Bahnenzahl)."""
        self.Z, self.subZ, self.maxD = {}, {}, {}
        z = 0
        for pid in self.pids:
            self.Z[pid] = z
            zs = []
            for sub in self.subs[pid]:
                zs.append(z)
                z += ROW * _body_rows(sub, lead_guess.get(pid, 0), self.width)
            self.subZ[pid] = zs
            # Verteiler: S bei jeder Teilleitung, dazwischen 1-Tick-Repeater
            D, d = [], 0
            prev = zs[0]
            for k, zk in enumerate(zs):
                if k > 0:
                    d += zk - prev - 1
                D.append(d)
                prev = zk
            self.maxD[pid] = max(D)
            self.subD = getattr(self, "subD", {})
            self.subD[pid] = D
        self.zend = z
        self.ZMIN = min(self.Z.values()) - 1
        self.ZMAX = max(self.Z.values()) + 1

    # ---------- Dirigenten-Pfad ----------
    def pass_cells(self, p):
        zs = range(self.ZMIN, self.ZMAX + 1)
        return [(p, z) for z in (zs if p % 2 == 0 else reversed(zs))]

    def path(self, P):
        """Zellenfolge des Dirigenten über P Durchgänge: [(kind, p, z)], kind 'P' (Durchgang) / 'T' (Kurve)."""
        cells = []
        for p in range(P):
            cells += [("P", p, z) for _, z in self.pass_cells(p)]
            if p + 1 < P:
                zt = self.ZMAX + 1 if p % 2 == 0 else self.ZMIN - 1
                cells += [("T", p, zt), ("T", p + 0.5, zt), ("T", p + 1, zt)]
        return cells

    def bounds(self, assign, P):
        """Für die aktuelle Zuordnung (use -> (p, pid)): Pfad, Segment-Grenzen [lo, hi]."""
        cells = self.path(P)
        index = {(c[1], c[2]): i for i, c in enumerate(cells) if c[0] == "P"}
        last_pass = defaultdict(lambda: -1)
        for u, (p, pid) in assign.items():
            last_pass[pid] = max(last_pass[pid], p)
        taps = sorted((index[(p, self.Z[pid])], u) for u, (p, pid) in assign.items())
        tap_at = {i: u for i, u in taps}
        kind = []          # 'R' erzwungen, 'D' fest (Kurve), 'F' frei, 'S' Abgriff
        for i, c in enumerate(cells):
            if i in tap_at:
                kind.append("S")
            elif c[0] == "T":
                kind.append("D")
            else:
                kind.append("F")
        kind[0] = "R" if kind[0] != "S" else kind[0]
        for i in tap_at:
            if i == 0 or kind[i - 1] in ("S", "D"):
                return None
            kind[i - 1] = "R"
        segs = []
        prev = -1
        for i, u in taps:
            segs.append((prev, i, u))
            prev = i
        return cells, kind, segs

    @staticmethod
    def seg_lohi(kind, a, b):
        """Zellen a+1..b-1 (zwischen zwei Abgriffen): min/max Verzögerung."""
        inner = kind[a + 1:b]
        br = _breakers(inner)
        if br is None:
            return None
        nR = sum(1 for k in inner if k == "R")
        nF = sum(1 for k in inner if k == "F")
        return nR + len(br), 4 * (nR + nF)

    def solve(self, assign, P):
        b = self.bounds(assign, P)
        if b is None:
            return None
        cells, kind, segs = b
        nodes = ["0", "G"]
        edges = []

        def rng(u, v, lo, hi, why=""):  # lo <= x_v - x_u <= hi
            edges.append((u, v, hi, why + " hi"))
            edges.append((v, u, -lo, why + " lo"))
        for a, i, u in segs:
            lh = self.seg_lohi(kind, a, i)
            if lh is None:
                return None
            lo, hi = lh
            src = "0" if a < 0 else ("t", a)
            nodes.append(("t", i))
            rng(src, ("t", i), lo, hi, f"pfad {a}->{i}")
        tap_cell = {u: i for _, i, u in segs}
        per = defaultdict(list)
        for u, (p, pid) in assign.items():
            per[pid].append((p, u))
        for pid, lst in per.items():
            lst.sort()
            lmin = 2 + self.maxD[pid]
            for k, (p, u) in enumerate(lst):
                zn = ("z", pid, p)
                if zn not in nodes:
                    nodes.append(zn)
                T = self.uses[u][0]
                rng(zn, ("t", tap_cell[u]), T, T, f"einsatz {u}")    # t_u - Z = T_u
                if k == 0:
                    rng(zn, "G", p + 1 + lmin, 4 * (p + 1) + lmin + self.lead_slack, f"vorlauf {pid}")
                else:
                    pp = lst[k - 1][0]
                    if pp == p:
                        return None
                    rng(zn, ("z", pid, pp), p - pp, 4 * (p - pp), f"bruecke {pid}")
        if self.G_fixed is not None:
            rng("0", "G", self.G_fixed, self.G_fixed, "G fest")
        self._last_graph = (nodes, edges)
        dist = _bf(nodes, edges, getattr(self, 'explain', False))
        if dist is None:
            return None
        # Ergebnis relativ zu Knoten "0"
        base = dist["0"]
        return {k: v - base for k, v in dist.items()}, cells, kind, segs

    def assign_all(self, max_extra=40, verbose=False):
        assign, P = {}, 1
        cur_p, cur_z = 0, None
        key = lambda u: self.uses[u][0] - self.maxD[self.uses[u][1]]
        order = sorted(range(len(self.uses)), key=key)
        i = 0
        while i < len(order):
            # zeitlich nahe Abgriffe (Fenster) gemeinsam, in Laufrichtung sortiert
            T = key(order[i])
            j = i
            while j < len(order) and key(order[j]) - T <= self.window:
                j += 1
            group = order[i:j]
            placed = False
            pids_g = [self.uses[u][1] for u in group]
            if len(set(pids_g)) == len(pids_g):
                # ganze Gruppe auf EINEN Durchgang (gleiche Reihenfolge -> Timing lösbar)
                for p in range(cur_p, cur_p + max_extra):
                    dirn = 1 if p % 2 == 0 else -1
                    gs = sorted(group, key=lambda u: self.Z[self.uses[u][1]] * dirn)
                    if p == cur_p and cur_z is not None and (self.Z[self.uses[gs[0]][1]] - cur_z) * dirn <= 0:
                        continue
                    if any(a[0] == p and a[1] in pids_g for a in assign.values()):
                        continue
                    trial = dict(assign)
                    for u in gs:
                        trial[u] = (p, self.uses[u][1])
                    if self.solve(trial, max(P, p + 1)) is not None:
                        assign, P = trial, max(P, p + 1)
                        cur_p, cur_z = p, self.Z[self.uses[gs[-1]][1]]
                        placed = True
                        break
            if not placed:
                dirn = 1 if cur_p % 2 == 0 else -1
                zc = cur_z if cur_z is not None else -10 ** 9 * dirn
                ahead = sorted((u for u in group if (self.Z[self.uses[u][1]] - zc) * dirn > 0),
                               key=lambda u: self.Z[self.uses[u][1]] * dirn)
                behind = sorted((u for u in group if u not in ahead),
                                key=lambda u: -self.Z[self.uses[u][1]] * dirn)
                group = ahead + behind
            for u in ([] if placed else group):
                pid = self.uses[u][1]
                z = self.Z[pid]
                ok = False
                for p in range(cur_p, cur_p + max_extra):
                    if p == cur_p and cur_z is not None:
                        ahead = (z > cur_z) if p % 2 == 0 else (z < cur_z)
                        if not ahead:
                            continue
                    if any(a[0] == p and a[1] == pid for a in assign.values()):
                        continue
                    trial = dict(assign)
                    trial[u] = (p, pid)
                    if self.solve(trial, max(P, p + 1)) is not None:
                        assign, P = trial, max(P, p + 1)
                        cur_p, cur_z = p, z
                        ok = True
                        break
                if not ok:
                    raise RuntimeError(f"Einsatz {u} ({pid} bei Tick {self.uses[u][0]}) nicht platzierbar")
            if verbose:
                print(f"  {j}/{len(order)} Einsätze, {P} Durchgänge", flush=True)
            i = j
        return assign, P


def _breakers(k):
    """Freie Zellen, die Repeater werden müssen, damit kein Redstone-Lauf > MAX_DUST wird."""
    br, run, lastF = set(), 0, None
    for n, kk in enumerate(k):
        if kk == "R":
            run, lastF = 0, None
            continue
        run += 1
        if kk == "F":
            lastF = n
        if run > MAX_DUST:
            if lastF is None:
                return None
            br.add(lastF)
            run, lastF = n - lastF, None
    return br


def _min_G(nodes, edges):
    """Kleinstmögliches G - 0 = -dist(G -> 0)."""
    INF = 10 ** 12
    d = {n: INF for n in nodes}
    d["G"] = 0
    for _ in range(len(nodes)):
        ch = False
        for e in edges:
            u, v, w = e[:3]
            if d[u] < INF and d[u] + w < d[v]:
                d[v] = d[u] + w
                ch = True
        if not ch:
            break
    return -d["0"]


def _distribute(total, n):
    """total Ticks auf n Repeater (je 1–4)."""
    assert n <= total <= 4 * n, (total, n)
    base, extra = divmod(total, n)
    return [base + (1 if k < extra else 0) for k in range(n)]


def _solve_floor(plan, width, verbose=False, G_fixed=None):
    cb = Crossbar(plan, width)
    cb.G_fixed = G_fixed
    lead_guess = {}
    for attempt in range(12):
        cb.place_bank(lead_guess)
        assign, P = cb.assign_all(verbose=verbose)
        dist, cells, kind, segs = cb.solve(assign, P)
        per = defaultdict(list)
        for u, (p, pid) in assign.items():
            per[pid].append((p, u))
        lead, sums = {}, {}
        for pid, lst in per.items():
            lst.sort()
            p1 = lst[0][0]
            s = dist["G"] - dist[("z", pid, p1)]                  # B(p1) + Λ
            lmin = 2 + cb.maxD[pid]
            B1 = max(p1 + 1, min(4 * (p1 + 1), s - lmin))
            sums[pid] = (B1, s - B1)
            lead[pid] = s - B1 - 2                                # λ (nach e=1 und Feed-Repeater)
        # passen die Teilleitungen mit dem echten Vorlauf in die reservierten Bahnen?
        bad = []
        for pid in cb.pids:
            zs = cb.subZ[pid] + [None]
            nxt = {k: (zs[k + 1] if zs[k + 1] is not None else None) for k in range(len(cb.subs[pid]))}
            alloc_end = [cb.subZ[pid][k + 1] if k + 1 < len(cb.subZ[pid]) else
                         (cb.Z[cb.pids[cb.pids.index(pid) + 1]] if cb.pids.index(pid) + 1 < len(cb.pids) else 10 ** 9)
                         for k in range(len(cb.subs[pid]))]
            for k, sub in enumerate(cb.subs[pid]):
                lk = lead[pid] - cb.subD[pid][k]
                if lk < 0 or cb.subZ[pid][k] + ROW * _body_rows(sub, lk, cb.width) > alloc_end[k]:
                    bad.append(pid)
                    break
        if not bad:
            break
        lead_guess = {pid: max(lead[pid] + (8 if pid in bad else 0), lead_guess.get(pid, 0)) for pid in lead}
    else:
        raise RuntimeError("Bank-Platzierung konvergiert nicht")
    cb.min_G = _min_G(*cb._last_graph) if G_fixed is None else G_fixed
    return cb, assign, P, dist, cells, kind, segs, per, sums, lead


FLOOR_H = 6
XP0 = 4                    # erster Durchgang (Bank-Ostkante bei x=0)


def build_layout_reuse(plan, floors=1, width=None, verbose=False):
    """Kreuzschienen-Layout, Körper auf `floors` übereinandergestapelte Etagen verteilt.
    Jede Etage hat einen eigenen Dirigenten; alle starten über eine Treppe vom selben Knopf."""
    from planner import Plan
    pats = plan.patterns
    size = {pid: sum(len(_tokens(sb, 0)) for sb in _sublines(p.notes)) for pid, p in pats.items()}
    if width is None:
        width = max(16, int((ROW * sum(size.values()) / floors) ** 0.5 * 0.9))
    groups = [[] for _ in range(floors)]
    load = [0] * floors
    for pid in sorted(pats, key=lambda q: -size[q]):
        f = min(range(floors), key=lambda k: load[k])
        groups[f].append(pid)
        load[f] += size[pid]
    groups = [g for g in groups if g]
    subs = [Plan(plan.tpb, plan.bar_ticks, plan.lead_ticks, {pid: pats[pid] for pid in g},
                 [pl for pl in plan.plays if pl[1] in set(g)], []) for g in groups]
    starts = [_trunk_start(f) for f in range(len(groups))]
    # 1. Durchgang frei -> kleinstes G je Etage; 2. Durchgang mit gemeinsamem Zeitbezug
    Gt = max(_solve_floor(sb, width)[0].min_G + starts[f] for f, sb in enumerate(subs))
    res = []
    for f, sb in enumerate(subs):
        r = _solve_floor(sb, width, verbose, G_fixed=Gt - starts[f])
        res.append(_realize(*r))
        if verbose:
            print(f"  Etage {f + 1}: {len(sb.patterns)} Muster, {r[2]} Durchgänge", flush=True)
    return _stack(res, starts, Gt)


XT = XP0 - 2               # Start-Stamm (Treppe) bei x = XT, negative z


def _trunk_start(f):
    """Zeit, zu der der Startblock (XP0, 2, -2) von Etage f gepowert wird (Knopf bei t=0)."""
    return 2 + 3 * f


def _stack(floors, starts, Gt):
    blocks, schedule = {}, []

    def put(x, y, z, st):
        blocks[(x, y, z)] = st

    # Knopf auf (XT-2, 2, -4). Je Etage: Repeater (XT-1) -> Abzweig-Block (XT, -4) -> Redstone ->
    # Block (XP0, -4) -> Repeater (XP0, -3) -> Startblock (XP0, -2). Weiter: Repeater (XT, -5),
    # Redstone nach Westen (z=-6, mit Repeater), Treppe nach Osten hoch (z=-4) zur nächsten Etage.
    put(XT - 2, 2, -4, "minecraft:stone")
    put(XT - 2, 3, -4, "minecraft:stone_button[face=floor,facing=north,powered=false]")
    for f in range(len(floors)):
        y = FLOOR_H * f + 2
        put(XT - 1, y - 1, -4, SUPPORT); put(XT - 1, y, -4, _repeater(1, "west"))
        put(XT, y, -4, SUPPORT)                                          # Abzweig-Block (stark)
        put(XT + 1, y - 1, -4, SUPPORT); put(XT + 1, y, -4, _wire())
        put(XP0, y, -4, SUPPORT)                                         # schwach gepowert
        put(XP0, y - 1, -3, SUPPORT); put(XP0, y, -3, _repeater(1, "north"))
        put(XP0, y, -2, "minecraft:stone")                               # Startblock der Etage
        if f + 1 < len(floors):
            put(XT, y - 1, -5, SUPPORT); put(XT, y, -5, _repeater(1, "south"))
            for x in range(XT, XT - 10, -1):                             # nach Westen (z = -6)
                put(x, y - 1, -6, SUPPORT)
                put(x, y, -6, _repeater(1, "east") if x == XT - 5 else _wire())
            put(XT - 9, y - 1, -5, SUPPORT); put(XT - 9, y, -5, _wire())
            put(XT - 9, y - 1, -4, SUPPORT); put(XT - 9, y, -4, _wire())
            for k in range(1, FLOOR_H + 1):                              # Treppe hoch nach Osten
                put(XT - 9 + k, y + k - 1, -4, SUPPORT); put(XT - 9 + k, y + k, -4, _wire())
            put(XT - 2, y + FLOOR_H - 1, -4, SUPPORT); put(XT - 2, y + FLOOR_H, -4, _wire())
    for f, fl in enumerate(floors):
        yb = FLOOR_H * f
        for (x, yy, zz), st in fl["blocks"].items():
            assert (x, yy + yb, zz) not in blocks or blocks[(x, yy + yb, zz)] == st, (x, yy + yb, zz, st)
            blocks[(x, yy + yb, zz)] = st
        for t, x, yy, zz, i, c in fl["schedule"]:
            schedule.append([t + starts[f], x, yy + yb, zz, i, c])
    xs = [p[0] for p in blocks]; ys = [p[1] for p in blocks]; zs_ = [p[2] for p in blocks]
    mn = (min(xs), min(ys), min(zs_))
    norm = {(x - mn[0], y - mn[1], z - mn[2]): st for (x, y, z), st in blocks.items()}
    for sch in schedule:
        sch[1] -= mn[0]; sch[2] -= mn[1]; sch[3] -= mn[2]
    size = (max(xs) - mn[0] + 1, max(ys) - mn[1] + 1, max(zs_) - mn[2] + 1)
    st = (XT - 2 - mn[0], 3 - mn[1], -4 - mn[2])
    return {"blocks": norm, "size": size, "schedule": sorted(schedule), "start_button": st,
            "lines": sum(fl["lines"] for fl in floors), "layers": len(floors),
            "width": floors[0]["width"], "offset": Gt, "passes": sum(fl["passes"] for fl in floors)}


def _compress(cb, assign, P, dist, cells, kind, segs):
    """Durchgänge nur so lang wie nötig: Wendepunkte so nah wie möglich an die Abgriffe,
    nur verlängern, wenn ein Abschnitt sonst nicht genug Verzögerung (max. 4 Ticks/Repeater) hätte."""
    tz = {u: (assign[u][0], cb.Z[assign[u][1]]) for u in assign}
    order = [u for _, _, u in segs]
    lastp = max(p for p, _ in tz.values())
    on = defaultdict(list)
    for u in order:
        on[tz[u][0]].append(tz[u][1])
    # Wende k liegt zwischen Durchgang k und k+1; gerade k: oben (h), ungerade: unten (l)
    lo_lim, hi_lim = cb.ZMIN, cb.ZMAX
    turn = {}
    for k in range(lastp):
        a, b = on.get(k, []), on.get(k + 1, [])
        if k % 2 == 0:
            turn[k] = max([lo_lim + 1] + a + [z + 1 for z in b])
        else:
            turn[k] = min([hi_lim - 1] + a + [z - 1 for z in b])

    def build():
        cl = []
        for p in range(lastp + 1):
            start = cb.ZMIN if p == 0 else turn[p - 1]
            end = turn[p] if p < lastp else (max(on[p]) if p % 2 == 0 else min(on[p]))
            rng = range(start, end + 1) if p % 2 == 0 else range(start, end - 1, -1)
            cl += [("P", p, z) for z in rng]
            if p < lastp:
                zt = turn[p] + 1 if p % 2 == 0 else turn[p] - 1
                cl += [("T", p, zt), ("T", p + 0.5, zt), ("T", p + 1, zt)]
        idx = {(c[1], c[2]): i for i, c in enumerate(cl) if c[0] == "P"}
        tl = [(idx[tz[u]], u) for u in order]
        kd = ["S" if i in {t for t, _ in tl} else ("D" if c[0] == "T" else "F") for i, c in enumerate(cl)]
        if kd[0] != "S":
            kd[0] = "R"
        for i, _ in tl:
            assert kd[i - 1] not in ("S", "D")
            kd[i - 1] = "R"
        sg, prev = [], -1
        for i, u in tl:
            sg.append((prev, i, u))
            prev = i
        return cl, kd, sg

    tval = {u: dist[("t", i)] for _, i, u in segs}
    for _ in range(100000):
        cl, kd, sg = build()
        grow = None
        for a, i, u in sg:
            prev = 0 if a < 0 else tval[next(uu for aa, ii, uu in sg if ii == a)]
            need = tval[u] - prev
            lohi = Crossbar.seg_lohi(kd, a, i)
            assert lohi is not None and lohi[0] <= need, (lohi, need)
            if lohi[1] < need:
                # Wenden in diesem Abschnitt nach außen schieben
                pa = cl[a][1] if a >= 0 else 0
                pb = cl[i][1]
                cand = [k for k in range(int(pa), int(pb)) if
                        (k % 2 == 0 and turn[k] < hi_lim) or (k % 2 == 1 and turn[k] > lo_lim)]
                assert cand, "Abschnitt braucht mehr Verzögerung als möglich"
                k = min(cand, key=lambda k: (turn[k] - lo_lim) if k % 2 == 0 else (hi_lim - turn[k]))
                step = max(1, (need - lohi[1]) // 8)
                turn[k] = min(hi_lim, turn[k] + step) if k % 2 == 0 else max(lo_lim, turn[k] - step)
                grow = k
                break
        if grow is None:
            break
    nd = dict(dist)
    for _, i, u in sg:
        nd[("t", i)] = tval[u]
    return cl, kd, sg, nd


def _realize(cb, assign, P, dist, cells, kind, segs, per, sums, lead):
    cells, kind, segs, dist = _compress(cb, assign, P, dist, cells, kind, segs)
    blocks = {}

    def put(x, y, z, s):
        old = blocks.get((x, y, z))
        if old is not None and old != s and not (old == SUPPORT or s == SUPPORT or old == FLOOR or s == FLOOR):
            raise AssertionError(f"Kollision bei {(x, y, z)}: {old} / {s}")
        blocks[(x, y, z)] = s

    W = cb.width
    XB0 = 0                    # Ostkante der Bank (Kurven-Spalte)
    xp = lambda p: XP0 + 2 * p
    schedule = []
    G = dist["G"]

    # ---------- Dirigent ----------
    tdelay = {}
    for a, i, u in segs:
        inner = list(range(a + 1, i))
        total = dist[("t", i)] - (0 if a < 0 else dist[("t", a)])
        k = [kind[c] for c in inner]
        # R-Zellen wählen: erzwungene + Läufe brechen + ggf. mehr für große Verzögerungen
        br = _breakers(k)
        isR = [kk == "R" or n in br for n, kk in enumerate(k)]
        free = [n for n, kk in enumerate(k) if kk == "F" and not isR[n]]
        need = -(-total // 4) - sum(isR)
        if need > 0:
            step = len(free) / need
            for q in range(need):
                isR[free[int(q * step)]] = True
        rcells = [inner[n] for n in range(len(inner)) if isR[n]]
        for c, dl in zip(rcells, _distribute(total, len(rcells))):
            tdelay[c] = dl
    last_tap = max(i for _, i, _ in segs)
    tap_cells = {i for _, i, _ in segs}
    for i, c in enumerate(cells[:last_tap + 1]):
        kd, p, z = c
        x = xp(p) if kd == "P" else XP0 + int(round(2 * p))
        if kd == "T":
            put(x, 1, z, SUPPORT); put(x, 2, z, _wire())
            continue
        dz = 1 if int(p) % 2 == 0 else -1
        if i in tap_cells:
            put(x, 2, z, S_BLOCK)
        elif i in tdelay:
            put(x, 2, z, _repeater(tdelay[i], _face(0, dz)))
        else:
            put(x, 2, z, _wire())
        if i not in tap_cells:
            put(x, 1, z, SUPPORT)
    assert cb.ZMIN == -1

    # ---------- Brücken ----------
    tap_of = {}
    for _, i, u in segs:
        tap_of[u] = i
    for pid in cb.pids:
        lst = sorted(per[pid])
        z = cb.Z[pid]
        last = lst[-1][0]
        connect = {p for p, _ in lst}
        # Verzögerungen b_q für q = 0..last
        b = [None] * (last + 1)
        B1, lam_sum = sums[pid]
        p1 = lst[0][0]
        for q, dl in zip(range(0, p1 + 1), _distribute(B1, p1 + 1)):
            b[q] = dl
        Bprev = B1
        for k in range(1, len(lst)):
            pp, p = lst[k - 1][0], lst[k][0]
            Bk = Bprev + dist[("z", pid, pp)] - dist[("z", pid, p)]
            for q, dl in zip(range(pp + 1, p + 1), _distribute(Bk - Bprev, p - pp)):
                b[q] = dl
            Bprev = Bk
        put(XB0 + 2, 0, z, _repeater(1, "east")); put(XB0 + 2, -1, z, FLOOR)       # e_j = 1
        for q in range(last + 1):
            put(xp(q) - 1, 0, z, _repeater(b[q], "east")); put(xp(q) - 1, -1, z, FLOOR)
            put(xp(q), 0, z, SUPPORT)                                                 # Brücken-Block
            if q in connect:
                put(xp(q), 1, z, _wire())                                             # S drüber -> Redstone -> Block
        # Teilleitungen (Bank)
        lam = lead[pid]
        zs = cb.subZ[pid]
        put(XB0 + 1, 0, z, S_BLOCK)                                                   # Verteiler-Start
        for zz in range(z + 1, zs[-1] + 1):
            if zz in zs:
                put(XB0 + 1, 0, zz, S_BLOCK)
            else:
                put(XB0 + 1, 0, zz, _repeater(1, "north")); put(XB0 + 1, -1, zz, FLOOR)
        for k, (sub, zk) in enumerate(zip(cb.subs[pid], zs)):
            put(XB0, 0, zk, _repeater(1, "east")); put(XB0, -1, zk, FLOOR)            # Feed
            lk = lam - cb.subD[pid][k]
            assert lk >= 0
            toks = _tokens({o + lk: v for o, v in sub.items()}, 0)
            walk, turns, rows = _lane_walk(toks, XB0 - 1, W)
            for x, dz, tok, d in walk:
                zz = zk + dz
                put(x, -1, zz, FLOOR)
                if tok[0] == "R":
                    put(x, 0, zz, _repeater(tok[1], _face(d, 0)))
                else:
                    _, off, notes = tok
                    del blocks[(x, -1, zz)]
                    top = notes["top"]
                    put(x, 0, zz, INST_BLOCK[top[0]] if top else S_BLOCK)
                    times = [cb.uses[u][0] + G + (off - lk) for _, u in lst]
                    if top:
                        put(x, 1, zz, _note_block(*top))
                        schedule += [[t, x, 1, zz, top[0], top[1]] for t in times]
                    for side, (ninst, clicks) in zip((1, -1), notes["sides"]):
                        put(x, -1, zz + side, INST_BLOCK[ninst])
                        put(x, 0, zz + side, _note_block(ninst, clicks))
                        schedule += [[t, x, 0, zz + side, ninst, clicks] for t in times]
            for xt, dz in turns:
                for q in range(ROW + 1):
                    put(xt, -1, zk + dz + q, FLOOR)
                    put(xt, 0, zk + dz + q, _wire())

    return {"blocks": blocks, "schedule": schedule, "G": G, "lines": len(cb.pids), "width": W, "passes": P}
