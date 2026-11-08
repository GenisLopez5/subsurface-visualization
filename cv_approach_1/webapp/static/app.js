/* Lithology Console front end: plain JS, no build step. Hash routes:
 *   #/new                 new run (upload / link a folder / open results)
 *   #/runs                run history
 *   #/how                 how the pipeline works
 *   #/run/<id>/setup      files + pre-flight + settings (before the run)
 *   #/run/<id>/monitor    live progress (SSE)
 *   #/run/<id>/results    dashboard
 *   #/run/<id>/log/<stem> one log: zoomable image viewer + intervals, clusters, legend, depth, accuracy
 */
"use strict";

// ---------------------------------------------------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------------------------------------------------
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const enc = encodeURIComponent;
const store = {
  get(k, d) { try { const v = localStorage.getItem("litho." + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("litho." + k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
};
const STAGES = ["column", "segmentation", "legend+naming", "depth", "render"];
const STAGE_COLOURS = ["#2a78d6", "#eda100", "#1baf7a", "#e87ba4", "#8c8b82"];
const PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6d5bd0", "#8c8b82"];  // = pipeline

async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers = { "Content-Type": "application/json" }; delete o.json; }
  const r = await fetch(path, o);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) {
    const d = data && data.detail;
    throw new Error(d ? (typeof d === "string" ? d : JSON.stringify(d)) : `${r.status} ${r.statusText}`);
  }
  return data;
}
// "📁 Browse…" next to a path input: opens the native folder chooser (server side, this computer) and fills it in
function addBrowse(input, title, after = input) {
  if (!input) return;
  const b = document.createElement("button");
  b.type = "button"; b.textContent = "📁 Browse…"; b.title = "Choose the folder in the file explorer";
  b.onclick = async () => {
    b.disabled = true;
    try {
      const r = await api("/api/pick-folder", { method: "POST", json: { start: input.value.trim(), title } });
      if (r.path) { input.value = r.path; input.dispatchEvent(new Event("input")); input.focus(); }
    } catch (e) { toast(e.message, true); } finally { b.disabled = false; }
  };
  after.insertAdjacentElement("afterend", b);
}
function toast(msg, err = false) {
  const t = document.createElement("div");
  t.className = "toast" + (err ? " err" : "");
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), err ? 7000 : 3500);
}
const fail = (e) => toast(e.message || String(e), true);
function fmtSec(s) {
  if (s == null || isNaN(s)) return "–";
  if (s < 60) return `${s.toFixed(1)} s`;
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = Math.round(s % 60);
  return h ? `${h} h ${String(m).padStart(2, "0")} min` : `${m}:${String(x).padStart(2, "0")} min`;
}
const fmtNum = (v, d = 1) => (v == null || isNaN(v) ? "–" : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const fmtInt = (v) => (v == null ? "–" : Number(v).toLocaleString());
const fmtMB = (v) => (v == null ? "–" : v >= 1024 ? `${(v / 1024).toFixed(1)} GB` : `${Math.round(v)} MB`);
const short = (s, n = 46) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const badge = (status) => `<span class="badge ${esc(status)}">${esc(status)}</span>`;

// Flags = what makes a log suspicious (also used for the "suspicious only" filter)
function flagsOf(r) {
  if (r.partial) return [];
  const f = [];
  const names = new Set((r.intervals || []).map((i) => i.lithology));
  if (!r.swatches || !r.swatches.length) f.push("no legend");
  if (!r.depth) f.push("no depth");
  else if (r.depth.n_used < 3) f.push("weak depth fit");
  if (r.n_intervals === 0) f.push("0 intervals");
  else if (r.n_intervals > 150) f.push("many intervals");
  if (names.size === 1 && names.has("Unknown") && r.swatches && r.swatches.length) f.push("only Unknown");
  if (r.scale && !r.scale.measured) f.push("period not measured");
  if (r.k === 1) f.push("one rock (k=1)");
  if (r.metrics && r.metrics.row_acc < 80) f.push("low accuracy");
  return f;
}
// Colour per lithology exactly as the pipeline assigns it (order of first appearance across the run)
function colourMap(summary) {
  const m = {};
  for (const r of (summary && summary.logs) || []) for (const i of r.intervals || []) if (!(i.lithology in m)) m[i.lithology] = PALETTE[Object.keys(m).length % PALETTE.length];
  return (name) => m[name] || PALETTE[[...String(name)].reduce((a, c) => a + c.charCodeAt(0), 0) % PALETTE.length];
}

// ---------------------------------------------------------------------------------------------------------------------
// App state, cleanup between views
// ---------------------------------------------------------------------------------------------------------------------
const S = { health: null, cfg: null, cleanup: [], pendingUpload: null, summaries: {} };
function onCleanup(fn) { S.cleanup.push(fn); }
function cleanup() { while (S.cleanup.length) { try { S.cleanup.pop()(); } catch (e) { /* ignore */ } } }
function view(html) { $("#view").innerHTML = html; window.scrollTo(0, 0); }
function setNav(key) { $$(".mainnav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === key)); }
async function getConfig() { if (!S.cfg) S.cfg = await api("/api/config"); return S.cfg; }

// ---------------------------------------------------------------------------------------------------------------------
// Header: Tesseract, memory, running job
// ---------------------------------------------------------------------------------------------------------------------
async function refreshHealth() {
  try {
    const h = await api("/api/health");
    S.health = h;
    const t = $("#tessPill"), o = h.ocr.lithology;
    t.className = "pill " + (o.ok ? "ok" : "err");
    t.textContent = o.ok ? `OCR ✓ ${o.version}` : "OCR missing";
    t.title = `lithology method: ${o.ok ? o.version : o.message}\ncv method: ${h.ocr.cv.ok ? "Tesseract ✓" : h.ocr.cv.message}`;
    $("#memPill").textContent = h.mem_available_mb != null ? `RAM free ${fmtMB(h.mem_available_mb)}` : "";
    $("#banner").innerHTML = o.ok ? "" : `<div class="bar"><strong>RapidOCR not installed.</strong> Legend names and depths need it. Run <code>.venv/bin/pip install -r requirements.txt</code> in the project folder, then restart the app.</div>`;
    const jp = $("#jobPill");
    if (h.busy) {
      const st = await api(`/api/runs/${h.busy}/status`);
      const p = st.progress;
      jp.textContent = `● Running ${p.finished}/${p.total || "?"} · ${Math.round(100 * p.fraction)}%`;
      jp.href = `#/run/${h.busy}/monitor`;
      jp.classList.remove("hidden");
    } else jp.classList.add("hidden");
  } catch (e) { /* server restarting */ }
}

// ---------------------------------------------------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------------------------------------------------
function route() {
  cleanup();
  const parts = location.hash.replace(/^#\/?/, "").split("/").map((p) => { try { return decodeURIComponent(p); } catch (e) { return p; } });
  const [a, id, tab, ...rest] = parts;
  setNav(a === "run" ? "runs" : a || "new");
  if (!a) return viewHome();
  if (a === "new") return viewNew();
  if (a === "runs") return viewRuns();
  if (a === "how") return viewHow();
  if (a === "eval" && id) return viewEval(id);
  if (a === "run" && id) return tab === "log" ? viewLog(id, rest.join("/")) : viewRun(id, tab);
  viewNew();
}
async function viewHome() {
  await refreshHealth();
  if (S.health && S.health.busy) location.hash = `#/run/${S.health.busy}/monitor`;
  else viewNew();
}

// ---------------------------------------------------------------------------------------------------------------------
// New run
// ---------------------------------------------------------------------------------------------------------------------
async function viewNew() {
  setNav("new");
  let mode = store.get("newMode2", "single");
  view(`
  <div class="page-head"><div class="title"><h1>New run</h1>
    <div class="muted">Your files are never modified.</div></div></div>
  <div class="modeseg" role="tablist">
    <button data-mode="single" role="tab">Single image</button><button data-mode="batch" role="tab">Batch</button><button data-mode="eval" role="tab">Evaluation</button>
  </div>
  <div class="card"><div class="field-row"><label for="runName" class="nowrap"><strong>Name</strong> <span class="muted">(optional)</span></label>
    <input type="text" id="runName" placeholder="e.g. Callaway, hybrid method" maxlength="120"></div></div>

  <div data-panel="single">
    <div class="card"><h2>One image</h2>
      <div class="dropzone" id="drop" tabindex="0" role="button" style="padding:40px 16px"><strong>Drop the image here</strong> (+ its .json if you have one) or click to choose<br>
        <span class="small">.tif / .tiff / .png / .jpg / .pdf. The boxes are found automatically; a bounding-box .json with the same name is the fallback.</span></div>
      <input type="file" id="fileInput" multiple class="hidden" accept=".tif,.tiff,.png,.jpg,.jpeg,.pdf,.json">
      <p class="small muted">Next: a quick check of both files, the settings, then Start.</p>
      <details style="margin-top:6px"><summary class="muted" style="cursor:pointer">…or pick an image already on this computer (no upload)</summary>
        <div style="margin-top:10px"><input type="text" id="logFilter" placeholder="Filter…" style="width:100%;margin-bottom:8px">
        <select id="logSel" size="10" style="width:100%"><option disabled>Loading…</option></select>
        <div id="logInfo" class="small muted" style="margin:8px 0;min-height:20px"></div>
        <div class="row"><button class="primary" id="runNowBtn" disabled>▶ Run now</button><button id="setupBtn" disabled>Settings first…</button>
          <span class="small muted">Run now = default settings.</span></div></div>
      </details>
    </div>
  </div>

  <div data-panel="batch">
    <div class="card"><h2>A whole dataset folder</h2>
      <label class="small muted" for="folderPath">Folder with <code>bbox_annotation/</code> + <code>images/</code> (or <code>.json</code> + images side by side)</label>
      <div class="field-row" style="margin:6px 0 10px"><input type="text" id="folderPath" list="dataList" placeholder="/path/to/dataset"><button class="primary" id="linkBtn">Next →</button></div>
      <p class="small muted">Nothing is copied. Next: tick the images to process (all by default) and adjust settings.</p>
    </div>
  </div>

  <div data-panel="eval">
    <div class="grid2">
      <div class="card"><h2>1 · Images with ground truth</h2>
        <div id="gtList" class="muted">Loading…</div>
        <p class="small muted">Ground truth = a <code>&lt;image name&gt;.csv</code> in <code id="defLabels">cv_approach_1/labels</code> (columns top_depth, bottom_depth, lithology, top_y, bottom_y). Add more CSVs there to evaluate more images.</p>
      </div>
      <div class="card"><h2>2 · Methods to compare</h2>
        <label class="check" style="display:flex;margin-bottom:6px"><input type="checkbox" data-method="automatic_lithology_cv" checked> <b>automatic_lithology_cv</b>&nbsp;<span class="muted small">lithology_cv + automatic bounding boxes (default)</span></label>
        <label class="check" style="display:flex;margin-bottom:6px"><input type="checkbox" data-method="lithology_cv" checked> <b>lithology_cv</b>&nbsp;<span class="muted small">hybrid_v3 + Pau's grid legend reader, boxes from the .json</span></label>
        <label class="check" style="display:flex;margin-bottom:6px"><input type="checkbox" data-method="hybrid_v3" checked> <b>hybrid_v3</b>&nbsp;<span class="muted small">hybrid_v2 + Pau's robust depth fit</span></label>
        <label class="check" style="display:flex;margin-bottom:6px"><input type="checkbox" data-method="hybrid_v2" checked> <b>hybrid_v2</b>&nbsp;<span class="muted small">colour mode, blank fix, legend quality</span></label>
        <label class="check" style="display:flex;margin-bottom:6px"><input type="checkbox" data-method="hybrid" checked> <b>hybrid</b>&nbsp;<span class="muted small">cv segmentation + naming, OCR fallbacks</span></label>
        <label class="check" style="display:flex;margin-bottom:6px"><input type="checkbox" data-method="lithology2" checked> <b>lithology2</b>&nbsp;<span class="muted small">lithology + shape naming, one name per pattern, noise → neighbour, no "?"</span></label>
        <label class="check" style="display:flex;margin-bottom:6px"><input type="checkbox" data-method="lithology" checked> <b>lithology</b>&nbsp;<span class="muted small">Pau's lithology/ package only</span></label>
        <label class="check" style="display:flex;margin-bottom:12px"><input type="checkbox" data-method="cv" checked> <b>cv</b>&nbsp;<span class="muted small">clustering only</span></label>
        <div class="row"><button class="primary big" id="evalBtn" disabled>▶ Start evaluation</button></div>
        <p class="small muted">One run per method, one after another (queued). You get a side-by-side comparison: accuracy, boundaries, confusion matrices.</p>
      </div>
    </div>
  </div>
  <datalist id="dataList"></datalist>
  <div class="card"><div class="card-head"><h2>Recent runs</h2><div class="spacer"></div><a href="#/runs">All runs →</a></div><div id="recent" class="muted">Loading…</div></div>`);

  const setMode = (m) => {
    mode = m; store.set("newMode2", m);
    $$("[data-mode]").forEach((b) => b.classList.toggle("on", b.dataset.mode === m));
    $$("[data-panel]").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== m));
  };
  $$("[data-mode]").forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
  setMode(mode);
  api("/api/suggestions").then((s) => {
    $("#dataList").innerHTML = s.data.map((p) => `<option value="${esc(p)}">`).join("");
    const ds = s.data.find((p) => /dataset/i.test(p)) || s.data[0];
    if (ds) $("#folderPath").value = ds;
  }).catch(() => {});
  api("/api/runs").then((r) => { $("#recent").innerHTML = runsTable(r.runs.slice(0, 5), true); }).catch(fail);
  const name = () => $("#runName").value.trim();
  const newRun = (fallback) => api("/api/runs", { method: "POST", json: { name: name() || fallback || "" } });

  // --- single image: drop, or pick from disk
  wireDrop($("#drop"), $("#fileInput"), async (files) => {
    if (!files.length) return;
    const imgs = files.filter((f) => !/\.(json|csv)$/i.test(f.name));
    if (imgs.length > 1) toast("Single image: drop one image (+ its .json if you have one). Use Batch for a folder.", true);
    try { const r = await newRun(imgs[0] ? imgs[0].name.replace(/\.[^.]+$/, "") : ""); S.pendingUpload = [...files]; location.hash = `#/run/${r.id}/setup`; } catch (e) { fail(e); }
  });
  let logs = [];
  const uniq = (ls) => { const seen = new Set(); return ls.filter((l) => !seen.has(l.stem) && seen.add(l.stem)); };
  const selected = () => { const v = $("#logSel").value; if (!v || !v.includes("\n")) return null; const [folder, stem] = v.split("\n"); return logs.find((l) => l.folder === folder && l.stem === stem); };
  const pick = () => {
    const l = selected();
    $("#runNowBtn").disabled = $("#setupBtn").disabled = !l;
    $("#logInfo").innerHTML = l ? `${esc(l.image)} · ${l.size_mb != null ? fmtNum(l.size_mb, 1) + " MB" : "?"}${l.width ? ` · ${fmtInt(l.width)} × ${fmtInt(l.height)} px` : ""}` : "";
  };
  const drawLogs = () => {
    const q = $("#logFilter").value.toLowerCase();
    const list = uniq(logs).filter((l) => l.stem.toLowerCase().includes(q));
    const prev = $("#logSel").value;
    $("#logSel").innerHTML = list.length ? list.map((l) => `<option value="${esc(l.folder + "\n" + l.stem)}">${esc(l.stem)}</option>`).join("") : `<option disabled>No image found.</option>`;
    if (prev) $("#logSel").value = prev;
    pick();
  };
  $("#logFilter").oninput = drawLogs;
  $("#logSel").onchange = pick;
  const singleRun = async (start) => {
    const l = selected();
    if (!l) return;
    $("#runNowBtn").disabled = $("#setupBtn").disabled = true;
    try {
      const r = await newRun(short(l.stem, 60));
      await api(`/api/runs/${r.id}/link`, { method: "POST", json: { path: l.folder, logs: [l.stem] } });
      if (start) { await api(`/api/runs/${r.id}/start`, { method: "POST", json: { config: {}, queue: true } }); location.hash = `#/run/${r.id}/monitor`; refreshHealth(); }
      else location.hash = `#/run/${r.id}/setup`;
    } catch (e) { fail(e); pick(); }
  };
  $("#runNowBtn").onclick = () => singleRun(true);
  $("#setupBtn").onclick = () => singleRun(false);
  $("#logSel").ondblclick = () => singleRun(true);

  // --- batch
  addBrowse($("#folderPath"), "Choose the dataset folder");
  $("#linkBtn").onclick = async () => {
    const path = $("#folderPath").value.trim();
    if (!path) return toast("Type the dataset folder path first", true);
    $("#linkBtn").disabled = true;
    try {
      const r = await newRun(`Batch: ${path.split("/").filter(Boolean).pop()}`);
      const res = await api(`/api/runs/${r.id}/link`, { method: "POST", json: { path } });
      toast(`Found ${res.linked} image(s)`);
      location.hash = `#/run/${r.id}/setup`;
    } catch (e) { fail(e); $("#linkBtn").disabled = false; }
  };

  // --- evaluation
  let gt = [];
  const evalReady = () => { $("#evalBtn").disabled = !$$("[data-gt]:checked").length || !$$("[data-method]:checked").length; };
  api("/api/logs").then((r) => {
    logs = r.logs; drawLogs();
    if (r.labels_dir) $("#defLabels").textContent = r.labels_dir;
    gt = uniq(r.logs.filter((l) => l.labelled));
    $("#gtList").innerHTML = gt.length ? gt.map((l, i) => `<label class="check" style="display:flex;margin-bottom:6px"><input type="checkbox" data-gt="${i}" checked> <span class="mono small ellipsis" title="${esc(l.stem)}">${esc(l.stem)}</span></label>`).join("")
      : `<div class="empty">No image has ground truth yet.</div>`;
    $$("[data-gt], [data-method]").forEach((c) => (c.onchange = evalReady));
    evalReady();
  }).catch(fail);
  $("#evalBtn").onclick = async () => {
    $("#evalBtn").disabled = true;
    try {
      const ev = await api("/api/evaluations", { method: "POST", json: { name: name(), logs: $$("[data-gt]:checked").map((c) => ({ folder: gt[c.dataset.gt].folder, stem: gt[c.dataset.gt].stem })), methods: $$("[data-method]:checked").map((c) => c.dataset.method) } });
      location.hash = `#/eval/${ev.id}`;
      refreshHealth();
    } catch (e) { fail(e); evalReady(); }
  };
}

// ---------------------------------------------------------------------------------------------------------------------
// Evaluation: methods side by side
// ---------------------------------------------------------------------------------------------------------------------
async function viewEval(id) {
  setNav("runs");
  view(`<div class="pad muted">Loading evaluation…</div>`);
  let charts = [];
  onCleanup(() => charts.forEach((c) => c.destroy()));
  let timer = null;
  onCleanup(() => clearTimeout(timer));
  let lastKey = "";
  const load = async () => {
    let ev;
    try { ev = await api(`/api/evaluations/${id}`); } catch (e) { view(`<div class="empty">${esc(e.message)} · <a href="#/runs">Back to runs</a></div>`); return; }
    const res = ev.results;
    const busy = res.some((r) => ["running", "queued"].includes(r.status));
    const key = JSON.stringify(res.map((r) => [r.status, Math.round(100 * (r.fraction || 0)), r.logs.length]));
    if (key !== lastKey) { lastKey = key; render(ev); }
    if (busy) timer = setTimeout(load, 2000);
  };
  const best = (vals, higher) => { const v = vals.filter((x) => x != null); return v.length ? (higher ? Math.max(...v) : Math.min(...v)) : null; };
  const render = (ev) => {
    charts.forEach((c) => c.destroy()); charts = [];
    const res = ev.results;
    const stems = [...new Set(ev.logs.map((l) => l.stem))];
    const per = (r, stem) => r.logs.find((x) => x.stem === stem);
    const mm = (k) => res.map((r) => (r.mean_metrics ? r.mean_metrics[k] : null));
    const bAcc = best(mm("row_acc"), true), bBnd = best(mm("boundary_mean_px"), false), bF1 = best(mm("boundary_f1"), true);
    const mark = (v, b) => (v != null && v === b ? ` class="num yes"` : ` class="num"`);
    view(`
    <div class="page-head"><div class="title"><div class="small"><a href="#/runs">← Runs</a></div><h1>${esc(ev.name)}</h1>
      <div class="small muted">Evaluation · created ${esc(ev.created)} · ${stems.length} image(s) with ground truth · ${ev.methods.length} method(s)</div></div>
      <div class="row"><button class="danger" id="delEval">Delete</button></div></div>
    <div class="stats" style="grid-template-columns:repeat(${res.length},minmax(220px,1fr))">
      ${res.map((r) => `<div class="stat"><div class="row"><strong style="font-size:15px">${esc(r.method)}</strong>${badge(r.status)}<div class="spacer"></div><a class="small" href="#/run/${r.run_id}">open run →</a></div>
        <div class="progress ${r.status === "done" ? "done" : ["failed", "cancelled", "interrupted"].includes(r.status) ? "failed" : r.status === "running" ? "striped" : ""}" style="margin:8px 0"><div style="width:${r.status === "done" ? 100 : Math.round(100 * (r.fraction || 0))}%"></div></div>
        <div class="value">${r.mean_metrics ? fmtNum(r.mean_metrics.row_acc, 2) + "%" : "–"}</div><div class="sub">row accuracy${r.mean_metrics ? ` · boundary ${fmtNum(r.mean_metrics.boundary_mean_px, 2)} px · F1 ${fmtNum(r.mean_metrics.boundary_f1, 1)}%` : ""}</div>
        <div class="sub">${r.total_s != null ? "runtime " + fmtSec(r.total_s) : r.status === "queued" ? "waiting for the previous method" : r.status === "running" ? `running ${fmtSec(r.elapsed_s)}` : ""}${r.errors && r.errors.length ? ` · <span class="no">${r.errors.length} error(s)</span>` : ""}</div></div>`).join("")}
    </div>
    <div class="card tight"><div class="table-wrap"><table class="data"><thead><tr><th>Metric (mean)</th>${res.map((r) => `<th class="num">${esc(r.method)}</th>`).join("")}</tr></thead><tbody>
      <tr><td>Row accuracy %</td>${res.map((r, i) => `<td${mark(mm("row_acc")[i], bAcc)}>${fmtNum(mm("row_acc")[i], 2)}</td>`).join("")}</tr>
      <tr><td>Boundary error px</td>${res.map((r, i) => `<td${mark(mm("boundary_mean_px")[i], bBnd)}>${fmtNum(mm("boundary_mean_px")[i], 2)}</td>`).join("")}</tr>
      <tr><td>Boundary F1 %</td>${res.map((r, i) => `<td${mark(mm("boundary_f1")[i], bF1)}>${fmtNum(mm("boundary_f1")[i], 1)}</td>`).join("")}</tr>
      <tr><td>Runtime</td>${res.map((r) => `<td class="num">${fmtSec(r.total_s)}</td>`).join("")}</tr>
    </tbody></table></div></div>
    <div class="card"><h2>Per image</h2><div class="table-wrap"><table class="data"><thead><tr><th>Image</th>${res.map((r) => `<th class="num">${esc(r.method)}: row acc.</th><th class="num">bnd px</th><th class="num">intervals</th>`).join("")}</tr></thead><tbody>
      ${stems.map((st) => `<tr><td class="ellipsis" style="max-width:320px" title="${esc(st)}">${esc(st)}</td>${res.map((r) => { const x = per(r, st); const m = x && x.metrics;
        return `<td class="num">${m ? `<a href="#/run/${r.run_id}/log/${enc(st)}">${fmtNum(m.row_acc, 2)}%</a>` : "–"}</td><td class="num">${m ? fmtNum(m.boundary_mean_px, 1) : "–"}</td><td class="num">${x ? fmtInt(x.n_intervals) : "–"}</td>`; }).join("")}</tr>`).join("")}
    </tbody></table></div></div>
    <div class="card"><h2>Accuracy by method</h2><div class="chart-box" style="height:260px"><canvas id="evChart"></canvas></div></div>
    <div class="card"><h2>Confusion matrices</h2><p class="small muted">Rows = true lithology, columns = predicted (share of the true rock's pixel rows). Look for big red cells: those are the names each method mixes up.</p>
      ${stems.map((st) => `<h3 class="mono small" style="margin-top:14px">${esc(st)}</h3><div class="grid2">${res.map((r) => { const x = per(r, st);
        return `<div><div class="small"><b>${esc(r.method)}</b></div>${x && x.metrics ? confusionTable(x.metrics.confusion) : `<div class="muted small">${r.status === "done" ? "no metrics" : "not finished yet"}</div>`}</div>`; }).join("")}</div>`).join("")}
    </div>`);
    $("#delEval").onclick = async () => {
      if (!confirm("Delete this evaluation and its runs?")) return;
      try { await api(`/api/evaluations/${id}`, { method: "DELETE" }); toast("Evaluation deleted"); location.hash = "#/runs"; } catch (e) { fail(e); }
    };
    if (chartsReady() && res.some((r) => r.mean_metrics)) {
      chartDefaults();
      charts.push(new Chart($("#evChart"), {
        type: "bar",
        data: { labels: res.map((r) => r.method), datasets: [
          { label: "Row accuracy (%)", data: mm("row_acc"), backgroundColor: "#2a78d6", fmt: (v) => `${v.toFixed(1)}%` },
          { label: "Boundary F1 (%)", data: mm("boundary_f1"), backgroundColor: "#1baf7a", fmt: (v) => `${v.toFixed(1)}%` }] },
        options: { maintainAspectRatio: false, animation: false, layout: { padding: { top: 18 } }, scales: { y: { beginAtZero: true, max: 100 } },
          plugins: { legend: { position: "top", labels: { boxWidth: 12 } }, valueLabels: { enabled: true, format: (v) => v.toFixed(1) } } },
        plugins: [valueLabels],
      }));
    } else $("#evChart").parentElement.innerHTML = `<div class="empty">Appears when a method has finished.</div>`;
  };
  load();
}

function wireDrop(zone, input, onFiles) {
  zone.onclick = () => input.click();
  zone.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } };
  input.onchange = () => { onFiles([...input.files]); input.value = ""; };
  zone.ondragover = (e) => { e.preventDefault(); zone.classList.add("over"); };
  zone.ondragleave = () => zone.classList.remove("over");
  zone.ondrop = (e) => { e.preventDefault(); zone.classList.remove("over"); onFiles([...e.dataTransfer.files]); };
}

// ---------------------------------------------------------------------------------------------------------------------
// Run history
// ---------------------------------------------------------------------------------------------------------------------
function runsTable(runs, compact = false) {
  if (!runs.length) return `<div class="empty">No runs yet. Start one above.</div>`;
  return `<div class="table-wrap"><table class="data"><thead><tr><th>Run</th><th>Created</th>${compact ? "" : "<th>Source</th>"}<th>Status</th>
    <th class="num">Logs</th><th class="num">Errors</th><th class="num">Runtime</th><th class="num">Mean row acc.</th>${compact ? "" : "<th></th>"}</tr></thead><tbody>
    ${runs.map((r) => `<tr class="click" data-href="#/run/${r.id}">
      <td><strong>${esc(r.name || r.id)}</strong>${r.name ? `<div class="small muted mono">${r.id}</div>` : ""}</td>
      <td class="nowrap">${esc(r.created || "")}</td>
      ${compact ? "" : `<td class="small ellipsis" style="max-width:280px" title="${esc(r.source || "")}">${esc(r.mode || "")}${r.source ? `: ${esc(r.source)}` : ""}</td>`}
      <td>${badge(r.status)}</td><td class="num">${fmtInt(r.n_logs)}</td>
      <td class="num">${r.n_errors ? `<span class="no">${r.n_errors}</span>` : r.n_errors === 0 ? "0" : "–"}</td>
      <td class="num">${fmtSec(r.total_s)}</td>
      <td class="num">${r.mean_metrics ? fmtNum(r.mean_metrics.row_acc, 2) + "%" : "–"}</td>
      ${compact ? "" : `<td class="nowrap"><button data-clone="${r.id}" ${r.mode === "import" ? "disabled" : ""} title="New draft with the same files and settings">Clone</button>
        <button class="danger" data-del="${r.id}" ${["running", "queued"].includes(r.status) ? "disabled" : ""}>Delete</button></td>`}
    </tr>`).join("")}</tbody></table></div>`;
}
document.addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-href]");
  if (tr && !e.target.closest("button,a,input")) location.hash = tr.dataset.href;
});
async function viewRuns() {
  view(`<div class="page-head"><div class="title"><h1>Runs</h1><div class="muted">Every run is a folder in <code>runs/</code>. Click one to reopen it.</div></div>
    <button id="importToggle">Import results folder…</button><a class="btn primary" href="#/new">New run</a></div>
    <div class="card hidden" id="importBox"><h2>Import existing results</h2>
      <label class="small muted" for="resultsPath">Folder with <code>summary.json</code> (e.g. the command line's <code>outputs/</code>). View only, nothing is re-run.</label>
      <div class="field-row" style="margin-top:6px"><input type="text" id="resultsPath" list="resultsList" placeholder="/path/to/outputs"><button class="primary" id="importBtn">Import</button></div>
      <datalist id="resultsList"></datalist></div>
    <div id="evalBox"></div>
    <div class="card tight" id="runsBox"><div class="pad muted">Loading…</div></div>`);
  $("#importToggle").onclick = () => { $("#importBox").classList.toggle("hidden"); api("/api/suggestions").then((s) => { $("#resultsList").innerHTML = s.results.map((p) => `<option value="${esc(p)}">`).join(""); if (s.results[0] && !$("#resultsPath").value) $("#resultsPath").placeholder = s.results[0]; }).catch(() => {}); };
  $("#importBtn").onclick = async () => {
    const path = $("#resultsPath").value.trim() || $("#resultsPath").placeholder;
    if (!path || path.startsWith("/path")) return toast("Type the results folder path first", true);
    try { const r = await api("/api/runs/import", { method: "POST", json: { path } }); location.hash = `#/run/${r.id}/results`; } catch (e) { fail(e); }
  };
  api("/api/evaluations").then((r) => {
    if (!r.evaluations.length) return;
    $("#evalBox").innerHTML = `<div class="card tight"><div class="row" style="padding:10px 12px;border-bottom:1px solid var(--line)"><h2 style="margin:0">Evaluations</h2><span class="small muted">same ground-truth images, methods side by side</span></div>
      <div class="table-wrap"><table class="data"><thead><tr><th>Evaluation</th><th>Created</th><th class="num">Images</th><th>Methods: row accuracy</th></tr></thead><tbody>
      ${r.evaluations.map((e) => `<tr class="click" data-href="#/eval/${e.id}"><td><strong>${esc(e.name)}</strong></td><td class="nowrap">${esc(e.created)}</td><td class="num">${e.n_images}</td>
        <td>${e.methods.map((m) => `<span class="nowrap" style="margin-right:12px">${esc(m)}: ${e.acc[m] != null ? `<b>${fmtNum(e.acc[m], 1)}%</b>` : badge(e.statuses[m] || "?")}</span>`).join("")}</td></tr>`).join("")}
      </tbody></table></div></div>`;
  }).catch(() => {});
  const load = async () => {
    const r = await api("/api/runs");
    $("#runsBox").innerHTML = runsTable(r.runs);
    $$("[data-del]").forEach((b) => (b.onclick = async () => {
      if (!confirm("Delete this run folder (outputs included)? Linked original files are not touched.")) return;
      try { await api(`/api/runs/${b.dataset.del}`, { method: "DELETE" }); toast("Run deleted"); load(); } catch (e) { fail(e); }
    }));
    $$("[data-clone]").forEach((b) => (b.onclick = () => cloneRun(b.dataset.clone)));
  };
  load().catch(fail);
}
async function cloneRun(id) {
  try { const r = await api(`/api/runs/${id}/clone`, { method: "POST", json: {} }); toast("Cloned: adjust settings and start"); location.hash = `#/run/${r.id}/setup`; } catch (e) { fail(e); }
}

// ---------------------------------------------------------------------------------------------------------------------
// How it works
// ---------------------------------------------------------------------------------------------------------------------
function viewHow() {
  const step = (n, title, task, text, eg) => `<li><div class="n">${n}</div><div><div class="t">${title}${task ? ` <span class="badge">${task}</span>` : ""}</div>
    <div class="d">${text}</div>${eg ? `<div class="eg">${eg}</div>` : ""}</div></li>`;
  view(`<div class="page-head"><div class="title"><h1>How it works</h1>
    <div class="muted">The default method, <b>automatic_lithology_cv</b> (<code>methods/lithology_cv_pipeline.py</code>), explained step by step.</div></div></div>

  <details class="group how-more"><summary>📋 Project description: the challenge we are solving</summary><div class="how-body">
    <h3>Viridien challenge: lithology segmentation and clustering</h3>
    <a class="company" href="https://www.viridiengroup.com/" target="_blank" rel="noopener">
      <svg viewBox="0 0 1544 1530" width="18" height="18" aria-hidden="true"><path d="M1458 86H365A279 279 0 0 0 86 365V1165A279 279 0 0 0 365 1444H1180A279 279 0 0 0 1458 1165Z"
        fill="none" stroke="#3cff1a" stroke-width="172"/></svg>https://www.viridiengroup.com/</a>
    <p><b>The problem:</b> Take scanned images of geological well logs and automatically pull out the lithology column as structured data. That means which rock type sits at which depth.</p>
    <p><b>Background</b></p>
    <ul><li>A lithology column is a vertical strip in a well log. It uses colours and patterns (dots, dashes and so on) to show the rock type at each depth: shale, siltstone, sandstone and others.</li>
      <li>A lot of this data is stuck in PDFs and scans. Extracting it by hand is slow and repetitive. The data is useful for geothermal, carbon storage, critical minerals, hydrogen, and oil and gas.</li></ul>
    <p><b>What your solution must do</b></p>
    <ol><li><b>Segment</b> the column into intervals and find each interval’s top and bottom boundary in pixels.</li>
      <li><b>Cluster</b> intervals that have the same or a similar visual pattern.</li>
      <li><b>Match</b> each cluster to the legend to get the rock name.</li>
      <li><b>Convert</b> pixel positions to real depths using the depth column, giving top and base depth.</li>
      <li><b>Bonus:</b> find the depth column, lithology column and legend automatically, without the bounding boxes they supply.</li></ol>
    <p><b>Data</b></p>
    <ul><li>50 document images, with annotations giving bounding boxes for the depth column, the lithology column and the legend.</li>
      <li>You’re allowed to label the development images by hand for training and tuning.</li>
      <li>Hidden evaluation images are released near the end, so the solution has to generalise.</li></ul>
    <p><b>What you deliver for each document</b></p>
    <ul><li>A <b>CSV</b> with one row per interval: <code>top_y, bottom_y, cluster_id, lithology, top_depth, base_depth</code></li>
      <li>An <b>annotated image</b> of the column showing the boundaries, depths and cluster or lithology labels.</li></ul>
    <p><b>How it’s judged</b></p>
    <ul><li>The criteria are correctness, generalisation, efficiency (runtime, memory, hardware, cost), simplicity and full automation, with no manual fixes in the final predictions.</li>
      <li>In the demo you explain your approach, show results and limitations, and report runtime and resource use.</li>
      <li>They stress <b>correctness and efficiency</b>.</li></ul>
  </div></details>

  <div class="card team"><div><h2>Team</h2><div class="muted small">Project made for the <b>HackTheBurgh 2026</b></div></div>
    <div class="members">${["Arnau Claramunt", "Genís López", "Jaume López", "Pau Mayench"].map((n) =>
      `<span class="member"><span class="avatar">${n.split(" ").map((w) => w[0]).join("")}</span>${n}</span>`).join("")}</div></div>

  <div class="card"><h2>The idea in one picture</h2>
    <div class="flow">
      <div class="fbox"><b>Input</b><span>scanned log → 3 boxes found by YOLO</span></div><div class="farrow">→</div>
      <div class="fcol">
        <div class="fbox"><b>Legend</b><span>which pattern = which rock</span></div>
        <div class="fbox"><b>Lithology column</b><span>where the patterns change</span></div>
        <div class="fbox"><b>Depth column</b><span>which pixel row = which depth</span></div>
      </div><div class="farrow">→</div>
      <div class="fbox"><b>Intervals</b><span>top, bottom, rock name, depth</span></div><div class="farrow">→</div>
      <div class="fbox"><b>Output</b><span>CSV + annotated image</span></div>
    </div>
    <p class="muted small" style="margin:10px 0 0">The three boxes are read separately and combined at the end. Like a person would: look up the patterns in the
      legend, follow the column from top to bottom, and read the depth off the scale next to it.</p>
  </div>

  <ol class="how">
    ${step(1, "Find the three boxes", "Bonus", "A detector trained on the development scans (two <b>YOLO11</b> models, run on the CPU) finds the <b>depth column</b>, the <b>lithology column</b> and the <b>legend</b> on the image. If it fails, the organisers' <code>.json</code> is used instead. Switch: <i>Automatic bounding boxes</i> on the run's setup page.")}
    ${step(2, "Clean the column", "", "Cut out the lithology column. Remove its frame and the thin grid lines printed across it, so only the rock patterns remain. Measure how big the pattern repeats (e.g. dots every 12 px): every later step adapts to that size, so small and large scans work the same way.")}
    ${step(3, "Read the legend", "", "Find every little sample box in the legend (a <i>swatch</i>) and read the word next to it with OCR. Several readers try in turn, from fast to slow, and the one whose names look most like real rock names wins. Pau's <b>grid reader</b> is one of them: the slowest but the most careful, it always runs, and wins when its names are better.",
      "Swatch <span class='sw-dots'></span> + text “Sandstone” → the rock <b>Sandstone</b> looks like dots.")}
    ${step(4, "Cut the column into intervals", "Segment", "Go down the column row by row and notice where the pattern changes. A change = a boundary between two rock layers. <b>Coloured logs</b>: compare each row's colour with the legend colours. <b>Black-and-white logs</b>: describe the texture (dots? lines? how dense?) and look for where it changes. Every boundary is then snapped to the exact pixel row.")}
    ${step(5, "Group similar intervals", "Cluster", "Intervals that look the same get the same cluster number, even if they are far apart in the column (e.g. three separate sandstone layers = one cluster).")}
    ${step(6, "Give each group its rock name", "Match", "Compare each cluster with every legend swatch and take the most similar one. The swatches are compared at several sizes and line thicknesses, because the column is rarely drawn at exactly the legend's size. If two swatches look identical, the name is marked as <b>ambiguous (?)</b>.")}
    ${step(7, "Turn pixels into depths", "Convert", "Read the numbers printed in the depth column with OCR. Each number gives one point: <i>pixel row → depth</i>. Draw the best straight line through the points, ignoring misread numbers. Now every pixel row has a depth. (This runs at the same time as steps 2–6, on the second processor core.)",
      "Row 1 200 says 1 000 m, row 3 200 says 1 100 m → 1 row = 0.05 m.")}
    ${step(8, "Write the results", "", "Join neighbouring intervals with the same rock, then write the <b>CSV</b> (one row per interval) and draw the <b>annotated image</b> (boundaries, depths and names on the column).")}
  </ol>

  <details class="group how-more"><summary>🔬 More details: the technical version</summary><div class="how-body">
    <h3>Column and segmentation</h3>
    <ul>
      <li><b>Ink mask</b>: each pixel is ink or paper; for colour logs, “relative ink” = pixels darker than the median lightness − 30, so thin dark lines on a coloured fill still count.</li>
      <li><b>Pattern period P</b>: measured from the column's vertical autocorrelation; windows, kernels and minimum interval lengths are all expressed in multiples of P.</li>
      <li><b>Colour mode</b> (≥ 3 legend swatches with real colour): every row's fill colour in CIE Lab is matched to legend colour groups (ΔE76 &lt; 10); swatches that share a colour are told apart by texture inside the group (density and stroke-direction floors). A row is blank only if it has no ink <i>and</i> paper colour (keeps white Chalk).</li>
      <li><b>Texture mode</b> (black-and-white): sliding windows described by stroke directions, blobs, ink density; k-means with k chosen by silhouette score; a guard removes short “transition” clusters between two real layers; boundaries are refined to the exact row.</li>
    </ul>
    <h3>Legend</h3>
    <ul>
      <li>Readers, all run: <b>size-groups</b> (Tesseract, boxes of equal size), <b>text-anchored</b> (Tesseract, words first), <b>grid</b> (Pau's <code>lithology/legend_grid.py</code>, RapidOCR, one OCR per swatch column, non-overlapping cells); Pau's older parser only if grid finds nothing.</li>
      <li>Each result gets a <b>name-quality score</b> (+1 per clean rock name, −0.5 glued, −1 junk); the best score wins, on a tie the Tesseract readers (validated on the labelled log).</li>
      <li>Strip-shaped legends are normalised (swatch boxes re-aligned to a common column/row grid). Look-alike swatches are grouped; ties are broken by how common the rock is, and flagged.</li>
    </ul>
    <h3>Automatic bounding boxes</h3>
    <ul>
      <li><code>bbox-final/</code>: a <b>general YOLO11s</b> (3 classes, page tiles at 1536 px) and a <b>legend specialist</b> (2048 px; fragments + frame reconstruction, multi-view retry), exported to <b>OpenVINO int8</b> for the CPU.</li>
      <li>Runs as a separate process in its own environment (<code>bbox-final/.venv</code>, created by <code>setup_auto_boxes.sh</code>). PDFs are rendered first, at the <code>.json</code>'s size when there is one.</li>
      <li>Order: detector → uploaded <code>.json</code> → error. A missed depth column is taken from the <code>.json</code>. The overlap (IoU) with the <code>.json</code> boxes is reported; the boxes used are saved as <code>&lt;stem&gt;.boxes.json</code>.</li>
    </ul>
    <h3>Depth</h3>
    <ul>
      <li><b>Tesseract</b> in 3 000-row strips: the middle strip is read both sideways and upright to find the label orientation, then 8 evenly spaced strips (16 if fewer than 8 labels fit).</li>
      <li><b>Robust fit</b> (Pau's algorithm): RANSAC over label pairs, then drop labels far from the line (MAD) and refit; piecewise-linear if one line leaves rms &gt; 3 px.</li>
      <li>Accepted with ≥ 4 labels and rms ≤ 5 px; otherwise the other orientation, then <b>RapidOCR</b> on 4 → 8 → 12 chunks.</li>
    </ul>
    <h3>Speed</h3>
    <ul>
      <li>Depth OCR runs in a background thread while the column is analysed. Tesseract uses one OpenMP thread per call (2–3× faster on small crops).</li>
      <li>Legend picture drawn with PIL instead of matplotlib; the full-resolution annotated TIF is only drawn when you download the Output zip.</li>
      <li>On 2 CPU cores, no GPU: the boxes take ~1–2 min per page, Pau's grid legend reader up to a few minutes, the rest ~10–25 s.</li>
    </ul>
    <h3>Other methods (for comparison on the Evaluation page)</h3>
    <dl class="kv">
      <dt><code>lithology_cv</code></dt><dd>The same, with the boxes from the uploaded <code>.json</code>.</dd>
      <dt><code>hybrid_v3</code></dt><dd>Same as lithology_cv without Pau's grid legend reader.</dd>
      <dt><code>hybrid_v2</code></dt><dd>Colour mode, blank fix and legend quality score; simpler depth fit. The shared engine the newer methods plug into.</dd>
      <dt><code>hybrid</code></dt><dd>First hybrid: classic CV segmentation with OCR fallbacks.</dd>
      <dt><code>lithology2</code></dt><dd>lithology + clean-up (<code>methods/lithology2.py</code>): black-and-white patterns named by the shapes of their ink blobs (dots, dashes, bars, Г hooks, bricks), intervals that look alike get one name, intervals that match nothing take the previous/next name (or stay blank), no "?".</dd>
      <dt><code>lithology</code></dt><dd>Pau's <code>lithology/</code> package on its own (classifies windows directly against the legend).</dd>
      <dt><code>cv</code></dt><dd>Classic computer vision only (clustering, Tesseract).</dd>
    </dl>
    <h3>Code map</h3>
    <dl class="kv">
      <dt><code>lithology_pipeline.py</code></dt><dd>Entry point: settings (<code>Config</code>), picks the method, runs a folder, command line.</dd>
      <dt><code>methods/lithology_cv_pipeline.py</code></dt><dd>Automatic boxes with fallback, legend readers competition, wiring of the steps above.</dd>
      <dt><code>methods/auto_boxes.py</code></dt><dd>Runs the YOLO detector (<code>bbox-final/</code>) on one scan.</dd>
      <dt><code>methods/hybrid_v3.py</code></dt><dd>Depth reading (Tesseract strips, robust fit, RapidOCR fallback), fast legend picture.</dd>
      <dt><code>methods/hybrid_v2.py</code></dt><dd>The engine: column, colour mode, segmentation, naming, outputs.</dd>
      <dt><code>methods/cv_pipeline.py</code></dt><dd>Building blocks: ink masks, texture features, clustering, rendering, metrics.</dd>
      <dt><code>lithology/</code></dt><dd>Pau's package: RapidOCR, legend grid parser, depth fit, TIF drawing.</dd>
    </dl>
  </div></details>

  <div class="card"><h2>What each output shows</h2><dl class="kv">
    <dt><code>&lt;stem&gt;.csv</code></dt><dd>One row per interval: top/bottom depth, lithology, top/bottom pixel row, cluster (submission format).</dd>
    <dt>Column view</dt><dd>Depth + lithology columns zoomed in; a coloured band per interval, a line and the depth at each boundary, the cluster id on the right (<b>?</b> = ambiguous name).</dd>
    <dt>Page view</dt><dd>The whole scan with the three annotation boxes and the same painting.</dd>
    <dt>Legend</dt><dd>The swatches found in the legend box and the names OCR read; look-alike swatches in red.</dd>
    <dt>Depth</dt><dd>Depth labels read by OCR (blue = used, red = rejected) and the fitted line depth = a·y + b.</dd>
    <dt>Accuracy</dt><dd>Only for logs with ground truth: % of pixel rows with the right rock, boundary error in px, boundary F1 within 10 px.</dd></dl></div>`);
}

// ---------------------------------------------------------------------------------------------------------------------
// One run: header + tabs
// ---------------------------------------------------------------------------------------------------------------------
async function viewRun(id, tab) {
  let st;
  try { st = await api(`/api/runs/${id}`); } catch (e) { view(`<div class="empty">Run not found. <a href="#/runs">Back to runs</a></div>`); return; }
  const m = st.meta;
  if (!tab) tab = m.status === "draft" ? "setup" : m.status === "running" ? "monitor" : "results";
  const hasData = m.mode !== "import";
  view(`
  <div class="page-head"><div class="title">
      <h1><span id="runTitle">${esc(m.name || "Run " + m.id)}</span> <button class="icon-btn" id="renameBtn" title="Rename" style="width:auto;padding:0 6px;font-size:12px">✎</button> <span id="runBadge">${badge(m.status)}</span></h1>
      <div class="small muted"><span class="mono">${m.id}</span> · created ${esc(m.created)}${m.source ? ` · source: <span class="mono">${esc(short(m.source, 90))}</span>` : ""}${m.error ? ` · <span class="no">${esc(m.error)}</span>` : ""}</div></div>
    <div class="row">
      ${hasData && m.status !== "draft" ? `<button id="cloneBtn" title="New draft with the same files and settings">Clone &amp; re-run</button>` : ""}
      ${m.status !== "draft" ? `<a class="btn" href="/api/runs/${id}/download?what=csv">⬇ CSVs (.zip)</a><a class="btn" href="/api/runs/${id}/download?what=all" title="CSVs + all PNGs (can be large)">⬇ Everything</a>` : ""}
      <button class="danger" id="delBtn" ${m.status === "running" ? "disabled" : ""}>Delete</button>
    </div></div>
  <nav class="tabs">
    ${hasData ? `<a href="#/run/${id}/setup" class="${tab === "setup" ? "active" : ""}">1 · Files &amp; settings</a>` : ""}
    ${hasData ? `<a href="#/run/${id}/monitor" class="${tab === "monitor" ? "active" : ""}">2 · Monitor</a>` : ""}
    <a href="#/run/${id}/results" class="${tab === "results" ? "active" : ""}">${hasData ? "3 · " : ""}Results</a>
  </nav>
  <div id="runBody"></div>`);
  $("#renameBtn").onclick = async () => {
    const n = prompt("Run name", m.name || "");
    if (n === null) return;
    try { await api(`/api/runs/${id}`, { method: "PATCH", json: { name: n } }); $("#runTitle").textContent = n || "Run " + id; } catch (e) { fail(e); }
  };
  if ($("#cloneBtn")) $("#cloneBtn").onclick = () => cloneRun(id);
  $("#delBtn").onclick = async () => {
    if (!confirm("Delete this run folder (outputs included)? Linked original files are not touched.")) return;
    try { await api(`/api/runs/${id}`, { method: "DELETE" }); toast("Run deleted"); location.hash = "#/runs"; } catch (e) { fail(e); }
  };
  if (tab === "setup") return tabSetup(id, st);
  if (tab === "monitor") return tabMonitor(id, st);
  return tabResults(id, st);
}

// ---------------------------------------------------------------------------------------------------------------------
// Tab 1: files, pre-flight, settings
// ---------------------------------------------------------------------------------------------------------------------
async function tabSetup(id, st) {
  const m = st.meta;
  const draft = m.status === "draft";
  const cfg = await getConfig();
  const body = $("#runBody");
  if (!draft) {
    const ov = m.config_overrides || {};
    body.innerHTML = `<div class="notice">This run has ${m.status === "running" ? "started" : "finished"}; files and settings are locked. Use <b>Clone &amp; re-run</b> to change them.</div>
      <div class="grid2"><div class="card"><h2>Settings used</h2>${Object.keys(ov).length ? `<dl class="kv">${Object.entries(ov).map(([k, v]) => `<dt class="mono">${esc(k)}</dt><dd>${esc(JSON.stringify(v))} <span class="muted small">(default ${esc(JSON.stringify(cfg.fields.find((f) => f.name === k)?.default))})</span></dd>`).join("")}</dl>` : `<div class="muted">All defaults.</div>`}</div>
      <div class="card"><h2>Logs in this run (${(m.selected || []).length})</h2><div class="scroll-y small mono">${(m.selected || []).map(esc).join("<br>")}</div></div></div>`;
    return;
  }
  S.sel = new Set();
  S.seen = new Set();                                   // images already shown as valid (so a manual untick sticks)
  S.cfgValues = Object.fromEntries(cfg.fields.map((f) => [f.name, f.default]));
  Object.assign(S.cfgValues, m.config_overrides || {});
  body.innerHTML = `
  <div class="card"><div class="card-head"><h2>Add logs</h2><span class="muted small">single log or batch: add as many as you like before starting</span></div>
    <div class="grid2">
      <div><div class="dropzone" id="drop" tabindex="0" role="button"><strong>Drop files</strong> or click to choose<br><span class="small">images, .json annotations, .csv labels</span></div>
        <input type="file" id="fileInput" multiple class="hidden" accept=".tif,.tiff,.png,.jpg,.jpeg,.pdf,.json,.csv"></div>
      <div><label class="small muted" for="folderPath">…or link a folder on this computer</label>
        <div class="field-row" style="margin:4px 0 6px"><input type="text" id="folderPath" list="dataList" placeholder="/path/to/dataset"></div>
        <div class="field-row"><input type="text" id="labelsPath" list="labelsList" placeholder="ground-truth folder (optional; default cv_approach_1/labels)"><button id="linkBtn">Add folder</button></div>
        <datalist id="dataList"></datalist><datalist id="labelsList"></datalist></div>
    </div>
    <div id="uploads" class="hidden"><div class="row" style="margin-top:12px"><strong class="small">Uploading</strong><span class="small muted" id="upTotal"></span></div>
      <div class="progress" style="margin:6px 0"><div id="upBar" style="width:0%"></div></div><div class="upload-list" id="upList"></div></div>
    <div id="fileList"></div>
  </div>
  <label class="auto-toggle card" id="autoBox"><input type="checkbox" id="autoBoxes">
    <div><b>Automatic bounding boxes</b><div class="small muted" id="autoNote"></div></div></label>
  <div class="card"><div class="card-head"><h2>Pre-flight check</h2><span id="pfSummary" class="small muted"></span>
    <span class="small muted" style="flex-basis:100%;margin-top:-6px">One row per image found: are its image, annotation (.json) and optional ground truth there and valid? Ticked images are processed (all valid ones by default).</span><div class="spacer"></div>
    <button id="selAll">Select all</button><button id="selNone">Select none</button></div><div id="pf"></div></div>
  <div class="card"><div class="card-head"><h2>Settings</h2><span class="small muted" id="cfgSummary"></span><div class="spacer"></div>
    <select id="presetSel" title="Load a preset"></select><button id="savePreset">Save as preset</button><button id="resetCfg">Reset to defaults</button></div>
    <p class="small muted" style="margin-top:-4px">Every pipeline parameter (<code>Config</code>). Lengths in <i>periods</i> scale with each log's measured pattern period P. Changed values are highlighted.</p>
    <div id="cfgForm"></div></div>
  <div class="card" style="position:sticky;bottom:10px;z-index:5"><div class="row"><div id="startInfo" class="spacer"></div>
    <button class="primary big" id="startBtn">▶ Start run</button></div></div>`;

  api("/api/suggestions").then((s) => {
    $("#dataList").innerHTML = s.data.map((p) => `<option value="${esc(p)}">`).join("");
    $("#labelsList").innerHTML = s.labels.map((p) => `<option value="${esc(p)}">`).join("");
  }).catch(() => {});
  wireDrop($("#drop"), $("#fileInput"), (files) => uploadFiles(id, files));
  addBrowse($("#folderPath"), "Choose a folder with images + .json annotations");
  addBrowse($("#labelsPath"), "Choose the ground-truth (.csv) folder");
  $("#linkBtn").onclick = async () => {
    const path = $("#folderPath").value.trim();
    if (!path) return toast("Type the folder path first", true);
    try {
      const r = await api(`/api/runs/${id}/link`, { method: "POST", json: { path, labels: $("#labelsPath").value.trim() } });
      toast(`Linked ${r.linked} log(s)`); renderPreflight(id, r.preflight);
    } catch (e) { fail(e); }
  };
  $("#selAll").onclick = () => { S.sel = new Set(S.pf.rows.filter((r) => r.ok).map((r) => r.stem)); renderPreflight(id, S.pf); };
  $("#selNone").onclick = () => { S.sel = new Set(); S.pf.rows.forEach((r) => S.seen.add(r.stem)); renderPreflight(id, S.pf); };
  S.setupId = id; S.cfg = cfg;
  $("#autoBoxes").onchange = (e) => { S.cfgValues.automatic_bounding_boxes = e.target.checked; renderConfigForm(cfg); };
  renderConfigForm(cfg);
  $("#startBtn").onclick = () => startRun(id, cfg);
  await refreshPreflight(id);
  if (S.pendingUpload) { const f = S.pendingUpload; S.pendingUpload = null; uploadFiles(id, f); }
}

// automatic bounding boxes: method automatic_lithology_cv + its switch. Then an image needs no .json.
const autoOn = () => S.cfgValues && S.cfgValues.method === "automatic_lithology_cv" && !!S.cfgValues.automatic_bounding_boxes;
async function refreshPreflight(id) {
  try { renderPreflight(id, await api(`/api/runs/${id}/preflight?auto=${autoOn()}`)); } catch (e) { fail(e); }
}
function onCfgChange() {
  const box = $("#autoBoxes");
  if (!box) return;
  const isAutoMethod = S.cfgValues.method === "automatic_lithology_cv";
  box.checked = autoOn();
  box.disabled = !isAutoMethod;
  $("#autoBox").classList.toggle("on", autoOn());
  $("#autoNote").innerHTML = !isAutoMethod ? `Only with the method <code>automatic_lithology_cv</code> (now <code>${esc(S.cfgValues.method)}</code>): boxes come from the .json.`
    : autoOn() ? "On: the depth column, lithology column and legend are found on the image, even when a .json is uploaded. If that fails, the uploaded .json is used. Images without a .json are accepted."
    : "Off: the boxes come from the uploaded .json (each image needs one).";
  if (S.pf && S.setupId && !!S.pf.auto !== autoOn()) refreshPreflight(S.setupId);
}

async function removeFile(id, name, el) {
  try {
    const pf = await api(`/api/runs/${id}/data/${enc(name)}`, { method: "DELETE" });
    if (el) el.remove();
    toast(`Removed ${name}`);
    renderPreflight(id, pf);
  } catch (e) { fail(e); }
}

function renderFileList(id, pf) {
  const box = $("#fileList");
  if (!box) return;
  const mism = new Set((pf.mismatches || []).flatMap((m) => [m.json, ...m.images]));
  const files = [];
  for (const r of pf.rows) {
    if (r.json) files.push({ name: r.json, kind: "annotation", ok: r.ok && !mism.has(r.json) });
    if (r.image) files.push({ name: r.image.name, kind: "image", ok: r.ok && !mism.has(r.image.name) });
    if (r.labels) files.push({ name: r.labels.name, kind: "ground truth", ok: r.labels.ok });
  }
  for (const u of pf.unmatched) if (!files.some((f) => f.name === u.name)) files.push({ name: u.name, kind: /\.json$/i.test(u.name) ? "annotation" : /\.csv$/i.test(u.name) ? "ground truth" : "image", ok: false, why: u.problem });
  // only images, or only annotations, so far: nothing can match yet. Wait for the other file instead of warning.
  const hasImg = files.some((f) => f.kind === "image"), hasJson = files.some((f) => f.kind === "annotation");
  const waiting = !(hasImg && hasJson) && !(pf.auto && hasImg);
  if (waiting) files.forEach((f) => { if (!f.ok && (f.kind === "image" || f.kind === "annotation")) { f.pending = true; f.why = f.kind === "image" ? "waiting for its .json annotation" : "waiting for its image"; } });
  box.innerHTML = files.length ? `<div class="row" style="margin-top:14px"><strong class="small">Files in this run (${files.length})</strong><span class="small muted">✕ removes a file (e.g. the one whose name does not match)</span></div>
    <div class="file-list">${files.map((f) => `<div class="file-item ${f.ok || f.pending ? "" : "bad"}">
      <span class="${f.ok ? "yes" : f.pending ? "muted" : "no"}" title="${f.ok ? "matched" : esc(f.why || "not matched / not usable")}">${f.ok ? "✓" : f.pending ? "…" : "⚠"}</span>
      <span class="ellipsis mono small" title="${esc(f.name)}">${esc(f.name)}${f.pending ? ` <span class="muted">· ${esc(f.why)}</span>` : ""}</span><span class="badge">${esc(f.kind)}</span>
      <button class="icon-btn" data-rmfile="${esc(f.name)}" title="Remove ${esc(f.name)} from this run" aria-label="Remove ${esc(f.name)}">✕</button></div>`).join("")}</div>` : "";
  $$("[data-rmfile]", box).forEach((b) => (b.onclick = () => removeFile(id, b.dataset.rmfile)));
}

function renderPreflight(id, pf) {
  if (S.setupId === id && !!pf.auto !== autoOn()) { S.pf = pf; refreshPreflight(id); return; }   // answer of another action: re-check
  S.pf = pf;
  renderFileList(id, pf);
  const ok = pf.rows.filter((r) => r.ok);
  for (const r of ok) if (!S.seen.has(r.stem)) { S.seen.add(r.stem); S.sel.add(r.stem); }   // new valid image: ticked by default
  for (const s of [...S.sel]) if (!ok.some((r) => r.stem === s)) S.sel.delete(s);
  const nLab = pf.rows.filter((r) => r.labels && r.labels.ok).length;
  $("#pfSummary").innerHTML = pf.rows.length ? `${ok.length} ready · ${pf.rows.length - ok.length} with errors · ${nLab} with labels` : "";
  const ck = (b) => (b ? `<span class="yes">✓</span>` : `<span class="no">✗</span>`);
  const nImg = pf.rows.filter((r) => r.image).length + pf.unmatched.filter((u) => u.problem.startsWith("image without")).length;
  const nJson = pf.rows.length;
  if (!(pf.auto && nImg) && (nImg === 0) !== (nJson === 0)) {   // one side only: wait for the other file (unless automatic boxes)
    $("#pf").innerHTML = `<div class="notice">${nImg ? "Image added. Now add its <code>.json</code> annotation (same name, e.g. <code>log.tif</code> + <code>log.json</code>)."
      : "Annotation added. Now add its image (same name, e.g. <code>log.json</code> + <code>log.tif</code>)."}</div>`;
    updateStartInfo();
    return;
  }
  $("#pf").innerHTML = !pf.rows.length && !pf.unmatched.length ? `<div class="empty">No files yet. Drop a log's image (+ its .json) above, or link a folder.</div>` : `
    <div class="table-wrap scroll-y"><table class="data"><thead><tr><th></th><th>Log</th><th>Image</th><th class="num">Size</th><th class="num">Pixels</th><th>Boxes</th><th>Labels</th><th>Problems</th><th></th></tr></thead><tbody>
    ${pf.rows.map((r) => `<tr class="${r.ok ? "" : "bad"}">
      <td><input type="checkbox" data-sel="${esc(r.stem)}" ${S.sel.has(r.stem) ? "checked" : ""} ${r.ok ? "" : "disabled"} aria-label="select"></td>
      <td class="ellipsis" style="max-width:340px" title="${esc(r.stem)}">${esc(r.stem)}</td>
      <td>${r.image ? ck(true) + ` <span class="small muted">${esc((r.image.name.split(".").pop() || "").toUpperCase())} · ${esc({ "1": "1-bit", L: "grey", P: "palette", RGB: "colour", RGBA: "colour", CMYK: "colour" }[r.image.mode] || r.image.mode || "")}</span>` : ck(false)}</td>
      <td class="num">${r.image ? fmtNum(r.image.size_mb, 1) + " MB" : "–"}</td>
      <td class="num nowrap">${r.image && r.image.width ? `${fmtInt(r.image.width)} × ${fmtInt(r.image.height)}` : "–"}</td>
      <td class="nowrap">${r.boxes_source === "automatic" ? `<span class="badge ok" title="found on the image by the detector">automatic</span>${r.json ? ` <span class="small muted" title="used if the automatic boxes fail">.json fallback ${r.json_ok ? "✓" : "✗"}</span>` : ""}` : ck(r.json_ok)}</td>
      <td>${r.labels ? (r.labels.ok ? `<span class="yes">✓</span> <span class="small muted">${esc(r.labels.name)}</span>` : `<span class="no">✗</span>`) : `<span class="muted">–</span>`}</td>
      <td class="small">${r.errors.map((e) => `<div class="no">${esc(e)}</div>`).join("")}${r.warnings.map((w) => `<div style="color:var(--warn)">${esc(w)}</div>`).join("")}</td>
      <td><button class="icon-btn" data-rm="${esc(r.stem)}" title="Remove this log from the run">×</button></td></tr>`).join("")}
    </tbody></table></div>
    ${(pf.mismatches || []).length ? `<div class="notice err" style="margin-top:12px" role="alert"><strong>⛔ File names do not match: the run cannot start.</strong>
      <div class="small" style="margin:4px 0 6px">Each <code>.json</code> annotation must have exactly the same name as its image, e.g. <code>Callaway.tif</code> + <code>Callaway.json</code>. Rename the file(s) and upload them again, or remove them.</div>
      ${pf.mismatches.map((m) => `<div class="small">• ${esc(m.message)} <a href="#" data-rmf="${esc(m.json)}">remove ${esc(m.json)}</a></div>`).join("")}</div>` : ""}
    ${pf.unmatched.length ? `<div class="notice warn" style="margin-top:12px"><strong>Not used (${pf.unmatched.length}):</strong>
      ${pf.unmatched.map((u) => `<div class="small"><span class="mono">${esc(u.name)}</span>: ${esc(u.problem)} <a href="#" data-rmf="${esc(u.name)}">remove</a></div>`).join("")}</div>` : ""}`;
  $$("[data-sel]").forEach((c) => (c.onchange = () => { c.checked ? S.sel.add(c.dataset.sel) : S.sel.delete(c.dataset.sel); updateStartInfo(); }));
  $$("[data-rm]").forEach((b) => (b.onclick = async () => { try { renderPreflight(id, await api(`/api/runs/${id}/logs/${enc(b.dataset.rm)}`, { method: "DELETE" })); } catch (e) { fail(e); } }));
  $$("[data-rmf]").forEach((a) => (a.onclick = async (e) => { e.preventDefault(); try { renderPreflight(id, await api(`/api/runs/${id}/data/${enc(a.dataset.rmf)}`, { method: "DELETE" })); } catch (er) { fail(er); } }));
  updateStartInfo();
}

function uploadFiles(id, files) {
  if (!files.length) return;
  const box = $("#uploads"), list = $("#upList");
  box.classList.remove("hidden");
  const total = files.reduce((a, f) => a + f.size, 0);
  let doneBytes = 0;
  const items = files.map((f) => {
    const el = document.createElement("div");
    el.className = "upload-item";
    el.innerHTML = `<span class="ellipsis" title="${esc(f.name)}">${esc(f.name)}</span><span class="num small muted">${fmtNum(f.size / 1e6, 1)} MB</span><div class="progress"><div style="width:0%"></div></div>
      <button class="icon-btn rm-file" title="Remove ${esc(f.name)} from this run" aria-label="Remove ${esc(f.name)}" disabled>✕</button>`;
    $(".rm-file", el).onclick = () => removeFile(id, f.name, el);
    list.prepend(el);
    return el;
  });
  const bar = (p) => { $("#upBar").style.width = `${(100 * p).toFixed(1)}%`; $("#upTotal").textContent = `${(100 * p).toFixed(0)}% of ${fmtNum(total / 1e6, 1)} MB`; };
  let i = 0;
  const next = () => {
    if (i >= files.length) { bar(1); toast(`Uploaded ${files.length} file(s)`); return; }
    const f = files[i], el = items[i];
    const fd = new FormData();
    fd.append("files", f, f.name);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/runs/${id}/files`);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) { $(".progress > div", el).style.width = `${(100 * e.loaded) / e.total}%`; bar((doneBytes + e.loaded) / total); } };
    xhr.onload = () => {
      let r = null;
      try { r = JSON.parse(xhr.responseText); } catch (e) { /* */ }
      if (xhr.status >= 300 || !r) { el.querySelector(".progress").classList.add("failed"); toast(`${f.name}: ${(r && r.detail) || xhr.statusText}`, true); }
      else {
        $(".progress > div", el).style.width = "100%";
        if (r.rejected.length) { el.querySelector(".progress").classList.add("failed"); toast(`${f.name}: ${r.rejected[0].error}`, true); }
        else { el.querySelector(".progress").classList.add("done"); $(".rm-file", el).disabled = false; }
        renderPreflight(id, r.preflight);
      }
      doneBytes += f.size; i++; next();
    };
    xhr.onerror = () => { toast(`${f.name}: upload failed`, true); doneBytes += f.size; i++; next(); };
    xhr.send(fd);
  };
  next();
}

function renderConfigForm(cfg) {
  const groups = {};
  cfg.fields.forEach((f) => (groups[f.group] = groups[f.group] || []).push(f));
  const val = (f) => S.cfgValues[f.name];
  const input = (f) => {
    const v = val(f);
    if (f.type === "bool") return `<label class="check"><input type="checkbox" data-cfg="${f.name}" ${v ? "checked" : ""}> ${v ? "on" : "off"}</label>`;
    if (f.type === "list") return `<input type="text" data-cfg="${f.name}" value="${esc(v.join(", "))}" title="comma-separated ${f.elem}s">`;
    if (f.type === "str") return f.choices ? `<select data-cfg="${f.name}">${f.choices.map((c) => `<option ${c === v ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>` : `<input type="text" data-cfg="${f.name}" value="${esc(v)}">`;
    return `<input type="number" data-cfg="${f.name}" value="${esc(v)}" step="${f.type === "int" ? 1 : "any"}">`;
  };
  const presetOpts = () => `<option value="">Load a preset…</option><optgroup label="Built-in">${Object.keys(cfg.presets.builtin).map((n) => `<option value="b:${esc(n)}">${esc(n)}</option>`).join("")}</optgroup>
    ${Object.keys(cfg.presets.user).length ? `<optgroup label="Saved">${Object.keys(cfg.presets.user).map((n) => `<option value="u:${esc(n)}">${esc(n)}</option>`).join("")}</optgroup>` : ""}`;
  $("#presetSel").innerHTML = presetOpts();
  $("#cfgForm").innerHTML = Object.entries(groups).map(([g, fs]) => `<details class="group" ${g === "Method" || g === "Lithology method" ? "open" : ""}><summary>${esc(g)} <span class="badge" data-gcount="${esc(g)}"></span></summary>
    <div class="cfg-grid">${fs.map((f) => `<div class="cfg" data-field="${f.name}"><div><div class="name">${f.name}</div><div class="help">${esc(f.help)} <span class="muted">· default ${esc(Array.isArray(f.default) ? f.default.join(", ") : f.default)}</span></div></div>
      ${input(f)}<button class="reset" data-reset="${f.name}" title="Reset to default">↺</button></div>`).join("")}</div></details>`).join("");
  const parse = (f, el) => {
    if (f.type === "bool") return el.checked;
    if (f.type === "list") return el.value.split(/[\s,]+/).filter(Boolean).map(Number);
    if (f.type === "str") return el.value;
    return f.type === "int" ? parseInt(el.value, 10) : parseFloat(el.value);
  };
  $$("[data-cfg]").forEach((el) => {
    const f = cfg.fields.find((x) => x.name === el.dataset.cfg);
    el.oninput = el.onchange = () => {
      S.cfgValues[f.name] = parse(f, el);
      if (f.type === "bool") el.parentElement.lastChild.textContent = el.checked ? " on" : " off";
      markChanged(cfg);
      onCfgChange();
    };
  });
  $$("[data-reset]").forEach((b) => (b.onclick = () => { const f = cfg.fields.find((x) => x.name === b.dataset.reset); S.cfgValues[f.name] = f.default; renderConfigForm(cfg); }));
  $("#resetCfg").onclick = () => { cfg.fields.forEach((f) => (S.cfgValues[f.name] = f.default)); renderConfigForm(cfg); };
  $("#presetSel").onchange = (e) => {
    const v = e.target.value;
    if (!v) return;
    const p = v.startsWith("b:") ? cfg.presets.builtin[v.slice(2)] : cfg.presets.user[v.slice(2)];
    cfg.fields.forEach((f) => (S.cfgValues[f.name] = f.default));
    Object.assign(S.cfgValues, p || {});
    renderConfigForm(cfg);
    toast(`Preset loaded: ${v.slice(2)}`);
  };
  $("#savePreset").onclick = async () => {
    const n = prompt("Preset name");
    if (!n) return;
    try { const r = await api("/api/presets", { method: "POST", json: { name: n, config: overrides(cfg) } }); cfg.presets = r; renderConfigForm(cfg); toast("Preset saved"); } catch (e) { fail(e); }
  };
  markChanged(cfg);
  onCfgChange();
}
function overrides(cfg) {
  const o = {};
  for (const f of cfg.fields) if (JSON.stringify(S.cfgValues[f.name]) !== JSON.stringify(f.default)) o[f.name] = S.cfgValues[f.name];
  return o;
}
function markChanged(cfg) {
  const o = overrides(cfg);
  $$(".cfg").forEach((el) => el.classList.toggle("changed", el.dataset.field in o));
  const per = {};
  cfg.fields.forEach((f) => { if (f.name in o) per[f.group] = (per[f.group] || 0) + 1; });
  $$("[data-gcount]").forEach((b) => { const n = per[b.dataset.gcount] || 0; b.textContent = n ? `${n} changed` : ""; b.className = "badge" + (n ? " running" : ""); });
  const n = Object.keys(o).length;
  $("#cfgSummary").textContent = n ? `${n} setting(s) changed from default` : "all defaults";
  updateStartInfo();
}
function updateStartInfo() {
  if (!$("#startInfo") || !S.pf || !S.cfg) return;
  const n = S.sel ? S.sel.size : 0, c = Object.keys(overrides(S.cfg)).length;
  const lab = S.pf.rows.filter((r) => S.sel && S.sel.has(r.stem) && r.labels && r.labels.ok).length;
  const busy = S.health && S.health.busy;
  const mism = (S.pf.mismatches || []).length;
  if (mism) {
    $("#startInfo").innerHTML = `<span class="no">⛔ Fix the file names first: ${mism} annotation(s) do not have the same name as their image.</span>`;
    $("#startBtn").disabled = true;
    return;
  }
  $("#startInfo").innerHTML = n ? `<strong>${n}</strong> log(s) selected${lab ? ` (${lab} with labels → accuracy)` : ""} · ${c ? `<strong>${c}</strong> changed setting(s)` : "default settings"}${busy ? ` · <span style="color:var(--warn)">another run is in progress: this one will be queued</span>` : ""}`
    : `<span class="muted">Select at least one valid log.</span>`;
  $("#startBtn").disabled = !n;
}
async function startRun(id, cfg) {
  $("#startBtn").disabled = true;
  try {
    await api(`/api/runs/${id}/start`, { method: "POST", json: { config: overrides(cfg), logs: [...S.sel], queue: true } });
    location.hash = `#/run/${id}/monitor`;
    refreshHealth();
  } catch (e) { fail(e); $("#startBtn").disabled = false; }
}

// ---------------------------------------------------------------------------------------------------------------------
// Tab 2: live monitor (Server-Sent Events)
// ---------------------------------------------------------------------------------------------------------------------
function tabMonitor(id, st) {
  const body = $("#runBody");
  if (st.meta.status === "draft") { body.innerHTML = `<div class="empty">Not started yet. <a href="#/run/${id}/setup">Add files and start the run →</a></div>`; return; }
  body.innerHTML = `
  <div class="card">
    <div class="card-head"><h2>Progress</h2><span id="mStatus"></span><div class="spacer"></div><button class="danger" id="cancelBtn">■ Cancel run</button></div>
    <div class="progress lg striped" id="pBar"><div style="width:0%"></div><span class="label" id="pLabel">0%</span></div>
    <div class="stats" style="margin:14px 0 0">
      <div class="stat"><div class="label">Logs done</div><div class="value" id="mDone">–</div><div class="sub" id="mErr"></div></div>
      <div class="stat"><div class="label">Elapsed</div><div class="value" id="mElapsed">–</div><div class="sub" id="mEta"></div></div>
      <div class="stat"><div class="label">Worker memory</div><div class="value" id="mRss">–</div><div class="sub" id="mPeak"></div></div>
      <div class="stat"><div class="label">Free RAM</div><div class="value" id="mFree">–</div><div class="sub">on this computer</div></div>
    </div>
    <div id="mCurrent" style="margin-top:14px"></div>
  </div>
  <div id="mFinished"></div>
  <div class="grid2">
    <div class="card"><div class="card-head"><h2>Finished logs</h2><span class="small muted">click a row to inspect it</span></div><div class="table-wrap scroll-y" id="mTable"></div></div>
    <div class="card"><div class="card-head"><h2>Event log</h2><div class="spacer"></div>
      <select id="evFilter"><option value="all">all events</option><option value="logs">logs only (no stages)</option><option value="err">errors only</option></select></div>
      <div class="console" id="console"></div></div>
  </div>`;
  const con = $("#console");
  let filter = store.get("evFilter", "logs");
  $("#evFilter").value = filter;
  $("#evFilter").onchange = (e) => { filter = e.target.value; store.set("evFilter", filter); applyFilter(); };
  const applyFilter = () => $$("div", con).forEach((d) => d.classList.toggle("hidden", (filter === "logs" && d.dataset.k === "stage") || (filter === "err" && d.dataset.k !== "err")));
  const line = (e) => {
    const t = e.t ? new Date(e.t * 1000).toLocaleTimeString() : "";
    let txt = "", cls = "", k = e.event;
    switch (e.event) {
      case "worker_start": txt = `worker started (pid ${e.pid})`; break;
      case "start": txt = `▶ ${e.n_logs} log(s) to process`; cls = "e-start"; break;
      case "log_start": txt = `[${e.index}/${e.total}] ${e.log}`; cls = "e-start"; break;
      case "stage": txt = `    ${e.stage.padEnd(14)} ${e.seconds.toFixed(2)} s`; break;
      case "log_done": {
        const m = e.metrics;
        txt = `    ✓ ${e.n_intervals} intervals · ${e.runtime_s.toFixed(1)} s${m ? ` · row acc ${m.row_acc}% · boundary ${m.boundary_mean_px} px` : ""}${e.ambiguous && e.ambiguous.length ? ` · ambiguous: ${e.ambiguous.join(", ")}` : ""}`;
        cls = "e-ok";
        if (e.legend) txt += `\n    legend (${e.legend.length}): ${e.legend.length ? e.legend.map((x) => x.name + (x.ignored ? " (ignored)" : "")).join(", ") : "no swatches found"}`;
        break;
      }
      case "log_error": txt = `    ✗ ERROR ${e.error}`; cls = "e-err"; k = "err"; break;
      case "done": txt = `■ done in ${fmtSec(e.total_s)} · errors: ${e.n_errors}${e.mean_metrics ? ` · mean row acc ${e.mean_metrics.row_acc}%` : ""}`; cls = "e-start"; break;
      case "fatal": txt = `✗ FATAL ${e.error}`; cls = "e-err"; k = "err"; break;
      default: txt = JSON.stringify(e);
    }
    const near = con.scrollHeight - con.scrollTop - con.clientHeight < 40;
    const d = document.createElement("div");
    d.dataset.k = k;
    d.innerHTML = `<span class="t">${esc(t)}</span><span class="${cls}">${esc(txt)}</span>`;
    if ((filter === "logs" && k === "stage") || (filter === "err" && k !== "err")) d.classList.add("hidden");
    con.appendChild(d);
    if (near) con.scrollTop = con.scrollHeight;
  };
  let tableTimer = null;
  const refreshTable = () => {
    clearTimeout(tableTimer);
    tableTimer = setTimeout(async () => {
      try {
        const s = await api(`/api/runs/${id}/status`);
        const rows = s.progress.logs.filter((r) => r.status !== "running").reverse();
        $("#mTable").innerHTML = rows.length ? `<table class="data"><thead><tr><th class="num">#</th><th>Log</th><th class="num">Intervals</th><th class="num">Time</th><th class="num">Row acc.</th><th>Ambiguous</th></tr></thead><tbody>
          ${rows.map((r) => `<tr class="click ${r.status === "error" ? "bad" : ""}" data-href="#/run/${id}/log/${enc(r.stem)}"><td class="num">${r.index}</td>
            <td class="ellipsis" style="max-width:260px" title="${esc(r.stem)}">${esc(r.stem)}</td>
            ${r.status === "error" ? `<td colspan="4" class="no small">${esc(r.error)}</td>` : `<td class="num">${fmtInt(r.n_intervals)}</td><td class="num">${fmtSec(r.runtime_s)}</td>
            <td class="num">${r.metrics ? fmtNum(r.metrics.row_acc, 1) + "%" : "–"}</td><td class="small">${esc((r.ambiguous || []).join(", "))}</td>`}</tr>`).join("")}</tbody></table>`
          : `<div class="empty">Nothing finished yet.</div>`;
      } catch (e) { /* */ }
    }, 250);
  };
  const setStatus = (s) => {
    const pct = Math.round(1000 * s.fraction) / 10;
    const bar = $("#pBar");
    $("div", bar).style.width = `${s.status === "done" ? 100 : pct}%`;
    $("#pLabel").textContent = s.status === "done" ? "100% · done" : `${pct}%`;
    bar.classList.toggle("striped", s.status === "running");
    bar.classList.toggle("done", s.status === "done");
    bar.classList.toggle("failed", ["failed", "cancelled", "interrupted"].includes(s.status));
    $("#mStatus").innerHTML = badge(s.status);
    if ($("#runBadge")) $("#runBadge").innerHTML = badge(s.status);
    $("#cancelBtn").classList.toggle("hidden", !["running", "queued"].includes(s.status));
    if (s.status === "queued") $("#mCurrent").innerHTML = `<div class="notice">Queued: waits for the run in progress to finish, then starts automatically.</div>`;
    if (s.worker && s.worker.cancelling) $("#cancelBtn").disabled = true;
    $("#mDone").textContent = `${s.finished} / ${s.total || "?"}`;
    $("#mErr").innerHTML = s.n_errors ? `<span class="no">${s.n_errors} error(s)</span>` : "no errors";
    $("#mElapsed").textContent = fmtSec(s.elapsed_s);
    $("#mEta").textContent = s.status === "running" ? (s.eta_s != null ? `≈ ${fmtSec(s.eta_s)} left` : "estimating…") : "";
    $("#mRss").textContent = s.worker && s.worker.rss_mb != null ? fmtMB(s.worker.rss_mb) : "–";
    $("#mPeak").textContent = s.worker && s.worker.peak_rss_mb ? `peak ${fmtMB(s.worker.peak_rss_mb)}` : "";
    $("#mFree").textContent = fmtMB(s.mem_available_mb);
    const c = s.current;
    if (s.status !== "queued") $("#mCurrent").innerHTML = c && s.status === "running" ? `<div class="row"><strong>Now:</strong> <span class="mono ellipsis" title="${esc(c.stem)}">[${c.index}/${s.total}] ${esc(c.stem)}</span>
      <span class="muted small">${c.running_s != null ? fmtSec(c.running_s) + " on this log" : ""}</span></div>
      <div class="stepper">${(c.stage_names || STAGES).map((g) => `<span class="step ${g in c.stages ? "done" : g === c.stage ? "now" : ""}">${g in c.stages ? "✓ " : ""}${g}${g in c.stages ? ` ${c.stages[g].toFixed(1)}s` : ""}</span>`).join("")}</div>
      <div class="progress"><div style="width:${(100 * Object.keys(c.stages).length) / (c.stage_names || STAGES).length}%"></div></div>` : "";
  };
  const finished = (status) => {
    $("#mFinished").innerHTML = `<div class="notice ${status === "done" ? "ok" : "warn"}">Run ${esc(status)}. <a href="#/run/${id}/results"><strong>Open the results dashboard →</strong></a></div>`;
    refreshHealth();
  };
  $("#cancelBtn").onclick = async () => {
    if (!confirm("Cancel this run? Logs already finished keep their outputs.")) return;
    try { await api(`/api/runs/${id}/cancel`, { method: "POST" }); toast("Cancelling…"); } catch (e) { fail(e); }
  };
  const es = new EventSource(`/api/runs/${id}/events`);
  onCleanup(() => es.close());
  onCleanup(() => clearTimeout(tableTimer));
  es.onmessage = (m) => { const e = JSON.parse(m.data); line(e); if (e.event === "log_done" || e.event === "log_error") refreshTable(); };
  es.addEventListener("status", (m) => setStatus(JSON.parse(m.data)));
  es.addEventListener("end", (m) => { es.close(); refreshTable(); finished(JSON.parse(m.data).status); });
  refreshTable();
}

// ---------------------------------------------------------------------------------------------------------------------
// Tab 3: results dashboard
// ---------------------------------------------------------------------------------------------------------------------
const COLUMNS = [
  { key: "stem", label: "Log", get: (r) => r.stem, always: true },
  { key: "flags", label: "Flags", get: (r) => r._flags.length, html: (r) => r._flags.map((f) => `<span class="flag ${f === "error" ? "err" : ""}">${esc(f)}</span>`).join("") },
  { key: "method", label: "Method", get: (r) => r.method || "cv" },
  { key: "n_intervals", label: "Intervals", num: true, get: (r) => r.n_intervals },
  { key: "k", label: "k", num: true, get: (r) => r.k },
  { key: "colour", label: "Colour", get: (r) => (r.colour_scan == null ? null : r.colour_scan ? 1 : 0), html: (r) => (r.colour_scan == null ? "–" : r.colour_scan ? "yes" : "no") },
  { key: "period", label: "Period P (px)", num: true, get: (r) => r.scale && r.scale.period, html: (r) => (r.scale ? fmtNum(r.scale.period, 0) + (r.scale.measured ? "" : " *") : "–") },
  { key: "swatches", label: "Swatches", num: true, get: (r) => r.swatches && r.swatches.length },
  { key: "depth", label: "Depth", get: (r) => (r.depth ? r.depth.n_used : -1), html: (r) => (r._error ? "–" : r.depth ? `<span class="yes">✓</span> ${r.depth.n_used}/${r.depth.n_read}` : r.partial ? "–" : `<span class="no">✗</span>`) },
  { key: "ambiguous", label: "Ambiguous names", get: (r) => (r.ambiguous || []).join(", "), html: (r) => `<span class="small">${esc((r.ambiguous || []).join(", "))}</span>` },
  { key: "names", label: "Lithologies", get: (r) => r._names.length, html: (r) => `<span class="small" title="${esc(r._names.join(", "))}">${esc(short(r._names.join(", "), 40))}</span>` },
  { key: "runtime_s", label: "Runtime", num: true, get: (r) => r.runtime_s, html: (r) => fmtSec(r.runtime_s) },
  { key: "render", label: "Render", num: true, get: (r) => r.timings_s && r.timings_s.render, html: (r) => fmtSec(r.timings_s && r.timings_s.render), hidden: true },
  { key: "peak_mb", label: "Peak MB", num: true, get: (r) => r.peak_mb, html: (r) => fmtInt(r.peak_mb) },
  { key: "row_acc", label: "Row acc. %", num: true, get: (r) => r.metrics && r.metrics.row_acc, html: (r) => (r.metrics ? fmtNum(r.metrics.row_acc, 2) : "–") },
  { key: "bnd", label: "Boundary err. px", num: true, get: (r) => r.metrics && r.metrics.boundary_mean_px, html: (r) => (r.metrics ? fmtNum(r.metrics.boundary_mean_px, 2) : "–") },
  { key: "f1", label: "Boundary F1 %", num: true, get: (r) => r.metrics && r.metrics.boundary_f1, html: (r) => (r.metrics ? fmtNum(r.metrics.boundary_f1, 1) : "–") },
];

async function loadSummary(id, force = false) {
  if (!force && S.summaries[id] && !S.summaries[id].partial) return S.summaries[id];
  S.summaries[id] = await api(`/api/runs/${id}/summary`);
  return S.summaries[id];
}

async function tabResults(id, st) {
  const body = $("#runBody");
  if (st.meta.status === "draft") { body.innerHTML = `<div class="empty">No results yet. <a href="#/run/${id}/setup">Add files and start the run →</a></div>`; return; }
  body.innerHTML = `<div class="pad muted">Loading results…</div>`;
  let s;
  try { s = await loadSummary(id, true); } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const rows = s.logs.map((r) => ({ ...r, _names: [...new Set((r.intervals || []).map((i) => i.lithology))], _error: false }));
  for (const e of s.errors || []) rows.push({ stem: e.log, _error: true, _names: [], error: e.error, ambiguous: [] });
  rows.forEach((r) => (r._flags = r._error ? ["error"] : flagsOf(r)));
  const labelled = rows.filter((r) => r.metrics);
  const mm = s.mean_metrics;
  const nSusp = rows.filter((r) => r._flags.length).length;
  const flagCounts = {};
  rows.forEach((r) => r._flags.forEach((f) => (flagCounts[f] = (flagCounts[f] || 0) + 1)));
  const totalIntervals = rows.reduce((a, r) => a + (r.n_intervals || 0), 0);
  body.innerHTML = `
  ${s.partial ? `<div class="notice">${st.meta.status === "running" ? "Run in progress: showing the logs finished so far (details complete when the run ends)." : "This run has no summary.json (it did not finish): showing what the events recorded."} </div>` : ""}
  <div class="stats">
    <div class="stat"><div class="label">Logs processed</div><div class="value">${fmtInt(s.logs.length)}</div><div class="sub">of ${fmtInt(s.n_logs)} · ${fmtInt(totalIntervals)} intervals</div></div>
    <div class="stat ${s.n_errors ? "err" : ""}"><div class="label">Errors</div><div class="value">${fmtInt(s.n_errors)}</div><div class="sub">${s.n_errors ? "see list below" : "none"}</div></div>
    <div class="stat ${nSusp ? "warn" : ""}"><div class="label">Suspicious logs</div><div class="value">${nSusp}</div><div class="sub">at least one flag</div></div>
    <div class="stat"><div class="label">Total runtime</div><div class="value">${fmtSec(s.total_s)}</div><div class="sub">${s.logs.length ? `${fmtSec(s.logs.reduce((a, r) => a + (r.runtime_s || 0), 0) / s.logs.length)} per log (no render)` : ""}</div></div>
    ${mm ? `<div class="stat"><div class="label">Mean row accuracy</div><div class="value">${fmtNum(mm.row_acc, 2)}%</div><div class="sub">${labelled.length} labelled log(s)</div></div>
      <div class="stat"><div class="label">Mean boundary error</div><div class="value">${fmtNum(mm.boundary_mean_px, 2)} px</div><div class="sub">max ${fmtNum(mm.boundary_max_px, 0)} px</div></div>
      <div class="stat"><div class="label">Boundary F1</div><div class="value">${fmtNum(mm.boundary_f1, 1)}%</div><div class="sub">within 10 px</div></div>`
      : `<div class="stat"><div class="label">Accuracy</div><div class="value" style="font-size:16px;margin-top:6px">no labels</div><div class="sub">add <code>labels/&lt;stem&gt;.csv</code> to measure it; judge the images below</div></div>`}
  </div>
  ${Object.keys(flagCounts).length ? `<div class="card"><div class="card-head"><h2>Flags</h2><span class="small muted">click a flag to filter the table</span></div><div class="row">
    ${Object.entries(flagCounts).sort((a, b) => b[1] - a[1]).map(([f, n]) => `<button data-flag="${esc(f)}"><span class="flag ${f === "error" ? "err" : ""}">${esc(f)}</span> ${n} log(s)</button>`).join("")}
    <button data-flag="">show all</button></div></div>` : ""}
  <div class="card tight"><div class="row" style="padding:10px 12px;border-bottom:1px solid var(--line)">
      <h2 style="margin:0">Logs</h2><input type="text" id="q" placeholder="Search logs…" style="width:220px">
      <label class="check small"><input type="checkbox" id="suspOnly"> suspicious only</label><span class="small muted" id="flagFilterInfo"></span>
      <div class="spacer"></div><div class="colmenu"><button id="colBtn">Columns ▾</button><div class="menu hidden" id="colMenu"></div></div></div>
    <div class="table-wrap" style="max-height:620px" id="logsTable"></div></div>
  <div class="grid2">
    <div class="card"><h2>Runtime per stage</h2><div class="chart-box" id="stageBox"><canvas id="stageChart"></canvas></div></div>
    <div class="card"><h2>Accuracy per labelled log</h2><div class="chart-box" id="accBox" style="height:300px"><canvas id="accChart"></canvas></div>
      <div id="confusion"></div></div>
  </div>
  ${(s.errors || []).length ? `<div class="card"><h2>Errors</h2>${s.errors.map((e) => `<div class="small"><a href="#/run/${id}/log/${enc(e.log)}" class="mono">${esc(e.log)}</a>: <span class="no">${esc(e.error)}</span></div>`).join("")}</div>` : ""}`;

  // table: sort, search, filters, column chooser (remembered in this browser)
  let sort = store.get("sort", { key: "stem", dir: 1 });
  let hidden = new Set(store.get("hiddenCols", COLUMNS.filter((c) => c.hidden).map((c) => c.key)));
  let flagFilter = "";
  const draw = () => {
    const q = $("#q").value.toLowerCase(), susp = $("#suspOnly").checked;
    const col = COLUMNS.find((c) => c.key === sort.key) || COLUMNS[0];
    const list = rows.filter((r) => r.stem.toLowerCase().includes(q) && (!susp || r._flags.length) && (!flagFilter || r._flags.includes(flagFilter)))
      .sort((a, b) => { const x = col.get(a), y = col.get(b); if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1; return (x > y ? 1 : x < y ? -1 : 0) * sort.dir; });
    const cols = COLUMNS.filter((c) => c.always || !hidden.has(c.key));
    $("#flagFilterInfo").innerHTML = flagFilter ? `filter: <span class="flag">${esc(flagFilter)}</span>` : "";
    $("#logsTable").innerHTML = `<table class="data"><thead><tr>${cols.map((c) => `<th class="sortable ${c.num ? "num" : ""}" data-sort="${c.key}">${c.label}${sort.key === c.key ? `<span class="arrow">${sort.dir > 0 ? "▲" : "▼"}</span>` : ""}</th>`).join("")}</tr></thead>
      <tbody>${list.map((r) => `<tr class="click ${r._error ? "bad" : ""}" data-href="#/run/${id}/log/${enc(r.stem)}">${cols.map((c) => c.key === "stem"
        ? `<td class="ellipsis" style="max-width:330px" title="${esc(r.stem)}">${esc(r.stem)}</td>`
        : r._error && c.key !== "flags" ? (c.key === "n_intervals" ? `<td colspan="${cols.length - 2}" class="small no">${esc(r.error)}</td>` : "")
        : `<td class="${c.num ? "num" : ""}">${c.html ? c.html(r) : c.get(r) == null ? "–" : esc(c.get(r))}</td>`).join("")}</tr>`).join("")}</tbody></table>
      ${list.length ? "" : `<div class="empty">No log matches.</div>`}`;
    $$("[data-sort]").forEach((th) => (th.onclick = () => { sort = { key: th.dataset.sort, dir: sort.key === th.dataset.sort ? -sort.dir : 1 }; store.set("sort", sort); draw(); }));
  };
  $("#q").oninput = draw;
  $("#suspOnly").onchange = draw;
  $$("[data-flag]").forEach((b) => (b.onclick = () => { flagFilter = b.dataset.flag; draw(); }));
  $("#colMenu").innerHTML = COLUMNS.filter((c) => !c.always).map((c) => `<label class="check small"><input type="checkbox" data-col="${c.key}" ${hidden.has(c.key) ? "" : "checked"}> ${c.label}</label>`).join("");
  $("#colBtn").onclick = (e) => { e.stopPropagation(); $("#colMenu").classList.toggle("hidden"); };
  const closeMenu = (e) => { if (!e.target.closest(".colmenu")) $("#colMenu") && $("#colMenu").classList.add("hidden"); };
  document.addEventListener("click", closeMenu);
  onCleanup(() => document.removeEventListener("click", closeMenu));
  $$("[data-col]").forEach((c) => (c.onchange = () => { c.checked ? hidden.delete(c.dataset.col) : hidden.add(c.dataset.col); store.set("hiddenCols", [...hidden]); draw(); }));
  draw();
  drawResultCharts(rows.filter((r) => !r._error), labelled);
}

// value labels at the end of bars (tiny Chart.js plugin, no extra library)
const valueLabels = {
  id: "valueLabels",
  afterDatasetsDraw(chart, args, opts) {
    if (!opts || !opts.enabled) return;
    const { ctx } = chart;
    ctx.save();
    ctx.font = "11px system-ui, sans-serif";
    ctx.fillStyle = cssVar("--muted");
    const horizontal = chart.options.indexAxis === "y";
    if (opts.stackTotal) {
      const metas = chart.data.datasets.map((_, i) => chart.getDatasetMeta(i)).filter((m) => !m.hidden);
      chart.data.labels.forEach((_, j) => {
        const total = chart.data.datasets.reduce((a, d, i) => a + (chart.getDatasetMeta(i).hidden ? 0 : d.data[j] || 0), 0);
        const last = metas[metas.length - 1];
        if (!last) return;
        const el = last.data[j];
        ctx.textBaseline = "middle"; ctx.textAlign = "left";
        ctx.fillText(opts.format(total), el.x + 4, el.y);
      });
    } else {
      chart.data.datasets.forEach((d, i) => {
        const meta = chart.getDatasetMeta(i);
        if (meta.hidden) return;
        meta.data.forEach((el, j) => {
          const v = d.data[j];
          if (v == null) return;
          ctx.textAlign = horizontal ? "left" : "center"; ctx.textBaseline = horizontal ? "middle" : "bottom";
          ctx.fillText((d.fmt || opts.format)(v), horizontal ? el.x + 4 : el.x, horizontal ? el.y : el.y - 3);
        });
      });
    }
    ctx.restore();
  },
};
function chartsReady() { return typeof window.Chart !== "undefined"; }
function chartDefaults() {
  Chart.defaults.color = cssVar("--muted");
  Chart.defaults.borderColor = cssVar("--line");
  Chart.defaults.font.family = "system-ui, -apple-system, Segoe UI, Roboto, sans-serif";
}
function drawResultCharts(rows, labelled) {
  if (!chartsReady()) { $("#stageBox").innerHTML = $("#accBox").innerHTML = `<div class="empty">Charts need Chart.js from the internet (cdn.jsdelivr.net).</div>`; return; }
  chartDefaults();
  const withT = rows.filter((r) => r.timings_s && Object.keys(r.timings_s).length)
    .sort((a, b) => Object.values(b.timings_s).reduce((x, y) => x + y, 0) - Object.values(a.timings_s).reduce((x, y) => x + y, 0));
  $("#stageBox").style.height = `${Math.max(220, 22 * withT.length + 70)}px`;
  const c1 = new Chart($("#stageChart"), {
    type: "bar",
    data: { labels: withT.map((r) => short(r.stem, 34)), datasets: STAGES.map((g, i) => ({ label: g, data: withT.map((r) => r.timings_s[g] || 0), backgroundColor: STAGE_COLOURS[i], borderWidth: 0, barPercentage: 0.8 })) },
    options: { indexAxis: "y", maintainAspectRatio: false, animation: false, layout: { padding: { right: 50 } },
      scales: { x: { stacked: true, beginAtZero: true, title: { display: true, text: "seconds" } }, y: { stacked: true, ticks: { autoSkip: false, font: { size: 10.5 } } } },
      plugins: { legend: { position: "top", labels: { boxWidth: 12 } }, valueLabels: { enabled: true, stackTotal: true, format: (v) => `${v.toFixed(1)} s` },
        tooltip: { callbacks: { title: (it) => withT[it[0].dataIndex].stem, label: (it) => ` ${it.dataset.label}: ${it.raw.toFixed(2)} s` } } },
      onClick: (e, els) => { if (els.length) location.hash = `#/run/${location.hash.split("/")[2]}/log/${enc(withT[els[0].index].stem)}`; } },
    plugins: [valueLabels],
  });
  onCleanup(() => c1.destroy());
  if (!labelled.length) {
    $("#accBox").style.height = "auto";
    $("#accBox").innerHTML = `<div class="empty">No labelled logs in this run.<br><span class="small">Put ground truth in <code>labels/&lt;stem&gt;.csv</code> (columns top_depth, bottom_depth, lithology, top_y, bottom_y).</span></div>`;
  } else {
    const c2 = new Chart($("#accChart"), {
      type: "bar",
      data: { labels: labelled.map((r) => short(r.stem, 28)), datasets: [
        { label: "Row accuracy (%)", data: labelled.map((r) => r.metrics.row_acc), backgroundColor: "#2a78d6", yAxisID: "y", fmt: (v) => `${v.toFixed(1)}%` },
        { label: "Boundary F1 (%)", data: labelled.map((r) => r.metrics.boundary_f1), backgroundColor: "#1baf7a", yAxisID: "y", fmt: (v) => `${v.toFixed(1)}%` },
        { label: "Boundary error (px)", data: labelled.map((r) => r.metrics.boundary_mean_px), backgroundColor: "#eda100", yAxisID: "y2", fmt: (v) => `${v.toFixed(2)} px` }] },
      options: { maintainAspectRatio: false, animation: false, layout: { padding: { top: 18 } },
        scales: { y: { beginAtZero: true, max: 100, title: { display: true, text: "%" } }, y2: { beginAtZero: true, position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "px" } } },
        plugins: { legend: { position: "top", labels: { boxWidth: 12 } }, valueLabels: { enabled: true, format: (v) => v.toFixed(1) },
          tooltip: { callbacks: { title: (it) => labelled[it[0].dataIndex].stem } } } },
      plugins: [valueLabels],
    });
    onCleanup(() => c2.destroy());
    $("#confusion").innerHTML = labelled.map((r) => `<h3 style="margin-top:16px">Confusion: <span class="mono small">${esc(short(r.stem, 50))}</span></h3>${confusionTable(r.metrics.confusion)}`).join("");
  }
}
function confusionTable(conf) {
  if (!conf || !Object.keys(conf).length) return `<div class="muted small">No confusion data.</div>`;
  const truths = Object.keys(conf), preds = [...new Set(truths.flatMap((t) => Object.keys(conf[t])))];
  return `<div class="table-wrap"><table class="data heat"><thead><tr><th>true ↓ / predicted →</th>${preds.map((p) => `<th class="num">${esc(p)}</th>`).join("")}</tr></thead><tbody>
    ${truths.map((t) => `<tr><th class="rowh">${esc(t)}</th>${preds.map((p) => {
      const v = conf[t][p] || 0, good = p.toLowerCase().split(" ").includes(t.toLowerCase());
      const bg = v ? (good ? `rgba(26,143,92,${0.12 + (0.6 * v) / 100})` : `rgba(192,54,44,${0.12 + (0.6 * v) / 100})`) : "transparent";
      return `<td class="cell" style="background:${bg}">${v ? v.toFixed(1) + "%" : ""}</td>`;
    }).join("")}</tr>`).join("")}</tbody></table><div class="small muted">Share of each true lithology's pixel rows (row-normalised). Green = correct.</div></div>`;
}

// ---------------------------------------------------------------------------------------------------------------------
// Log view: tiled image viewer + details
// ---------------------------------------------------------------------------------------------------------------------
class TileViewer {
  /* Shows a (very tall) PNG as 2048-px tiles, loading only those in view. Zoom, fit, minimap, highlight band, hover readout. */
  constructor(root, runId) {
    this.root = root; this.runId = runId; this.z = 1; this.m = null; this.imgs = [];
    root.innerHTML = `<div class="viewer-body"><div class="minimap" title="Overview: click or drag to move"><img alt=""><div class="vp"></div><div class="mk hidden"></div></div>
      <div class="scroller"><div class="canvas"><div class="band hidden"></div><div class="hline hidden"></div></div></div></div>
      <div class="readout">Move the mouse over the image.</div>`;
    this.sc = $(".scroller", root); this.cv = $(".canvas", root); this.mini = $(".minimap", root);
    this.sc.addEventListener("scroll", () => this.update(), { passive: true });
    this.sc.addEventListener("mousemove", (e) => this.hover(e));
    this.sc.addEventListener("mouseleave", () => { $(".hline", this.root).classList.add("hidden"); this.onLeave && this.onLeave(); });
    this.sc.addEventListener("wheel", (e) => { if (e.ctrlKey) { e.preventDefault(); this.zoomBy(e.deltaY < 0 ? 1.2 : 1 / 1.2, e); } }, { passive: false });
    let drag = false;
    const go = (e) => { const r = this.mini.getBoundingClientRect(); const f = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)); this.sc.scrollTop = f * this.cv.offsetHeight - this.sc.clientHeight / 2; };
    this.mini.addEventListener("mousedown", (e) => { drag = true; go(e); });
    const mm = (e) => drag && go(e), mu = () => (drag = false);
    window.addEventListener("mousemove", mm); window.addEventListener("mouseup", mu);
    this.ro = new ResizeObserver(() => this.update());
    this.ro.observe(this.sc);
    this.dispose = () => { window.removeEventListener("mousemove", mm); window.removeEventListener("mouseup", mu); this.ro.disconnect(); };
  }
  async load(file, fit = true) {
    this.root.querySelector(".readout").textContent = "Preparing tiles… (first time only, a few seconds for long logs)";
    const m = await api(`/api/runs/${this.runId}/tiles/${enc(file)}`);
    this.m = m;
    this.file = file;
    $$("img", this.cv).forEach((i) => i.remove());
    this.imgs = m.tiles.map((t, i) => {
      const img = document.createElement("img");
      img.alt = ""; img.draggable = false; img.dataset.src = `/api/runs/${this.runId}/files/${t.split("/").map(enc).join("/")}`;
      img.dataset.h = Math.min(m.tile_h, m.height - i * m.tile_h);
      this.cv.prepend(img);
      return img;
    });
    $("img", this.mini).src = `/api/runs/${this.runId}/files/${m.overview.split("/").map(enc).join("/")}`;
    this.root.querySelector(".readout").textContent = `${fmtInt(m.width)} × ${fmtInt(m.height)} px image · ${m.n} tile(s). Ctrl + wheel to zoom.`;
    if (fit) this.fit(); else this.layout();
  }
  layout() {
    if (!this.m) return;
    const { width: W, height: H, tile_h: T } = this.m, z = this.z;
    this.cv.style.width = `${W * z}px`; this.cv.style.height = `${H * z}px`;
    this.imgs.forEach((img, i) => { img.style.top = `${i * T * z}px`; img.style.width = `${W * z}px`; img.style.height = `${img.dataset.h * z}px`; });
    if (this.bandRange) this.band(...this.bandRange, false);
    this.update();
    this.onZoom && this.onZoom(z);
  }
  fit() { if (!this.m) return; this.z = Math.min(2, Math.max(0.05, (this.sc.clientWidth - 2) / this.m.width)); this.layout(); }
  zoomBy(f, e) {
    if (!this.m) return;
    const r = this.sc.getBoundingClientRect(), cy = e ? e.clientY - r.top : this.sc.clientHeight / 2;
    const yImg = (this.sc.scrollTop + cy) / this.z;
    this.z = Math.min(4, Math.max(0.05, this.z * f));
    this.layout();
    this.sc.scrollTop = yImg * this.z - cy;
  }
  setZoom(z) { this.zoomBy(z / this.z); }
  update() {
    if (!this.m) return;
    const top = this.sc.scrollTop, h = this.sc.clientHeight, T = this.m.tile_h * this.z, margin = h;
    this.imgs.forEach((img, i) => { const y0 = i * T, y1 = y0 + img.dataset.h * this.z; if (!img.src && y1 > top - margin && y0 < top + h + margin) img.src = img.dataset.src; });
    const H = this.cv.offsetHeight || 1, vp = $(".vp", this.mini);
    vp.style.top = `${(100 * top) / H}%`; vp.style.height = `${Math.min(100, (100 * h) / H)}%`;
  }
  scrollToImgY(y) { this.sc.scrollTop = y * this.z - this.sc.clientHeight / 3; }
  band(y0, y1, scroll = true) {
    this.bandRange = [y0, y1];
    const b = $(".band", this.root);
    b.classList.remove("hidden");
    b.style.top = `${y0 * this.z}px`; b.style.height = `${Math.max(2, (y1 - y0) * this.z)}px`;
    const mk = $(".mk", this.mini);
    mk.classList.remove("hidden"); mk.style.top = `${(100 * y0) / this.m.height}%`;
    if (scroll) this.sc.scrollTo({ top: Math.max(0, ((y0 + y1) / 2) * this.z - this.sc.clientHeight / 2), behavior: "smooth" });
  }
  hover(e) {
    if (!this.m) return;
    const r = this.cv.getBoundingClientRect(), y = (e.clientY - r.top) / this.z;
    const hl = $(".hline", this.root);
    hl.classList.remove("hidden"); hl.style.top = `${e.clientY - r.top}px`;
    if (this.onHover) this.root.querySelector(".readout").innerHTML = this.onHover(y);
  }
}

async function viewLog(id, stem) {
  setNav("runs");
  view(`<div class="pad muted">Loading log…</div>`);
  let d, summary = null;
  try {
    [d, summary] = await Promise.all([api(`/api/runs/${id}/logs/${enc(stem)}`), loadSummary(id).catch(() => null)]);
  } catch (e) { view(`<div class="empty">${esc(e.message)} · <a href="#/run/${id}/results">Back to results</a></div>`); return; }
  const r = d.record;
  const rows = r ? r.intervals : d.rows || [];
  const colour = colourMap(summary);
  const idx = d.order.indexOf(stem), prev = d.order[idx - 1], next = d.order[idx + 1];
  const flags = r ? flagsOf(r) : d.error ? ["error"] : [];
  const cb = d.boxes && d.boxes.interpreted_lithology_column;
  // full-image row y  <->  row in the rendered PNG (34-px colour key on top)
  const maps = {
    column_png: cb ? { toImg: (y) => 34 + (y - cb[1]) * d.column_scale, toFull: (py) => cb[1] + (py - 34) / d.column_scale } : null,
    page_png: { toImg: (y) => 34 + y * d.page_scale, toFull: (py) => (py - 34) / d.page_scale },
  };
  const depthAt = (y) => (r && r.depth ? r.depth.a * y + r.depth.b : null);
  const intervalAt = (y) => rows.findIndex((i) => y >= i.top_y && y < i.bottom_y);
  const cl = (cid) => (r && r.clusters ? r.clusters[String(cid)] : null);
  const hasMetrics = r && r.metrics;
  const tabs = [["intervals", `Intervals <span class="count">${rows.length}</span>`], ["clusters", "Clusters"], ["legend", `Legend <span class="count">${r ? r.swatches.length : "–"}</span>`],
    ["origlegend", "Original legend"], ["depth", "Depth"], ["info", "Info"], ...(hasMetrics ? [["acc", "Accuracy"]] : [])];
  let tab = store.get("logTab", "intervals");
  if (!tabs.some((t) => t[0] === tab)) tab = "intervals";
  let img = store.get("viewerImg", "column_png");
  if (!d.files[img]) img = d.files.column_png ? "column_png" : "page_png";

  view(`
  <div class="page-head"><div class="title">
      <div class="small"><a href="#/run/${id}/results">← Results</a> · log ${idx + 1} of ${d.order.length}</div>
      <h1 class="ellipsis" title="${esc(stem)}" style="max-width:100%">${esc(stem)}</h1>
      <div>${flags.map((f) => `<span class="flag ${f === "error" ? "err" : ""}">${esc(f)}</span>`).join("")}${r && !flags.length ? `<span class="badge ok">no flags</span>` : ""}</div></div>
    <div class="row">
      <button id="prevBtn" ${prev ? "" : "disabled"} title="Previous log (←)">← Prev</button><button id="nextBtn" ${next ? "" : "disabled"} title="Next log (→)">Next →</button>
      ${d.files.annotated_tif ? `<a class="btn" href="/api/runs/${id}/files/outputs/${enc(d.files.annotated_tif)}?download=true" title="High-resolution result in the challenge notebook's style">⬇ Annotated TIF</a>` : ""}
      ${d.files.csv ? `<a class="btn primary" id="outputBtn" href="/api/runs/${id}/logs/${enc(stem)}/output" title="Zip with the CSV and the annotated image (.tif)">⬇ Output</a>` : ""}
    </div></div>
  ${d.error ? `<div class="notice err"><strong>This log failed:</strong> ${esc(d.error)}</div>` : ""}
  ${!r && !d.error ? `<div class="notice">Details (clusters, legend, depth, timings) appear as soon as this log has finished; intervals come from the CSV.</div>` : ""}
  <div class="log-layout">
    <div class="card tight viewer-card">
      <div class="viewer-tools">
        <div class="seg">${d.files.column_png ? `<button data-img="column_png" class="${img === "column_png" ? "on" : ""}">Column</button>` : ""}${d.files.page_png ? `<button data-img="page_png" class="${img === "page_png" ? "on" : ""}">Whole page</button>` : ""}</div>
        <button id="zOut" title="Zoom out (-)">−</button><span class="small mono" id="zLbl" style="min-width:44px;text-align:center">100%</span><button id="zIn" title="Zoom in (+)">+</button>
        <button id="zFit" title="Fit width (0)">Fit</button><button id="z1" title="Actual size (1)">1:1</button>
        <div class="spacer"></div><a class="btn" id="fullBtn" target="_blank" rel="noopener" title="Open the full PNG in a new tab (heavy for long logs)">Full size ↗</a>
      </div>
      <div id="viewer">${d.files[img] ? "" : `<div class="empty">No image for this log.</div>`}</div>
    </div>
    <div class="side-panel"><div class="card">
      <nav class="tabs">${tabs.map(([k, l]) => `<button data-tab="${k}" class="${k === tab ? "active" : ""}">${l}</button>`).join("")}</nav>
      <div id="sideBody"></div></div></div>
  </div>`);

  const go = (s) => s && (location.hash = `#/run/${id}/log/${enc(s)}`);
  if ($("#outputBtn") && !d.files.annotated_tif) $("#outputBtn").onclick = () => toast("Preparing the annotated TIFF (first time only, a few seconds)…");
  $("#prevBtn").onclick = () => go(prev);
  $("#nextBtn").onclick = () => go(next);
  const key = (e) => {
    if (e.target.closest("input,select,textarea")) return;
    if (e.key === "ArrowLeft") go(prev);
    else if (e.key === "ArrowRight") go(next);
    else if (e.key === "+" || e.key === "=") viewer && viewer.zoomBy(1.25);
    else if (e.key === "-") viewer && viewer.zoomBy(0.8);
    else if (e.key === "0") viewer && viewer.fit();
    else if (e.key === "1") viewer && viewer.setZoom(1);
  };
  document.addEventListener("keydown", key);
  onCleanup(() => document.removeEventListener("keydown", key));

  let viewer = null, selected = -1;
  const highlightRow = (i, scrollRow) => {
    $$("#ivTable tr[data-i]").forEach((tr) => tr.classList.toggle("sel", Number(tr.dataset.i) === i));
    if (scrollRow) { const tr = $(`#ivTable tr[data-i="${i}"]`); tr && tr.scrollIntoView({ block: "nearest" }); }
  };
  const showInterval = (i) => {
    selected = i;
    const iv = rows[i], mp = maps[img];
    highlightRow(i);
    if (viewer && mp && iv) viewer.band(mp.toImg(iv.top_y), mp.toImg(iv.bottom_y));
  };
  if (d.files[img]) {
    viewer = new TileViewer($("#viewer"), id);
    onCleanup(() => viewer.dispose());
    viewer.onZoom = (z) => ($("#zLbl").textContent = `${Math.round(z * 100)}%`);
    viewer.onHover = (py) => {
      const mp = maps[img];
      if (!mp) return `png row ${Math.round(py)}`;
      const y = mp.toFull(py), dep = depthAt(y), i = intervalAt(y);
      highlightRow(i, true);
      const iv = rows[i];
      return `y = ${fmtInt(Math.round(y))} px` + (dep != null ? ` · depth ${fmtNum(dep, 1)}` : "") +
        (iv ? ` · <span class="swatch" style="background:${colour(iv.lithology)}"></span><strong>${esc(iv.lithology)}</strong> (cluster ${iv.cluster_id}${iv.ambiguous ? ", ambiguous" : ""})` : "");
    };
    viewer.onLeave = () => highlightRow(selected);
    const setFull = () => ($("#fullBtn").href = `/api/runs/${id}/files/outputs/${enc(d.files[img])}`);
    setFull();
    viewer.load(d.files[img]).catch(fail);
    $$("[data-img]").forEach((b) => (b.onclick = async () => {
      img = b.dataset.img; store.set("viewerImg", img);
      $$("[data-img]").forEach((x) => x.classList.toggle("on", x === b));
      setFull(); viewer.bandRange = null; $(".band", viewer.root).classList.add("hidden"); $(".mk", viewer.mini).classList.add("hidden");
      await viewer.load(d.files[img]).catch(fail);
      if (selected >= 0) showInterval(selected);
    }));
    $("#zIn").onclick = () => viewer.zoomBy(1.25);
    $("#zOut").onclick = () => viewer.zoomBy(0.8);
    $("#zFit").onclick = () => viewer.fit();
    $("#z1").onclick = () => viewer.setZoom(1);
  }

  const file = (f) => `/api/runs/${id}/files/outputs/${enc(f)}`;
  const side = {
    intervals: () => !rows.length ? `<div class="empty">No intervals.</div>` : `
      <div class="small muted" style="margin-bottom:6px">Click a row to show it on the image. <span class="q" style="cursor:default">?</span> = name is ambiguous (hover for alternatives).</div>
      <div class="table-wrap" style="max-height:calc(100vh - 290px)"><table class="data" id="ivTable"><thead><tr><th class="num">#</th><th class="num">Rows y (px)</th>
        <th class="num">Depth top → bottom</th><th>Lithology</th><th class="num">Cl.</th></tr></thead><tbody>
      ${rows.map((iv, i) => { const c = cl(iv.cluster_id); const alts = c && c.alternatives && c.alternatives.length ? c.alternatives.join(", ") : "";
        return `<tr class="click ${iv.ambiguous ? "amb" : ""}" data-i="${i}"><td class="num muted">${i + 1}</td><td class="num">${fmtInt(iv.top_y)}–${fmtInt(iv.bottom_y)}</td>
        <td class="num">${iv.top_depth == null ? "–" : `${fmtNum(iv.top_depth, 1)} → ${fmtNum(iv.bottom_depth, 1)}`}</td>
        <td class="nowrap"><span class="swatch" style="background:${colour(iv.lithology)}"></span>${esc(iv.lithology)}${iv.ambiguous ? `<span class="q" title="Ambiguous. Alternatives: ${esc(alts || "look-alike swatch")}">?</span>` : ""}</td>
        <td class="num">${iv.cluster_id}</td></tr>`; }).join("")}</tbody></table></div>`,
    clusters: () => !r ? `<div class="muted">Available when the run has finished.</div>` : !Object.keys(r.clusters).length ? `<div class="empty">No clusters (blank column).</div>` : `
      <div class="small muted" style="margin-bottom:8px">${(r.method === "lithology" || r.method === "lithology2") ? "Each interval is classified directly against the legend fingerprints; one row per lithology found. Confidence = (2nd best - best) / 2nd best distance, averaged over its intervals (higher = surer)." : "Each cluster of similar texture is named after the nearest legend swatch. Margin = how much better the best swatch is than the 2nd (higher = surer)."}</div>
      <table class="data"><thead><tr><th class="num">Cl.</th><th>Name</th><th class="num">Intervals</th><th class="num">Share</th><th class="num">${(r.method === "lithology" || r.method === "lithology2") ? "Confidence" : "Margin"}</th><th>Nearest swatches (distance)</th></tr></thead><tbody>
      ${Object.entries(r.clusters).map(([cid, c]) => {
        const ivs = rows.filter((iv) => String(iv.cluster_id) === cid), px = ivs.reduce((a, iv) => a + iv.bottom_y - iv.top_y, 0), tot = rows.reduce((a, iv) => a + iv.bottom_y - iv.top_y, 0) || 1;
        return `<tr class="${c.ambiguous ? "amb" : ""}"><td class="num">${cid}</td><td class="nowrap"><span class="swatch" style="background:${colour(c.name)}"></span>${esc(c.name)}${c.ambiguous ? `<span class="q" title="Alternatives: ${esc(c.alternatives.join(", "))}">?</span>` : ""}</td>
          <td class="num">${ivs.length}</td><td class="num">${fmtNum((100 * px) / tot, 0)}%</td><td class="num">${fmtNum(c.margin, 2)}</td>
          <td class="small">${Object.entries(c.distances).map(([n, v]) => `${esc(n)} <span class="muted">${fmtNum(v, 2)}</span>`).join("<br>") || "–"}</td></tr>`; }).join("")}</tbody></table>
      <dl class="kv" style="margin-top:12px"><dt>${(r.method === "lithology" || r.method === "lithology2") ? "Lithologies found" : "k (clusters)"}</dt><dd>${r.k}</dd>${r.silhouette != null ? `<dt>Silhouette</dt><dd>${fmtNum(r.silhouette, 3)}</dd>` : ""}<dt>Transition clusters removed</dt><dd>${(r.transition_clusters_removed || []).length ? r.transition_clusters_removed.join(", ") : "none"}</dd></dl>`,
    legend: () => !r ? `<div class="muted">Available when the run has finished.</div>` : `
      ${r.swatches.length ? "" : `<div class="notice warn"><strong>No legend swatches found.</strong> Every cluster is named "Unknown". The swatch finder expects same-size framed squares with a readable name below or to the right.</div>`}
      ${d.files.legend_png ? `<img class="legend-img zoomable" data-big="${file(d.files.legend_png)}" data-title="Detected legend swatches" src="${file(d.files.legend_png)}" alt="Detected legend swatches" title="Click to enlarge">
        <div class="row" style="margin-top:8px"><button id="cmpLegend" title="Detected legend (left) next to the legend printed on the scan (right)">⇆ Compare with original</button></div>` : ""}
      ${r.swatches.length ? `<table class="data" style="margin-top:10px"><thead><tr><th>Swatch name (OCR)</th><th>Looks like</th></tr></thead><tbody>
        ${r.swatches.map((w) => `<tr><td>${esc(w.name)}${w.ignored ? ` <span class="muted small">(ignored: ${esc(w.ignored)})</span>` : ""}</td><td class="${w.look_alikes.length ? "no" : "muted"}">${esc(w.look_alikes.join(", ") || "–")}</td></tr>`).join("")}</tbody></table>` : ""}`,
    depth: () => !r ? `<div class="muted">Available when the run has finished.</div>` : !r.depth ? `<div class="notice warn"><strong>No depth found.</strong> OCR could not read at least two consistent depth labels, so depths are empty.</div>` : `
      ${d.files.depth_png ? `<img class="depth-img zoomable" data-big="${file(d.files.depth_png)}" data-title="Depth calibration" src="${file(d.files.depth_png)}" alt="Depth calibration" title="Click to enlarge">` : ""}
      <dl class="kv" style="margin-top:10px"><dt>Equation</dt><dd class="mono">depth = ${r.depth.a.toFixed(6)} · y + ${fmtNum(r.depth.b, 2)}</dd>
        <dt>Labels used / read</dt><dd>${r.depth.n_used} / ${r.depth.n_read}</dd>${r.depth.model ? `<dt>Model</dt><dd>${esc(r.depth.model)} · rms ${fmtNum(r.depth.rms_px, 2)} px</dd>` : ""}
        <dt>Depth range</dt><dd>${rows.length && rows[0].top_depth != null ? `${fmtNum(rows[0].top_depth, 1)} → ${fmtNum(rows[rows.length - 1].bottom_depth, 1)}` : "–"}</dd>
        <dt>Scale</dt><dd>${fmtNum(1 / r.depth.a, 1)} px per depth unit</dd></dl>`,
    origlegend: () => {
      const src = `/api/runs/${id}/original-legend/${enc(stem)}`;
      const box = d.boxes && d.boxes.lithology_legend;
      return `<div class="small muted" style="margin-bottom:8px">The legend exactly as printed on the scan (the JSON's <code>lithology_legend</code> box${box ? `: x ${box[0]}–${box[2]}, y ${box[1]}–${box[3]}` : ""}). Compare it with the <b>Legend</b> tab to spot missing or misread names. Click to enlarge.</div>
        <img class="legend-img zoomable" data-big="${src}" data-title="Original legend (from the scan)" src="${src}" alt="Original legend" title="Click to enlarge"
          onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'notice warn',textContent:'Original legend not available (no legend box or original image).'}))">`;
    },
    info: () => !r ? `<div class="muted">Available when the run has finished.</div>` : `
      <dl class="kv"><dt>Colour scan</dt><dd>${r.colour_scan ? "yes (fill colour used)" : "no"}</dd>
        <dt>Method</dt><dd>${esc(r.method || "cv")}${r.segmentation ? ` · segmentation: ${esc(r.segmentation)}` : ""}</dd>${r.legend_method ? `<dt>Legend source</dt><dd class="small">${esc(r.legend_method)}</dd>` : ""}
        ${r.scale ? `<dt>Pattern period P</dt><dd>${fmtNum(r.scale.period, 1)} px ${r.scale.measured ? "(measured)" : '<span class="no">(not measured: default used)</span>'}</dd>
        <dt>Window / stride</dt><dd>${r.scale.window} / ${r.scale.stride} px</dd><dt>Min bed</dt><dd>${r.scale.min_bed} px</dd>
        <dt>Refine radius / window</dt><dd>${r.scale.refine_radius} / ${r.scale.refine_window} px</dd><dt>Stroke / diagonal kernel</dt><dd>${r.scale.stroke} / ${r.scale.diag} px</dd>`
        : `<dt>Window</dt><dd>${r.window_px ? r.window_px + " px" : "auto (median swatch height)"}</dd>`}
        ${r.depth_error ? `<dt>Depth</dt><dd class="no">${esc(r.depth_error)}</dd>` : ""}${r.annotated_tif_error ? `<dt>Annotated TIF</dt><dd class="no">${esc(r.annotated_tif_error)}</dd>` : ""}
        <dt>Runtime (no render)</dt><dd>${fmtSec(r.runtime_s)}</dd><dt>Peak memory (Python)</dt><dd>${fmtMB(r.peak_mb)}</dd>
        <dt>Image</dt><dd class="mono small">${esc(r.image)}</dd></dl>
      <h3 style="margin-top:14px">Stage timings</h3>
      ${(() => { const t = r.timings_s, tot = Object.values(t).reduce((a, b) => a + b, 0) || 1, STAGES = Object.keys(t);
        return `<div style="display:flex;height:16px;border-radius:4px;overflow:hidden;margin-bottom:6px">${STAGES.map((g, i) => `<div title="${g}: ${(t[g] || 0).toFixed(2)} s" style="width:${(100 * (t[g] || 0)) / tot}%;background:${STAGE_COLOURS[i]}"></div>`).join("")}</div>
        <table class="data"><tbody>${STAGES.map((g, i) => `<tr><td><span class="swatch" style="background:${STAGE_COLOURS[i]}"></span>${g}</td><td class="num">${(t[g] || 0).toFixed(2)} s</td><td class="num muted">${fmtNum((100 * (t[g] || 0)) / tot, 0)}%</td></tr>`).join("")}</tbody></table>`; })()}`,
    acc: () => !hasMetrics ? "" : `
      <div class="stats"><div class="stat"><div class="label">Row accuracy</div><div class="value">${fmtNum(r.metrics.row_acc, 2)}%</div></div>
        <div class="stat"><div class="label">Boundary error</div><div class="value">${fmtNum(r.metrics.boundary_mean_px, 2)} px</div><div class="sub">max ${fmtNum(r.metrics.boundary_max_px, 0)} px</div></div>
        <div class="stat"><div class="label">Boundary F1</div><div class="value">${fmtNum(r.metrics.boundary_f1, 1)}%</div><div class="sub">${r.metrics.n_pred} predicted / ${r.metrics.n_true} true intervals</div></div></div>
      ${confusionTable(r.metrics.confusion)}`,
  };
  const renderSide = () => {
    $("#sideBody").innerHTML = side[tab]();
    if ($("#cmpLegend")) $("#cmpLegend").onclick = () => openViewer([
      { src: file(d.files.legend_png), title: "Detected (what the pipeline found and read)" },
      { src: `/api/runs/${id}/original-legend/${enc(stem)}`, title: "Original (printed on the scan)" }], `Legend: ${stem}`);
    $$("#ivTable tr[data-i]").forEach((tr) => (tr.onclick = () => showInterval(Number(tr.dataset.i))));
    if (selected >= 0) highlightRow(selected);
  };
  $$("[data-tab]").forEach((b) => (b.onclick = () => { tab = b.dataset.tab; store.set("logTab", tab); $$("[data-tab]").forEach((x) => x.classList.toggle("active", x === b)); renderSide(); }));
  renderSide();
}

// ---------------------------------------------------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------------------------------------------------
$("#themeBtn").onclick = () => {
  const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "light" : "dark";
  store.set("theme", document.documentElement.dataset.theme);
  try { localStorage.setItem("litho.theme", document.documentElement.dataset.theme); } catch (e) { /* */ }
  route();
};
window.addEventListener("hashchange", route);
refreshHealth();
setInterval(refreshHealth, 3000);
route();

// ---------------------------------------------------------------------------------------------------------------------
// Lightbox: click any .zoomable image to see it big (click = fit / actual size, Esc or ✕ closes)
// ---------------------------------------------------------------------------------------------------------------------
function openLightbox(src, title) { openViewer([{ src, title }], title); }
function openViewer(panels, title) {
  /* Full-screen viewer with one image, or several side by side (e.g. detected vs original legend). */
  closeLightbox();
  const box = document.createElement("div");
  box.id = "lightbox";
  const one = panels.length === 1;
  box.innerHTML = `<div class="lb-bar"><span class="lb-title">${esc(title || "")}</span><span class="small lb-hint">click an image: fit ↔ actual size · Esc to close</span>
    ${one ? `<a class="btn" href="${esc(panels[0].src)}" target="_blank" rel="noopener">Open in new tab ↗</a>` : ""}<button class="btn" id="lbClose" aria-label="Close">✕</button></div>
    <div class="lb-panels">${panels.map((p) => `<div class="lb-panel">${one ? "" : `<div class="lb-cap"><span>${esc(p.title || "")}</span><a href="${esc(p.src)}" target="_blank" rel="noopener">open ↗</a></div>`}
      <div class="lb-body"><img src="${esc(p.src)}" alt="${esc(p.title || "")}" class="fit"></div></div>`).join("")}</div>`;
  document.body.appendChild(box);
  document.body.style.overflow = "hidden";
  $$("img", box).forEach((img) => (img.onclick = (e) => { e.stopPropagation(); img.classList.toggle("fit"); }));
  $$(".lb-body", box).forEach((b) => (b.onclick = (e) => { if (e.target === e.currentTarget) closeLightbox(); }));
  $("#lbClose").onclick = closeLightbox;
}
function closeLightbox() { const b = $("#lightbox"); if (b) { b.remove(); document.body.style.overflow = ""; } }
document.addEventListener("click", (e) => {
  const z = e.target.closest("img.zoomable");
  if (z) openLightbox(z.dataset.big || z.src, z.dataset.title || z.alt);
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeLightbox(); });
window.addEventListener("hashchange", closeLightbox);
