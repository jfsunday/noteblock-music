"""3D-Layout des Songs als echte Blockstruktur (für LiteCraft / .schem).

Kompakt und robust (keine Pattern/Clocks, alles ausgerollt):

* Jede Stimme ist eine Leitung  Repeater(n) -> Block S -> Repeater(n) -> Block S …
  Ein Repeater, der in S zeigt, powert S stark; die Notenblöcke direkt links/rechts
  von S (z±1) werden dadurch aktiviert (max. 2 pro Schritt). Instrumentblock drunter.
* Leitungen laufen in Bahnen hin und her (Schlange, Bahnabstand 4 in Z). Die Kurve am
  Bahnende ist reines Redstone (0 Ticks), gefolgt von einem Repeater.
* Bis zu `max_layers` Schichten übereinander (je 3 Blöcke hoch: Boden, Technik, Luft).
  Jede Schicht hat eine Startleitung bei x=0 (+Z, 1-Tick-Repeater alle 16 Blöcke),
  die Schichten sind über eine Redstone-Treppe hinter dem Start verbunden.
* Start: Knopf auf dem Block (0,1,-2).

Repeater-Blockstate: `facing` zeigt zum EINGANG (Minecraft-Konvention).
Zeitplan: Notenblock klingt `tick` Redstone-Ticks nach dem Knopfdruck. Wird mit
`simulate_redstone()` (kleiner Redstone-Simulator) gegengeprüft.
"""
import gzip
import io
import json
import math
import struct
from collections import defaultdict

from planner import split_delay

INST_BLOCK = {
    "harp": "minecraft:dirt", "bass": "minecraft:oak_planks", "didgeridoo": "minecraft:pumpkin",
    "guitar": "minecraft:white_wool", "flute": "minecraft:clay", "bell": "minecraft:gold_block",
    "chime": "minecraft:packed_ice", "xylophone": "minecraft:bone_block[axis=y]",
    "iron_xylophone": "minecraft:iron_block", "cow_bell": "minecraft:soul_sand",
    "bit": "minecraft:emerald_block", "banjo": "minecraft:hay_block[axis=y]",
    "pling": "minecraft:glowstone", "basedrum": "minecraft:stone", "snare": "minecraft:sand",
    "hat": "minecraft:glass",
}
FLOOR = "minecraft:stone"
S_BLOCK = "minecraft:smooth_stone"      # leitender Block, den der Repeater powert
ROW = 3                                 # Bahnabstand in Z (Notenblöcke bei z±1 berühren nur ihren Schaltblock)
BUS_REPEATER_EVERY = 16                 # Repeater bei z = 14, 30, 46 … (nie auf einer Bahn)
PER_STEP = 3                            # Notenblöcke pro Schritt: links, rechts + oben auf dem Schaltblock
LAYER_H = 4                             # Boden, Technik, Notenblock oben, Luft
NON_CONDUCTIVE = ("glass", "glowstone", "soul_sand")   # können nicht als Schaltblock dienen


def _conductive(inst):
    return not any(n in INST_BLOCK[inst] for n in NON_CONDUCTIVE)
X0 = 3                                  # erste Spalte der Bahnen (x=0 Bus, x=1 Einspeisung, x=2 Kurven)
DIR = {1: "west", -1: "east"}           # Repeater-facing (= Eingangsseite) je Laufrichtung


def _repeater(delay, facing):
    return f"minecraft:repeater[delay={delay},facing={facing},locked=false,powered=false]"


def _note_block(inst, clicks):
    return f"minecraft:note_block[instrument={inst},note={clicks},powered=false]"


def _wire():
    return "minecraft:redstone_wire[east=none,north=none,power=0,south=none,west=none]"


def _split_lines(played):
    """Noten auf Leitungen verteilen. Instrumente dürfen sich eine Leitung teilen (das Instrument
    kommt nur vom Block unter dem Notenblock) -> so wenige, dichte Leitungen wie möglich:
    Leitung k bekommt zu jedem Zeitpunkt die Noten 2k und 2k+1."""
    by_tick = defaultdict(set)
    for tick, inst, clicks, *_ in played:
        by_tick[tick].add((inst, clicks))
    lines = defaultdict(dict)
    for t, v in by_tick.items():
        cond = sorted(n for n in v if _conductive(n[0]))
        nonc = sorted(n for n in v if not _conductive(n[0]))
        k = 0
        while cond or nonc:
            top = cond.pop(0) if (cond and PER_STEP >= 3) else None
            sides = []
            while len(sides) < 2 and (nonc or cond):
                sides.append(nonc.pop(0) if nonc else cond.pop(0))
            lines[k][t] = {"sides": sides, "top": top}
            k += 1
    return [(None, lines[k]) for k in sorted(lines)]


def _tokens(steps, start_time):
    """Leitung als Token-Folge: ('R', delay) / ('S', tick, [clicks])."""
    toks, prev = [], start_time
    for tick in sorted(steps):
        for d in split_delay(tick - prev):
            toks.append(("R", d))
        toks.append(("S", tick, steps[tick]))
        prev = tick
    return toks


def _turn_here(x, d, nxt, width):
    """Bahn nach Spalte x beenden? Nur vor einem Repeater und so, dass die Bahn
    zwischen X0 und X0+width-1 bleibt (nach einem S kommt immer ein R -> 1 Puffer reicht)."""
    remaining = (X0 + width - 1 - x) if d == 1 else (x - X0)
    return nxt is not None and nxt[0] == "R" and remaining <= 1


def _rows_needed(toks, width):
    rows, x, d = 1, X0, 1
    for i in range(len(toks)):
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if _turn_here(x, d, nxt, width):
            rows, d = rows + 1, -d
        else:
            x += d
    return rows


def build_layout(played, max_layers=3, width=None):
    """played: [(tick, inst, clicks, ...)] -> dict mit blocks, schedule, size, …"""
    lines = _split_lines(played)
    # Sehr lange Leitungen abwechselnd aufteilen (Schritt 1,3,5… / 2,4,6…), damit sie sich
    # gleichmäßig auf die Schichten verteilen lassen
    target = max_layers
    for _ in range(64):
        lens = [len(_tokens(st, 0)) for _, st in lines]
        i = max(range(len(lines)), key=lambda j: lens[j])
        if len(lines) >= target and lens[i] <= 1.6 * sum(lens) / target:
            break
        if len(lines[i][1]) < 2 or len(lines) >= 4 * max_layers + 8:
            break
        ticks = sorted(lines[i][1])
        a = {t: lines[i][1][t] for t in ticks[0::2]}
        b = {t: lines[i][1][t] for t in ticks[1::2]}
        lines[i:i + 1] = [(None, a), (None, b)]
    # grobe Leitungslängen für Breite/Schichten (Startzeit egal, nur Länge)
    lengths = [len(_tokens(st, 0)) for _, st in lines]
    layers = max(1, min(max_layers, len(lines)))

    # Leitungen gleichmäßig auf Schichten verteilen (nach Länge)
    order = sorted(range(len(lines)), key=lambda i: -lengths[i])
    load = [0] * layers
    assign = defaultdict(list)
    for i in order:
        k = min(range(layers), key=lambda j: load[j])
        assign[k].append(i)
        load[k] += lengths[i]
    layers = len([k for k in assign if assign[k]]) or 1
    if width is None:   # Grundfläche ~ quadratisch für die vollste Schicht: W ≈ sqrt(ROW * Länge)
        width = max(16, int(math.sqrt(ROW * max(load))))
    rows_est = [_rows_needed(_tokens(st, 0), width) for _, st in lines]

    blocks = {}

    def put(x, y, z, state):
        blocks[(x, y, z)] = state

    def place_first_rows():
        fz = {}
        for k in range(layers):
            z = 0
            for i in sorted(assign[k]):
                fz[i] = z
                z += ROW * rows_est[i]
        return fz
    first_z = place_first_rows()

    # Startbereich + Treppen zwischen den Schichten
    put(0, 0, -2, FLOOR)
    put(0, 1, -2, "minecraft:stone")                                     # E0 (Knopfblock)
    put(0, 2, -2, "minecraft:stone_button[face=floor,facing=north,powered=false]")
    for k in range(layers):
        y = LAYER_H * k + 1
        put(0, y - 1, -1, FLOOR)
        put(0, y, -1, _repeater(1, "north"))                             # Einspeisung Schicht k
        if k + 1 < layers:                                               # Treppe zu Schicht k+1
            H = LAYER_H
            put(0, y, -3, _repeater(1, "south")); put(0, y - 1, -3, FLOOR)
            put(0, y, -4, _wire()); put(0, y - 1, -4, FLOOR)
            for k2 in range(1, H + 1):                                   # Redstone-Treppe hoch
                put(0, y + k2, -4 - k2, _wire()); put(0, y + k2 - 1, -4 - k2, FLOOR)
            ztop = -4 - H
            put(-1, y + H, ztop, _wire()); put(-1, y + H - 1, ztop, FLOOR)
            for z in range(ztop, -1):                                    # Rückweg bei x=-2
                put(-2, y + H, z, _wire()); put(-2, y + H - 1, z, FLOOR)
            put(-1, y + H, -2, _wire()); put(-1, y + H - 1, -2, FLOOR)
            put(0, y + H, -2, _wire())                                   # verteilt auf Einspeisung + nächste Treppe
            put(0, y + H - 1, -2, FLOOR)

    # Bahnzahl mit echten Startzeiten nachrechnen, bis stabil
    for _ in range(6):
        entry_time = _bus_times_formula(layers, assign, first_z)
        offset = 2 + max(entry_time.values(), default=0)
        real = {i: _rows_needed(_tokens({t + offset: v for t, v in lines[i][1].items()},
                                        entry_time[i] + 1), width) for i in range(len(lines))}
        if all(real[i] <= rows_est[i] for i in real):
            break
        rows_est = [max(rows_est[i], real[i]) for i in range(len(lines))]
        first_z = place_first_rows()

    # Startleitungen je Schicht
    for k in range(layers):
        y = LAYER_H * k + 1
        zmax = max((first_z[i] for i in assign[k]), default=0)
        breps = set(_bus_reps({first_z[i] for i in assign[k]}, zmax))
        for z in range(0, zmax + 1):
            put(0, y - 1, z, FLOOR)
            if z in breps:
                put(0, y, z, _repeater(1, "north"))
            else:
                put(0, y, z, _wire())

    assert entry_time == _bus_times(blocks, layers, assign, first_z)

    schedule = []
    for k in range(layers):
        y = LAYER_H * k + 1
        for i in assign[k]:
            inst, steps = lines[i]
            z = first_z[i]
            shifted = {t + offset: v for t, v in steps.items()}
            # Einspeise-Repeater bei x=1 (+1 Tick), Redstone bei x=2, dann Bahn ab x=3
            toks = _tokens(shifted, entry_time[i] + 1)
            put(1, y - 1, z, FLOOR); put(1, y, z, _repeater(1, "west"))
            put(2, y - 1, z, FLOOR); put(2, y, z, _wire())
            x, d = X0, 1
            for ti, tok in enumerate(toks):
                put(x, y - 1, z, FLOOR)
                if tok[0] == "R":
                    put(x, y, z, _repeater(tok[1], DIR[d]))
                else:
                    _, tick, notes = tok
                    del blocks[(x, y - 1, z)]                            # S braucht keinen Boden
                    top = notes["top"]
                    put(x, y, z, INST_BLOCK[top[0]] if top else S_BLOCK)  # Schaltblock = Instrument oben
                    if top:
                        put(x, y + 1, z, _note_block(*top))
                        schedule.append([tick, x, y + 1, z, top[0], top[1]])
                    for side, (ninst, clicks) in zip((1, -1), notes["sides"]):
                        put(x, y - 1, z + side, INST_BLOCK[ninst])
                        put(x, y, z + side, _note_block(ninst, clicks))
                        schedule.append([tick, x, y, z + side, ninst, clicks])
                nxt = toks[ti + 1] if ti + 1 < len(toks) else None
                if _turn_here(x, d, nxt, width):
                    xt = x + d                                           # Kurve: Redstone 0 Ticks
                    for dz in range(ROW + 1):
                        put(xt, y - 1, z + dz, FLOOR)
                        put(xt, y, z + dz, _wire())
                    z += ROW
                    d = -d
                    # x bleibt: nächste Bahn beginnt in derselben Spalte, Gegenrichtung
                else:
                    x += d

    xs = [p[0] for p in blocks]; ys = [p[1] for p in blocks]; zs = [p[2] for p in blocks]
    mn = (min(xs), min(ys), min(zs))
    norm = {(x - mn[0], y - mn[1], z - mn[2]): s for (x, y, z), s in blocks.items()}
    for s in schedule:
        s[1] -= mn[0]; s[2] -= mn[1]; s[3] -= mn[2]
    size = (max(xs) - mn[0] + 1, max(ys) - mn[1] + 1, max(zs) - mn[2] + 1)
    start = (0 - mn[0], 2 - mn[1], -2 - mn[2])
    return {"blocks": norm, "size": size, "schedule": sorted(schedule), "start_button": start,
            "lines": len(lines), "layers": layers, "width": width, "offset": offset}


def _bus_reps(first_rows, zmax):
    """Repeater-Positionen der Startleitung: spätestens alle 16 Blöcke, nie auf einer Einspeise-Bahn."""
    reps, last = [], -1
    while last + 16 <= zmax:
        z = last + 16
        while z in first_rows:
            z -= 1
        reps.append(z)
        last = z
    return reps


def _bus_times_formula(layers, assign, first_z):
    """Wie _bus_times, aber ohne Blöcke: Repeater liegen bei z ≡ 14 (mod 16)."""
    times = {}
    for k in range(layers):
        for i in assign[k]:
            z = first_z[i]
            rows = {first_z[j] for j in assign[k]}
            reps = _bus_reps(rows, max(rows))
            times[i] = k + 1 + sum(1 for zz in reps if zz < z)
    return times


def _bus_times(blocks, layers, assign, first_z):
    """Zeit (Ticks nach Knopfdruck), zu der das Signal an der Startleitung bei z=first_z ankommt."""
    times = {}
    for k in range(layers):
        t = k + 1                     # Treppen: je Schicht ein 1-Tick-Repeater, plus Einspeise-Repeater
        y = LAYER_H * k + 1
        bus = {}
        for z in range(0, max((first_z[i] for i in assign[k]), default=0) + 1):
            if "repeater" in blocks.get((0, y, z), ""):
                t += 1
            bus[z] = t
        for i in assign[k]:
            times[i] = bus[first_z[i]]
    return times


# ---------------- Export: LiteCraft-Zwischenablage (JSON) ----------------
def to_litecraft_json(layout, title, bpm):
    palette, index, data = [], {}, []
    for (x, y, z), state in sorted(layout["blocks"].items()):
        if state not in index:
            index[state] = len(palette)
            palette.append(state)
        data.append([x, y, z, index[state]])
    return json.dumps({
        "format": "litecraft-structure", "version": 1,
        "name": f"Notenblock: {title}", "size": list(layout["size"]),
        "palette": palette, "blocks": data,
        "noteblock": {
            "title": title, "bpm": bpm, "tick_seconds": 0.1,
            "start_button": list(layout["start_button"]),
            "schedule": [{"tick": t, "pos": [x, y, z], "instrument": i, "note": c}
                         for t, x, y, z, i, c in layout["schedule"]],
        },
    }, separators=(",", ":"))


# ---------------- Export: Sponge-Schematic v2 (.schem) ----------------
def _nbt(tag_type, name, value, out):
    out.write(bytes([tag_type]))
    nb = name.encode()
    out.write(struct.pack(">H", len(nb)) + nb)
    _payload(tag_type, value, out)


def _payload(t, v, out):
    if t == 2:
        out.write(struct.pack(">h", v))
    elif t == 3:
        out.write(struct.pack(">i", v))
    elif t == 7:
        out.write(struct.pack(">i", len(v)) + bytes(v))
    elif t == 8:
        b = v.encode()
        out.write(struct.pack(">H", len(b)) + b)
    elif t == 9:
        out.write(bytes([10]) + struct.pack(">i", len(v)))
        for comp in v:
            for (ct, cn, cv) in comp:
                _nbt(ct, cn, cv, out)
            out.write(b"\x00")
    elif t == 10:
        for (ct, cn, cv) in v:
            _nbt(ct, cn, cv, out)
        out.write(b"\x00")
    elif t == 11:
        out.write(struct.pack(">i", len(v)) + b"".join(struct.pack(">i", i) for i in v))


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return out


def to_schem(layout, data_version=4786):
    w, h, l = layout["size"]
    palette = {"minecraft:air": 0}
    grid = [0] * (w * h * l)
    for (x, y, z), state in layout["blocks"].items():
        pid = palette.setdefault(state, len(palette))
        grid[x + z * w + y * w * l] = pid
    data = bytearray()
    for v in grid:
        data += _varint(v)
    root = [
        (3, "Version", 2), (3, "DataVersion", data_version),
        (2, "Width", w), (2, "Height", h), (2, "Length", l),
        (3, "PaletteMax", len(palette)),
        (10, "Palette", [(3, k, v) for k, v in palette.items()]),
        (7, "BlockData", data),
        (9, "BlockEntities", []),
        (11, "Offset", [0, 0, 0]),
    ]
    buf = io.BytesIO()
    _nbt(10, "Schematic", root, buf)
    return gzip.compress(buf.getvalue())


def build_layout_fit(played, footprint=50, max_height=None):
    """Kleinstmögliche Schichtenzahl, sodass die Grundfläche höchstens footprint × footprint ist."""
    width = max(16, footprint - 6)
    best = None
    for layers in range(1, 64):
        L = build_layout(played, max_layers=layers, width=width)
        best = L
        if L["size"][0] <= footprint and L["size"][2] <= footprint:
            return L
        if L["layers"] < layers:          # mehr Schichten bringen nichts mehr
            break
    return best
