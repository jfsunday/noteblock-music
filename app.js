import { marked } from "https://cdn.jsdelivr.net/npm/marked@15/lib/marked.esm.js";

const $ = (s) => document.querySelector(s);
const MIDI_EXT = /\.(mid|midi|kar)$/i;

// ---------------- Worker (Python) ----------------
const worker = new Worker("worker.js", { type: "module" });
let msgId = 0;
const pending = new Map();
worker.onmessage = (e) => {
  const { id, ok, result, error } = e.data;
  const p = pending.get(id);
  pending.delete(id);
  ok ? p.resolve(result) : p.reject(new Error(error));
};
const py = (cmd, args = {}) => new Promise((resolve, reject) => {
  const id = ++msgId;
  pending.set(id, { resolve, reject });
  worker.postMessage({ id, cmd, args });
});

// ---------------- Status ----------------
function status(text, pct = null, error = false) {
  $("#status").hidden = !text;
  $("#status-text").textContent = text || "";
  $("#status").classList.toggle("error", error);
  const bar = $("#status-bar");
  bar.hidden = pct === null;
  if (pct !== null) bar.style.width = Math.round(pct * 100) + "%";
}

let engineReady = false;
status("Lade Python-Kern (einmalig, ~10 MB) …", 0.05);
py("init").then(() => { engineReady = true; if (!$("#settings").hidden) return; status(""); })
  .catch((e) => status("Python-Kern konnte nicht geladen werden: " + e.message, null, true));

// ---------------- Datei laden ----------------
let current = null;     // Beschreibung des Songs (Spuren, Tempi …)
let audioCache = null;  // { title, melodic, drums, bpm }
let lastResult = null;

const drop = $("#drop");
drop.addEventListener("click", () => $("#file").click());
$("#file").addEventListener("change", (e) => e.target.files[0] && handleFile(e.target.files[0]));
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("hover"); });
drop.addEventListener("dragleave", () => drop.classList.remove("hover"));
drop.addEventListener("drop", (e) => {
  e.preventDefault(); drop.classList.remove("hover");
  if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
});

async function handleFile(file) {
  const title = file.name.replace(/\.[^.]+$/, "");
  $("#results").hidden = true;
  stopPlayback();
  try {
    if (MIDI_EXT.test(file.name)) {
      audioCache = null;
      if (!engineReady) status("Warte auf Python-Kern …", 0.1);
      const data = new Uint8Array(await file.arrayBuffer());
      current = await py("midi", { data, title });
    } else {
      status("Dekodiere Audio …", 0.02);
      const pcm = await decodeTo22k(file);
      status("Erkenne Noten (basic-pitch) …", 0.05);
      const melodic = await transcribe(pcm, (p) => status(`Erkenne Noten (basic-pitch) … ${Math.round(p * 100)} %`, 0.05 + p * 0.8));
      status("Erkenne Schlagzeug & Tempo …", 0.9);
      const { drums, bpm } = await detectDrumsAndTempo(pcm);
      audioCache = { title, melodic, drums, bpm };
      current = await loadAudioSong();
    }
    buildSettings();
    await generate();
  } catch (e) {
    console.error(e);
    status("Fehler: " + e.message, null, true);
  }
}

async function loadAudioSong() {
  const c = audioCache;
  const bpm = parseFloat($("#audio-bpm")?.value) || c.bpm;
  const drums = $("#audio-drums")?.checked === false ? [] : c.drums;
  return py("audio", { title: c.title, bpm, melodic: c.melodic, drums });
}

// ---------------- Audio: Dekodieren, basic-pitch, Drums ----------------
async function decodeTo22k(file) {
  const ctx = new AudioContext();
  const buf = await ctx.decodeAudioData(await file.arrayBuffer());
  ctx.close();
  const off = new OfflineAudioContext(1, Math.ceil(buf.duration * 22050), 22050);
  const src = off.createBufferSource();
  src.buffer = buf;
  src.connect(off.destination);
  src.start();
  return (await off.startRendering()).getChannelData(0);
}

let bpModule = null;
async function transcribe(pcm, onProgress) {
  if (!bpModule) bpModule = await import("https://esm.sh/@spotify/basic-pitch@1.0.1");
  const { BasicPitch, outputToNotesPoly, addPitchBendsToNoteEvents, noteFramesToTime } = bpModule;
  const model = new BasicPitch("https://cdn.jsdelivr.net/npm/@spotify/basic-pitch@1.0.1/model/model.json");
  const frames = [], onsets = [], contours = [];
  await model.evaluateModel(pcm, (f, o, c) => { frames.push(...f); onsets.push(...o); contours.push(...c); }, onProgress);
  const sens = { low: [0.6, 0.4, 11], mid: [0.5, 0.3, 8], high: [0.35, 0.25, 5] }[$("#audio-sens")?.value || "mid"];
  const notes = noteFramesToTime(addPitchBendsToNoteEvents(contours, outputToNotesPoly(frames, onsets, ...sens)));
  return notes.map((n) => [n.startTimeSeconds, n.pitchMidi, n.amplitude]);
}

async function bandEnergy(pcm, type, freq) {
  const off = new OfflineAudioContext(1, pcm.length, 22050);
  const b = off.createBuffer(1, pcm.length, 22050);
  b.copyToChannel(pcm, 0);
  const src = off.createBufferSource();
  src.buffer = b;
  const f = off.createBiquadFilter();
  f.type = type; f.frequency.value = freq; f.Q.value = type === "bandpass" ? 1.2 : 0.7;
  src.connect(f).connect(off.destination);
  src.start();
  const y = (await off.startRendering()).getChannelData(0);
  const hop = 256, n = Math.floor(y.length / hop), e = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let s = 0;
    for (let j = i * hop; j < (i + 1) * hop; j++) s += y[j] * y[j];
    e[i] = Math.log10(1e-9 + s / hop);
  }
  return e;
}

function peaks(env, hopSec, k = 1.6, minGap = 0.07) {
  const flux = env.map((v, i) => (i ? Math.max(0, v - env[i - 1]) : 0));
  const out = [], w = Math.round(0.6 / hopSec);
  let last = -1;
  for (let i = 1; i < flux.length - 1; i++) {
    if (flux[i] < flux[i - 1] || flux[i] < flux[i + 1]) continue;
    let s = 0, s2 = 0, c = 0;
    for (let j = Math.max(0, i - w); j < Math.min(flux.length, i + w); j++) { s += flux[j]; s2 += flux[j] ** 2; c++; }
    const mean = s / c, sd = Math.sqrt(Math.max(0, s2 / c - mean * mean));
    if (flux[i] > mean + k * sd && flux[i] > 0.15 && (last < 0 || (i - last) * hopSec >= minGap)) {
      out.push([i * hopSec, flux[i]]); last = i;
    }
  }
  return { out, flux };
}

async function detectDrumsAndTempo(pcm) {
  const hopSec = 256 / 22050;
  const bands = [["lowpass", 120, 36], ["bandpass", 1800, 38], ["highpass", 7000, 42]];
  const drums = [];
  let total = null;
  for (const [type, freq, gm] of bands) {
    const { out, flux } = peaks(await bandEnergy(pcm, type, freq), hopSec);
    for (const [t, s] of out) drums.push([t, gm, Math.min(127, Math.round(50 + s * 60))]);
    total = total ? total.map((v, i) => v + (flux[i] || 0)) : Float32Array.from(flux);
  }
  // Tempo per Autokorrelation des Onset-Verlaufs (60–200 BPM, leichte Vorliebe für ~120)
  let best = 120, bestScore = -Infinity;
  for (let bpm = 60; bpm <= 200; bpm += 0.5) {
    const lag = 60 / bpm / hopSec, L = Math.floor(lag);
    const fr = lag - L;
    let s = 0;
    for (let i = 0; i + L + 1 < total.length; i++) s += total[i] * ((1 - fr) * total[i + L] + fr * total[i + L + 1]);
    s *= Math.exp(-0.5 * (Math.log2(bpm / 120) / 1.0) ** 2);
    if (s > bestScore) { bestScore = s; best = bpm; }
  }
  return { drums: drums.sort((a, b) => a[0] - b[0]), bpm: best };
}

// ---------------- Einstellungen ----------------
function buildSettings() {
  const c = current;
  $("#settings").hidden = false;
  $("#song-title").textContent = c.title;
  $("#song-meta").textContent = `${c.source.toUpperCase()} · ${c.notes} Noten · ${c.bpm.toFixed(0)} BPM · ${c.tracks.length} Spuren`;

  const sel = $("#tpb");
  sel.innerHTML = "";
  for (const t of c.tempos) {
    const o = document.createElement("option");
    o.value = t.tpb;
    const dev = Math.round((t.bpm / c.bpm - 1) * 100);
    o.textContent = `${t.bpm.toFixed(1)} BPM · ${t.tpb} Ticks/Schlag · ${dev >= 0 ? "+" : ""}${dev} % · Raster-Fehler ${t.grid_err.toFixed(3)}${t.tpb === c.recommended ? "  ★ Empfehlung" : ""}`;
    if (t.tpb === c.recommended) o.selected = true;
    sel.append(o);
  }

  $("#audio-opts").hidden = !audioCache;
  if (audioCache && !$("#audio-bpm").dataset.set) {
    $("#audio-bpm").value = audioCache.bpm.toFixed(1);
  }

  const tb = $("#tracks tbody");
  tb.innerHTML = "";
  const melodicInst = c.instruments.filter((i) => !i.percussion);
  for (const t of c.tracks) {
    const tr = document.createElement("tr");
    const instCell = t.drum ? `<span class="muted">Schlagzeug (automatisch)</span>` :
      `<select data-track="${escapeAttr(t.name)}">${c.instruments.map((i) =>
        `<option value="${i.key}" ${i.key === t.inst ? "selected" : ""}>${i.name} — ${i.block.split(" (")[0]}</option>`).join("")}</select>`;
    tr.innerHTML = `<td><input type="checkbox" checked data-use="${escapeAttr(t.name)}"></td>
      <td>${escapeHtml(t.name)}</td><td class="num">${t.notes}</td><td>${instCell}</td>`;
    tb.append(tr);
  }
  void melodicInst;
}

const escapeHtml = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const escapeAttr = escapeHtml;

function options() {
  const override = {}, skip = [];
  document.querySelectorAll("#tracks select[data-track]").forEach((s) => {
    const t = current.tracks.find((x) => x.name === s.dataset.track);
    if (s.value !== t.inst) override[s.dataset.track] = s.value;
  });
  document.querySelectorAll("#tracks input[data-use]").forEach((c) => { if (!c.checked) skip.push(c.dataset.use); });
  const layers = [...document.querySelectorAll("input[name=layer]:checked")].map((c) => c.value);
  return {
    tpb: parseInt($("#tpb").value),
    transpose: parseInt($("#transpose").value) || 0,
    max_chord: { beat: +$("#mc-beat").value, bass: +$("#mc-bass").value, melodie: +$("#mc-melodie").value },
    repeats: $("#repeats").checked, layers, override, skip,
  };
}

let genTimer = null;
$("#settings").addEventListener("change", async (e) => {
  if (e.target.id === "audio-bpm" || e.target.id === "audio-drums") {
    $("#audio-bpm").dataset.set = "1";
    current = await loadAudioSong();
    const keep = options();
    buildSettings();
    restore(keep);
  }
  if (e.target.id === "audio-sens") { status("Bitte Datei neu laden, um die Empfindlichkeit anzuwenden."); return; }
  clearTimeout(genTimer);
  genTimer = setTimeout(generate, 150);
});

function restore(o) {
  document.querySelectorAll("#tracks input[data-use]").forEach((c) => { c.checked = !o.skip.includes(c.dataset.use); });
  document.querySelectorAll("#tracks select[data-track]").forEach((s) => { if (o.override[s.dataset.track]) s.value = o.override[s.dataset.track]; });
}

async function generate() {
  status("Erzeuge Bauplan …", 0.95);
  try {
    const r = await py("generate", options());
    lastResult = r;
    renderResult(r);
    status("");
  } catch (e) {
    status("Fehler: " + e.message, null, true);
  }
}

// ---------------- Ergebnis ----------------
function renderResult(r) {
  $("#results").hidden = false;
  const s = r.stats;
  $("#stats").innerHTML = [
    ["Notenblöcke", s.noteblocks], ["Repeater (ca.)", s.repeaters], ["Pattern", s.patterns],
    ["Clocks", s.clocks], ["Länge", `${Math.round(r.info.length_s)} s`], ["Tempo", `${(600 / r.tpb).toFixed(0)} BPM`],
  ].map(([k, v]) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join("");
  $("#selftest").textContent = r.info.selftest_ok
    ? "✔ Selbsttest: Die simulierte Schaltung spielt exakt diese Noten."
    : "⚠ Selbsttest: Simulation weicht ab – bitte melden.";
  $("#selftest").className = r.info.selftest_ok ? "ok" : "warn";
  const md = r.markdown.replace(/^# .*\n/, "");
  $("#plan").innerHTML = marked.parse(md);
  decoratePlan();
}

function decoratePlan() {
  // Instrument-Namen farbig markieren
  $("#plan").querySelectorAll("strong").forEach((el) => {
    const inst = (current.instruments || []).find((i) => i.name === el.textContent);
    if (inst) el.classList.add("chip", "i-" + inst.key);
  });
  // Pattern-Abschnitte einklappbar
  const h3s = [...$("#plan").querySelectorAll("h3")];
  for (const h of h3s) {
    const d = document.createElement("details");
    const sum = document.createElement("summary");
    sum.innerHTML = h.innerHTML;
    d.append(sum);
    h.replaceWith(d);
    let n = d.nextSibling;
    while (n && !(n.tagName && /^H[23]$/.test(n.tagName)) && !(n.tagName === "DETAILS")) {
      const next = n.nextSibling; d.append(n); n = next;
    }
  }
}

function download(name, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
$("#dl-md").onclick = () => download(current.title + "_bauplan.md", lastResult.markdown, "text/markdown");
$("#dl-json").onclick = () => download(current.title + "_bauplan.json", lastResult.json, "application/json");
$("#print").onclick = () => { document.querySelectorAll("#plan details").forEach((d) => (d.open = true)); window.print(); };

// ---------------- Hörprobe (Notenblock-Synth) ----------------
let actx = null, playing = null;
function tone(inst, freq, sr) {
  const n = Math.floor(sr * 0.6), out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const t = i / sr, w = 2 * Math.PI * freq * t;
    let v;
    switch (inst) {
      case "basedrum": { const f = 55 * (1 + 2 * Math.exp(-t * 30)); v = Math.sin(2 * Math.PI * f * t) * Math.exp(-t * 18); break; }
      case "snare": v = ((Math.random() * 2 - 1) * 0.7 + Math.sin(2 * Math.PI * 180 * t) * 0.3) * Math.exp(-t * 25); break;
      case "hat": v = (Math.random() * 2 - 1) * 0.35 * Math.exp(-t * 70); break;
      case "bass": case "didgeridoo": v = (Math.sin(w) + 0.5 * Math.sin(2 * w) + 0.25 * Math.sin(3 * w)) * Math.exp(-t * 5); break;
      case "bit": v = Math.sign(Math.sin(w)) * 0.45 * Math.exp(-t * 4); break;
      case "flute": v = Math.sin(w + 0.3 * Math.sin(2 * Math.PI * 5 * t)) * Math.min(1, t * 30) * Math.exp(-t * 3); break;
      case "bell": case "chime": case "iron_xylophone": case "xylophone": case "cow_bell":
        v = (Math.sin(w) + 0.4 * Math.sin(2.76 * w)) * Math.exp(-t * (inst === "xylophone" ? 12 : 4)); break;
      default: v = (Math.sin(w) + 0.3 * Math.sin(2 * w) + 0.15 * Math.sin(3 * w)) * Math.exp(-t * 6);
    }
    out[i] = v;
  }
  return out;
}

function stopPlayback() {
  if (playing) { playing.src.stop(); cancelAnimationFrame(playing.raf); playing = null; }
  $("#play").textContent = "▶ Hörprobe";
  $("#progress").style.width = "0";
}

$("#play").onclick = () => {
  if (playing) return stopPlayback();
  actx = actx || new AudioContext();
  const sr = actx.sampleRate, notes = lastResult.played;
  const end = Math.max(...notes.map((n) => n[0])) * 0.1 + 1;
  const buf = actx.createBuffer(1, Math.ceil(end * sr), sr), data = buf.getChannelData(0);
  const cache = {};
  for (const [tick, midi, inst] of notes) {
    const key = inst + midi;
    const s = cache[key] || (cache[key] = tone(inst, 440 * 2 ** ((midi - 69) / 12), sr));
    const a = Math.floor(tick * 0.1 * sr);
    for (let i = 0; i < s.length && a + i < data.length; i++) data[a + i] += s[i] * 0.22;
  }
  let peak = 0;
  for (let i = 0; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i]));
  if (peak > 1) for (let i = 0; i < data.length; i++) data[i] /= peak;
  const src = actx.createBufferSource();
  src.buffer = buf;
  src.connect(actx.destination);
  src.start();
  const t0 = actx.currentTime;
  const tick = () => {
    $("#progress").style.width = Math.min(100, ((actx.currentTime - t0) / end) * 100) + "%";
    playing.raf = requestAnimationFrame(tick);
  };
  playing = { src, raf: 0 };
  src.onended = stopPlayback;
  $("#play").textContent = "■ Stopp";
  tick();
};
