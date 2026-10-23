"""3D-Layout des Songs als echte Blockstruktur (für LiteCraft / .schem).

Bewusst einfach und robust (keine Pattern/Clocks, alles ausgerollt):

  y=0  Boden (Stein) + Instrument-Blöcke unter den Notenblöcken
  y=1  Startleitung (Redstone entlang +Z bei x=0, alle 15 Blöcke ein 1-Tick-Repeater)
       Stimmen-Leitungen entlang +X, je 4 Blöcke Abstand in Z:
         Repeater(n) -> Block S -> Repeater(n) -> Block S ...
       Ein Repeater, der in S zeigt, powert S stark; die Notenblöcke direkt
       links/rechts von S (z±1) werden dadurch aktiviert (max. 2 pro Schritt).
  y=2  Knopf auf dem Startblock (0,1,-1)

Repeater-Blockstate: `facing` zeigt zum EINGANG (Minecraft-Konvention), Signal läuft
also bei facing=west nach Osten (+X).
Zeitplan: Notenblock bei (x,1,z) klingt `tick` Redstone-Ticks nach dem Knopfdruck.
"""
import gzip
import io
import json
import struct
from collections import defaultdict

from instruments import INSTRUMENTS
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
LINE_SPACING = 4
BUS_REPEATER_EVERY = 16                 # Repeater bei z = 14, 30, 46 … (zwischen den Leitungen)
PER_STEP = 2                            # Notenblöcke pro Schritt (links + rechts)


def _repeater(delay, facing):
    return f"minecraft:repeater[delay={delay},facing={facing},locked=false,powered=false]"


def _note_block(inst, clicks):
    return f"minecraft:note_block[instrument={inst},note={clicks},powered=false]"


def build_layout(played):
    """played: [(tick, inst, clicks, ...)] (Song-Ticks ab 0) -> dict mit blocks, schedule, meta."""
    # 1) Noten auf Leitungen verteilen: pro Instrument, je Zeitpunkt max. 2 pro Leitung
    by_inst = defaultdict(lambda: defaultdict(list))
    for tick, inst, clicks, *_ in played:
        if clicks not in by_inst[inst][tick]:
            by_inst[inst][tick].append(clicks)
    lines = []       # [(inst, {tick: [clicks…]})]
    for inst in sorted(by_inst):
        width = max(len(v) for v in by_inst[inst].values())
        for k in range(0, width, PER_STEP):
            steps = {t: sorted(v)[k:k + PER_STEP] for t, v in by_inst[inst].items() if len(v) > k}
            lines.append((inst, steps))

    blocks = {}

    def put(x, y, z, state):
        blocks[(x, y, z)] = state

    # 2) Startleitung bei x=0 entlang +Z
    zmax = (len(lines) - 1) * LINE_SPACING
    put(0, 0, -1, FLOOR)
    put(0, 1, -1, "minecraft:stone")                       # Startblock
    put(0, 2, -1, "minecraft:stone_button[face=floor,facing=north,powered=false]")
    bus_delay = {}
    delay = 0
    line_z = {i * LINE_SPACING for i in range(len(lines))}
    for z in range(0, zmax + 1):
        put(0, 0, z, FLOOR)
        if z % BUS_REPEATER_EVERY == BUS_REPEATER_EVERY - 2 and z < zmax:
            put(0, 1, z, _repeater(1, "north"))             # Signal läuft nach +Z (Süden)
            delay += 1
        else:
            east = "side" if z in line_z else "none"
            put(0, 1, z, f"minecraft:redstone_wire[east={east},north=side,power=0,south=side,west=none]")
        bus_delay[z] = delay
    offset = 1 + max(bus_delay.values(), default=0)        # alle Noten um diesen Wert verschoben

    # 3) Stimmen-Leitungen entlang +X
    schedule = []
    for i, (inst, steps) in enumerate(lines):
        z = i * LINE_SPACING
        x = 1
        prev = bus_delay[z]                                 # Zeitpunkt, zu dem das Signal hier ist
        for tick in sorted(steps):
            target = tick + offset
            for d in split_delay(target - prev):
                put(x, 0, z, FLOOR)
                put(x, 1, z, _repeater(d, "west"))
                x += 1
            put(x, 0, z, FLOOR)
            put(x, 1, z, S_BLOCK)
            for side, clicks in zip((1, -1), steps[tick]):
                put(x, 0, z + side, INST_BLOCK[inst])
                put(x, 1, z + side, _note_block(inst, clicks))
                schedule.append([target, x, 1, z + side, inst, clicks])
            prev = target
            x += 1

    xs = [p[0] for p in blocks]
    ys = [p[1] for p in blocks]
    zs = [p[2] for p in blocks]
    mn = (min(xs), min(ys), min(zs))
    norm = {(x - mn[0], y - mn[1], z - mn[2]): s for (x, y, z), s in blocks.items()}
    for s in schedule:
        s[1] -= mn[0]; s[2] -= mn[1]; s[3] -= mn[2]
    size = (max(xs) - mn[0] + 1, max(ys) - mn[1] + 1, max(zs) - mn[2] + 1)
    start = (0 - mn[0], 2 - mn[1], -1 - mn[2])
    return {"blocks": norm, "size": size, "schedule": sorted(schedule), "start_button": start,
            "lines": len(lines), "offset": offset}


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
