Feature for LiteCraft: **paste and play note block songs from the "Notenblock-Generator"** (https://jfsunday.github.io/noteblock-music/, repo `~/Projekte/noteblock-music`).

## Goal
In the note block generator I click "Für LiteCraft kopieren". Then in LiteCraft I press Ctrl+V and the song appears as a real block structure. I press the button at the start (or a "Play" button in the UI) and the song plays with the real Minecraft note block sounds, driven by an actual redstone simulation, not just a replay of a list.

## 1. Paste from the clipboard (Ctrl+V / menu "Einfügen")
The clipboard contains JSON in this format:
```json
{
  "format": "litecraft-structure", "version": 1,
  "name": "Notenblock: Final Countdown", "size": [w, h, l],
  "palette": ["minecraft:stone", "minecraft:repeater[delay=2,facing=west,locked=false,powered=false]", ...],
  "blocks": [[x, y, z, paletteIndex], ...],
  "noteblock": {
    "title": "...", "bpm": 120, "tick_seconds": 0.1,
    "start_button": [x, y, z],
    "schedule": [{"tick": 7, "pos": [x, y, z], "instrument": "flute", "note": 12}, ...]
  }
}
```
- Paste it at the cursor position (or at the origin if the world is empty) as one undo step through the existing history.
- The format should be **generic**: other tools can paste structures this way too. `noteblock` is optional metadata.
- Ideally also offer **copy** (Ctrl+C on a selection) in this format, which covers the open copy/paste idea.
- A `.schem` with the same structure is also available as a file (`~/Projekte/noteblock-music/final-countdown-riff.schem`). It already imports correctly today (1899 blocks, 162×3×23).
- Test file for the JSON: `~/Projekte/noteblock-music/final-countdown-riff_litecraft.json` (267 note blocks).

## 2. Redstone simulation (minimum set for the songs)
Tick-based like Java Edition: 1 redstone tick = 2 game ticks = 0.1 s. Needed at minimum:
- `stone_button` (face=floor): when pressed it strongly powers the block it sits on/at for 10 redstone ticks.
- `redstone_wire`: power 15 → decreases by 1 per block; powered by strongly powered blocks and components next to it; powers the block it points into and the block below it.
- `repeater`: **`facing` points to the INPUT** (Minecraft convention: facing=west = signal travels east). Delay 1–4 redstone ticks, strongly powers the block in front of it, extends pulses to at least the delay length. Locking via a side repeater or comparator would be nice to have (the generator uses it for clocks later).
- Solid blocks: strongly powered (by a repeater) or weakly powered (by dust). Strongly powered blocks power adjacent dust and repeaters (input from behind).
- `note_block`: plays on a **rising edge** when it is itself powered or when a directly adjacent block is (strongly) powered. It only plays if the block above it is air. Instrument = block below it (vanilla mapping), pitch = `note` 0–24.
- Optional, for later: comparator (subtract mode), redstone torch, lever, RS latch.

The generator's layout uses only: button on a stone block → per layer a 1-tick repeater + dust bus (1-tick repeaters every 16 blocks) → lines `repeater(s) → smooth_stone → repeater(s) …` running back and forth in rows (serpentine; turns are pure dust over 5 blocks), note blocks to the left and right of the switching block (instrument block below them) and **one note block on top of the switching block** (the switching block is then itself that note block's instrument block, e.g. clay/dirt/planks). Layers are 4 blocks high. Layers (3 blocks high each) are connected by a **dust staircase** (dust climbing diagonally upward: the connection only exists if the block above the lower dust is not opaque). So the sim also needs dust stairs (up and down).
The generator contains a reference simulator in Python: `~/Projekte/noteblock-music/redstone_sim.py`. Your sim must reach the same result (the generator verifies all 8485 note blocks of the full Final Countdown with it).

**Acceptance test:** after the start button is pressed, the simulation must trigger exactly the note blocks in `noteblock.schedule`, each at exactly the listed `tick` (redstone ticks after the button press). Build this as an automated test (`npm test`) with the test file above. A difference of even one tick counts as a bug.

## 3. Sound
- Original note block sounds **not in the repo** (EULA, same as the textures). Load them at runtime from Mojang: version manifest/asset index via piston-meta → `https://resources.download.minecraft.net/<first-2-hash-chars>/<hash>`. Cache them in IndexedDB like the client jar. If CORS fails: fall back to loading a local `.minecraft/assets` folder or a zip.
- Files (`minecraft/sounds/note/…ogg`): harp=harp2, bass=bass, basedrum=bd, snare=snare, hat=hat, bell=bell, flute=flute, chime=icechime, guitar=guitar, xylophone=xylobone, iron_xylophone=iron_xylophone, cow_bell=cow_bell, didgeridoo=didgeridoo, bit=bit, banjo=banjo, pling=pling.
- Pitch like vanilla: playback rate `2^((note-12)/12)`, Web Audio, all at equal volume.

## 4. UI
- Clicking the button in the world (or a "Play" button in the panel) starts the simulation; stop/reset; speed 1× (optionally 0.5×/2×).
- While playing: briefly highlight active note blocks and powered dust/repeaters (glow), show the current tick/time.
- Load the sounds only when the user first presses Play (data saving), with a progress indicator.

Please keep the existing tests green, add tests for paste + redstone schedule, and test end-to-end in the browser with the test file (headless screenshot).
