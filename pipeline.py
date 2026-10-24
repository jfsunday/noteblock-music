"""Gemeinsame Pipeline für CLI und Web-UI (Pyodide)."""
import json
import math
from dataclasses import replace

from auto import auto_config
from arrange import DEFAULT_MAX_CHORD, LAYERS, arrange, tempo_candidates
from instruments import INSTRUMENTS
from output import stats, to_json, to_markdown
from planner import build_plan, simulate


def run(song, tpb=None, max_chord=None, transpose=0, override=None, skip=(), layers=LAYERS,
        repeats=True, doubles=None, simple_drums=False, sustain=None, start_s=None, end_s=None,
        auto=True, keep=(), max_poly=None):
    """auto=True: Auto-Arrangeur (auto.py) schlägt Spuren/Instrumente/Tremolo vor,
    explizite Angaben überschreiben ihn. keep: Spuren trotz Auto-Auswahl behalten."""
    roles = {}
    if auto:
        cfg = auto_config(song)
        roles = cfg["roles"]
        skip = (set(cfg["skip"]) - set(keep)) | set(skip)
        override = {**cfg["override"], **(override or {})}
        doubles = {**cfg["doubles"], **(doubles or {})}
        sustain = {**cfg["sustain"], **(sustain or {})}
        simple_drums = simple_drums or cfg["simple_drums"]
        if not max_chord:
            max_chord = {"melodie": 3}
    max_chord = {k: int(v) for k, v in (max_chord or {}).items()}
    notes_backup = song.notes
    song.notes = [n for n in song.notes if n.track not in set(skip)]
    if start_s or end_s:   # Ausschnitt (Sekunden im Original), Start auf Taktanfang
        bpb = song.beats_per_bar
        b0 = math.floor((start_s or 0) * song.bpm / 60 / bpb) * bpb
        b1 = (end_s * song.bpm / 60) if end_s else float("inf")
        song.notes = [replace(n, beat=n.beat - b0) for n in song.notes if b0 <= n.beat < b1]
    try:
        if not song.notes:
            raise ValueError("Keine Noten (alle Spuren abgewählt?)")
        tpb = float(tpb or tempo_candidates(song)[0]["tpb"])
        tpb = int(tpb) if tpb.is_integer() else tpb
        nbnotes, track_cfg, dropped = arrange(song, tpb, max_chord, transpose, override or {},
                                             doubles, simple_drums, sustain, max_poly)
        nbnotes = [n for n in nbnotes if n.layer in set(layers)]
        if not nbnotes:
            raise ValueError("Keine Noten in den gewählten Layern")
        plan, placed = build_plan(nbnotes, tpb, song.beats_per_bar, allow_repeats=repeats)
    finally:
        song.notes = notes_backup
    played = simulate(plan)
    ok = sorted((n.tick, n.inst, n.clicks) for n in placed) == sorted((t, i, c) for t, i, c, _ in played)
    folded = [f"{t} → {INSTRUMENTS[i].name}" + (f" ({s:+d} Okt.)" if s else "")
              for (t, _), (i, s) in sorted(track_cfg.items())]
    info = {"dropped": dropped, "max_chord": {**DEFAULT_MAX_CHORD, **max_chord},
            "length_s": (max(n.tick for n in placed) + 1) / 10, "folded": folded,
            "layers": plan.layer_info, "selftest_ok": ok, "roles": roles}
    return {"song": song, "plan": plan, "placed": placed, "played": played, "info": info,
            "track_cfg": track_cfg, "tpb": tpb,
            "markdown": to_markdown(song, plan, None, info),
            "json": to_json(song, plan, info)}


# ---------- Web-Schnittstelle (wird aus app.js über Pyodide aufgerufen) ----------
_SONG = None


def web_load_midi(data, title):
    global _SONG
    from sources import load_midi
    if hasattr(data, "to_py"):
        data = data.to_py()
    _SONG = load_midi(None, data=bytes(data), title=title)
    return _describe()


def web_load_audio(title, bpm, melodic_json, drums_json):
    global _SONG
    from sources import song_from_audio
    _SONG = song_from_audio(title, float(bpm), json.loads(melodic_json), json.loads(drums_json))
    return _describe()


def _describe():
    song = _SONG
    cands = tempo_candidates(song)
    _, track_cfg, _ = arrange(song, cands[0]["tpb"])
    tracks = {}
    sec = 60 / song.bpm
    length_s = max((n.beat + n.duration) * sec for n in song.notes) if song.notes else 0
    for n in song.notes:
        t = tracks.setdefault(n.track, {"name": n.track, "notes": 0, "drum": n.drum, "inst": None,
                                        "orig": [], "activity": [0] * (int(length_s) + 1)})
        t["notes"] += 1
        t["orig"].append([round(n.beat * sec, 3), n.pitch, round(n.duration * sec, 3)])
        t["activity"][int(n.beat * sec)] += 1
    for (track, _), (inst, _) in track_cfg.items():
        tracks[track]["inst"] = inst
    return json.dumps({
        "title": song.title, "bpm": song.bpm, "source": song.source, "notes": len(song.notes),
        "length_s": length_s,
        "tempos": sorted(cands, key=lambda c: c["tpb"]), "recommended": cands[0]["tpb"],
        "tracks": list(tracks.values()),
        "auto": {k: v for k, v in auto_config(song).items()},
        "instruments": [{"key": i.key, "name": i.name, "block": i.block, "percussion": i.percussion}
                        for i in INSTRUMENTS.values()],
    })


_LAST = None


def web_generate(opts_json):
    global _LAST
    o = json.loads(opts_json)
    r = run(_SONG, auto=o.get("auto", False), tpb=o.get("tpb"), max_chord=o.get("max_chord"), transpose=o.get("transpose", 0),
            override=o.get("override"), skip=o.get("skip", []), layers=o.get("layers", LAYERS),
            repeats=o.get("repeats", True), doubles=o.get("doubles"),
            simple_drums=o.get("simple_drums", False), sustain=o.get("sustain"),
            start_s=o.get("start_s"), end_s=o.get("end_s"), max_poly=o.get("max_poly"))
    _LAST = r
    nb, reps, blocks = stats(r["plan"])
    return json.dumps({
        "markdown": r["markdown"], "json": r["json"], "info": r["info"], "tpb": r["tpb"],
        "stats": {"noteblocks": nb, "repeaters": reps, "patterns": len(r["plan"].patterns),
                  "clocks": len(r["plan"].clocks), "taps": len(r["plan"].taps),
                  "blocks": blocks.most_common()},
        "played": [[n.tick, INSTRUMENTS[n.inst].low + n.clicks, n.inst, n.clicks, n.track.split(" (+")[0]]
                   for n in sorted(r["placed"], key=lambda n: n.tick)],
    })


def build_3d(r, mode="kompakt", layers=4):
    """3D-Struktur aus einem run()-Ergebnis.
    kompakt: Abschnitte wiederverwenden (Kreuzschiene, ~halb so viele Notenblöcke), Etagen = layers.
    ausgerollt: jede Note ein eigener Notenblock (einfachste Schaltung)."""
    layers = int(layers or 1)
    if mode == "ausgerollt":
        from layout import build_layout
        return build_layout(r["played"], max_layers=layers)
    from sections import build_section_plan
    from layout_reuse import build_layout_reuse
    plan, _ = build_section_plan(r["placed"], r["tpb"], r["song"].beats_per_bar)
    return build_layout_reuse(plan, floors=layers)


def web_export(kind, max_layers=4, mode="kompakt"):
    """LiteCraft-JSON (Zwischenablage) oder .schem (base64) aus dem letzten Ergebnis."""
    import base64
    from layout import to_litecraft_json, to_schem
    r = _LAST
    lay = build_3d(r, mode, max_layers)
    if kind == "litecraft":
        text = to_litecraft_json(lay, r["song"].title, round(600 / r["tpb"], 1))
    else:
        text = base64.b64encode(to_schem(lay)).decode()
    nbs = sum(1 for st in lay["blocks"].values() if "note_block" in st)
    return json.dumps({"data": text, "size": lay["size"], "lines": lay["lines"], "layers": lay["layers"],
                       "noteblocks": nbs, "blocks": len(lay["blocks"])})
