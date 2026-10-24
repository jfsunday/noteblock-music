#!/usr/bin/env python3
"""Musik (MIDI oder Audio) -> Minecraft-Notenblock-Bauplan.

Beispiele:
  nbgen.py pigstep.midi
  nbgen.py song.mp3 --tpb 5
  nbgen.py pigstep.midi --tempos           # Tempo-Optionen anzeigen
  nbgen.py song.mid --instrument "Piano=bell" --max-chord melodie=3
"""
import argparse
import sys
from pathlib import Path

from arrange import DEFAULT_MAX_CHORD, LAYERS, tempo_candidates
from instruments import INSTRUMENTS
from output import stats
from pipeline import run
from sources import load


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="MIDI (.mid) oder Audio (.mp3/.wav/.ogg/.flac …)")
    ap.add_argument("-o", "--out", help="Ausgabe-Präfix (Standard: neben der Eingabedatei)")
    ap.add_argument("--tpb", type=float, help="Redstone-Ticks pro Schlag (Tempo = 600/tpb BPM)")
    ap.add_argument("--tempos", action="store_true", help="nur Tempo-Optionen anzeigen")
    ap.add_argument("--max-chord", action="append", default=[], metavar="LAYER=N",
                    help=f"max. gleichzeitige Notenblöcke pro Layer (Standard {DEFAULT_MAX_CHORD})")
    ap.add_argument("--transpose", type=int, default=0, help="Halbtöne transponieren")
    ap.add_argument("--instrument", action="append", default=[], metavar="SPUR=INSTRUMENT",
                    help=f"Instrument einer Spur erzwingen, optional mit Oktave, z.B. MELODY=harp+1 ({', '.join(INSTRUMENTS)})")
    ap.add_argument("--double", action="append", default=[], metavar="SPUR=INSTRUMENT",
                    help="Spur zusätzlich mit 2. Instrument spielen (lauter), z.B. MELODY=bell+2")
    ap.add_argument("--sustain", action="append", default=[], metavar="SPUR=TICKS",
                    help="lange Töne alle N Ticks neu anschlagen (Tremolo statt kurzem Pling), z.B. STRINGS=2")
    ap.add_argument("--from", dest="start_s", type=float, help="Ausschnitt ab Sekunde (Original)")
    ap.add_argument("--to", dest="end_s", type=float, help="Ausschnitt bis Sekunde (Original)")
    ap.add_argument("--schichten", type=int, default=3, help="3D-Export: max. Schichten übereinander (Standard 3)")
    ap.add_argument("--export", action="store_true", help="zusätzlich 3D-Struktur als .schem + LiteCraft-JSON schreiben")
    ap.add_argument("--max-gleichzeitig", dest="max_poly", type=int, default=0,
                    help="höchstens N Notenblöcke gleichzeitig (weniger Leitungen = kompakter, ändert den Klang; 0 = aus = Standard)")
    ap.add_argument("--manual", action="store_true", help="Auto-Arrangeur aus (alle Spuren, nichts automatisch)")
    ap.add_argument("--keep", action="append", default=[], metavar="SPUR", help="Spur trotz Auto-Auswahl behalten")
    ap.add_argument("--simple-drums", action="store_true", help="nur Kick/Snare/Clap")
    ap.add_argument("--sounds", help="Minecraft-assets-Ordner für echte Sounds (Standard: automatisch suchen)")
    ap.add_argument("--skip", action="append", default=[], metavar="SPUR", help="Spur weglassen")
    ap.add_argument("--layers", default=",".join(LAYERS), help="welche Layer bauen (beat,bass,melodie)")
    ap.add_argument("--no-repeats", action="store_true", help="keine Pattern/Clocks, alles als eine Leitung")
    ap.add_argument("--no-preview", action="store_true", help="keine WAV-Hörprobe")
    ap.add_argument("--no-drums", action="store_true", help="Audio: kein Schlagzeug erkennen")
    ap.add_argument("--save-midi", action="store_true", help="Audio: erkannte Noten zusätzlich als MIDI speichern")
    a = ap.parse_args()

    src = Path(a.input)
    song = load(str(src), drums=not a.no_drums)
    if a.skip:
        song.notes = [n for n in song.notes if n.track not in a.skip]
    if not song.notes:
        sys.exit("Keine Noten gefunden.")
    tracks = sorted({n.track for n in song.notes})
    print(f"{song.title}: {len(song.notes)} Noten, {song.bpm:.1f} BPM, Spuren: {', '.join(tracks)}")

    cands = tempo_candidates(song)
    if a.tempos:
        print("\nTicks/Schlag  BPM     Tempo-Abw.  Raster-Fehler (Schläge)")
        for c in sorted(cands, key=lambda c: c["tpb"]):
            mark = "  <- Empfehlung" if c is cands[0] else ""
            print(f"  {c['tpb']:>6g}    {c['bpm']:6.1f}  {c['tempo_dev'] * 100:6.1f} %   {c['grid_err']:.3f}{mark}{'  (Originaltempo exakt)' if c.get('exact') else ''}")
        return
    max_chord = {k: int(v) for k, v in (x.split("=") for x in a.max_chord)}
    override = dict(x.split("=", 1) for x in a.instrument)
    r = run(song, tpb=a.tpb, max_chord=max_chord, transpose=a.transpose, override=override,
            layers=a.layers.split(","), repeats=not a.no_repeats,
            doubles=dict(x.split("=", 1) for x in a.double), simple_drums=a.simple_drums,
            sustain=dict(x.split("=", 1) for x in a.sustain), start_s=a.start_s, end_s=a.end_s,
            auto=not a.manual, keep=a.keep, max_poly=a.max_poly)
    plan, placed, played, tpb = r["plan"], r["placed"], r["played"], r["tpb"]
    if r["info"]["roles"]:
        print("Auto-Arrangement: " + ", ".join(f"{t} = {role}" for t, role in sorted(r["info"]["roles"].items())))
    if not r["info"]["selftest_ok"]:
        print("WARNUNG: Simulation der Schaltung weicht von den Noten ab!")

    base = Path(a.out) if a.out else src.with_suffix("")
    md = base.with_name(base.name + "_bauplan.md")
    md.write_text(r["markdown"], encoding="utf-8")
    base.with_name(base.name + "_bauplan.json").write_text(r["json"], encoding="utf-8")
    print(f"Tempo: {600 / tpb:.1f} BPM ({tpb:g} Ticks/Schlag), {len(placed)} Noten → {stats(plan)[0]} Notenblöcke zu bauen, "
          f"{len(plan.patterns)} Pattern, {len(plan.clocks)} Clocks, {len(plan.taps)} Dirigent-Abgriffe")
    for layer, li in plan.layer_info.items():
        unit = f"{li['bars']:g} Takt(e)" if li['bars'] else "ganzer Song"
        print(f"  {layer}: {li['notes']} Noten, {li['patterns']} Pattern à {unit}")
    print(f"→ {md}")
    if not a.no_preview:
        from preview import render
        wav = base.with_name(base.name + "_vorschau.wav")
        real = render(played, wav, assets=a.sounds)
        print(f"→ {wav} (Hörprobe, {'echte Minecraft-Sounds' if real else 'Synth-Ersatzsounds'})")
    if a.export:
        from layout import build_layout, to_litecraft_json, to_schem
        lay = build_layout(played, max_layers=a.schichten)
        base.with_name(base.name + ".schem").write_bytes(to_schem(lay))
        base.with_name(base.name + "_litecraft.json").write_text(
            to_litecraft_json(lay, song.title, round(600 / tpb, 1)), encoding="utf-8")
        w, h, l = lay["size"]
        print(f"→ 3D: {base.name}.schem + _litecraft.json ({w}×{l} Grundfläche, {h} hoch, "
              f"{lay['layers']} Schicht(en), {len(lay['schedule'])} Notenblöcke)")
    if a.save_midi and song.source == "audio":
        save_midi(song, base.with_name(base.name + "_erkannt.mid"))


def save_midi(song, path):
    import mido
    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(song.bpm)))
    evs = []
    for n in song.notes:
        ch = 9 if n.drum else 0
        t = int(n.beat * 480)
        evs += [(t, 1, mido.Message("note_on", note=n.pitch, velocity=n.velocity, channel=ch)),
                (t + 120, 0, mido.Message("note_off", note=n.pitch, channel=ch))]
    last = 0
    for t, _, m in sorted(evs, key=lambda e: (e[0], e[1])):
        tr.append(m.copy(time=t - last))
        last = t
    mid.save(str(path))
    print(f"→ {path}")


if __name__ == "__main__":
    main()
