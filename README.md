# Noteblock Music Generator

Turns music (MIDI or audio like MP3) into a **Minecraft note block build guide**. There is no schematic file. You get a step-by-step plan instead:

- **which note block**: instrument and number of right-clicks (pitch)
- **which block underneath**: this sets the instrument
- **repeater delays** between steps (1–4 ticks)
- **a conductor line plus clocks**: repeated parts (beat loops, choruses) are built only once and then triggered several times

**Web UI:** https://jfsunday.github.io/noteblock-music/. It runs entirely in your browser: Python via Pyodide, audio transcription via Spotify basic-pitch. Nothing is uploaded.

## CLI

```bash
python -m venv .venv
.venv/bin/pip install mido numpy
# Audio support (optional):
.venv/bin/pip install "setuptools<81" onnxruntime librosa pretty_midi scipy "resampy<0.4.3" scikit-learn mir_eval
.venv/bin/pip install --no-deps basic-pitch

.venv/bin/python nbgen.py song.mid            # -> song_bauplan.md / .json / _vorschau.wav
.venv/bin/python nbgen.py song.mp3 --save-midi
.venv/bin/python nbgen.py song.mid --tempos   # list the tempo options
.venv/bin/python nbgen.py song.mid --tpb 5 --max-chord melodie=3 --instrument "Piano=bell" --layers beat,melodie
```

## How it works

1. **Load:** MIDI is read directly. Audio goes through basic-pitch to get notes, and drums are found by onset detection.
2. **Tempo:** 1 redstone tick = 0.1 s, so tempo = 600 / ticks-per-beat BPM. The tool picks the grid that best balances tempo deviation against timing error.
3. **Arrange:** GM program → note block instrument. Each track is shifted by octaves to fit the 2-octave range. Chord size is capped per layer (beat, bass, melody).
4. **Structure:** each layer, or each instrument/sound if that is cheaper, is cut into chunks and identical chunks become one *pattern*. Three or more identical chunks in a row become a *clock* (a repeater loop with an RS latch and a lock repeater).
5. **Circuit:** the conductor is a repeater chain with taps. Each tap starts a pattern or starts/stops a clock. All delays are computed so every note lands on the right tick.
6. **Self-test:** the circuit is simulated and compared note-for-note with the arrangement.

## 3D export (`--export`)

```bash
.venv/bin/python nbgen.py song.mid --export                        # compact (default), 4 floors
.venv/bin/python nbgen.py song.mid --export --schichten 5          # more floors = smaller footprint
.venv/bin/python nbgen.py song.mid --export --bauweise ausgerollt  # one note block per note
```

- **kompakt** (`sections.py` + `layout_reuse.py`): every track is cut into 1-bar patterns, and repeated runs of bars become sections. Tracks with identical start times are merged. Each section is built **once** and started by the conductor at every occurrence through a fixed "crossbar": the conductor passes run above the section inputs, so there is no free routing. Sections are spread over stacked floors, and one button starts everything via a stair. All delays are solved exactly (difference constraints / Bellman-Ford) and checked with `redstone_sim.py`. Final Countdown (full, 5:16): **3692 note blocks** instead of 8485, 106×136 footprint, 22 high.
- **ausgerollt** (`layout.py`): one note block per note, simplest circuit.

The web UI has the same choice ("Bauweise" + "Etagen"), a preview with real Minecraft note block sounds (`mc-*.ogg`), seeking, and per-track activity strips: click a strip to set the excerpt start, Shift+click to set the end. Each track also has "▶ Original" / "▶ Notenblock" buttons so you can find the riff/melody track by ear.

Files: `instruments.py` (instruments/blocks), `sources.py` (MIDI/audio), `arrange.py`, `planner.py` (patterns, clocks, conductor, simulation), `output.py` (Markdown/JSON), `pipeline.py` (shared by CLI + web), `preview.py` (WAV preview), `index.html`/`app.js`/`worker.js`/`style.css` (web UI).
