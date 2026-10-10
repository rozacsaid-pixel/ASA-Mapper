"use strict";

const $ = (sel) => document.querySelector(sel);
const OPTION_WIDGETS = new Set(["Radio", "MultipleCheckbox"]);
const STATUS_TEXT = { pending: "Not reviewed", accepted: "Accepted", changed: "Changed", none: "No ASA" };
const state = { session: null, decisions: {}, saveTimer: null };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const when = (iso) => (iso ? new Date(iso).toLocaleString() : "");

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (res.status === 401) { location.href = "/login"; throw new Error("Not signed in"); }
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const body = isJson ? await res.json() : null;
  if (!res.ok) throw new Error((body && body.detail) || res.statusText);
  return body;
}
const jsonOpts = (method, data) => ({ method, headers: { "content-type": "application/json" }, body: JSON.stringify(data) });

/* ---------- library ---------- */
async function loadLibraryStatus() {
  const s = await api("/api/library");
  const el = $("#library-status");
  if (!s.loaded) {
    el.innerHTML = `<span class="warn">No ASA Library loaded.</span> Upload the .xlsx or refresh from Google.`;
  } else {
    const issues = s.issue_count ? ` · <span class="muted" title="${esc(s.issues.join("\n"))}">${s.issue_count} library issues</span>` : "";
    el.innerHTML = `ASA List · ${s.names} field names, ${s.options} options · ${esc(s.source)} · ${when(s.synced_at)}${issues}`;
  }
  if (s.last_error) el.innerHTML += ` <span class="warn">${esc(s.last_error)}</span>`;
  $("#refresh-library").hidden = !s.google_configured;
}

$("#refresh-library").onclick = async () => {
  try { await api("/api/library/refresh", { method: "POST" }); } catch (e) { alert(e.message); }
  loadLibraryStatus();
};
$("#library-file").onchange = async (ev) => {
  const fd = new FormData();
  fd.append("file", ev.target.files[0]);
  try { await api("/api/library/upload", { method: "POST", body: fd }); } catch (e) { alert(e.message); }
  ev.target.value = "";
  loadLibraryStatus();
};

/* ---------- home ---------- */
async function showHome() {
  const rows = await api("/api/sessions");
  $("#sessions").innerHTML = rows.map((r) =>
    `<tr><td><a href="#/review/${r.id}">${esc(r.form_name)}</a></td><td>${when(r.created_at)}</td><td>${when(r.finished_at)}</td></tr>`
  ).join("") || `<tr><td colspan="3" class="muted">No reviews yet.</td></tr>`;
}

async function uploadExport(file) {
  const msg = $("#upload-msg");
  msg.textContent = "Reading the export and preparing suggestions…";
  const fd = new FormData();
  fd.append("file", file);
  const asaFile = $("#asa-file").files[0];
  if (asaFile) fd.append("asa_file", asaFile);
  try {
    const r = await api("/api/sessions", { method: "POST", body: fd });
    msg.textContent = "";
    if (r.asa_unmatched.length) alert(`ASA export lines not found in this form: ${r.asa_unmatched.join(", ")}`);
    $("#asa-file").value = "";
    $("#asa-file-name").textContent = "";
    location.hash = `#/review/${r.id}`;
  } catch (e) { msg.textContent = e.message; }
}
$("#asa-file").onchange = (ev) => { $("#asa-file-name").textContent = ev.target.files[0] ? ev.target.files[0].name : ""; };
$("#export-file").onchange = (ev) => { if (ev.target.files[0]) uploadExport(ev.target.files[0]); ev.target.value = ""; };
const drop = $("#drop");
drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = (e) => {
  e.preventDefault();
  drop.classList.remove("over");
  if (e.dataTransfer.files[0]) uploadExport(e.dataTransfer.files[0]);
};

/* ---------- review: helpers ---------- */
const sectionOf = (f) => f.path.slice(1).join(" › ") || f.path[0] || "(no section)";
const fieldByKey = (key) => state.session.review.fields.find((f) => f.key === key);
// Skipped fields have no stored suggestions; give them an empty entry so they can still be mapped by hand.
const suggOf = (key) => state.session.review.suggestions[key] ||
  (state.session.review.suggestions[key] = { field: [], options: {}, parent: "", flags: [] });
const decided = (d) => !!d && !!d.status && d.status !== "pending";
const hasOptions = (f) => OPTION_WIDGETS.has(f.widget) && f.options.length > 0;

function ensure(key) {
  if (!state.decisions[key]) state.decisions[key] = { status: "pending", asa: "", options: {} };
  if (!state.decisions[key].options) state.decisions[key].options = {};
  return state.decisions[key];
}

function isDone(f) {
  const d = state.decisions[f.key];
  if (!decided(d)) return false;
  return !hasOptions(f) || f.options.every((o) => decided((d.options || {})[o.key]));
}

function needsReview(f) {
  if (isDone(f)) return false;
  const sug = suggOf(f.key);
  if (sug.flags.length || (sug.field[0] || {}).confidence !== "High") return true;
  return hasOptions(f) && f.options.some((o) => ((sug.options[o.key] || [])[0] || {}).confidence !== "High");
}

/* ---------- review: rendering ---------- */
function asaSelect(ranked, existing, current) {
  const seen = new Set();
  const opts = [];
  const add = (asa, text) => {
    if (!asa || seen.has(asa)) return;
    seen.add(asa);
    opts.push(`<option value="${esc(asa)}"${asa === current ? " selected" : ""}>${esc(text)}</option>`);
  };
  ranked.forEach((r, i) => add(r.asa, `${i === 0 ? "★ " : ""}${r.asa}`));
  existing.forEach((a) => add(a, `${a} (existing)`));
  add(current, current);
  return `<select data-asa>${opts.join("")}<option value="__none"${current ? "" : " selected"}>— No ASA —</option>` +
    `<option value="__search">Search library…</option></select>`;
}

function confBadge(s) {
  return s ? `<span class="conf conf-${s.confidence.toLowerCase()}" title="${esc(s.reason)}">${s.confidence}</span>` : "";
}
const statusBadge = (status) => `<span class="status status-${status || "pending"}">${STATUS_TEXT[status || "pending"]}</span>`;

function existingCell(asas, flags) {
  const list = asas.map((a) => `<div class="mono">${esc(a)}</div>`).join("");
  return list + flags.map((f) => `<div class="flag">⚠ ${esc(f)}</div>`).join("");
}

const copyButton = (text) =>
  `<button type="button" class="copy mono" data-copy="${esc(text)}" title="Copy to search in the form builder">${esc(text)} ⧉</button>`;
const copyAsaButton = `<button type="button" class="copy" data-copy-select title="Copy the selected ASA">⧉</button>`;

function labelCell(f) {
  const meta = [f.widget, f.hidden ? "hidden" : "", f.headings.join(" › ")].filter(Boolean).join(" · ");
  return `<div class="label">${esc(f.label)}</div><div class="meta">${copyButton(f.key)} ${esc(meta)}</div>`;
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  const before = button.innerHTML;
  button.textContent = "Copied";
  setTimeout(() => { button.innerHTML = before; }, 900);
}

function currentAsa(d, ranked) {
  return decided(d) ? d.asa : ((ranked[0] || {}).asa || "");
}

function fieldRows(f) {
  if (f.skip_reason) {
    const d = state.decisions[f.key];
    return `<tr class="skipped" data-key="${esc(f.key)}">
      <td>${labelCell(f)}<div class="meta">Skipped: ${esc(f.skip_reason)}</div></td>
      <td>${existingCell(f.existing_asa, [])}</td>
      <td>${asaSelect([], f.existing_asa, decided(d) ? d.asa : "")} ${copyAsaButton}</td>
      <td></td>
      <td>${statusBadge(d && d.status)}</td>
      <td><button type="button" data-accept>Accept</button></td></tr>`;
  }
  const sug = suggOf(f.key);
  const d = state.decisions[f.key];
  const current = currentAsa(d, sug.field);
  let html = `<tr class="field" data-key="${esc(f.key)}">
    <td>${labelCell(f)}</td>
    <td>${existingCell(f.existing_asa, sug.flags)}</td>
    <td>${asaSelect(sug.field, f.existing_asa, current)} ${copyAsaButton}</td>
    <td>${confBadge(sug.field.find((r) => r.asa === current))}</td>
    <td>${statusBadge(d && d.status)}</td>
    <td><button type="button" data-accept>Accept</button></td></tr>`;
  if (!hasOptions(f)) return html;
  for (const o of f.options) {
    const od = ((d && d.options) || {})[o.key];
    const ranked = sug.options[o.key] || [];
    const cur = currentAsa(od, ranked);
    html += `<tr class="option" data-key="${esc(f.key)}" data-opt="${esc(o.key)}">
      <td class="opt-label">↳ ${esc(o.label)}<div class="meta">${copyButton(`${f.key}#${o.key}`)}</div></td>
      <td>${existingCell(o.existing_asa, [])}</td>
      <td>${asaSelect(ranked, o.existing_asa, cur)} ${copyAsaButton}</td>
      <td>${confBadge(ranked.find((r) => r.asa === cur))}</td>
      <td>${statusBadge(od && od.status)}</td>
      <td><button type="button" data-accept>Accept</button></td></tr>`;
  }
  return html;
}

function renderReview() {
  const s = state.session;
  const status = $("#filter-status").value;
  const section = $("#filter-section").value;
  const text = $("#filter-text").value.toLowerCase();
  const groups = new Map();
  for (const f of s.review.fields) {
    const skipped = !!f.skip_reason;
    if (status === "skipped" ? !skipped : skipped) continue;
    if (section && sectionOf(f) !== section) continue;
    if (text && !f.label.toLowerCase().includes(text) && !f.key.toLowerCase().includes(text)) continue;
    if (status === "pending" && isDone(f)) continue;
    if (status === "done" && !isDone(f)) continue;
    if (status === "needs" && !needsReview(f)) continue;
    const key = sectionOf(f);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(f);
  }
  let html = "";
  for (const [name, fields] of groups) {
    html += `<div class="section"><div class="section-head"><h3>${esc(name)}</h3>` +
      (status === "skipped" ? "" : `<button type="button" data-accept-high="${esc(name)}">Accept all High</button>`) +
      `</div><table class="fields"><thead><tr><th>Field</th><th>Existing ASA</th><th>ASA</th><th>Confidence</th><th>Status</th><th></th></tr></thead><tbody>` +
      fields.map(fieldRows).join("") + `</tbody></table></div>`;
  }
  $("#review-body").innerHTML = html || `<p class="muted">No fields match the filters.</p>`;
  updateProgress();
}

function updateProgress() {
  let total = 0;
  let done = 0;
  for (const f of state.session.review.fields) {
    if (f.skip_reason) continue;
    const d = state.decisions[f.key];
    total += 1;
    done += decided(d) ? 1 : 0;
    if (!hasOptions(f)) continue;
    for (const o of f.options) {
      total += 1;
      done += decided(((d && d.options) || {})[o.key]) ? 1 : 0;
    }
  }
  $("#progress-bar").style.width = total ? `${(100 * done) / total}%` : "0";
  $("#progress-text").textContent = `${done} of ${total} reviewed`;
}

/* ---------- review: changes ---------- */
async function setDecision(key, opt, asa, forcedStatus) {
  const sug = suggOf(key);
  const d = ensure(key);
  if (opt === undefined) {
    const top = (sug.field[0] || {}).asa || "";
    d.asa = asa;
    d.status = forcedStatus || (asa === top ? "accepted" : "changed");
    const f = fieldByKey(key);
    if (!f.skip_reason && hasOptions(f) && asa && asa !== sug.parent) {
      sug.options = await api(`/api/sessions/${state.session.id}/options`, jsonOpts("POST", { field_key: key, parent_asa: asa }));
      sug.parent = asa;
      d.options = {};
    }
  } else {
    const top = ((sug.options[opt] || [])[0] || {}).asa || "";
    d.options[opt] = { asa, status: forcedStatus || (asa === top ? "accepted" : "changed") };
  }
  scheduleSave();
  renderReview();
}

function acceptHigh(section) {
  for (const f of state.session.review.fields) {
    if (f.skip_reason || sectionOf(f) !== section) continue;
    const sug = suggOf(f.key);
    const d = ensure(f.key);
    const top = sug.field[0];
    if (!decided(d) && top && top.confidence === "High") Object.assign(d, { asa: top.asa, status: "accepted" });
    if (!hasOptions(f)) continue;
    for (const o of f.options) {
      const ot = (sug.options[o.key] || [])[0];
      if (!decided(d.options[o.key]) && ot && ot.confidence === "High") d.options[o.key] = { asa: ot.asa, status: "accepted" };
    }
  }
  scheduleSave();
  renderReview();
}

function scheduleSave() {
  clearTimeout(state.saveTimer);
  $("#review-msg").textContent = "Saving…";
  state.saveTimer = setTimeout(save, 400);
}

async function save() {
  try {
    await api(`/api/sessions/${state.session.id}/decisions`, jsonOpts("PUT", state.decisions));
    $("#review-msg").textContent = "All changes saved.";
  } catch (e) { $("#review-msg").textContent = `Save failed: ${e.message}`; }
}

function openSearch(kind, parent) {
  const dlg = $("#search-dialog");
  const input = $("#search-input");
  const results = $("#search-results");
  input.value = "";
  return new Promise((resolve) => {
    let chosen = null;
    const run = async () => {
      const rows = await api(`/api/search?${new URLSearchParams({ q: input.value, kind, parent })}`);
      results.innerHTML = rows.map((r) =>
        `<li><button type="button" data-pick="${esc(r.asa)}"><strong class="mono">${esc(r.asa)}</strong>` +
        `<span>${parent && r.parent === parent ? "Under this field · " : ""}${esc(r.summary)} · ${esc(r.section)}${r.group ? ` · ${esc(r.group)}` : ""}</span></button></li>`
      ).join("") || `<li class="muted">No matches</li>`;
    };
    input.oninput = run;
    // Enter would submit the dialog form and close it with nothing chosen; pick the first result instead.
    input.onkeydown = (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      const first = results.querySelector("[data-pick]");
      if (first) { chosen = first.dataset.pick; dlg.close(); }
    };
    results.onclick = (e) => {
      const b = e.target.closest("[data-pick]");
      if (b) { chosen = b.dataset.pick; dlg.close(); }
    };
    dlg.onclose = () => resolve(chosen);
    dlg.showModal();
    input.focus();
    run();
  });
}

$("#review-body").addEventListener("change", async (ev) => {
  const sel = ev.target.closest("select[data-asa]");
  if (!sel) return;
  const row = sel.closest("tr");
  const { key, opt } = row.dataset;
  let value = sel.value;
  if (value === "__search") {
    value = await openSearch(opt === undefined ? "name" : "option", opt === undefined ? "" : suggOf(key).parent);
    if (value === null) { renderReview(); return; }
  }
  if (value === "__none") await setDecision(key, opt, "", "none");
  else await setDecision(key, opt, value, null);
});

$("#review-body").addEventListener("click", async (ev) => {
  const copy = ev.target.closest("[data-copy]");
  if (copy) { copyText(copy.dataset.copy, copy); return; }
  const copySelected = ev.target.closest("[data-copy-select]");
  if (copySelected) {
    const value = copySelected.closest("td").querySelector("select[data-asa]").value;
    if (!value.startsWith("__")) copyText(value, copySelected);
    return;
  }
  const high = ev.target.closest("[data-accept-high]");
  if (high) { acceptHigh(high.dataset.acceptHigh); return; }
  const btn = ev.target.closest("[data-accept]");
  if (!btn) return;
  const row = btn.closest("tr");
  const value = row.querySelector("select[data-asa]").value;
  if (value === "__search") return;
  if (value === "__none") await setDecision(row.dataset.key, row.dataset.opt, "", "none");
  else await setDecision(row.dataset.key, row.dataset.opt, value, null);
});

for (const id of ["#filter-status", "#filter-section"]) $(id).onchange = renderReview;
$("#filter-text").oninput = renderReview;

$("#finish").onclick = async () => {
  if (!confirm("Finish this review and save the accepted ASAs to improve future suggestions?")) return;
  clearTimeout(state.saveTimer);
  await save();
  try {
    const r = await api(`/api/sessions/${state.session.id}/finish`, { method: "POST" });
    $("#review-msg").textContent = `Review finished. Saved ${r.saved} mappings for future suggestions.`;
  } catch (e) { $("#review-msg").textContent = e.message; }
};

async function showReview(id) {
  const s = await api(`/api/sessions/${id}`);
  state.session = s;
  state.decisions = s.decisions || {};
  $("#form-name").textContent = s.form_name;
  $("#download").href = `/api/sessions/${id}/sheet.xlsx`;
  $("#download-asa").href = `/api/sessions/${id}/asa.txt`;
  const sections = [...new Set(s.review.fields.filter((f) => !f.skip_reason).map(sectionOf))];
  $("#filter-section").innerHTML = `<option value="">All sections</option>` +
    sections.map((x) => `<option>${esc(x)}</option>`).join("");
  $("#review-msg").innerHTML = (s.missing_asas || []).length
    ? `<span class="warn">Accepted ASAs no longer in the library: ${esc(s.missing_asas.join(", "))}</span>` : "";
  renderReview();
}

/* ---------- accuracy ---------- */
$("#run-accuracy").onclick = async () => {
  const body = $("#accuracy-body");
  body.textContent = "Running…";
  try {
    const r = await api("/api/accuracy");
    const pct = (x) => (x === null ? "–" : `${Math.round(x * 100)}%`);
    const row = (name, b) => `<tr><td>${name}</td><td>${b.total}</td><td>${pct(b.top1_rate)}</td><td>${pct(b.topn_rate)}</td></tr>`;
    body.innerHTML = `<p>${r.forms} form(s) with known mappings.</p>
      <table class="list"><thead><tr><th></th><th>Tested</th><th>First suggestion correct</th><th>In top ${r.top_n}</th></tr></thead>
      <tbody>${row("Field names", r.fields)}${row("Field options", r.options)}</tbody></table>
      <h3>Misses (${r.misses.length})</h3>
      <table class="list"><thead><tr><th>Kind</th><th>Label</th><th>Section</th><th>Expected</th><th>Got</th></tr></thead>
      <tbody>${r.misses.map((m) => `<tr><td>${m.kind}</td><td>${esc(m.label)}</td><td>${esc(m.path)}</td>` +
        `<td class="mono">${esc(m.expected)}</td><td class="mono">${esc(m.got)}</td></tr>`).join("")}</tbody></table>`;
  } catch (e) { body.textContent = e.message; }
};

/* ---------- routing ---------- */
async function route() {
  const hash = location.hash || "#/";
  for (const v of ["home", "review", "accuracy"]) $(`#view-${v}`).hidden = true;
  try {
    if (hash.startsWith("#/review/")) {
      $("#view-review").hidden = false;
      await showReview(hash.slice("#/review/".length));
    } else if (hash === "#/accuracy") {
      $("#view-accuracy").hidden = false;
    } else {
      $("#view-home").hidden = false;
      await showHome();
    }
  } catch (e) { alert(e.message); }
}

window.onhashchange = route;
loadLibraryStatus();
api("/api/me").then((u) => { $("#user").textContent = u.email || ""; }).catch(() => {});
route();
