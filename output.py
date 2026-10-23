"""Bauplan als Markdown + JSON ausgeben."""
import json
from collections import Counter

from arrange import LAYERS, LAYER_TITLES
from instruments import INSTRUMENTS, note_name
from planner import CLOCK_INJECT, split_delay


def fmt_delay(d):
    reps = split_delay(d)
    if not reps:
        return "–"
    fours = reps.count(4)
    rest = [r for r in reps if r != 4]
    parts = []
    if fours:
        parts.append(f"{fours}×4" if fours > 1 else "4")
    parts += [str(r) for r in rest]
    return " + ".join(parts)


def fmt_nb(inst, clicks):
    i = INSTRUMENTS[inst]
    tone = "" if i.percussion else f" ({note_name(i.low + clicks)})"
    return f"**{i.name}** auf {i.block.split(' (')[0]}, {clicks} Klicks{tone}"


def bar_pos(tick, plan):
    bpb = getattr(plan, "beats_per_bar", plan.bar_ticks / plan.tpb)
    b = tick / plan.tpb + 1e-9
    bar = int(b // bpb)
    inbar = b - bar * bpb
    beat = int(inbar)
    sub = round((inbar - beat) * plan.tpb)
    return f"T{bar + 1}.{beat + 1}" + (f"+{sub}" if sub else "")


def fmt_tpb(tpb):
    return f"{tpb:g}" if float(tpb).is_integer() else f"~{tpb:.2f}"


def stats(plan):
    nb = sum(len(ns) for p in plan.patterns.values() for _, _, ns in p.steps)
    reps = sum(p.repeaters for p in plan.patterns.values())
    prev = 0
    for tick, _ in plan.taps:
        reps += len(split_delay(tick - prev))
        prev = tick
    reps += sum(len(acts) for _, acts in plan.taps)                     # Dioden
    reps += sum(1 + len(split_delay(c.period - 1)) + 3 for c in plan.clocks)
    blocks = Counter()
    for p in plan.patterns.values():
        for _, _, ns in p.steps:
            for inst, _ in ns:
                blocks[INSTRUMENTS[inst].block.split(" (")[0]] += 1
    return nb, reps, blocks


def to_markdown(song, plan, cfg, info):
    nb, reps, blocks = stats(plan)
    bpm = 600 / plan.tpb
    L = []
    L.append(f"# Notenblock-Bauplan: {song.title}\n")
    L.append(f"- Quelle: {song.source.upper()}, Originaltempo **{song.bpm:.0f} BPM**")
    L.append(f"- Minecraft-Tempo: **{bpm:.1f} BPM** = {fmt_tpb(plan.tpb)} Redstone-Ticks pro Schlag "
             f"({plan.bar_ticks} Ticks pro Takt), Tempo-Faktor {bpm / song.bpm:.2f}"
             + ("" if float(plan.tpb).is_integer() else
                " – *Originaltempo exakt: Noten einzeln auf Ticks gerundet, Abstände leicht ungleichmäßig*"))
    L.append(f"- Länge: {info['length_s']:.0f} s · **{nb} Notenblöcke**, ca. **{reps} Repeater**, "
             f"{len(plan.patterns)} Pattern, {len(plan.clocks)} Clocks")
    if info["dropped"]:
        L.append(f"- {info['dropped']} Noten weggelassen (zu viele gleichzeitig, max. Akkord "
                 f"{info['max_chord']})")
    if info.get("folded"):
        L.append(f"- Spuren → Instrument: " + "; ".join(info["folded"]))
    L.append("\n**Material:** " + ", ".join(f"{v}× {k}" for k, v in blocks.most_common())
             + f", {nb}× Notenblock, ~{reps}× Repeater, Redstone, Baublöcke\n")

    L.append("## So funktioniert's\n")
    L.append("""- **1 Redstone-Tick = 0,1 s.** Repeater-Stufe 1–4 = 1–4 Ticks (Stufe 1 = frisch gesetzt, jeder Rechtsklick +1).
- **Notenblock-Ton:** „Klicks" = so oft rechtsklicken (0 = F♯, Grundton). Bei 24 ist das Maximum.
- **Instrument** = Block *unter* dem Notenblock. Über dem Notenblock muss **Luft** sein.
- **Pattern-Leitung** (ein Muster, das gebaut wird):
  `Eingang → Eingangs-Repeater (1) → [Repeater laut Tabelle] → Notenblock → [Repeater] → Notenblock → …`
  Ein Notenblock leitet das Signal weiter: der nächste Repeater zeigt einfach vom Notenblock weg.
  Bei „–" (0 Ticks Abstand) hängt der Notenblock direkt am vorherigen Punkt.
- **Akkorde** (mehrere Notenblöcke im selben Schritt): Der Repeater zeigt in Notenblock 1; von diesem
  Notenblock gehen kurze Redstone-Stücke zur Seite/nach unten zu den weiteren Notenblöcken
  (Redstone muss *in* den Notenblock zeigen bzw. auf ihm enden). Alle im selben Schritt klingen gleichzeitig.
- **Dirigent** (Hauptleitung): Startknopf → Puls-Verkürzer → lange Repeater-Kette. An jedem *Abgriff*
  zweigt Redstone ab zu den Pattern bzw. Clocks. **Jeder Abzweig endet mit einem Dioden-Repeater (1 Tick)**
  vor dem Ziel – so laufen Signale nicht zurück, und mehrere Abzweige dürfen in denselben Pattern-Eingang
  münden (Redstone-Stück als Eingang, alle Dioden zeigen darauf). Abzweige: nur Redstone (max. 15 Blöcke),
  **keine zusätzlichen Repeater**, sonst verschiebt sich der Takt.
- **Puls-Verkürzer:** Knopf → Komparator (Subtrahier-Modus) von hinten; dieselbe Leitung über einen
  1-Tick-Repeater von der Seite in den Komparator. Ergibt einen kurzen Puls (nötig für Clocks).
""")
    if plan.clocks:
        L.append(f"""- **Clock** (spielt ein Pattern mehrfach hintereinander): Schleife aus Repeatern, Gesamtverzögerung = Periode.
  Aufbau: Punkt **S** (Redstone) → **Sperr-Repeater** (1 Tick) → restliche Schleifen-Repeater → zurück zu S.
  Von S geht eine Diode (1 Tick) zum Pattern-Eingang.
  Steuerung über ein **RS-Latch** (z.B. zwei überkreuzte Fackeln): Ausgang Q̅ („aus"-Seite) → Repeater (1) →
  *seitlich* in den Sperr-Repeater. Latch muss vor Songstart auf **Reset** stehen (Clock gesperrt).
  - *Start-Abgriff* → Diode → Latch **Set**, **und** über Repeater mit {CLOCK_INJECT} Ticks gesamt (z.B. 4 + 2) in Punkt S (Startpuls).
  - *Stopp-Abgriff* → Diode → Latch **Reset**. Die Zeitpunkte sind so berechnet, dass genau die angegebene
    Zahl Durchläufe spielt. Falls einer zu viel/zu wenig: Stopp-Abgriff ±1–2 Ticks verschieben.
""")

    # Ablauf
    L.append("## Ablauf (Übersicht)\n")
    rows = [(t0, pid, "einmal") for t0, pid in plan.plays]
    rows += [(c.first_start, c.pattern, f"{c.runs}× per {c.cid}") for c in plan.clocks]
    for title, li in plan.layer_info.items():
        lrows = sorted(r for r in rows if r[1].rsplit("-", 1)[0] == title)
        if not lrows:
            continue
        unit = f"{li['bars']:g} Takt(e)" if li["bars"] else "ganzer Song"
        L.append(f"**{title}** (Pattern-Länge: {unit})  ")
        L.append(" → ".join(f"{bar_pos(t, plan)} {pid} ({how})" for t, pid, how in lrows) + "\n")

    # Dirigent
    L.append("## Dirigent (Hauptleitung)\n")
    L.append("Startknopf → Puls-Verkürzer → Abgriff 1 → Repeater → Abgriff 2 → …\n")
    L.append("| Abgriff | Repeater davor | Tick | Zeit | Abzweige (je mit Dioden-Repeater) |")
    L.append("|---|---|---|---|---|")
    prev = 0
    names = {"play": "▶ Pattern **{}** starten", "clock_start": "⏵ **{}** START (Latch Set + Startpuls)",
             "clock_stop": "⏹ **{}** STOPP (Latch Reset)"}
    for i, (tick, acts) in enumerate(plan.taps, 1):
        what = "<br>".join(names[k].format(r) for k, r in acts)
        L.append(f"| {i} | {fmt_delay(tick - prev)} | {tick} | {tick / 10:.1f} s | {what} |")
        prev = tick
    L.append("")

    if plan.clocks:
        L.append("## Clocks\n")
        L.append("| Clock | Pattern | Periode | Schleife (Sperr-Repeater + Rest) | Durchläufe | Start-Abgriff | Stopp-Abgriff |")
        L.append("|---|---|---|---|---|---|---|")
        tap_no = {tick: i for i, (tick, _) in enumerate(plan.taps, 1)}
        for c in plan.clocks:
            L.append(f"| {c.cid} | {c.pattern} | {c.period} Ticks | 1 + {fmt_delay(c.period - 1)} | "
                     f"{c.runs}× | #{tap_no[c.start_tap]} (Tick {c.start_tap}) | "
                     f"#{tap_no[c.stop_tap]} (Tick {c.stop_tap}) |")
        L.append("")

    L.append("## Pattern-Leitungen\n")
    for pid, p in plan.patterns.items():
        n = sum(len(ns) for _, _, ns in p.steps)
        L.append(f"### {pid}\n")
        L.append(f"{n} Notenblöcke, {len(p.steps)} Schritte, Länge {p.length} Ticks, wird {p.uses}× gespielt. "
                 f"Start: Eingang → Eingangs-Repeater (1).\n")
        L.append("| Schritt | Repeater davor | Tick im Pattern | Position | Notenblöcke (Instrument auf Block, Klicks) |")
        L.append("|---|---|---|---|---|")
        for si, (d, off, ns) in enumerate(p.steps, 1):
            L.append(f"| {si} | {fmt_delay(d)} | {off} | {bar_pos(off, plan)} | "
                     + "<br>".join(fmt_nb(i, c) for i, c in ns) + " |")
        L.append("")
    return "\n".join(L)


def to_json(song, plan, info):
    return json.dumps({
        "title": song.title, "source": song.source, "original_bpm": song.bpm,
        "ticks_per_beat": plan.tpb, "bpm": 600 / plan.tpb, "bar_ticks": plan.bar_ticks,
        "info": info,
        "conductor": [{"tick": t, "actions": [{"type": k, "target": r} for k, r in a]} for t, a in plan.taps],
        "clocks": [c.__dict__ for c in plan.clocks],
        "patterns": {pid: {"layer": p.layer, "length": p.length, "uses": p.uses,
                           "steps": [{"delay": d, "repeaters": split_delay(d), "tick": off,
                                      "noteblocks": [{"instrument": i, "block": INSTRUMENTS[i].block,
                                                      "clicks": c,
                                                      "note": None if INSTRUMENTS[i].percussion
                                                      else note_name(INSTRUMENTS[i].low + c)}
                                                     for i, c in ns]}
                                     for d, off, ns in p.steps]}
                     for pid, p in plan.patterns.items()},
    }, indent=1, ensure_ascii=False)
