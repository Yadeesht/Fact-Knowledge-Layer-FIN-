// ==========================================================================
// Financial Fact Knowledge Layer - Frontend Controller
// ==========================================================================

let API_BASE = (window.location.protocol === "file:" || (window.location.port && window.location.port !== "8000"))
  ? "http://127.0.0.1:8000"
  : "";

function api(path) {
  return `${API_BASE}${path}`;
}

let allDocuments = [];
let processedFiles = [];
let allObservations = [];
let allRelationships = [];
let allFacts = [];
let showcaseData = [];
let activeCaseIndex = 0;
let currentDocFilter = "all";
let currentView = "landing"; // Default starting page is ALWAYS upload doc

document.addEventListener("DOMContentLoaded", () => {
  initApp();
  setupDragAndDrop();
});

async function testConnection() {
  const candidates = [
    API_BASE,
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    ""
  ];

  for (const base of candidates) {
    try {
      const res = await fetch(`${base}/api/health`, { method: "GET" });
      if (res.ok) {
        const data = await res.json();
        if (data.status === "ok") {
          API_BASE = base;
          return true;
        }
      }
    } catch (e) {
      // Continue to next candidate
    }
  }
  return false;
}

async function initApp() {
  updateApiStatus("checking", "Connecting...");
  const isOnline = await testConnection();

  if (isOnline) {
    updateApiStatus("ready", "Engine Ready");
    try {
      const cfg = await fetch(api("/api/config")).then(r => r.json());
      console.log("Connected to Knowledge Layer with config:", cfg);
    } catch (e) { }
  } else {
    updateApiStatus("offline", "Engine Offline");
  }

  // Check state and load filings
  await refreshData();
}

function updateApiStatus(status, text) {
  const badge = document.getElementById("apiStatusBadge");
  const dot = badge ? badge.querySelector(".status-dot") : null;
  const label = document.getElementById("apiStatusText");

  if (label) label.innerText = text;
  if (dot) {
    if (status === "ready") {
      dot.style.background = "var(--color-corroborated)";
    } else if (status === "busy") {
      dot.style.background = "var(--accent-primary)";
    } else {
      dot.style.background = "var(--color-contradicted)";
    }
  }
}

// -----------------------------------------------------------------
// Data Loading & State Management
// -----------------------------------------------------------------
async function refreshData() {
  await loadProcessedFilesList();
  await loadStarterFiles();

  try {
    const docRes = await fetch(api("/api/documents"));
    allDocuments = await docRes.json();
  } catch (e) {
    allDocuments = [];
  }

  const landingView = document.getElementById("landingView");
  const dashboardView = document.getElementById("dashboardView");
  const btnBack = document.getElementById("btnBackToUpload");

  if (currentView === "landing") {
    // STARTING PAGE IS ALWAYS UPLOAD DOC BY DEFAULT
    if (landingView) landingView.style.display = "flex";
    if (dashboardView) dashboardView.style.display = "none";
    if (btnBack) btnBack.style.display = "none";
  } else {
    // Active Dashboard View
    if (landingView) landingView.style.display = "none";
    if (dashboardView) dashboardView.style.display = "block";
    if (btnBack) btnBack.style.display = "inline-flex";

    renderFilingSwitcher();
    await loadActiveData();
    renderDocuments(allDocuments);
  }
}

function showUploadPage() {
  currentView = "landing";
  const landingView = document.getElementById("landingView");
  const dashboardView = document.getElementById("dashboardView");
  const btnBack = document.getElementById("btnBackToUpload");

  if (landingView) landingView.style.display = "flex";
  if (dashboardView) dashboardView.style.display = "none";
  if (btnBack) btnBack.style.display = "none";

  loadProcessedFilesList();
  loadStarterFiles();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function openDashboardWithFiling(docId) {
  currentView = "dashboard";
  const landingView = document.getElementById("landingView");
  const dashboardView = document.getElementById("dashboardView");
  const btnBack = document.getElementById("btnBackToUpload");

  if (landingView) landingView.style.display = "none";
  if (dashboardView) dashboardView.style.display = "block";
  if (btnBack) btnBack.style.display = "inline-flex";

  try {
    const docRes = await fetch(api("/api/documents"));
    allDocuments = await docRes.json();
  } catch (e) {
    // fallback
  }

  await switchFilingFocus(docId || "all");
  renderDocuments(allDocuments);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function loadActiveData() {
  await Promise.all([
    loadObservations(currentDocFilter),
    loadRelationships(currentDocFilter),
    loadGroupedFacts(),
    loadShowcaseCases(),
  ]);
  updateMetricsSummary();
}

// -----------------------------------------------------------------
// Processed Filings (Option Section on Starting Page)
// -----------------------------------------------------------------
async function loadProcessedFilesList() {
  const container = document.getElementById("landingProcessedList");
  if (!container) return;

  try {
    const res = await fetch(api("/api/processed-files"));
    processedFiles = await res.json();

    if (!processedFiles || processedFiles.length === 0) {
      container.innerHTML = `
        <div class="empty-processed-box">
          <div style="font-size: 1.5rem; margin-bottom: 0.35rem;">📂</div>
          <div style="font-weight: 600; font-size: 0.88rem; color: var(--text-primary); margin-bottom: 0.25rem;">No Processed Filings on Disk Yet</div>
          <div style="font-size: 0.78rem; color: var(--text-tertiary); max-width: 320px; margin: 0 auto;">
            No processed JSON artifacts found. Upload a financial filing above or pick a starter dataset to run extraction.
          </div>
        </div>
      `;
      return;
    }

    let itemsHtml = `
      <div class="processed-summary-bar">
        <div class="processed-summary-text">
          <span class="processed-count-badge">${processedFiles.length} Processed</span>
          <span>Previously analyzed filings stored on disk. Open any filing individually or view consensus:</span>
        </div>
        <button class="btn btn-primary btn-sm" onclick="openDashboardWithFiling('all')">
          Open All Combined (Consensus) ➔
        </button>
      </div>
      <div class="processed-grid">
    `;

    processedFiles.forEach(f => {
      itemsHtml += `
        <div class="processed-file-item" onclick="openDashboardWithFiling('${f.doc_id}')">
          <div class="processed-file-info">
            <div class="processed-file-name" title="${f.filename}">
              📄 ${f.filename}
            </div>
            <div class="processed-file-stats">
              <span class="stat-pill">${f.observations_count} claims</span>
              <span class="stat-pill">${f.relationships_count} relations</span>
              <span class="stat-pill">${f.page_count || 1} pages</span>
            </div>
          </div>
          <button class="btn btn-secondary btn-sm" style="flex-shrink: 0;" onclick="event.stopPropagation(); openDashboardWithFiling('${f.doc_id}')">
            Inspect ➔
          </button>
        </div>
      `;
    });

    itemsHtml += `</div>`;
    container.innerHTML = itemsHtml;
  } catch (err) {
    container.innerHTML = `<div class="empty-processed-box">Error loading processed filings: ${err.message}</div>`;
  }
}

// -----------------------------------------------------------------
// Filing Switcher (Dashboard Context Switching)
// -----------------------------------------------------------------
function renderFilingSwitcher() {
  const container = document.getElementById("filingSwitcherButtons");
  if (!container) return;

  let html = `
    <button class="switcher-btn ${currentDocFilter === 'all' ? 'active' : ''}" onclick="switchFilingFocus('all')">
      ⊞ All Filings Combined (${allDocuments.length})
    </button>
  `;

  allDocuments.forEach(d => {
    const isActive = currentDocFilter === d.id;
    html += `
      <button class="switcher-btn ${isActive ? 'active' : ''}" onclick="switchFilingFocus('${d.id}')" title="${d.filename}">
        📄 ${d.filename.length > 25 ? d.filename.substring(0, 22) + '...' : d.filename}
      </button>
    `;
  });

  container.innerHTML = html;
}

async function switchFilingFocus(docId) {
  currentDocFilter = docId;

  const titleEl = document.getElementById("activeFilingTitle");
  if (titleEl) {
    if (docId === "all") {
      titleEl.innerText = "All Filings (Cross-Filing Consensus)";
    } else {
      const doc = allDocuments.find(d => d.id === docId) || processedFiles.find(f => f.doc_id === docId);
      titleEl.innerText = doc ? doc.filename : docId;
    }
  }

  renderFilingSwitcher();
  await loadActiveData();
}

// -----------------------------------------------------------------
// Starter Files Loading
// -----------------------------------------------------------------
async function loadStarterFiles() {
  const grid = document.getElementById("starterGrid");
  if (!grid) return;

  try {
    const res = await fetch(api("/api/starter-files"));
    const starters = await res.json();

    if (!starters || starters.length === 0) {
      grid.innerHTML = `<div class="starter-card-loading">No starter files found in repository.</div>`;
      return;
    }

    grid.innerHTML = "";
    starters.forEach(item => {
      const card = document.createElement("div");
      card.className = "starter-card";
      card.onclick = () => ingestStarterFiling(item.filename);

      card.innerHTML = `
        <div class="starter-card-title">${item.filename}</div>
        <div class="starter-card-meta">
          <span>${item.dataset}</span>
          <span>${item.size_mb} MB</span>
        </div>
      `;
      grid.appendChild(card);
    });
  } catch (err) {
    grid.innerHTML = `<div class="starter-card-loading">Drop or upload a PDF above to begin.</div>`;
  }
}

// -----------------------------------------------------------------
// Processing Pipeline Stepper & Ingestion
// -----------------------------------------------------------------
let activeAbortController = null;
let stepperTimers = [];

function clearStepperTimers() {
  stepperTimers.forEach(id => clearTimeout(id));
  stepperTimers = [];
}

function showProcessingModal(filename) {
  clearStepperTimers();
  const modal = document.getElementById("processingModal");
  const sub = document.getElementById("procModalFilename");
  if (sub) sub.innerText = filename;
  if (modal) modal.classList.add("show");

  resetStepper();
  advanceStep(1);
}

function hideProcessingModal() {
  clearStepperTimers();
  const modal = document.getElementById("processingModal");
  if (modal) modal.classList.remove("show");
  activeAbortController = null;
}

async function cancelActiveProcessing() {
  if (confirm("Are you sure you want to stop processing this filing?")) {
    // 1. Immediately abort client fetch request
    if (activeAbortController) {
      activeAbortController.abort();
      activeAbortController = null;
    }

    // 2. Clear all animation timers and dismiss modal immediately
    hideProcessingModal();

    // 3. Notify backend to abort pipeline loop asynchronously
    try {
      fetch(api("/api/cancel"), { method: "POST" }).catch(e => {
        console.warn("Cancel request error:", e);
      });
    } catch (e) {
      console.warn("Cancel request error:", e);
    }

    alert("Processing was cancelled by user.");
  }
}

function resetStepper() {
  for (let i = 1; i <= 4; i++) {
    const step = document.getElementById(`step${i}`);
    if (step) {
      step.classList.remove("active", "completed");
    }
  }
}

function advanceStep(num) {
  for (let i = 1; i < num; i++) {
    const step = document.getElementById(`step${i}`);
    if (step) {
      step.classList.remove("active");
      step.classList.add("completed");
    }
  }
  const current = document.getElementById(`step${num}`);
  if (current) {
    current.classList.add("active");
  }
}

async function handlePDFUpload(event) {
  const file = event.target.files[0];
  if (!file) return;

  if (!file.name.toLowerCase().endsWith(".pdf")) {
    alert("Please upload a PDF file.");
    return;
  }

  showProcessingModal(file.name);
  advanceStep(1);

  const formData = new FormData();
  formData.append("file", file);

  activeAbortController = new AbortController();

  try {
    stepperTimers.push(setTimeout(() => advanceStep(2), 700));
    stepperTimers.push(setTimeout(() => advanceStep(3), 1500));

    const res = await fetch(api("/api/documents"), {
      method: "POST",
      body: formData,
      signal: activeAbortController.signal,
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Processing failed");
    }

    const data = await res.json();
    if (data.status === "cancelled") {
      hideProcessingModal();
      return;
    }

    advanceStep(4);
    stepperTimers.push(setTimeout(async () => {
      hideProcessingModal();
      await openDashboardWithFiling(data.document_id || "all");
    }, 600));
  } catch (err) {
    hideProcessingModal();
    if (err.name !== "AbortError") {
      alert("Ingestion error: " + err.message);
    }
  } finally {
    event.target.value = "";
  }
}

async function ingestStarterFiling(filename) {
  showProcessingModal(filename);
  advanceStep(1);

  activeAbortController = new AbortController();

  try {
    stepperTimers.push(setTimeout(() => advanceStep(2), 600));
    stepperTimers.push(setTimeout(() => advanceStep(3), 1300));

    const res = await fetch(api("/api/ingest-starter"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename }),
      signal: activeAbortController.signal,
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Ingestion failed");
    }

    const data = await res.json();
    if (data.status === "cancelled") {
      hideProcessingModal();
      return;
    }

    advanceStep(4);
    stepperTimers.push(setTimeout(async () => {
      hideProcessingModal();
      await openDashboardWithFiling(data.document_id || "all");
    }, 600));
  } catch (err) {
    hideProcessingModal();
    if (err.name !== "AbortError") {
      alert("Starter ingestion error: " + err.message);
    }
  }
}

function setupDragAndDrop() {
  const dropZone = document.getElementById("dropZone");
  if (!dropZone) return;

  ["dragenter", "dragover"].forEach(eventName => {
    dropZone.addEventListener(eventName, e => {
      e.preventDefault();
      dropZone.classList.add("dragover");
    });
  });

  ["dragleave", "drop"].forEach(eventName => {
    dropZone.addEventListener(eventName, e => {
      e.preventDefault();
      dropZone.classList.remove("dragover");
    });
  });

  dropZone.addEventListener("drop", e => {
    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      const file = files[0];
      const input = document.getElementById("pdfFileInput");
      if (input) {
        const dataTransfer = new DataTransfer();
        dataTransfer.items.add(file);
        input.files = dataTransfer.files;
        handlePDFUpload({ target: input });
      }
    }
  });
}

// -----------------------------------------------------------------
// Clear Workspace
// -----------------------------------------------------------------
async function confirmClearWorkspace() {
  if (!confirm("Are you sure you want to clear all processed documents and reset the workspace?")) {
    return;
  }

  try {
    await fetch(api("/api/clear"), { method: "POST" });
    allDocuments = [];
    processedFiles = [];
    currentDocFilter = "all";
    showUploadPage();
  } catch (err) {
    alert("Error clearing workspace: " + err.message);
  }
}

// -----------------------------------------------------------------
// Tabs Navigation
// -----------------------------------------------------------------
function switchTab(tabId) {
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.classList.toggle("active", btn.getAttribute("data-tab") === tabId);
  });
  document.querySelectorAll(".tab-content").forEach(content => {
    content.classList.toggle("active", content.id === tabId);
  });
}

// -----------------------------------------------------------------
// Metrics Summary Strip
// -----------------------------------------------------------------
function updateMetricsSummary() {
  const docCount = allDocuments.length;
  const obsCount = allObservations.length;

  let corrCount = 0;
  let contCount = 0;
  let ctxCount = 0;

  allRelationships.forEach(r => {
    const t = (r.relationship_type || "").toLowerCase();
    if (t === "corroborates") corrCount++;
    else if (t === "contradicts") contCount++;
    else if (t === "contextualizes") ctxCount++;
  });

  const reviewCount = allObservations.filter(o => o.needs_review).length;

  const elDocs = document.getElementById("statDocCount");
  const elObs = document.getElementById("statObsCount");
  const elCorr = document.getElementById("statCorrCount");
  const elCont = document.getElementById("statContCount");
  const elCtx = document.getElementById("statCtxCount");
  const elRev = document.getElementById("statReviewCount");

  if (elDocs) elDocs.innerText = currentDocFilter === 'all' ? docCount : 1;
  if (elObs) elObs.innerText = obsCount;
  if (elCorr) elCorr.innerText = corrCount;
  if (elCont) elCont.innerText = contCount;
  if (elCtx) elCtx.innerText = ctxCount;
  if (elRev) elRev.innerText = reviewCount;
}

// -----------------------------------------------------------------
// Showcase Inspector
// -----------------------------------------------------------------
async function loadShowcaseCases() {
  try {
    const res = await fetch(api("/api/showcase"));
    showcaseData = await res.json();
  } catch (e) {
    showcaseData = [];
  }

  renderShowcaseSelectors();
  renderActiveShowcase(activeCaseIndex);
}

function renderShowcaseSelectors() {
  const container = document.getElementById("showcaseSelectorGrid");
  if (!container) return;
  container.innerHTML = "";

  if (!showcaseData || showcaseData.length === 0) {
    container.innerHTML = `
      <div style="font-size: 0.8rem; color: var(--text-tertiary); padding: 0.5rem 0;">
        No showcase cases generated yet. Ingest multiple filings to compare.
      </div>
    `;
    return;
  }

  showcaseData.forEach((item, idx) => {
    const card = document.createElement("div");
    card.className = `showcase-item-card ${idx === activeCaseIndex ? "active" : ""}`;
    card.onclick = () => {
      activeCaseIndex = idx;
      renderShowcaseSelectors();
      renderActiveShowcase(idx);
    };

    const badgeClass = `badge-${item.category}`;
    card.innerHTML = `
      <span class="showcase-item-badge ${badgeClass}">${item.badge}</span>
      <div class="showcase-item-title">${item.title}</div>
      <div class="showcase-item-summary">${item.takeaway}</div>
    `;
    container.appendChild(card);
  });
}

function renderActiveShowcase(idx) {
  const container = document.getElementById("showcaseInspector");
  if (!container) return;

  const item = showcaseData[idx];
  if (!item || showcaseData.length === 0) {
    container.innerHTML = `
      <div style="text-align: center; padding: 2.5rem 1rem; color: var(--text-secondary);">
        <div style="font-size: 1rem; font-weight: 600; margin-bottom: 0.35rem;">Awaiting Cross-Filing Comparison</div>
        <p style="font-size: 0.82rem; color: var(--text-tertiary);">
          Ingest additional filings to trigger automated pairwise reconciliation cascade.
        </p>
      </div>
    `;
    return;
  }

  if (item.category === "needs_review") {
    // Single observation anomaly
    const obs = item.observation;
    const ev = obs && obs.evidence && obs.evidence[0];

    container.innerHTML = `
      <div class="obs-box-header">
        <div>
          <span class="showcase-item-badge badge-review">Quarantine Defense</span>
          <h3 style="font-size: 1.15rem; font-weight: 600; margin-top: 0.25rem;">${item.title}</h3>
          <p style="color: var(--text-secondary); font-size: 0.82rem; margin-top: 0.25rem;">${item.takeaway}</p>
        </div>
      </div>

      <div class="observation-box" style="margin-top: 1rem; border-color: var(--color-review-border);">
        <div class="obs-box-header">
          <span class="obs-box-tag" style="color: var(--color-review);">Quarantined Observation</span>
          <span class="showcase-item-badge badge-review">Needs Review</span>
        </div>
        <div class="obs-entity-title">${obs ? obs.entity.canonical_name : 'Unknown Entity'}</div>
        <div class="obs-concept-sub">Metric: ${obs ? obs.concept.canonical_name : 'Unknown'}</div>

        <div class="value-display">
          <span class="reported-value">${obs && obs.value ? (obs.value.amount || obs.value.text) : 'N/A'}</span>
          <span class="normalized-pill">Raw Unit: ${obs && obs.value ? (obs.value.unit || 'MISSING') : 'MISSING'}</span>
        </div>

        <table class="details-table">
          <tr><td>Timeframe</td><td>${obs && obs.time && obs.time.label ? obs.time.label : '<span style="color: var(--color-contradicted)">Omitted / Ambiguous</span>'}</td></tr>
          <tr><td>Assertion Status</td><td>${obs ? obs.assertion_status : 'N/A'}</td></tr>
          <tr><td>Extraction Confidence</td><td>${obs ? (obs.confidence * 100).toFixed(0) : 0}%</td></tr>
          <tr><td>Escalation Reason</td><td style="color: #e9d5ff">${item.review_reason || 'Missing required grounding anchors'}</td></tr>
        </table>

        <div class="evidence-quote-box">
          "${ev ? ev.quote : 'N/A'}"
          <div class="evidence-meta">
            <span>Filing: ${ev ? ev.document_id : 'N/A'}</span>
            <span>Page ${ev ? ev.page_number : 'N/A'}</span>
          </div>
        </div>
      </div>
    `;
    return;
  }

  // Dual observation comparison (Corroborated, Contradicted, Contextualized)
  const a = item.observation_a;
  const b = item.observation_b;
  const rel = item.relationship;
  const evA = a && a.evidence && a.evidence[0];
  const evB = b && b.evidence && b.evidence[0];
  const badgeClass = `badge-${item.category}`;

  container.innerHTML = `
    <div class="obs-box-header">
      <div>
        <span class="showcase-item-badge ${badgeClass}">${item.badge}</span>
        <h3 style="font-size: 1.15rem; font-weight: 600; margin-top: 0.25rem;">${item.title}</h3>
        <p style="color: var(--text-secondary); font-size: 0.82rem; margin-top: 0.25rem;">${item.takeaway}</p>
      </div>
    </div>

    <div class="comparison-grid">
      <!-- Observation A -->
      <div class="observation-box">
        <div class="obs-box-header">
          <span class="obs-box-tag">Observation A</span>
          <span class="showcase-item-badge ${badgeClass}">${a ? a.assertion_status : ''}</span>
        </div>
        <div class="obs-entity-title">${a ? a.entity.canonical_name : ''}</div>
        <div class="obs-concept-sub">${a ? a.concept.canonical_name : ''}</div>

        <div class="value-display">
          <span class="reported-value">${a ? a.value.amount : ''} ${a && a.value.unit ? a.value.unit : ''}</span>
          <span class="normalized-pill">Norm: ${a && a.value.normalized_amount !== null ? a.value.normalized_amount.toLocaleString() : 'N/A'} ${a ? a.value.normalized_unit : ''}</span>
        </div>

        <table class="details-table">
          <tr><td>Reporting Period</td><td>${a && a.time ? (a.time.label || 'N/A') : 'N/A'}</td></tr>
          <tr><td>Scope</td><td>${a && a.scope ? `${a.scope.level || ''} (${a.scope.consolidation || ''})` : 'N/A'}</td></tr>
          <tr><td>Confidence</td><td>${a ? (a.confidence * 100).toFixed(0) : 0}%</td></tr>
        </table>

        <div class="evidence-quote-box">
          "${evA ? evA.quote : 'N/A'}"
          <div class="evidence-meta">
            <span>${evA ? evA.document_id : 'N/A'}</span>
            <span>p. ${evA ? evA.page_number : 'N/A'}</span>
          </div>
        </div>
      </div>

      <!-- Observation B -->
      <div class="observation-box">
        <div class="obs-box-header">
          <span class="obs-box-tag">Observation B</span>
          <span class="showcase-item-badge ${badgeClass}">${b ? b.assertion_status : ''}</span>
        </div>
        <div class="obs-entity-title">${b ? b.entity.canonical_name : ''}</div>
        <div class="obs-concept-sub">${b ? b.concept.canonical_name : ''}</div>

        <div class="value-display">
          <span class="reported-value">${b ? b.value.amount : ''} ${b && b.value.unit ? b.value.unit : ''}</span>
          <span class="normalized-pill">Norm: ${b && b.value.normalized_amount !== null ? b.value.normalized_amount.toLocaleString() : 'N/A'} ${b ? b.value.normalized_unit : ''}</span>
        </div>

        <table class="details-table">
          <tr><td>Reporting Period</td><td>${b && b.time ? (b.time.label || 'N/A') : 'N/A'}</td></tr>
          <tr><td>Scope</td><td>${b && b.scope ? `${b.scope.level || ''} (${b.scope.consolidation || ''})` : 'N/A'}</td></tr>
          <tr><td>Confidence</td><td>${b ? (b.confidence * 100).toFixed(0) : 0}%</td></tr>
        </table>

        <div class="evidence-quote-box">
          "${evB ? evB.quote : 'N/A'}"
          <div class="evidence-meta">
            <span>${evB ? evB.document_id : 'N/A'}</span>
            <span>p. ${evB ? evB.page_number : 'N/A'}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Deterministic Cascade Trace -->
    ${rel ? `
      <div class="reconciliation-result-box">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div style="font-size: 0.85rem; font-weight: 600;">Deterministic Cascade Audit Verdict</div>
          <span class="showcase-item-badge ${badgeClass}">Confidence: ${(rel.confidence * 100).toFixed(0)}%</span>
        </div>
        <p style="font-size: 0.82rem; color: var(--text-secondary); line-height: 1.5; margin-top: 0.5rem;">
          <strong>Explanation:</strong> ${rel.explanation}
        </p>
        <div style="margin-top: 0.65rem;">
          ${(rel.reasons || []).map(r => `<span class="reason-tag">${r}</span>`).join('')}
        </div>
      </div>
    ` : ''}
  `;
}

// -----------------------------------------------------------------
// Canonical Facts List
// -----------------------------------------------------------------
async function loadGroupedFacts() {
  try {
    const res = await fetch(api("/api/facts"));
    allFacts = await res.json();
    renderFacts(allFacts);
  } catch (e) {
    allFacts = [];
  }
}

function renderFacts(facts) {
  const container = document.getElementById("factsList");
  if (!container) return;
  container.innerHTML = "";

  if (!facts || facts.length === 0) {
    container.innerHTML = `<div style="padding: 2rem; color: var(--text-tertiary); text-align: center;">No canonical facts grouped yet.</div>`;
    return;
  }

  facts.forEach(f => {
    const card = document.createElement("div");
    card.className = "fact-card";
    const badgeClass = `badge-${f.status}`;

    let obsHtml = "";
    (f.observations || []).forEach(o => {
      const ev = o.evidence && o.evidence[0];
      obsHtml += `
        <div style="background: var(--bg-surface-elevated); padding: 0.65rem 0.85rem; border-radius: var(--radius-sm); margin-top: 0.45rem; border: 1px solid var(--border-subtle);">
          <div style="display:flex; justify-content:space-between; font-size: 0.78rem;">
            <span>Reported: <strong>${o.value.amount !== null ? o.value.amount : o.value.text} ${o.value.unit || ''}</strong></span>
            <span style="font-family: var(--font-mono); color: var(--text-secondary)">Norm: ${o.value.normalized_amount ? o.value.normalized_amount.toLocaleString() : 'N/A'} ${o.value.normalized_unit || ''}</span>
          </div>
          <div style="font-size: 0.72rem; color: var(--text-tertiary); margin-top: 0.25rem;">
            Source: ${ev ? ev.document_id : 'N/A'} &bull; p. ${ev ? ev.page_number : 'N/A'} &bull; Status: ${o.assertion_status}
          </div>
        </div>
      `;
    });

    card.innerHTML = `
      <div class="rel-header">
        <span class="showcase-item-badge ${badgeClass}">${f.status}</span>
        <span style="font-size: 0.75rem; font-family: var(--font-mono); color: var(--text-tertiary);">${f.period || 'General'}</span>
      </div>
      <div class="rel-title">${f.entity} &bull; ${f.concept}</div>
      <div style="font-size: 1.15rem; font-weight: 700; font-family: var(--font-mono); color: var(--text-primary); margin-bottom: 0.5rem;">
        ${f.consensus_value !== null ? f.consensus_value.toLocaleString() : 'Nuanced'} ${f.consensus_unit || ''}
      </div>
      <div style="font-size: 0.78rem; color: var(--text-secondary); margin-bottom: 0.45rem;">
        Grounding Observations (${(f.observations || []).length}):
      </div>
      ${obsHtml}
    `;
    container.appendChild(card);
  });
}

// -----------------------------------------------------------------
// Atomic Observations Grid
// -----------------------------------------------------------------
async function loadObservations(docId = "all") {
  try {
    const url = docId && docId !== "all"
      ? api(`/api/observations?document_id=${docId}`)
      : api("/api/observations");
    const res = await fetch(url);
    allObservations = await res.json();
    renderObservations(allObservations);
    renderNeedsReview(allObservations.filter(o => o.needs_review));
  } catch (e) {
    allObservations = [];
  }
}

function renderObservations(items) {
  const container = document.getElementById("observationsGrid");
  if (!container) return;
  container.innerHTML = "";

  if (!items || items.length === 0) {
    container.innerHTML = `<div style="grid-column: 1 / -1; padding: 2rem; color: var(--text-tertiary); text-align: center;">No observations found for this context.</div>`;
    return;
  }

  items.forEach(obs => {
    const card = document.createElement("div");
    card.className = "obs-card";
    const ev = obs.evidence && obs.evidence[0];

    card.innerHTML = `
      <div class="obs-box-header">
        <span class="obs-entity-title" style="font-size: 0.95rem;">${obs.entity.canonical_name}</span>
        ${obs.needs_review
        ? `<span class="showcase-item-badge badge-review">Quarantine</span>`
        : `<span class="showcase-item-badge badge-corroborated">${obs.assertion_status}</span>`}
      </div>
      <div class="obs-concept-sub">${obs.concept.canonical_name}</div>
      <div class="value-display">
        <span class="reported-value" style="font-size: 1.25rem;">
          ${obs.value.amount !== null && obs.value.amount !== undefined ? obs.value.amount : (obs.value.text || 'N/A')} ${obs.value.unit || ''}
        </span>
        ${obs.value.normalized_amount !== null && obs.value.normalized_amount !== undefined
        ? `<span class="normalized-pill">Norm: ${obs.value.normalized_amount.toLocaleString()} ${obs.value.normalized_unit}</span>`
        : ''}
      </div>

      <div style="font-size: 0.74rem; color: var(--text-tertiary); margin-bottom: 0.65rem;">
        Period: <strong>${obs.time ? obs.time.label : 'N/A'}</strong> &bull; Scope: <strong>${obs.scope ? obs.scope.consolidation : 'N/A'}</strong>
      </div>

      ${obs.review_reason ? `
        <div style="background: var(--color-review-bg); border-left: 2px solid var(--color-review); padding: 0.45rem 0.65rem; font-size: 0.72rem; color: #e9d5ff; margin-bottom: 0.65rem;">
          ⚠ ${obs.review_reason}
        </div>
      ` : ''}

      <div class="evidence-quote-box" style="margin-top: auto;">
        "${ev ? ev.quote : 'N/A'}"
        <div class="evidence-meta">
          <span>${ev ? ev.document_id : 'N/A'}</span>
          <span>p. ${ev ? ev.page_number : 'N/A'}</span>
        </div>
      </div>
    `;
    container.appendChild(card);
  });
}

function filterObservations() {
  const statusVal = document.getElementById("obsFilterStatus").value;
  const searchVal = document.getElementById("obsSearchInput").value.toLowerCase();

  const filtered = allObservations.filter(o => {
    let matchStatus = true;
    if (statusVal === "review_only") matchStatus = o.needs_review;
    else if (statusVal === "actual") matchStatus = o.assertion_status === "actual";
    else if (statusVal === "estimate") matchStatus = ["estimate", "forecast", "projected"].includes(o.assertion_status);

    const matchSearch =
      !searchVal ||
      o.entity.canonical_name.toLowerCase().includes(searchVal) ||
      o.concept.canonical_name.toLowerCase().includes(searchVal) ||
      (o.time && o.time.label && o.time.label.toLowerCase().includes(searchVal));

    return matchStatus && matchSearch;
  });

  renderObservations(filtered);
}

// -----------------------------------------------------------------
// Quarantine Queue Grid
// -----------------------------------------------------------------
function renderNeedsReview(items) {
  const container = document.getElementById("needsReviewGrid");
  if (!container) return;
  container.innerHTML = "";

  if (!items || items.length === 0) {
    container.innerHTML = `
      <div style="grid-column: 1 / -1; padding: 2.5rem 1rem; color: var(--text-tertiary); text-align: center;">
        ✓ Quarantine queue is empty. All claims have complete temporal and unit grounding.
      </div>
    `;
    return;
  }

  items.forEach(obs => {
    const card = document.createElement("div");
    card.className = "obs-card";
    card.style.borderColor = "var(--color-review-border)";
    const ev = obs.evidence && obs.evidence[0];

    card.innerHTML = `
      <div class="obs-box-header">
        <span class="obs-entity-title" style="font-size: 0.95rem;">${obs.entity.canonical_name}</span>
        <span class="showcase-item-badge badge-review">Quarantine Audit</span>
      </div>
      <div class="obs-concept-sub">${obs.concept.canonical_name}</div>
      <div class="value-display">
        <span class="reported-value" style="font-size: 1.25rem;">
          ${obs.value.amount !== null ? obs.value.amount : (obs.value.text || 'N/A')} ${obs.value.unit || ''}
        </span>
      </div>

      <div style="background: var(--color-review-bg); border-left: 2px solid var(--color-review); padding: 0.45rem 0.65rem; font-size: 0.72rem; color: #e9d5ff; margin-bottom: 0.65rem;">
        ⚠ Reason: <strong>${obs.review_reason || 'Incomplete grounding'}</strong>
      </div>

      <div class="evidence-quote-box" style="margin-top: auto;">
        "${ev ? ev.quote : 'N/A'}"
        <div class="evidence-meta">
          <span>${ev ? ev.document_id : 'N/A'}</span>
          <span>p. ${ev ? ev.page_number : 'N/A'}</span>
        </div>
      </div>
    `;
    container.appendChild(card);
  });
}

// -----------------------------------------------------------------
// Relationships List
// -----------------------------------------------------------------
async function loadRelationships(docId = "all") {
  try {
    const url = docId && docId !== "all"
      ? api(`/api/relationships?document_id=${docId}`)
      : api("/api/relationships");
    const res = await fetch(url);
    allRelationships = await res.json();
    renderRelationships(allRelationships);
  } catch (e) {
    allRelationships = [];
  }
}

function renderRelationships(items) {
  const container = document.getElementById("relationshipsList");
  if (!container) return;
  container.innerHTML = "";

  if (!items || items.length === 0) {
    container.innerHTML = `<div style="padding: 2rem; color: var(--text-tertiary); text-align: center;">No relationships found for this context.</div>`;
    return;
  }

  items.forEach(rel => {
    const card = document.createElement("div");
    card.className = "relationship-card";
    const badgeClass = `badge-${rel.relationship_type}`;

    card.innerHTML = `
      <div class="rel-header">
        <span class="showcase-item-badge ${badgeClass}">${rel.relationship_type}</span>
        <span style="font-size: 0.72rem; color: var(--text-tertiary); font-family: var(--font-mono)">
          Confidence: ${(rel.confidence * 100).toFixed(0)}%
        </span>
      </div>
      <div class="rel-title">${rel.observation_a} &bull; ${rel.observation_b}</div>
      <div class="rel-explanation">${rel.explanation}</div>
      <div style="margin-top: 0.5rem;">
        ${(rel.reasons || []).map(r => `<span class="reason-tag">${r}</span>`).join('')}
      </div>
    `;
    container.appendChild(card);
  });
}

function filterRelationships() {
  const typeVal = document.getElementById("relTypeFilter").value;
  const searchVal = document.getElementById("relSearchInput").value.toLowerCase();

  const filtered = allRelationships.filter(r => {
    const matchType = typeVal === "all" || (r.relationship_type || "").toLowerCase() === typeVal.toLowerCase();
    const matchSearch =
      !searchVal ||
      r.explanation.toLowerCase().includes(searchVal) ||
      (r.reasons || []).some(reason => reason.toLowerCase().includes(searchVal)) ||
      r.observation_a.toLowerCase().includes(searchVal) ||
      r.observation_b.toLowerCase().includes(searchVal);

    return matchType && matchSearch;
  });

  renderRelationships(filtered);
}

// -----------------------------------------------------------------
// Filings List
// -----------------------------------------------------------------
function renderDocuments(docs) {
  const container = document.getElementById("documentsGrid");
  if (!container) return;
  container.innerHTML = "";

  if (!docs || docs.length === 0) {
    container.innerHTML = `<div style="grid-column: 1 / -1; padding: 2rem; color: var(--text-tertiary); text-align: center;">No filing documents processed.</div>`;
    return;
  }

  docs.forEach(d => {
    const card = document.createElement("div");
    card.className = "obs-card";
    card.innerHTML = `
      <div class="obs-box-header">
        <span class="obs-entity-title" style="font-size: 0.95rem;">${d.filename}</span>
        <span class="showcase-item-badge badge-corroborated">${d.document_type || 'PDF'}</span>
      </div>
      <div style="font-size: 0.8rem; color: var(--accent-primary); font-family: var(--font-mono); margin-bottom: 0.45rem;">
        Dataset: ${d.dataset} &bull; ${d.page_count} Pages
      </div>
      <div style="font-size: 0.72rem; color: var(--text-tertiary); margin-top: auto;">
        Document ID: <code>${d.id}</code><br/>Ingested: ${d.created_at}
      </div>
    `;
    container.appendChild(card);
  });
}
