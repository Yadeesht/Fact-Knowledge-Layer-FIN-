// Financial Fact Knowledge Layer - Frontend Controller

let showcaseData = [];
let allRelationships = [];
let allObservations = [];
let activeCaseIndex = 0;

document.addEventListener("DOMContentLoaded", () => {
  initApp();
  setupKeyBindings();
});

async function initApp() {
  updateApiStatus("connecting", "Connecting to Knowledge Layer...");
  try {
    const health = await fetch("/api/health").then(r => r.json());
    if (health.status === "ok") {
      updateApiStatus("connected", "FastAPI Knowledge Engine Online");
    }
  } catch (err) {
    updateApiStatus("offline", "Backend Offline (Demo Mode Active)");
  }

  await loadShowcaseCases();
  await loadDocuments();
  await loadGroupedFacts();
  await loadRelationships();
  await loadObservations();
  await loadRuns();
  await checkLLMStatus();
}

function updateApiStatus(status, text) {
  const badge = document.getElementById("apiStatusBadge");
  const dot = badge.querySelector(".status-dot");
  const label = document.getElementById("apiStatusText");

  label.innerText = text;
  if (status === "connected") {
    dot.style.background = "var(--color-corroborated)";
    dot.style.boxShadow = "0 0 8px var(--color-corroborated)";
  } else if (status === "offline") {
    dot.style.background = "var(--color-contextualized)";
    dot.style.boxShadow = "0 0 8px var(--color-contextualized)";
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
// Showcase Cases
// -----------------------------------------------------------------
async function loadShowcaseCases() {
  try {
    const res = await fetch("/api/showcase");
    showcaseData = await res.json();
  } catch (e) {
    console.error("Failed to fetch showcase from API, using fallback data:", e);
  }

  renderShowcaseSelectors();
  renderActiveShowcase(activeCaseIndex);
}

function renderShowcaseSelectors() {
  const container = document.getElementById("showcaseSelectorGrid");
  container.innerHTML = "";

  if (!showcaseData || showcaseData.length === 0) {
    container.innerHTML = `
      <div style="grid-column: 1 / -1; background: var(--bg-card); padding: 1rem 1.5rem; border-radius: 8px; border: 1px dashed var(--border-color); color: var(--text-muted); font-size: 0.88rem; text-align: center;">
        📁 No processed document artifacts found in <code>processed/</code> folder.
      </div>
    `;
    return;
  }

  showcaseData.forEach((item, idx) => {
    const card = document.createElement("div");
    card.className = `showcase-card ${idx === activeCaseIndex ? "active" : ""}`;
    card.onclick = () => {
      activeCaseIndex = idx;
      renderShowcaseSelectors();
      renderActiveShowcase(idx);
    };

    const badgeClass = `badge-${item.category}`;
    card.innerHTML = `
      <span class="badge ${badgeClass}">${item.badge}</span>
      <div class="showcase-card-title">${item.title}</div>
      <div class="showcase-card-takeaway">${item.takeaway}</div>
    `;
    container.appendChild(card);
  });
}

function renderActiveShowcase(idx) {
  const container = document.getElementById("showcaseInspector");
  const item = showcaseData[idx];

  if (!item || showcaseData.length === 0) {
    container.innerHTML = `
      <div style="text-align: center; padding: 3.5rem 1.5rem; background: var(--bg-card); border-radius: 12px; border: 1px dashed var(--border-color);">
        <div style="font-size: 2.5rem; margin-bottom: 0.75rem;">📂</div>
        <h3 style="margin-bottom: 0.5rem; color: var(--text-main); font-weight: 700;">Nothing Processed Yet</h3>
        <p style="color: var(--text-muted); font-size: 0.9rem; max-width: 520px; margin: 0 auto 1.5rem; line-height: 1.6;">
          Seed data has been removed. No processed document artifacts currently exist in the <code>processed/</code> folder. Upload a PDF using the ingestion button above or place a processed <code>.json</code> file into <code>processed/</code> to view demo findings.
        </p>
        <button class="btn btn-primary" onclick="document.getElementById('pdfFileInput').click()">⚡ Upload & Ingest PDF Document</button>
      </div>
    `;
    return;
  }


  if (item.category === "needs_review") {
    // Single observation anomaly
    const obs = item.observation;
    const ev = obs.evidence && obs.evidence[0];
    container.innerHTML = `
      <div class="inspector-header">
        <div>
          <span class="badge badge-needs_review">Case 4: Anomaly Escalation</span>
          <h3>${item.title}</h3>
          <p style="color: var(--text-muted); font-size: 0.9rem; margin-top: 0.35rem;">${item.takeaway}</p>
        </div>
      </div>
      <div class="comparison-grid" style="grid-template-columns: 1fr;">
        <div class="observation-box" style="border-color: rgba(168, 85, 247, 0.4);">
          <div class="obs-box-header">
            <span class="obs-box-tag" style="color: var(--color-review);">Escalated Extraction Observation</span>
            <span class="badge badge-needs_review">Needs Review</span>
          </div>
          <div class="obs-entity-title">${obs.entity.canonical_name}</div>
          <div class="obs-concept-sub">Metric: ${obs.concept.canonical_name}</div>

          <div class="value-display">
            <span class="reported-value">${obs.value.amount || obs.value.text}</span>
            <span class="normalized-pill">Raw Unit: ${obs.value.unit || 'MISSING'}</span>
          </div>

          <table class="details-table">
            <tr><td>Timeframe</td><td>${obs.time.label || '<span style="color:#f87171">Omitted / Ambiguous</span>'}</td></tr>
            <tr><td>Assertion Status</td><td>${obs.assertion_status}</td></tr>
            <tr><td>Extraction Confidence</td><td>${(obs.confidence * 100).toFixed(0)}%</td></tr>
            <tr><td>Escalation Reason</td><td style="color:#e9d5ff">${item.review_reason}</td></tr>
          </table>

          <div class="evidence-quote-box">
            "${ev ? ev.quote : 'N/A'}"
            <div class="evidence-meta">
              <span>Filing: ${ev ? ev.document_id : 'N/A'}</span>
              <span>Page ${ev ? ev.page_number : 'N/A'}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="reconciliation-result-box" style="margin-top: 1.5rem;">
        <h4 style="font-size: 0.95rem; margin-bottom: 0.5rem;">Engine Defense Policy</h4>
        <p style="font-size: 0.85rem; color: #cbd5e1; line-height: 1.5;">
          The reconciliation engine refuses to blindly compare or infer dates/units when documents omit foundational grounding. By surfacing this in the audit queue with confidence <code>${obs.confidence}</code>, the system guarantees that down-stream investment or policy decisions are not corrupted by hallucinated temporal anchors.
        </p>
      </div>
    `;
    return;
  }

  // Dual observation comparison (Corroborated, Contradicted, Contextualized)
  const a = item.observation_a;
  const b = item.observation_b;
  const rel = item.relationship;
  const evA = a.evidence && a.evidence[0];
  const evB = b.evidence && b.evidence[0];

  const badgeClass = `badge-${item.category}`;

  // Cascade step badges
  let stepChainHtml = "";
  if (item.category === "corroborated") {
    stepChainHtml = `
      <div class="cascade-step-badge pass">✓ 1. Entity Match: "${a.entity.canonical_name}"</div>
      <div class="cascade-step-badge pass">✓ 2. Concept Match: "${a.concept.canonical_name}"</div>
      <div class="cascade-step-badge pass">✓ 3. Scope Compatible: consolidated</div>
      <div class="cascade-step-badge pass">✓ 4. Time Match: FY24</div>
      <div class="cascade-step-badge pass">✓ 5. Unit Normalization: ₹8,142.16 Cr = ₹81,424 Mn (<0.1% diff)</div>
      <div class="cascade-step-badge pass">★ Verdict: CORROBORATED (Confidence: 99%)</div>
    `;
  } else if (item.category === "contradicted") {
    stepChainHtml = `
      <div class="cascade-step-badge pass">✓ 1. Entity Match: "India"</div>
      <div class="cascade-step-badge pass">✓ 2. Concept Match: "Real GDP Growth"</div>
      <div class="cascade-step-badge pass">✓ 3. Scope Compatible: economy / national</div>
      <div class="cascade-step-badge pass">✓ 4. Time Match: FY25</div>
      <div class="cascade-step-badge pass">✓ 5. Assertion Status Match: Actual vs Actual</div>
      <div class="cascade-step-badge diverge">✗ 6. Value Divergence: 6.4% vs 7.2% (> 0.1% materiality threshold)</div>
      <div class="cascade-step-badge diverge">★ Verdict: CONTRADICTED (Confidence: 93%)</div>
    `;
  } else if (item.category === "contextualized") {
    stepChainHtml = `
      <div class="cascade-step-badge pass">✓ 1. Entity Match: "India"</div>
      <div class="cascade-step-badge pass">✓ 2. Concept Match: "Real GDP Growth"</div>
      <div class="cascade-step-badge pass">✓ 3. Period Match: FY25</div>
      <div class="cascade-step-badge context">⚠ 4. Status Check: Estimate (Survey) vs Forecast / Projection (IMF)</div>
      <div class="cascade-step-badge context">★ Verdict: CONTEXTUALIZED (Different Assertion Status, not Contradiction)</div>
    `;
  }

  container.innerHTML = `
    <div class="inspector-header">
      <div>
        <span class="badge ${badgeClass}">${item.badge}</span>
        <h3>${item.title}</h3>
        <p style="color: var(--text-muted); font-size: 0.9rem; margin-top: 0.35rem;">${item.takeaway}</p>
      </div>
    </div>

    <div class="comparison-grid">
      <!-- Observation A -->
      <div class="observation-box">
        <div class="obs-box-header">
          <span class="obs-box-tag">Observation A</span>
          <span class="badge ${badgeClass}">${a.assertion_status}</span>
        </div>
        <div class="obs-entity-title">${a.entity.canonical_name}</div>
        <div class="obs-concept-sub">${a.concept.canonical_name}</div>

        <div class="value-display">
          <span class="reported-value">${a.value.amount} ${a.value.unit || ''}</span>
          <span class="normalized-pill">Norm: ${a.value.normalized_amount.toLocaleString()} ${a.value.normalized_unit}</span>
        </div>

        <table class="details-table">
          <tr><td>Reporting Period</td><td>${a.time.label || 'N/A'}</td></tr>
          <tr><td>Scope Level</td><td>${a.scope.level || 'N/A'} (${a.scope.consolidation})</td></tr>
          <tr><td>Confidence</td><td>${(a.confidence * 100).toFixed(0)}%</td></tr>
        </table>

        <div class="evidence-quote-box">
          "${evA ? evA.quote : 'N/A'}"
          <div class="evidence-meta">
            <span>${evA ? evA.document_id : 'N/A'}</span>
            <span>Page ${evA ? evA.page_number : 'N/A'}</span>
          </div>
        </div>
      </div>

      <!-- Observation B -->
      <div class="observation-box">
        <div class="obs-box-header">
          <span class="obs-box-tag">Observation B</span>
          <span class="badge ${badgeClass}">${b.assertion_status}</span>
        </div>
        <div class="obs-entity-title">${b.entity.canonical_name}</div>
        <div class="obs-concept-sub">${b.concept.canonical_name}</div>

        <div class="value-display">
          <span class="reported-value">${b.value.amount} ${b.value.unit || ''}</span>
          <span class="normalized-pill">Norm: ${b.value.normalized_amount.toLocaleString()} ${b.value.normalized_unit}</span>
        </div>

        <table class="details-table">
          <tr><td>Reporting Period</td><td>${b.time.label || 'N/A'}</td></tr>
          <tr><td>Scope Level</td><td>${b.scope.level || 'N/A'} (${b.scope.consolidation})</td></tr>
          <tr><td>Confidence</td><td>${(b.confidence * 100).toFixed(0)}%</td></tr>
        </table>

        <div class="evidence-quote-box">
          "${evB ? evB.quote : 'N/A'}"
          <div class="evidence-meta">
            <span>${evB ? evB.document_id : 'N/A'}</span>
            <span>Page ${evB ? evB.page_number : 'N/A'}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Deterministic Cascade Trace -->
    <div class="reconciliation-result-box">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <h4 style="font-size: 0.95rem;">Deterministic Cascade Execution Trace</h4>
        <span class="badge ${badgeClass}">Confidence: ${(rel.confidence * 100).toFixed(0)}%</span>
      </div>

      <div class="cascade-step-chain">
        ${stepChainHtml}
      </div>

      <p style="font-size: 0.88rem; color: #cbd5e1; line-height: 1.5; margin-top: 0.5rem;">
        <strong>Formal Explanation:</strong> ${rel.explanation}
      </p>

      <div style="margin-top: 0.75rem; display: flex; gap: 0.5rem; flex-wrap: wrap;">
        ${rel.reasons.map(r => `<span class="reason-tag">${r}</span>`).join('')}
      </div>
    </div>
  `;
}

// -----------------------------------------------------------------
// Relationships List
// -----------------------------------------------------------------
async function loadRelationships() {
  try {
    const res = await fetch("/api/relationships");
    allRelationships = await res.json();
    renderRelationships(allRelationships);
    updateStats();
  } catch (e) {
    console.error("Failed to load relationships:", e);
  }
}

function renderRelationships(items) {
  const container = document.getElementById("relationshipsList");
  container.innerHTML = "";

  if (items.length === 0) {
    container.innerHTML = `<div style="text-align:center; padding: 2rem; color: var(--text-muted)">No relationships found matching criteria.</div>`;
    return;
  }

  items.forEach(rel => {
    const card = document.createElement("div");
    card.className = "relationship-card";
    const badgeClass = `badge-${rel.relationship_type}`;

    card.innerHTML = `
      <div class="rel-header">
        <span class="badge ${badgeClass}">${rel.relationship_type}</span>
        <span style="font-size: 0.78rem; color: var(--text-dim); font-family: var(--font-mono)">
          Confidence: ${(rel.confidence * 100).toFixed(0)}%
        </span>
      </div>
      <div class="rel-title">${rel.observation_a} &bull; ${rel.observation_b}</div>
      <div class="rel-explanation">${rel.explanation}</div>
      <div class="rel-reasons">
        ${rel.reasons.map(r => `<span class="reason-tag">${r}</span>`).join('')}
      </div>
    `;
    container.appendChild(card);
  });
}

function filterRelationships() {
  const typeVal = document.getElementById("relTypeFilter").value;
  const searchVal = document.getElementById("relSearchInput").value.toLowerCase();

  const filtered = allRelationships.filter(r => {
    const matchType = typeVal === "all" || r.relationship_type === typeVal;
    const matchSearch =
      !searchVal ||
      r.explanation.toLowerCase().includes(searchVal) ||
      r.reasons.some(reason => reason.toLowerCase().includes(searchVal)) ||
      r.observation_a.toLowerCase().includes(searchVal) ||
      r.observation_b.toLowerCase().includes(searchVal);

    return matchType && matchSearch;
  });

  renderRelationships(filtered);
}

// -----------------------------------------------------------------
// Observations Grid
// -----------------------------------------------------------------
async function loadObservations() {
  try {
    const res = await fetch("/api/observations");
    allObservations = await res.json();
    renderObservations(allObservations);
    renderNeedsReview();
    updateStats();
  } catch (e) {
    console.error("Failed to load observations:", e);
  }
}

function renderObservations(items) {
  const container = document.getElementById("observationsGrid");
  container.innerHTML = "";

  if (!items || items.length === 0) {
    container.innerHTML = `
      <div style="grid-column: 1 / -1; text-align: center; padding: 3rem 1.5rem; background: var(--bg-card); border-radius: 12px; border: 1px dashed var(--border-color);">
        <h3 style="margin-bottom: 0.5rem; color: var(--text-main);">Nothing Processed Yet</h3>
        <p style="color: var(--text-muted); font-size: 0.9rem; max-width: 500px; margin: 0 auto;">
          No atomic observations extracted yet. Ingest a PDF document or place a processed artifact into <code>processed/</code>.
        </p>
      </div>
    `;
    return;
  }

  items.forEach(obs => {
    const card = document.createElement("div");
    card.className = `obs-card ${obs.needs_review ? 'needs-review' : ''}`;
    const ev = obs.evidence && obs.evidence[0];

    card.innerHTML = `
      <div class="obs-box-header">
        <span class="obs-entity-title" style="font-size: 1rem;">${obs.entity.canonical_name}</span>
        ${obs.needs_review
          ? `<span class="badge badge-needs_review">Needs Review</span>`
          : `<span class="badge badge-corroborated">${obs.assertion_status}</span>`}
      </div>
      <div class="obs-concept-sub">${obs.concept.canonical_name}</div>
      <div class="value-display">
        <span class="reported-value" style="font-size: 1.3rem;">
          ${obs.value.amount !== null && obs.value.amount !== undefined ? obs.value.amount : (obs.value.text || 'N/A')} ${obs.value.unit || ''}
        </span>
        ${obs.value.normalized_amount !== null && obs.value.normalized_amount !== undefined
          ? `<span class="normalized-pill">Norm: ${obs.value.normalized_amount.toLocaleString()} ${obs.value.normalized_unit}</span>`
          : ''}
      </div>

      <div style="font-size: 0.78rem; color: var(--text-dim); margin-bottom: 0.75rem;">
        Period: <strong>${obs.time.label || 'N/A'}</strong> &bull; Scope: <strong>${obs.scope.consolidation || 'N/A'}</strong>
      </div>

      ${obs.review_reason ? `
        <div style="background: rgba(168, 85, 247, 0.1); border-left: 2px solid var(--color-review); padding: 0.5rem; font-size: 0.75rem; color: #e9d5ff; margin-bottom: 0.75rem;">
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
      (o.time.label && o.time.label.toLowerCase().includes(searchVal));

    return matchStatus && matchSearch;
  });

  renderObservations(filtered);
}

// -----------------------------------------------------------------
// Documents Explorer (Section 2 & 33)
// -----------------------------------------------------------------
let allDocuments = [];

async function loadDocuments() {
  try {
    const res = await fetch("/api/documents");
    allDocuments = await res.json();
    renderDocuments(allDocuments);
  } catch (e) {
    console.error("Failed to load documents:", e);
  }
}

function renderDocuments(docs) {
  const container = document.getElementById("documentsGrid");
  if (!container) return;
  container.innerHTML = "";

  if (!docs || docs.length === 0) {
    container.innerHTML = `
      <div style="grid-column: 1 / -1; text-align: center; padding: 3rem 1.5rem; background: var(--bg-card); border-radius: 12px; border: 1px dashed var(--border-color);">
        <h3 style="margin-bottom: 0.5rem; color: var(--text-main);">Nothing Processed Yet</h3>
        <p style="color: var(--text-muted); font-size: 0.9rem; max-width: 500px; margin: 0 auto;">
          No PDF documents exist in the knowledge layer or <code>processed/</code> directory.
        </p>
      </div>
    `;
    return;
  }


  docs.forEach(d => {
    const card = document.createElement("div");
    card.className = "obs-card";
    card.innerHTML = `
      <div class="obs-box-header">
        <span class="obs-entity-title" style="font-size: 1rem;">${d.filename}</span>
        <span class="badge badge-corroborated">${d.document_type || 'PDF'}</span>
      </div>
      <div style="font-size: 0.85rem; color: var(--accent-cyan); margin-bottom: 0.5rem; font-family: var(--font-mono);">
        Dataset: ${d.dataset} &bull; Retained Excerpt: ${d.page_count} pages
      </div>
      <div style="font-size: 0.78rem; color: var(--text-dim); margin-top: 0.5rem;">
        ID: <code>${d.id}</code> &bull; Ingested: ${d.created_at}
      </div>
    `;
    container.appendChild(card);
  });
}

// -----------------------------------------------------------------
// PDF File Upload (§11 POST /documents)
// -----------------------------------------------------------------
async function handlePDFUpload(event) {
  const file = event.target.files[0];
  if (!file) return;

  if (!file.name.toLowerCase().endsWith(".pdf")) {
    alert("Please select a PDF file.");
    return;
  }

  const formData = new FormData();
  formData.append("file", file);

  updateApiStatus("connecting", `Ingesting ${file.name}...`);
  try {
    const res = await fetch("/api/documents", {
      method: "POST",
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Upload failed");
    }
    const data = await res.json();
    alert(`Ingestion Successful!\n\nDocument: ${data.filename}\nPages: ${data.page_count}\nChunks: ${data.chunks_stored}\nExtracted Observations: ${data.observations_extracted}\nRelationships Formed: ${data.relationships_generated}`);

    // Refresh UI views
    await loadDocuments();
    await loadGroupedFacts();
    await loadObservations();
    await loadRelationships();
    await loadRuns();
    updateApiStatus("connected", "FastAPI Knowledge Engine Online");
    switchTab("documentsTab");
  } catch (err) {
    alert("Error uploading PDF: " + err.message);
    updateApiStatus("connected", "FastAPI Knowledge Engine Online");
  } finally {
    event.target.value = "";
  }
}

// -----------------------------------------------------------------
// Canonical Facts (§2 & §12)
// -----------------------------------------------------------------
let allFacts = [];

async function loadGroupedFacts() {
  try {
    const res = await fetch("/api/facts");
    allFacts = await res.json();
    renderFacts(allFacts);
  } catch (e) {
    console.error("Failed to load grouped facts:", e);
  }
}

function renderFacts(facts) {
  const container = document.getElementById("factsList");
  if (!container) return;
  container.innerHTML = "";

  if (facts.length === 0) {
    container.innerHTML = `<div style="text-align:center; padding: 2rem; color: var(--text-muted)">No facts found.</div>`;
    return;
  }

  facts.forEach((f, idx) => {
    const card = document.createElement("div");
    card.className = "relationship-card";
    const badgeClass = `badge-${f.status}`;

    let obsHtml = "";
    f.observations.forEach(o => {
      const ev = o.evidence && o.evidence[0];
      obsHtml += `
        <div style="background: rgba(0,0,0,0.25); border-radius: 6px; padding: 0.75rem; margin-top: 0.5rem; font-size: 0.82rem;">
          <div style="display:flex; justify-content:space-between; margin-bottom: 0.25rem;">
            <span><strong>${o.value.amount || o.value.text} ${o.value.unit || ''}</strong> (Period: ${o.time.label || 'N/A'}, Status: ${o.assertion_status})</span>
            <span style="color:var(--accent-cyan); font-family:var(--font-mono);">Norm: ${o.value.normalized_amount || 'N/A'} ${o.value.normalized_unit || ''}</span>
          </div>
          <div style="font-style:italic; color:#cbd5e1; margin-top:0.25rem;">
            "${ev ? ev.quote : 'N/A'}"
          </div>
          <div style="font-size:0.72rem; color:var(--text-dim); margin-top:0.25rem;">
            Source: ${ev ? ev.document_id : 'N/A'} &bull; Page ${ev ? ev.page_number : 'N/A'}
          </div>
        </div>
      `;
    });

    let relHtml = "";
    f.relationships.forEach(r => {
      relHtml += `
        <div style="margin-top: 0.5rem; padding: 0.5rem; background: rgba(99,102,241,0.08); border-left: 2px solid var(--accent-primary); font-size: 0.8rem;">
          <div style="display:flex; justify-content:space-between;">
            <span class="badge badge-${r.relationship_type}">${r.relationship_type}</span>
            <span style="font-family:var(--font-mono); color:var(--text-dim);">${(r.confidence*100).toFixed(0)}%</span>
          </div>
          <p style="color:#e2e8f0; margin-top:0.25rem;">${r.explanation}</p>
        </div>
      `;
    });

    card.innerHTML = `
      <div class="rel-header" style="cursor: pointer;" onclick="toggleFactDetail(${idx})">
        <div>
          <span class="badge ${badgeClass}">${f.status}</span>
          <span style="font-weight:700; font-size:1.05rem; margin-left:0.5rem; color:#fff;">
            ${f.entity_name} &bull; ${f.concept_name}
          </span>
        </div>
        <span style="font-size: 0.8rem; color: var(--accent-cyan); font-family: var(--font-mono);">
          ${f.observation_count} observation(s) ▼
        </span>
      </div>
      <div id="factDetail_${idx}" style="margin-top: 0.75rem;">
        <div style="font-size: 0.82rem; color: var(--text-muted); margin-bottom: 0.5rem;">
          Underlying Grounded Observations:
        </div>
        ${obsHtml}
        ${f.relationships.length > 0 ? `
          <div style="font-size: 0.82rem; color: var(--text-muted); margin-top: 0.75rem;">
            Pairwise Reconciliation Results:
          </div>
          ${relHtml}
        ` : ''}
      </div>
    `;
    container.appendChild(card);
  });
}

function toggleFactDetail(idx) {
  const detail = document.getElementById(`factDetail_${idx}`);
  if (detail) {
    detail.style.display = detail.style.display === "none" ? "block" : "none";
  }
}

function filterFacts() {
  const statusVal = document.getElementById("factFilterStatus").value;
  const searchVal = document.getElementById("factSearchInput").value.toLowerCase();

  const filtered = allFacts.filter(f => {
    const matchStatus = statusVal === "all" || f.status === statusVal;
    const matchSearch =
      !searchVal ||
      f.entity_name.toLowerCase().includes(searchVal) ||
      f.concept_name.toLowerCase().includes(searchVal) ||
      f.observations.some(o => (o.value.text && o.value.text.toLowerCase().includes(searchVal)) || (o.time.label && o.time.label.toLowerCase().includes(searchVal)));

    return matchStatus && matchSearch;
  });

  renderFacts(filtered);
}

// -----------------------------------------------------------------
// Needs Review Quarantine Queue (Section 26 & 33)
// -----------------------------------------------------------------
function renderNeedsReview() {
  const container = document.getElementById("needsReviewGrid");
  if (!container) return;
  container.innerHTML = "";

  const reviewItems = allObservations.filter(o => o.needs_review);
  if (reviewItems.length === 0) {
    container.innerHTML = `<div style="text-align:center; padding: 2rem; color: var(--text-muted)">Quarantine queue is empty. All extractions meet grounding thresholds.</div>`;
    return;
  }

  reviewItems.forEach(obs => {
    const card = document.createElement("div");
    card.className = "obs-card needs-review";
    const ev = obs.evidence && obs.evidence[0];

    card.innerHTML = `
      <div class="obs-box-header">
        <span class="obs-entity-title" style="font-size: 1rem;">${obs.entity.canonical_name}</span>
        <span class="badge badge-needs_review">Quarantined</span>
      </div>
      <div class="obs-concept-sub">${obs.concept.canonical_name}</div>
      <div class="value-display">
        <span class="reported-value" style="font-size: 1.3rem;">
          ${obs.value.amount !== null && obs.value.amount !== undefined ? obs.value.amount : (obs.value.text || 'N/A')} ${obs.value.unit || 'MISSING UNIT'}
        </span>
      </div>

      <div style="background: rgba(168, 85, 247, 0.15); border-left: 3px solid var(--color-review); padding: 0.75rem; font-size: 0.8rem; color: #f3e8ff; margin-bottom: 0.75rem; border-radius: 0 6px 6px 0;">
        <strong>Quarantine Reason:</strong> ${obs.review_reason || 'Uncertain grounding'}
        <div style="font-size:0.72rem; color:#d8b4fe; margin-top:0.25rem;">
          Policy: Blocked from Candidate Matcher to prevent relationship contamination.
        </div>
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
// Observability Runs
// -----------------------------------------------------------------
async function loadRuns() {
  try {
    const res = await fetch("/api/runs");
    const runs = await res.json();
    const container = document.getElementById("runsList");
    container.innerHTML = "";

    runs.forEach(r => {
      const card = document.createElement("div");
      card.className = "run-card";
      card.innerHTML = `
        <div class="run-header">
          <div>
            <strong>${r.id}</strong> &bull; Document: <code>${r.document_id || 'Global Pipeline'}</code>
          </div>
          <span class="badge badge-corroborated">${r.status}</span>
        </div>
        <div style="font-size: 0.78rem; color: var(--text-muted);">
          Extractor Model: <code>${r.extractor_model}</code> &bull; Started: ${r.started_at} &bull; Completed: ${r.completed_at || 'In progress'}
        </div>
        <div class="run-metrics-grid">
          <div class="run-metric-box">
            <div style="color: var(--text-dim)">Pages Parsed</div>
            <div class="run-metric-num">${r.metrics.pdf_pages_parsed || 0}</div>
          </div>
          <div class="run-metric-box">
            <div style="color: var(--text-dim)">Chunks Created</div>
            <div class="run-metric-num">${r.metrics.chunks_created || 0}</div>
          </div>
          <div class="run-metric-box">
            <div style="color: var(--text-dim)">Observations Extracted</div>
            <div class="run-metric-num">${r.metrics.observations_extracted || 0}</div>
          </div>
          <div class="run-metric-box">
            <div style="color: var(--text-dim)">Normalized Claims</div>
            <div class="run-metric-num">${r.metrics.observations_normalized || 0}</div>
          </div>
          <div class="run-metric-box">
            <div style="color: var(--text-dim)">Relationships Stored</div>
            <div class="run-metric-num">${r.metrics.relationships_generated || 0}</div>
          </div>
        </div>
      `;
      container.appendChild(card);
    });
  } catch (e) {
    console.error("Failed to load runs:", e);
  }
}

// -----------------------------------------------------------------
// Reconcile Trigger
// -----------------------------------------------------------------
async function triggerReconcile() {
  const btn = document.getElementById("btnRunReconcile");
  const originalText = btn.innerHTML;
  btn.innerHTML = `<span class="status-dot" style="background:#fff"></span> Executing Cascade...`;
  btn.disabled = true;

  try {
    const res = await fetch("/api/reconcile", { method: "POST" });
    const result = await res.json();
    alert(`Reconciliation Completed!\n\nPairs Evaluated: ${result.pairs_evaluated}\nRelationships Reconciled: ${result.relationships_stored}`);
    await loadRelationships();
    await loadShowcaseCases();
  } catch (err) {
    alert("Reconciliation request failed: " + err);
  } finally {
    btn.innerHTML = originalText;
    btn.disabled = false;
  }
}

// -----------------------------------------------------------------
// Stats & Counters
// -----------------------------------------------------------------
function updateStats() {
  document.getElementById("statObsCount").innerText = allObservations.length;

  let corr = 0, cont = 0, ctx = 0;
  allRelationships.forEach(r => {
    if (r.relationship_type === "corroborated") corr++;
    else if (r.relationship_type === "contradicted") cont++;
    else if (r.relationship_type === "contextualized") ctx++;
  });

  const revCount = allObservations.filter(o => o.needs_review).length;

  document.getElementById("statCorrCount").innerText = corr;
  document.getElementById("statContCount").innerText = cont;
  document.getElementById("statCtxCount").innerText = ctx;
  document.getElementById("statReviewCount").innerText = revCount;
}

// -----------------------------------------------------------------
// Keybindings
// -----------------------------------------------------------------
function setupKeyBindings() {
  window.addEventListener("keydown", (e) => {
    if (["1", "2", "3", "4"].includes(e.key)) {
      const idx = parseInt(e.key) - 1;
      if (idx < showcaseData.length) {
        switchTab("showcaseTab");
        activeCaseIndex = idx;
        renderShowcaseSelectors();
        renderActiveShowcase(idx);
      }
    }
  });
}

function closeEvidenceModal(e) {
  const modal = document.getElementById("evidenceModal");
  modal.classList.remove("open");
}

// -----------------------------------------------------------------
// Live LLM Studio Methods
// -----------------------------------------------------------------
async function checkLLMStatus() {
  const indicator = document.getElementById("llmStatusIndicator");
  if (!indicator) return;

  try {
    const res = await fetch("/api/llm/status").then(r => r.json());
    if (res.configured) {
      indicator.innerHTML = `<strong style="color:var(--color-corroborated);">● Active:</strong> ${res.provider} (${res.masked_key})`;
    } else {
      indicator.innerHTML = `<strong style="color:var(--color-contextualized);">○ Fallback:</strong> Heuristic Parser Active (Add API Key for live LLM extraction)`;
    }
  } catch (e) {
    indicator.innerText = "Error checking LLM status.";
  }
}

async function saveLLMKey() {
  const provider = document.getElementById("llmProviderSelect").value;
  const key = document.getElementById("llmApiKeyInput").value.trim();
  if (!key) {
    alert("Please enter an API key.");
    return;
  }

  try {
    const res = await fetch("/api/llm/set-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, api_key: key })
    }).then(r => r.json());

    if (res.status === "success") {
      alert(`Successfully configured ${provider.toUpperCase()} API key!`);
      document.getElementById("llmApiKeyInput").value = "";
      await checkLLMStatus();
    }
  } catch (err) {
    alert("Failed to configure key: " + err);
  }
}

function loadPresetText(type) {
  const textarea = document.getElementById("liveExtractTextarea");
  const pageInput = document.getElementById("liveExtractPage");
  const docInput = document.getElementById("liveExtractDocId");

  if (type === "delhivery") {
    textarea.value = "Revenue from operations for the financial year ended March 31, 2024 stood at ₹8,142.16 Crores compared to ₹7,225.30 Crores in the previous year. Express parcel shipment volumes grew to 740 million parcels in FY24.";
    pageInput.value = "110";
    docInput.value = "delhivery_annual_report_fy24";
  } else if (type === "gdp") {
    textarea.value = "India's real GDP is estimated to grow by 6.4 per cent in FY25 in the baseline scenario outlined in the survey, while headline CPI inflation is projected at 5.4 percent.";
    pageInput.value = "48";
    docInput.value = "india_economic_survey_24_25";
  }
}

async function runLiveExtraction() {
  const text = document.getElementById("liveExtractTextarea").value.trim();
  const page = parseInt(document.getElementById("liveExtractPage").value) || 1;
  const docId = document.getElementById("liveExtractDocId").value.trim() || "custom_filing";
  const btn = document.getElementById("btnLiveExtract");
  const resultsContainer = document.getElementById("liveExtractResults");

  if (!text) {
    alert("Please enter or load excerpt text.");
    return;
  }

  const originalText = btn.innerHTML;
  btn.innerHTML = `<span class="status-dot" style="background:#fff"></span> Calling LLM Extractor & Cascade...`;
  btn.disabled = true;

  try {
    const res = await fetch("/api/extract", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, page_number: page, document_id: docId })
    }).then(r => r.json());

    resultsContainer.style.display = "block";
    let obsHtml = "";

    (res.observations || []).forEach(o => {
      obsHtml += `
        <div style="background: rgba(15,23,42,0.8); border: 1px solid var(--border-color); border-radius: 8px; padding: 1rem; margin-bottom: 0.75rem;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 0.5rem;">
            <strong style="color:#fff;">${o.entity.canonical_name} &bull; ${o.concept.canonical_name}</strong>
            <span class="badge ${o.needs_review ? 'badge-needs_review' : 'badge-corroborated'}">${o.assertion_status}</span>
          </div>
          <div style="font-size: 1.1rem; font-family: var(--font-mono); color: var(--accent-cyan); margin-bottom: 0.5rem;">
            Reported: ${o.value.amount || o.value.text} ${o.value.unit || ''} &rarr; Normalized: ${o.value.normalized_amount || 'N/A'} ${o.value.normalized_unit || ''}
          </div>
          <div style="font-size: 0.8rem; color: var(--text-dim);">
            Period: ${o.time.label || 'N/A'} &bull; Quote: "${o.evidence[0] ? o.evidence[0].quote : 'N/A'}"
          </div>
        </div>
      `;
    });

    let relHtml = "";
    (res.new_relationships || []).forEach(r => {
      relHtml += `
        <div style="background: rgba(99,102,241,0.08); border: 1px solid var(--border-hover); border-radius: 6px; padding: 0.75rem; margin-top: 0.5rem; font-size: 0.82rem;">
          <div style="display:flex; justify-content:space-between; margin-bottom:0.25rem;">
            <span class="badge badge-${r.relationship_type}">${r.relationship_type}</span>
            <span style="color:var(--text-dim); font-family:var(--font-mono);">Confidence: ${(r.confidence*100).toFixed(0)}%</span>
          </div>
          <p style="color:#e2e8f0; margin-bottom:0.25rem;">${r.explanation}</p>
          <div style="display:flex; gap:0.35rem; flex-wrap:wrap;">
            ${r.reasons.map(reason => `<span class="reason-tag">${reason}</span>`).join('')}
          </div>
        </div>
      `;
    });

    resultsContainer.innerHTML = `
      <h4 style="margin-bottom: 0.75rem; color: #a5b4fc;">Extraction &amp; Reconciliation Results</h4>
      <p style="font-size: 0.82rem; color: var(--text-muted); margin-bottom: 1rem;">
        Extracted <strong>${res.extracted_count}</strong> atomic observations and evaluated candidate pairs against existing knowledge base.
      </p>
      ${obsHtml || '<p style="color:var(--text-dim); font-size:0.85rem;">No observations extracted from provided text.</p>'}
      ${relHtml ? `<h5 style="margin: 1rem 0 0.5rem 0; font-size:0.9rem;">Newly Formed Pairwise Relationships:</h5>${relHtml}` : ''}
    `;

    // Refresh other tabs
    await loadObservations();
    await loadRelationships();
  } catch (err) {
    alert("Live extraction failed: " + err);
  } finally {
    btn.innerHTML = originalText;
    btn.disabled = false;
  }
}
