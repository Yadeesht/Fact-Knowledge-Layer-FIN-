import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from backend.db.repository import Repository
from backend.ingestion.pdf_parser import PyMuPDFParser
from backend.ingestion.extractor import extract_observations_from_text
from backend.reconciliation.cascade import reconcile_deterministically
from backend.reconciliation.llm_judge import reconcile_with_llm
from backend.reconciliation.candidate_matcher import CandidateMatcher
from backend.processed_manager import save_to_processed_folder



def process_pdf_document(
    pdf_path: Path,
    filename: Optional[str] = None,
    dataset: str = "uploaded",
    extractor_model: Optional[str] = None,
    repo: Optional[Repository] = None,
) -> Dict[str, Any]:
    """
    Synchronous end-to-end PDF processing pipeline (§5):
    1. Register document and start processing run
    2. Extract layout-aware chunks and store in `chunks` table

    3. Extract observations per chunk
    4. Validate and normalize into `Observation` + `Evidence`
    5. Bucket and reconcile candidate pairs
    6. Complete processing run with metrics
    """
    close_repo = False
    if repo is None:
        repo = Repository()
        close_repo = True

    try:
        fname = filename or pdf_path.name
        doc_id = f"doc_{uuid.uuid4().hex[:8]}"
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        start_time = datetime.utcnow().isoformat()
        model_name = extractor_model or os.getenv("EXTRACTOR_MODEL", "gemini-1.5-flash")

        # Step 1: Detect metadata and register document using PyMuPDF (fitz)
        parser = PyMuPDFParser()
        meta = parser.detect_document_metadata(pdf_path)
        doc_type = meta.get("document_type", "pdf")
        page_count = meta.get("page_count", 0)

        repo.save_document(
            doc_id=doc_id,
            filename=fname,
            dataset=dataset,
            document_type=doc_type,
            page_count=page_count,
        )

        repo.log_processing_run(
            run_id=run_id,
            document_id=doc_id,
            status="started",
            extractor_model=model_name,
            metrics={"pdf_pages_parsed": page_count, "chunks_created": 0},
            started_at=start_time,
        )


        # Step 2: Extract and persist chunks
        chunks = parser.chunk_document(pdf_path, document_id=doc_id)
        for chk in chunks:
            repo.save_chunk(
                chunk_id=chk["id"],
                document_id=doc_id,
                page_number=chk["page_number"],
                section=chk.get("section"),
                chunk_type=chk.get("content_type", "text"),
                text=chk["text"],
            )

        # Step 3 & 4: Extract and validate observations per chunk
        # Process first 10 most informative chunks for synchronous speed
        extracted_observations = []
        chunks_to_process = chunks  #while testing process less number to avoid wasting tokens

        for chk in chunks_to_process:
            obs_list = extract_observations_from_text(
                text=chk["text"],
                page_number=chk["page_number"],
                document_id=doc_id,
                chunk_id=chk["id"],
            )
            for obs in obs_list:
                repo.save_observation(obs, dataset=dataset)
                extracted_observations.append(obs)

        # Step 5: Candidate pairing & reconciliation
        all_obs = repo.list_observations()
        new_relationships = []

        for new_obs in extracted_observations:
            for existing_obs in all_obs:
                if new_obs.id != existing_obs.id:
                    # Candidate Matcher Gate (Quarantine & Entity/Concept compatibility)
                    is_cand, _ = CandidateMatcher.is_comparable_candidate(new_obs, existing_obs)
                    if not is_cand:
                        continue

                    # Deterministic Cascade
                    rel = reconcile_deterministically(new_obs, existing_obs)
                    if rel is None:
                        rel = reconcile_with_llm(new_obs, existing_obs)

                    if rel:
                        repo.save_relationship(rel)
                        new_relationships.append(rel)

        # Step 6: Complete run and save processed JSON artifact to processed/ folder
        completed_time = datetime.utcnow().isoformat()
        metrics = {
            "pdf_pages_parsed": page_count,
            "chunks_created": len(chunks),
            "observations_extracted": len(extracted_observations),
            "observations_normalized": len(extracted_observations),
            "needs_review": sum(1 for o in extracted_observations if o.needs_review),
            "relationships_generated": len(new_relationships),
        }

        doc_meta = {
            "id": doc_id,
            "filename": fname,
            "dataset": dataset,
            "document_type": doc_type,
            "page_count": page_count,
            "created_at": start_time,
        }
        
        save_to_processed_folder(
            doc_id=doc_id,
            doc_meta=doc_meta,
            chunks=chunks,
            observations=extracted_observations,
            relationships=new_relationships,
        )

        repo.log_processing_run(
            run_id=run_id,
            document_id=doc_id,
            status="completed",
            extractor_model=model_name,
            metrics=metrics,
            started_at=start_time,
            completed_at=completed_time,
        )


        return {
            "status": "success",
            "document_id": doc_id,
            "run_id": run_id,
            "filename": fname,
            "document_type": doc_type,
            "page_count": page_count,
            "chunks_stored": len(chunks),
            "observations_extracted": len(extracted_observations),
            "relationships_generated": len(new_relationships),
            "processed_file": f"processed/{doc_id}.json",
        }


    except Exception as e:
        if 'run_id' in locals() and 'doc_id' in locals():
            repo.log_processing_run(
                run_id=run_id,
                document_id=doc_id,
                status="failed",
                extractor_model=extractor_model,
                metrics={},
                started_at=start_time,
                completed_at=datetime.utcnow().isoformat(),
                error=str(e),
            )
        raise e
    finally:
        if close_repo:
            repo.close()
