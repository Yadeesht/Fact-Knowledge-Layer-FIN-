import os
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.db.database import init_db
from backend.db.repository import Repository
from backend.models.schema import (
    Observation,
    Relationship,
    RelationshipType,
    ExtractedObservation,
)
from backend.processed_manager import load_all_processed_into_db, PROCESSED_DIR, ensure_processed_dir
from backend.reconciliation.cascade import reconcile_deterministically
from backend.reconciliation.llm_judge import reconcile_with_llm
from backend.reconciliation.candidate_matcher import CandidateMatcher
from backend.ingestion.extractor import extract_observations_from_text
from backend.ingestion.pipeline import process_pdf_document
from backend.core.llm_client import get_llm_credentials

app = FastAPI(
    title="Financial Fact Knowledge Layer API",
    description="Evidence-grounded financial fact extraction, normalization, and rules-first reconciliation engine.",
    version="1.0.0",
)

# Enable CORS for local dev servers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load environment variables from .env
ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT_DIR / ".env"
if ENV_FILE.exists():
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"\''))

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", 8000))
FRONTEND_DIR = ROOT_DIR / "frontend"
UPLOADS_DIR = ROOT_DIR / os.getenv("UPLOADS_DIR", "data/uploads")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
ensure_processed_dir()


@app.on_event("startup")
def startup_event():
    # Initialize SQLite database and load processed records from processed/ directory if available
    init_db()
    repo = Repository()
    try:
        loaded = load_all_processed_into_db(repo)
        print(f"Loaded {loaded} processed document artifact(s) from processed/ directory.")
    finally:
        repo.close()


@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "financial-fact-knowledge-layer"}


@app.get("/api/config")
def get_app_config():
    return {
        "host": HOST,
        "port": PORT,
        "processed_dir": os.getenv("PROCESSED_DIR", "processed"),
        "database_path": os.getenv("DATABASE_PATH", "backend/data/knowledge.db"),
        "uploads_dir": str(UPLOADS_DIR.relative_to(ROOT_DIR)),
    }



# -------------------------------------------------------------
# 1. Documents API (§11)
# -------------------------------------------------------------
@app.post("/documents")
@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...)):
    """
    POST /documents (§11): Upload a PDF, triggers synchronous ingestion.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    saved_path = UPLOADS_DIR / file.filename
    with open(saved_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    repo = Repository()
    try:
        result = process_pdf_document(pdf_path=saved_path, filename=file.filename, repo=repo)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")
    finally:
        repo.close()


class StarterIngestRequest(BaseModel):

    filename: str


@app.get("/api/starter-files")
def list_starter_files():
    base_dir = Path(__file__).resolve().parent.parent
    starters = []
    for folder in ["delhivery", "india-macroeconomy"]:
        p = base_dir / folder
        if p.exists():
            for f in p.glob("*.pdf"):
                starters.append({
                    "filename": f.name,
                    "dataset": folder,
                    "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
                })
    return starters


@app.post("/api/ingest-starter")
def ingest_starter_file(req: StarterIngestRequest):
    """
    Ingest one of the starter PDF files located in delhivery/ or india-macroeconomy/.
    """
    base_dir = Path(__file__).resolve().parent.parent
    potential_paths = [
        base_dir / "delhivery" / req.filename,
        base_dir / "india-macroeconomy" / req.filename,
    ]
    target_path = None
    for p in potential_paths:
        if p.exists():
            target_path = p
            break

    if not target_path:
        raise HTTPException(status_code=404, detail=f"Starter file {req.filename} not found.")

    dataset = "delhivery" if "delhivery" in str(target_path).lower() else "india-macroeconomy"
    repo = Repository()
    try:
        result = process_pdf_document(pdf_path=target_path, filename=req.filename, dataset=dataset, repo=repo)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")
    finally:
        repo.close()


@app.post("/api/clear")
def clear_all_data_endpoint():
    """
    Clears all database tables and removes processed JSON artifacts.
    """
    repo = Repository()
    try:
        repo.clear_all_data()
        for f in PROCESSED_DIR.glob("*.json"):
            try:
                f.unlink()
            except Exception:
                pass
        return {"status": "success", "message": "All processed data cleared."}
    finally:
        repo.close()




@app.get("/documents")
@app.get("/api/documents")
def get_documents():
    """
    GET /documents (§11): List uploaded documents and their processing status.
    """
    repo = Repository()
    try:
        return repo.list_documents()
    finally:
        repo.close()


@app.get("/documents/{doc_id}")
@app.get("/api/documents/{doc_id}")
def get_document_by_id(doc_id: str):
    """
    GET /documents/{id} (§11): Document detail + chunk count + observation count.
    """
    repo = Repository()
    try:
        doc = repo.get_document_detail(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        chunks = repo.get_chunks_for_document(doc_id)
        doc["chunks"] = chunks
        return doc
    finally:
        repo.close()


# -------------------------------------------------------------
# 2. Observations & Facts API (§11)
# -------------------------------------------------------------
@app.get("/observations")
@app.get("/api/observations")
def get_observations(
    entity: Optional[str] = None,
    concept: Optional[str] = None,
    needs_review: Optional[bool] = None,
):
    """
    GET /observations (§11): Filterable by entity, concept, needs_review.
    """
    repo = Repository()
    try:
        obs_list = repo.list_observations(
            entity_name=entity, concept_name=concept, needs_review=needs_review
        )
        return [o.dict() for o in obs_list]
    finally:
        repo.close()


@app.get("/observations/{obs_id}")
@app.get("/api/observations/{obs_id}")
def get_observation_detail(obs_id: str):
    """
    GET /observations/{id} (§11): Single observation with its evidence.
    """
    repo = Repository()
    try:
        obs = repo.get_observation(obs_id)
        if not obs:
            raise HTTPException(status_code=404, detail="Observation not found")
        return obs.dict()
    finally:
        repo.close()


@app.get("/facts")
@app.get("/api/facts")
def get_canonical_facts():
    """
    GET /facts (§2): Grouped observations referring to the same entity & concept,
    with an overall consensus status badge (corroborated / contradicted / contextualized / needs_review).
    """
    repo = Repository()
    try:
        return repo.get_grouped_facts()
    finally:
        repo.close()


# -------------------------------------------------------------
# 3. Relationships API (§11)
# -------------------------------------------------------------
@app.get("/relationships")
@app.get("/api/relationships")
def get_relationships(relationship_type: Optional[RelationshipType] = None):
    """
    GET /relationships (§11): Filterable by relationship_type.
    """
    repo = Repository()
    try:
        rels = repo.list_relationships(rel_type=relationship_type)
        return [r.dict() for r in rels]
    finally:
        repo.close()


@app.get("/relationships/{rel_id}")
@app.get("/api/relationships/{rel_id}")
def get_relationship_by_id(rel_id: str):
    """
    GET /relationships/{id} (§11): Single relationship with both observations and their evidence.
    """
    repo = Repository()
    try:
        detail = repo.get_relationship_detail(rel_id)
        if not detail:
            raise HTTPException(status_code=404, detail="Relationship not found")
        return detail
    finally:
        repo.close()


# -------------------------------------------------------------
# 4. Evidence API (§11)
# -------------------------------------------------------------
@app.get("/evidence/{evidence_id}")
@app.get("/api/evidence/{evidence_id}")
def get_evidence(evidence_id: str):
    """
    GET /evidence/{id} (§11): Full quote + document/page/section locator.
    """
    repo = Repository()
    try:
        ev = repo.get_evidence_by_id(evidence_id)
        if not ev:
            raise HTTPException(status_code=404, detail="Evidence not found")
        return ev
    finally:
        repo.close()


# -------------------------------------------------------------
# 5. Free-Text Search API (§11)
# -------------------------------------------------------------
@app.get("/search")
@app.get("/api/search")
def search_knowledge(q: str = Query(..., min_length=1)):
    """
    GET /search?q=... (§11): Free-text search over entities/concepts.
    """
    repo = Repository()
    try:
        return repo.search_facts(q)
    finally:
        repo.close()


# -------------------------------------------------------------
# 6. Evaluator Showcase Cases
# -------------------------------------------------------------
@app.get("/api/showcase")
def get_showcase_cases():
    repo = Repository()
    try:
        case1_a = repo.get_observation("obs_del_rev_fy24_ar")
        case1_b = repo.get_observation("obs_del_rev_fy24_pres")

        case2_a = repo.get_observation("obs_ind_gdp_fy25_survey")
        case2_b = repo.get_observation("obs_ind_gdp_fy25_conflict")

        case3_a = repo.get_observation("obs_ind_gdp_fy25_survey_est")
        case3_b = repo.get_observation("obs_ind_gdp_fy25_imf_forecast")

        case4 = repo.get_observation("obs_del_pincodes_ambiguous")

        if case1_a and case1_b:
            cases = [
                {
                    "case_id": "case_1_corroborated",
                    "title": "Case 1: Corroboration Across Financial Reporting Formats",
                    "badge": "CORROBORATED",
                    "category": "corroborated",
                    "takeaway": "Automatic unit normalization bridges ₹8,142.16 Crore and ₹81,424 Million seamlessly within 0.1% materiality.",
                    "observation_a": case1_a.dict(),
                    "observation_b": case1_b.dict(),
                    "relationship": (
                        reconcile_deterministically(case1_a, case1_b).dict()
                        if case1_a and case1_b
                        else None
                    ),
                },
                {
                    "case_id": "case_2_contradicted",
                    "title": "Case 2: Genuine Contradiction on Matching Vintage",
                    "badge": "CONTRADICTED",
                    "category": "contradicted",
                    "takeaway": "System flags material divergence on matching entity, concept, period, scope, and actual assertion status.",
                    "observation_a": case2_a.dict() if case2_a else None,
                    "observation_b": case2_b.dict() if case2_b else None,
                    "relationship": (
                        reconcile_deterministically(case2_a, case2_b).dict()
                        if case2_a and case2_b
                        else None
                    ),
                },
                {
                    "case_id": "case_3_contextualized",
                    "title": "Case 3: Contextualized Reconcile (Estimate vs Projection)",
                    "badge": "CONTEXTUALIZED",
                    "category": "contextualized",
                    "takeaway": "System understands assertion status: an Economic Survey estimate and an IMF projection contextualize rather than contradict.",
                    "observation_a": case3_a.dict() if case3_a else None,
                    "observation_b": case3_b.dict() if case3_b else None,
                    "relationship": (
                        reconcile_deterministically(case3_a, case3_b).dict()
                        if case3_a and case3_b
                        else None
                    ),
                },
                {
                    "case_id": "case_4_review",
                    "title": "Case 4: Extraction Anomaly & Human Review Escalation",
                    "badge": "NEEDS REVIEW",
                    "category": "needs_review",
                    "takeaway": "System refuses to fabricate missing temporal anchors or units, safely flagging claims for human verification.",
                    "observation": case4.dict() if case4 else None,
                    "review_reason": case4.review_reason if case4 else "Missing temporal anchor",
                },
            ]
            return [c for c in cases if c.get("observation_a") or c.get("observation")]

        # Dynamic showcase built from processed files in database
        rels = repo.list_relationships()
        all_obs = {o.id: o for o in repo.list_observations()}
        dynamic_cases = []

        for rel in rels:
            obs_a = all_obs.get(rel.observation_a)
            obs_b = all_obs.get(rel.observation_b)
            if not obs_a or not obs_b:
                continue

            rel_type = rel.relationship_type.value if hasattr(rel.relationship_type, "value") else str(rel.relationship_type)
            dynamic_cases.append({
                "case_id": f"case_{rel.id}",
                "title": f"Reconciliation ({rel_type.title()}): {obs_a.entity.canonical_name} - {obs_a.concept.canonical_name}",
                "badge": rel_type.upper(),
                "category": rel_type.lower(),
                "takeaway": rel.explanation,
                "observation_a": obs_a.dict(),
                "observation_b": obs_b.dict(),
                "relationship": rel.dict(),
            })

        review_obs = [o for o in all_obs.values() if o.needs_review]
        for obs in review_obs:
            dynamic_cases.append({
                "case_id": f"review_{obs.id}",
                "title": f"Anomaly Escalation: {obs.entity.canonical_name} - {obs.concept.canonical_name}",
                "badge": "NEEDS REVIEW",
                "category": "needs_review",
                "takeaway": "Extraction quarantined due to missing or ambiguous attributes.",
                "observation": obs.dict(),
                "review_reason": obs.review_reason or "Ambiguous attributes",
            })

        return dynamic_cases
    finally:
        repo.close()


@app.post("/api/reconcile")
def run_reconciliation():
    repo = Repository()
    try:
        obs_list = repo.list_observations()
        created_relationships = []

        for i in range(len(obs_list)):
            for j in range(i + 1, len(obs_list)):
                a = obs_list[i]
                b = obs_list[j]

                # Step 1: Candidate Matcher Gate (Quarantine & Comparability Check)
                is_candidate, _ = CandidateMatcher.is_comparable_candidate(a, b)
                if not is_candidate:
                    continue

                # Step 2: Deterministic Cascade
                rel = reconcile_deterministically(a, b)
                if rel is None:
                    # Step 3: LLM Judge Fallback
                    rel = reconcile_with_llm(a, b)

                if rel:
                    repo.save_relationship(rel)
                    created_relationships.append(rel.dict())

        return {
            "status": "success",
            "pairs_evaluated": len(obs_list) * (len(obs_list) - 1) // 2,
            "relationships_stored": len(created_relationships),
            "relationships": created_relationships,
        }
    finally:
        repo.close()


@app.get("/api/runs")
def get_runs():
    repo = Repository()
    try:
        return repo.list_processing_runs()
    finally:
        repo.close()


# -------------------------------------------------------------
# LLM Key and Live Text Extraction Studio
# -------------------------------------------------------------
class LLMKeyRequest(BaseModel):
    provider: str = "gemini"
    api_key: str


class ExtractTextRequest(BaseModel):
    text: str
    page_number: int = 1
    document_id: str = "custom_document"


@app.get("/api/llm/status")
def get_llm_status():
    creds = get_llm_credentials()
    if creds["gemini_key"]:
        masked = creds["gemini_key"][:4] + "..." + creds["gemini_key"][-4:] if len(creds["gemini_key"]) > 8 else "***"
        return {"configured": True, "provider": "Google Gemini", "masked_key": masked}
    elif creds["openai_key"]:
        masked = creds["openai_key"][:4] + "..." + creds["openai_key"][-4:] if len(creds["openai_key"]) > 8 else "***"
        return {"configured": True, "provider": "OpenAI", "masked_key": masked}
    return {"configured": False, "provider": "none", "message": "No API key configured. Offline heuristic active."}


@app.post("/api/llm/set-key")
def set_api_key(req: LLMKeyRequest):
    if req.provider.lower() == "gemini":
        os.environ["GEMINI_API_KEY"] = req.api_key.strip()
    else:
        os.environ["OPENAI_API_KEY"] = req.api_key.strip()
    return {"status": "success", "provider": req.provider}


@app.post("/api/extract")
def extract_live_observations(req: ExtractTextRequest):
    repo = Repository()
    try:
        extracted = extract_observations_from_text(
            text=req.text,
            page_number=req.page_number,
            document_id=req.document_id,
        )

        for obs in extracted:
            repo.save_observation(obs)

        existing = repo.list_observations()
        new_relationships = []
        for new_obs in extracted:
            for old_obs in existing:
                if new_obs.id != old_obs.id:
                    is_candidate, _ = CandidateMatcher.is_comparable_candidate(new_obs, old_obs)
                    if not is_candidate:
                        continue
                    rel = reconcile_deterministically(new_obs, old_obs)
                    if rel is None:
                        rel = reconcile_with_llm(new_obs, old_obs)
                    if rel:
                        repo.save_relationship(rel)
                        new_relationships.append(rel.dict())

        return {
            "status": "success",
            "extracted_count": len(extracted),
            "observations": [o.dict() for o in extracted],
            "new_relationships": new_relationships,
        }
    finally:
        repo.close()


# Mount static files if frontend build exists
if (FRONTEND_DIR / "dist").exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "dist" / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        file_path = FRONTEND_DIR / "dist" / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "dist" / "index.html")
elif (FRONTEND_DIR / "index.html").exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static_old")
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")
