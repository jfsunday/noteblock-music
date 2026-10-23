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
        auto=True, keep=()):
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
                                             doubles, simple_drums, sustain)
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
    for n in song.notes:
        t = tracks.setdefault(n.track, {"name": n.track, "notes": 0, "drum": n.drum, "inst": None})
        t["notes"] += 1
    for (track, _), (inst, _) in track_cfg.items():
        tracks[track]["inst"] = inst
    return json.dumps({
        "title": song.title, "bpm": song.bpm, "source": song.source, "notes": len(song.notes),
        "tempos": sorted(cands, key=lambda c: c["tpb"]), "recommended": cands[0]["tpb"],
        "tracks": list(tracks.values()),
        "instruments": [{"key": i.key, "name": i.name, "block": i.block, "percussion": i.percussion}
                        for i in INSTRUMENTS.values()],
    })


def web_generate(opts_json):
    o = json.loads(opts_json)
    r = run(_SONG, tpb=o.get("tpb"), max_chord=o.get("max_chord"), transpose=o.get("transpose", 0),
            override=o.get("override"), skip=o.get("skip", []), layers=o.get("layers", LAYERS),
            repeats=o.get("repeats", True), doubles=o.get("doubles"),
            simple_drums=o.get("simple_drums", False), sustain=o.get("sustain"),
            start_s=o.get("start_s"), end_s=o.get("end_s"))
    nb, reps, blocks = stats(r["plan"])
    return json.dumps({
        "markdown": r["markdown"], "json": r["json"], "info": r["info"], "tpb": r["tpb"],
        "stats": {"noteblocks": nb, "repeaters": reps, "patterns": len(r["plan"].patterns),
                  "clocks": len(r["plan"].clocks), "taps": len(r["plan"].taps),
                  "blocks": blocks.most_common()},
        "played": [[t, INSTRUMENTS[i].low + c, i] for t, i, c, _ in r["played"]],
    })
