"""Kleiner Redstone-Simulator (Java-Regeln, vereinfacht) zum Prüfen der 3D-Layouts.

Unterstützt: Steinknopf (Boden), Redstone (inkl. Treppen hoch/runter), Repeater (Delay,
Pulsverlängerung), stark/schwach gepowerte Vollblöcke, Notenblöcke (steigende Flanke,
Luft darüber). 1 Schritt = 1 Redstone-Tick (0,1 s).
"""
import re
from collections import deque

H = {"north": (0, 0, -1), "south": (0, 0, 1), "west": (-1, 0, 0), "east": (1, 0, 0)}
DIRS6 = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
TRANSPARENT = ("glass", "glowstone", "air", "redstone_wire", "repeater", "button")


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _name(state):
    return state.split("[")[0].replace("minecraft:", "")


def _props(state):
    m = re.search(r"\[(.*)\]", state)
    return dict(kv.split("=") for kv in m.group(1).split(",")) if m else {}


class Sim:
    def __init__(self, blocks):
        self.b = {p: s for p, s in blocks.items()}
        self.name = {p: _name(s) for p, s in self.b.items()}
        self.rep = {}       # pos -> [delay, input_dir(vec), out_dir(vec)]
        for p, s in self.b.items():
            if self.name[p] == "repeater":
                pr = _props(s)
                inp = H[pr["facing"]]
                self.rep[p] = (int(pr["delay"]), inp, (-inp[0], 0, -inp[2]))
        self.wires = [p for p in self.b if self.name[p] == "redstone_wire"]
        self.wire_set = set(self.wires)
        self.notes = [p for p in self.b if self.name[p] == "note_block"]
        self.rep_out = {p: False for p in self.rep}
        self.pending = {}   # pos -> (tick, value)
        self.note_on = {p: False for p in self.notes}
        self._wire_links = {p: self._links(p) for p in self.wires}

    def opaque(self, p):
        n = self.name.get(p, "air")
        return not any(t in n for t in TRANSPARENT)

    def _links(self, p):
        """Verbindungen einer Redstone-Leitung: (Nachbar-Draht, Richtung) inkl. Treppen."""
        out = []
        for d, v in H.items():
            q = _add(p, v)
            if q in self.wire_set:
                out.append((q, v))
                continue
            up = _add(q, (0, 1, 0))
            if up in self.wire_set and not self.opaque(_add(p, (0, 1, 0))):
                out.append((up, v))
                continue
            down = _add(q, (0, -1, 0))
            if down in self.wire_set and not self.opaque(q):
                out.append((down, v))
                continue
            if q in self.rep:
                _, inp, outd = self.rep[q]
                if v in (inp, outd) or (-v[0], 0, -v[2]) in (inp, outd):
                    out.append((None, v))
        return out

    def _points(self, p):
        dirs = {v for _, v in self._wire_links[p]}
        if not dirs:
            return list(H.values())
        if len(dirs) == 1:
            v = next(iter(dirs))
            return [v, (-v[0], 0, -v[2])]
        return list(dirs)

    def step(self, t, button_on):
        strong = set()
        if button_on:
            strong.add(self.button_block)
        for p, on in self.rep_out.items():
            if on:
                q = _add(p, self.rep[p][2])
                if self.opaque(q):
                    strong.add(q)
        # Draht-Pegel
        level = {}
        dq = deque()
        for w in self.wires:
            src = 0
            for v in DIRS6:
                q = _add(w, v)
                if q in strong:
                    src = 15
                if q in self.rep and self.rep_out[q] and _add(q, self.rep[q][2]) == w:
                    src = 15
            if src:
                level[w] = 15
                dq.append(w)
        while dq:
            w = dq.popleft()
            for q, _ in self._wire_links[w]:
                if q is not None and level.get(q, 0) < level[w] - 1:
                    level[q] = level[w] - 1
                    dq.append(q)
        weak = set()
        for w, lv in level.items():
            if lv <= 0:
                continue
            below = _add(w, (0, -1, 0))
            if self.opaque(below):
                weak.add(below)
            for v in self._points(w):
                q = _add(w, v)
                if self.opaque(q):
                    weak.add(q)
        powered_block = strong | weak

        def wire_into(w, target):
            return level.get(w, 0) > 0 and any(_add(w, v) == target for v in self._points(w))

        # Repeater-Eingänge -> Ausgänge planen
        for p, (delay, inp, outd) in self.rep.items():
            q = _add(p, inp)
            on = (q in powered_block) or (level.get(q, 0) > 0) or \
                 (q in self.rep and self.rep_out[q] and _add(q, self.rep[q][2]) == p)
            if p in self.pending:
                continue
            if on != self.rep_out[p]:
                self.pending[p] = (t + delay, on)
        # Notenblöcke
        fired = []
        for n in self.notes:
            act = False
            for v in DIRS6:
                q = _add(n, v)
                if q in powered_block:
                    act = True
                elif q in self.rep and self.rep_out[q] and _add(q, self.rep[q][2]) == n:
                    act = True
                elif q in level and wire_into(q, n):
                    act = True
            if act and not self.note_on[n] and not self.opaque(_add(n, (0, 1, 0))):
                fired.append(n)
            self.note_on[n] = act
        # geplante Repeater-Änderungen anwenden (wirksam ab nächstem Tick)
        for p, (tt, val) in list(self.pending.items()):
            if tt <= t + 1:
                self.rep_out[p] = val
                del self.pending[p]
        return fired


def simulate_redstone(blocks, start_button, ticks, press_ticks=10):
    """Gibt {pos: [Ticks, an denen der Notenblock klingt]} zurück. Knopfdruck bei Tick 0."""
    sim = Sim(blocks)
    bx, by, bz = start_button
    sim.button_block = (bx, by - 1, bz)
    res = {}
    for t in range(ticks):
        for n in sim.step(t, t < press_ticks):
            res.setdefault(n, []).append(t)
    return res
