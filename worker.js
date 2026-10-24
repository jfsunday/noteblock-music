// Web-Worker: führt den Python-Kern (pipeline.py & Co.) über Pyodide aus.
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
const PY_FILES = ["instruments.py", "sources.py", "arrange.py", "planner.py", "output.py", "auto.py", "layout.py", "sections.py", "layout_reuse.py", "pipeline.py"];

import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs";

let api = null;
const ready = (async () => {
  const py = await loadPyodide({ indexURL: PYODIDE });
  await py.loadPackage("micropip");
  await py.pyimport("micropip").install("mido");
  for (const f of PY_FILES) {
    const src = await (await fetch(f + "?v=" + Date.now())).text();
    py.FS.writeFile("/home/pyodide/" + f, src);
  }
  py.runPython("import sys; sys.path.insert(0, '/home/pyodide')");
  api = py.pyimport("pipeline");
  return py;
})();

self.onmessage = async (e) => {
  const { id, cmd, args } = e.data;
  try {
    await ready;
    let out;
    if (cmd === "init") out = "{}";
    else if (cmd === "midi") out = api.web_load_midi(args.data, args.title);
    else if (cmd === "audio") out = api.web_load_audio(args.title, args.bpm, JSON.stringify(args.melodic), JSON.stringify(args.drums));
    else if (cmd === "generate") out = api.web_generate(JSON.stringify(args));
    else if (cmd === "export") out = api.web_export(args.kind, args.layers, args.mode || "kompakt");
    self.postMessage({ id, ok: true, result: JSON.parse(out) });
  } catch (err) {
    let msg = String(err && err.message || err);
    const m = msg.match(/(\w+Error: .*)\s*$/);   // Python-Traceback kürzen
    self.postMessage({ id, ok: false, error: m ? m[1] : msg });
  }
};
