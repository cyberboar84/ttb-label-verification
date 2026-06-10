"use strict";

const ICON = { PASS: "✓", MISMATCH: "✗", MISSING: "✗", REVIEW: "!" };

const FIELD_LABELS = {
  brand_name: "Brand name",
  class_type: "Class / type",
  alcohol_content: "Alcohol content",
  net_contents: "Net contents",
  name_address: "Bottler name & address",
  country_of_origin: "Country of origin",
  government_warning: "Government warning",
};

// Definitive status headline. REVIEW stays a distinct third state for genuinely
// borderline cases rather than forcing a false binary.
const STATUS = {
  PASS: "Compliant",
  MISMATCH: "Not Compliant",
  MISSING: "Not Compliant",
  REVIEW: "Needs Review",
};

// Short reason under the status. Detail lives in the per-field rows.
function statusSubtitle(r) {
  if (r.overall === "PASS") {
    return r.mode === "match"
      ? "All fields match the application; warning is valid."
      : "Government warning valid and all required elements present.";
  }
  if (r.overall === "REVIEW") return "Borderline; please confirm the details below.";
  const issues = [...(r.fields || []), ...(r.warning ? [r.warning] : [])]
    .filter((f) => f.verdict !== "PASS").length;
  return `${issues} issue${issues > 1 ? "s" : ""} found. See details below.`;
}

// ---- government banner toggle ----
const bannerToggle = document.getElementById("bannerToggle");
const bannerContent = document.getElementById("bannerContent");
bannerToggle.addEventListener("click", () => {
  const open = bannerToggle.getAttribute("aria-expanded") === "true";
  bannerToggle.setAttribute("aria-expanded", String(!open));
  bannerContent.hidden = open;
});

// ---- tabs ----
document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    document.getElementById(t.dataset.tab).classList.add("active");
  });
});

// ---- mode badge ----
fetch("/health").then((r) => r.json()).then((h) => {
  const b = document.getElementById("modeBadge");
  b.textContent = h.mode === "azure" ? "Live (Azure)" : "Demo mode";
  b.className = "badge " + h.mode;
}).catch(() => {});

// ---- single: panel handling (front / back / side of ONE bottle) ----
// Required info is split across panels, so we collect one or more images of the
// same bottle and verify them together.
const fileInput = document.getElementById("fileInput");
const dropzone = document.getElementById("dropzone");
const dropText = document.getElementById("dropText");
const panelThumbs = document.getElementById("panelThumbs");
const clearSingle = document.getElementById("clearSingle");
const verifyBtn = document.getElementById("verifyBtn");
const resultEl = document.getElementById("result");
let panels = [];

function addPanels(fileList) {
  for (const f of fileList) {
    if (f.type.startsWith("image/") &&
        !panels.some((x) => x.name === f.name && x.size === f.size))
      panels.push(f);
  }
  renderThumbs();
}

function renderThumbs() {
  dropText.hidden = panels.length > 0;
  clearSingle.hidden = panels.length === 0;
  panelThumbs.innerHTML = panels.map((f, i) =>
    `<div class="thumb"><img src="${URL.createObjectURL(f)}" alt="bottle panel ${i + 1}">
      <button type="button" class="thumb-remove" data-i="${i}"
        aria-label="Remove image">✕</button></div>`).join("");
}

panelThumbs.addEventListener("click", (e) => {
  const btn = e.target.closest(".thumb-remove");
  if (!btn) return;
  panels.splice(Number(btn.dataset.i), 1);
  renderThumbs();
});

function clearSingleLabel() {
  panels = [];
  fileInput.value = "";
  renderThumbs();
  resultEl.hidden = true;
  resultEl.innerHTML = "";
}
clearSingle.addEventListener("click", clearSingleLabel);

fileInput.addEventListener("change", (e) => addPanels(e.target.files));
["dragover", "dragenter"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove("drag"); }));
dropzone.addEventListener("drop", (e) => addPanels(e.dataTransfer.files));

verifyBtn.addEventListener("click", runVerify);

async function runVerify() {
  if (!panels.length) {
    alert("Please add at least one bottle image (add the front and back if you have them).");
    return;
  }
  const app = {
    brand_name: val("brand_name"),
    class_type: val("class_type"),
    alcohol_content: val("alcohol_content"),
    net_contents: val("net_contents"),
  };
  const fd = new FormData();
  for (const f of panels) fd.append("images", f);  // all panels of one bottle
  if (Object.values(app).some((v) => v)) fd.append("application", JSON.stringify(app));

  setLoading(verifyBtn, true, "Verifying…");
  resultEl.hidden = true;
  try {
    const res = await fetch("/api/verify", { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    renderSingle(await res.json());
  } catch (err) {
    resultEl.hidden = false;
    resultEl.innerHTML = `<div class="verdict-banner v-MISMATCH">
      <span class="big-icon">✗</span> Could not check label: ${escapeHtml(err.message)}</div>`;
  } finally {
    setLoading(verifyBtn, false, "Verify label");
  }
}

// Shared per-field detail markup, used by both the single view and each
// expanded batch row, so they show identical information.
function fieldRowsHtml(r) {
  let html = "";
  if (r.security_flags && r.security_flags.length) {
    html += `<div class="security-alert">
      <strong>⚠ Security alert:</strong> this label contains text directed at the
      verification system, so the automatic reading may be unreliable. Review the
      label by hand before deciding.
    </div>`;
  }
  const all = [...(r.fields || []), ...(r.warning ? [r.warning] : [])];
  for (const f of all) {
    const name = FIELD_LABELS[f.field] || f.field.replace(/_/g, " ");
    html += `<div class="field-row ${f.verdict}">
      <div class="icon">${ICON[f.verdict]}</div>
      <div>
        <div class="fname">${escapeHtml(name)}</div>
        <div class="detail">${escapeHtml(f.note || "")}</div>
        ${cmpLine(f, r.mode)}
      </div></div>`;
  }
  return html;
}

function renderSingle(r) {
  let html = `<div class="verdict-banner v-${r.overall}">
      <span class="big-icon">${ICON[r.overall]}</span>
      <span class="verdict-text">
        <span class="status">${STATUS[r.overall]}</span>
        <span class="status-sub">${statusSubtitle(r)}</span>
      </span></div>`;
  html += fieldRowsHtml(r);
  if (r.processing_ms != null)
    html += `<div class="timing">Checked in ${(r.processing_ms / 1000).toFixed(1)} seconds</div>`;
  html += `<button type="button" class="btn-primary check-another">Check another label</button>`;
  resultEl.innerHTML = html;
  resultEl.hidden = false;
  resultEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// "Check another label" resets the single view for the next item in the queue.
resultEl.addEventListener("click", (e) => {
  if (e.target.closest(".check-another")) {
    clearSingleLabel();
    dropzone.scrollIntoView({ behavior: "smooth", block: "center" });
  }
});

function cmpLine(f, mode) {
  if (f.field === "government_warning") {
    return f.found ? `<div class="cmp">On label: <b>${escapeHtml(trim(f.found, 120))}</b></div>` : "";
  }
  // Compliance mode: just show what was read off the label.
  if (mode === "compliance") {
    return f.found ? `<div class="cmp">On label: <b>${escapeHtml(f.found)}</b></div>` : "";
  }
  if (f.expected == null && f.found == null) return "";
  return `<div class="cmp">Application: <b>${escapeHtml(f.expected || "—")}</b>
          &nbsp;·&nbsp; Label: <b>${escapeHtml(f.found || "—")}</b></div>`;
}

// ---- batch ----
// The file <input> is read-only, so we keep the selection in our own array.
// That lets the agent add files across multiple picks, remove individual ones,
// and clear the whole set before (or after) running.
const batchBtn = document.getElementById("batchBtn");
const batchFilesInput = document.getElementById("batchFiles");
const batchText = document.getElementById("batchText");
const batchList = document.getElementById("batchList");
const clearBatch = document.getElementById("clearBatch");
const batchResult = document.getElementById("batchResult");
let selectedBatch = [];

batchFilesInput.addEventListener("change", () => {
  for (const f of batchFilesInput.files) {
    if (!selectedBatch.some((x) => x.name === f.name && x.size === f.size))
      selectedBatch.push(f);
  }
  batchFilesInput.value = ""; // reset so a removed file can be re-added
  renderBatchList();
});

function renderBatchList() {
  const n = selectedBatch.length;
  batchText.textContent = n
    ? `${n} label${n > 1 ? "s" : ""} selected. Add more, or verify.`
    : "Select label images to verify";
  clearBatch.hidden = n === 0;
  batchList.innerHTML = selectedBatch.map((f, i) =>
    `<li class="file-item"><span class="file-name">${escapeHtml(f.name)}</span>
      <button type="button" class="file-remove" data-i="${i}"
        aria-label="Remove ${escapeHtml(f.name)}">✕</button></li>`).join("");
}

batchList.addEventListener("click", (e) => {
  const btn = e.target.closest(".file-remove");
  if (!btn) return;
  selectedBatch.splice(Number(btn.dataset.i), 1);
  renderBatchList();
});

clearBatch.addEventListener("click", () => {
  selectedBatch = [];
  renderBatchList();
  batchResult.hidden = true;
  batchResult.innerHTML = "";
});

batchBtn.addEventListener("click", async () => {
  if (!selectedBatch.length) { alert("Please select label images first."); return; }
  const fd = new FormData();
  for (const f of selectedBatch) fd.append("images", f);
  let manifest = document.getElementById("manifest").value.trim() || "{}";
  try { JSON.parse(manifest); } catch { alert("The application data manifest is not valid JSON. Please check it."); return; }
  fd.append("manifest", manifest);

  setLoading(batchBtn, true, "Verifying…");
  batchResult.hidden = true;
  try {
    const res = await fetch("/api/verify/batch", { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    renderBatch(await res.json());
  } catch (err) {
    batchResult.hidden = false;
    batchResult.innerHTML = `<div class="verdict-banner v-MISMATCH"><span class="big-icon">✗</span>
      ${escapeHtml(err.message)}</div>`;
  } finally {
    setLoading(batchBtn, false, "Verify batch");
  }
});

function renderBatch(data) {
  const { summary, results } = data;
  let html = `<div class="batch-summary">${summary.total} labels checked ·
    ${summary.flagged} flagged</div>
    <p class="hint">Select a row to see the full breakdown.</p>`;
  results.forEach((r, i) => {
    const v = r.overall || "MISMATCH";
    html += `<div class="batch-item">
      <button type="button" class="batch-row ${v}" aria-expanded="false" data-i="${i}">
        <span class="icon">${ICON[v] || "✗"}</span>
        <span class="batch-name">${escapeHtml(r.label_id || "—")}</span>
        <span class="pill ${v}">${STATUS[v] || v}</span>
        <span class="chevron">▸</span>
      </button>
      <div class="batch-detail" id="bd-${i}" hidden>
        ${r.error ? `<p class="detail">${escapeHtml(r.error)}</p>` : fieldRowsHtml(r)}
      </div></div>`;
  });
  batchResult.innerHTML = html;
  batchResult.hidden = false;
}

batchResult.addEventListener("click", (e) => {
  const row = e.target.closest(".batch-row");
  if (!row) return;
  const det = document.getElementById("bd-" + row.dataset.i);
  const open = row.getAttribute("aria-expanded") === "true";
  row.setAttribute("aria-expanded", String(!open));
  det.hidden = open;
  row.querySelector(".chevron").textContent = open ? "▸" : "▾";
});

// ---- helpers ----
function val(id) { return document.getElementById(id).value.trim() || null; }
function setLoading(btn, on, label) {
  btn.disabled = on;
  btn.innerHTML = on ? `<span class="spinner"></span> ${label}` : label;
}
function trim(s, n) { return s.length > n ? s.slice(0, n) + "…" : s; }
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
