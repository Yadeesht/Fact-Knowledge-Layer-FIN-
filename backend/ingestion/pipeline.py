import os
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Callable
from backend.db.repository import Repository
from backend.ingestion.pdf_parser import PyMuPDFParser
from backend.ingestion.extractor import extract_observations_from_batch
from backend.ingestion.batcher import HierarchicalBatcher
from backend.reconciliation.cascade import reconcile_deterministically
from backend.reconciliation.llm_judge import reconcile_with_llm
from backend.reconciliation.candidate_matcher import CandidateMatcher
from backend.processed_manager import save_to_processed_folder



import time
import threading

# Thread-safe cancellation control
_CANCEL_EVENT = threading.Event()


def request_pipeline_cancellation():
    """Signals any running processing pipeline to abort immediately."""
    _CANCEL_EVENT.set()
    print("[Pipeline] Cancellation requested by user.")


def reset_pipeline_cancellation():
    """Resets the cancellation flag for future pipeline runs."""
    _CANCEL_EVENT.clear()


def is_cancellation_requested() -> bool:
    """Returns True if the current pipeline run has been flagged for cancellation."""
    return _CANCEL_EVENT.is_set()


def sleep_or_cancel(seconds: float) -> bool:
    """
    Waits for specified duration, but returns True immediately if cancellation is requested.
    Returns True if cancelled, False if sleep completed normally.
    """
    return _CANCEL_EVENT.wait(timeout=seconds)


def process_pdf_document(
    pdf_path: Path,
    filename: Optional[str] = None,
    dataset: str = "uploaded",
    extractor_model: Optional[str] = None,
    comparison_mode: str = "cross_document",
    target_doc_ids: Optional[List[str]] = None,
    session_id: Optional[str] = None,
    repo: Optional[Repository] = None,
    progress_callback: Optional[Callable[[int, int, int, int, str], None]] = None,
) -> Dict[str, Any]:
    """
    Synchronous end-to-end PDF processing pipeline:
    1. Compute SHA-256 binary hash to detect if file was already parsed (idempotent cache hit)
    2. If new: extract layout-aware chunks and store in `chunks` table
    3. Extract observations per chunk using hierarchical LLM batches
    4. Validate and normalize into `Observation` + `Evidence` with relational `document_id`
    5. Scoped cross-document candidate pairing & strict deterministic reconciliation
    6. Complete processing run with metrics
    """
    reset_pipeline_cancellation()
    close_repo = False
    if repo is None:
        repo = Repository()
        close_repo = True

    try:
        fname = filename or pdf_path.name
        start_time = datetime.utcnow().isoformat()
        model_name = extractor_model or os.getenv("EXTRACTOR_MODEL", "gemini-1.5-flash")

        # Step 0: SHA-256 Content-Based Idempotency Check
        file_bytes = pdf_path.read_bytes()
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        file_size = len(file_bytes)

        existing_doc = repo.get_document_by_hash(file_hash)
        is_cache_hit = False

        if existing_doc:
            doc_id = existing_doc["id"]
            doc_type = existing_doc.get("document_type", "pdf")
            page_count = existing_doc.get("page_count", 0)
            run_id = f"run_{uuid.uuid4().hex[:8]}"
            is_cache_hit = True

            extracted_observations = repo.list_observations(document_id=doc_id)
            chunks = repo.get_chunks_for_document(doc_id)
            batches = []

            print(
                f"[Pipeline] Cache hit for '{fname}' (SHA256: {file_hash[:10]}...). "
                f"Reusing {len(extracted_observations)} pre-extracted observations and {len(chunks)} chunks from document '{doc_id}'."
            )
            if progress_callback:
                progress_callback(1, 100, page_count, page_count, f"Cache hit: {page_count} pages reused")
                progress_callback(2, 100, len(extracted_observations), len(extracted_observations), f"Loaded {len(extracted_observations)} cached claims")
        else:
            doc_id = f"doc_{uuid.uuid4().hex[:8]}"
            run_id = f"run_{uuid.uuid4().hex[:8]}"

            # Step 1: Detect metadata and register document
            if progress_callback:
                progress_callback(1, 30, 0, 0, "Extracting PDF layout and metadata...")

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
                file_hash=file_hash,
                file_size=file_size,
            )

            repo.log_processing_run(
                run_id=run_id,
                document_id=doc_id,
                status="started",
                extractor_model=model_name,
                metrics={"pdf_pages_parsed": page_count, "chunks_created": 0},
                started_at=start_time,
            )

            if is_cancellation_requested():
                raise InterruptedError("Processing cancelled by user before chunking.")

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

            if progress_callback:
                progress_callback(1, 100, page_count, page_count, f"Parsed {page_count} pages into {len(chunks)} chunks")

            # Step 3 & 4: Hierarchical Batching & Observation Extraction
            batcher = HierarchicalBatcher()
            batches = batcher.build_batches(chunks, document_id=doc_id)
            total_b = max(1, len(batches))
            print(f"[Pipeline] Partitioned {len(chunks)} atomic chunks into {total_b} hierarchical LLM batches.")

            if progress_callback:
                progress_callback(2, 0, 0, total_b, f"Partitioned {len(chunks)} chunks into {total_b} batches")

            extracted_observations = []

            for b_idx, batch in enumerate(batches):
                # Check for mid-state cancellation
                if is_cancellation_requested():
                    raise InterruptedError(f"Processing cancelled by user at batch {b_idx + 1}/{total_b}.")

                sec_info = f" ({batch.section})" if batch.section else ""
                print(f"[Pipeline] [Batch {b_idx + 1}/{total_b}] Processing pages {batch.page_start}–{batch.page_end}{sec_info} ({len(batch.chunks)} chunks, ~{batch.total_chars} chars)...")

                b_num = b_idx + 1
                b_start_pct = round((b_idx / total_b) * 100)
                if progress_callback:
                    progress_callback(
                        2,
                        b_start_pct,
                        b_idx,
                        total_b,
                        f"Extracting batch {b_num}/{total_b} (pp. {batch.page_start}–{batch.page_end})...",
                    )

                try:
                    obs_list = extract_observations_from_batch(batch, document_id=doc_id, repo=repo)
                except InterruptedError:
                    raise
                except Exception as exc:
                    print(f"[Pipeline] [Batch {b_num}/{total_b}] Warning: Batch extraction encountered error: {exc}. Gracefully continuing with remaining batches.")
                    obs_list = []

                for obs in obs_list:
                    repo.save_observation(obs, dataset=dataset, document_id=doc_id, session_id=session_id)
                    extracted_observations.append(obs)

                needs_rev_cnt = sum(1 for o in obs_list if o.needs_review)
                sample_facts = [f"{o.concept.canonical_name}: {o.value.amount} {o.value.unit or ''}".strip() for o in obs_list[:3]]
                sample_str = f" (e.g. {', '.join(sample_facts)})" if sample_facts else ""
                print(f"[Pipeline] [Batch {b_idx + 1}/{total_b}] Extracted {len(obs_list)} observation(s){sample_str} | Running Total: {len(extracted_observations)} facts ({needs_rev_cnt} flagged for review).")

                b_pct = round((b_num / total_b) * 100)
                if progress_callback:
                    progress_callback(
                        2,
                        b_pct,
                        b_num,
                        total_b,
                        f"Batch {b_num}/{total_b} ({b_pct}%): {len(obs_list)} claims (Total: {len(extracted_observations)})",
                    )

                # Rate-limit pause between LLM batch calls
                if b_idx < len(batches) - 1:
                    if sleep_or_cancel(1.5):
                        raise InterruptedError(f"Processing cancelled by user after batch {b_idx + 1}/{total_b}.")

        if is_cancellation_requested():
            raise InterruptedError("Processing cancelled by user before reconciliation.")

        # Step 5: Scoped Candidate pairing & reconciliation
        pairs_to_evaluate: List[Tuple[Any, Any]] = []

        # 1. Intra-document pairing (internal consistency within this filing)
        if comparison_mode in ("intra_document", "combined", "all"):
            for i in range(len(extracted_observations)):
                for j in range(i + 1, len(extracted_observations)):
                    pairs_to_evaluate.append((extracted_observations[i], extracted_observations[j]))

        # 2. Cross-document pairing (against other filings)
        if comparison_mode in ("cross_document", "combined", "all"):
            comparison_pool = repo.get_comparison_candidates(
                current_doc_id=doc_id,
                comparison_mode="cross_document",
                target_doc_ids=target_doc_ids,
            )
            for new_obs in extracted_observations:
                for cand_obs in comparison_pool:
                    pairs_to_evaluate.append((new_obs, cand_obs))

        total_pairs = max(1, len(pairs_to_evaluate))
        print(
            f"[Pipeline] Starting scoped reconciliation ({comparison_mode}): "
            f"evaluating {total_pairs} candidate pairs for {len(extracted_observations)} observations..."
        )
        if progress_callback:
            progress_callback(3, 0, 0, total_pairs, f"Evaluating {total_pairs} candidate pairs ({comparison_mode.replace('_', ' ')})...")

        # Clear previous relationships for this document to avoid duplicates on re-reconciliation
        repo.clear_relationships_for_document(doc_id)

        new_relationships = []
        pairs_evaluated = 0
        step_freq = max(1, min(20, total_pairs // 20))

        for obs_a, obs_b in pairs_to_evaluate:
            if is_cancellation_requested():
                raise InterruptedError("Processing cancelled by user during reconciliation.")

            pairs_evaluated += 1
            # Candidate Matcher Gate (Quarantine, Entity, Dimension, Scope, Time, Concept)
            is_cand, cand_type = CandidateMatcher.is_comparable_candidate(obs_a, obs_b)
            if not is_cand:
                if progress_callback and (pairs_evaluated % step_freq == 0 or pairs_evaluated == total_pairs):
                    p_pct = round((pairs_evaluated / total_pairs) * 100)
                    progress_callback(3, p_pct, pairs_evaluated, total_pairs, f"Reconciled {pairs_evaluated}/{total_pairs} pairs ({len(new_relationships)} relations)")
                continue

            # Deterministic Cascade (returns None on non-comparable)
            rel = reconcile_deterministically(obs_a, obs_b, candidate_type=cand_type)
            if rel:
                repo.save_relationship(rel, session_id=session_id)
                new_relationships.append(rel)

            if progress_callback and (pairs_evaluated % step_freq == 0 or pairs_evaluated == total_pairs):
                p_pct = round((pairs_evaluated / total_pairs) * 100)
                progress_callback(3, p_pct, pairs_evaluated, total_pairs, f"Reconciled {pairs_evaluated}/{total_pairs} pairs ({len(new_relationships)} relations)")

        rel_breakdown = {}
        for r in new_relationships:
            k = r.relationship_type.value
            rel_breakdown[k] = rel_breakdown.get(k, 0) + 1
        breakdown_str = ", ".join(f"{cnt} {k}" for k, cnt in rel_breakdown.items()) or "none"
        print(f"[Pipeline] Reconciliation completed ({comparison_mode}): evaluated {pairs_evaluated} pairs -> generated {len(new_relationships)} relationship(s) ({breakdown_str}).")

        # Step 6: Complete run and save processed JSON artifact to processed/ folder
        completed_time = datetime.now(timezone.utc).isoformat()
        if progress_callback:
            progress_callback(4, 100, 1, 1, f"Saved processed/{doc_id}.json")
        metrics = {
            "pdf_pages_parsed": page_count,
            "chunks_created": len(chunks),
            "batches_processed": len(batches) if 'batches' in locals() else 0,
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
            "file_hash": file_hash,
            "file_size": file_size,
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
            "file_hash": file_hash,
            "cache_hit": is_cache_hit,
            "chunks_stored": len(chunks),
            "batches_processed": len(batches) if 'batches' in locals() else 0,
            "observations_extracted": len(extracted_observations),
            "relationships_generated": len(new_relationships),
            "processed_file": f"processed/{doc_id}.json",
        }


    except InterruptedError as e:
        print(f"[Pipeline] Pipeline interrupted: {e}")
        if 'run_id' in locals() and 'doc_id' in locals():
            try:
                repo.rollback_document(doc_id)
                print(f"[Pipeline] Rolled back partial records for document {doc_id}")
            except Exception as rb_err:
                print(f"[Pipeline] Rollback warning: {rb_err}")

            repo.log_processing_run(
                run_id=run_id,
                document_id=doc_id,
                status="cancelled",
                extractor_model=model_name if 'model_name' in locals() else (extractor_model or "gemini-1.5-flash"),
                metrics={},
                started_at=start_time if 'start_time' in locals() else datetime.utcnow().isoformat(),
                completed_at=datetime.utcnow().isoformat(),
                error=str(e),
            )
        raise e
    except Exception as e:
        if 'run_id' in locals() and 'doc_id' in locals():
            repo.log_processing_run(
                run_id=run_id,
                document_id=doc_id,
                status="failed",
                extractor_model=model_name if 'model_name' in locals() else (extractor_model or "gemini-1.5-flash"),
                metrics={},
                started_at=start_time if 'start_time' in locals() else datetime.utcnow().isoformat(),
                completed_at=datetime.utcnow().isoformat(),
                error=str(e),
            )
        raise e
    finally:
        if close_repo:
            repo.close()


def reconcile_existing_document(
    doc_id: str,
    comparison_mode: str = "combined",
    target_doc_ids: Optional[List[str]] = None,
    session_id: Optional[str] = None,
    repo: Optional[Repository] = None,
    progress_callback: Optional[Callable[[int, int, int, int, str], None]] = None,
) -> Dict[str, Any]:
    """
    Reconciles an already processed document stored in the database with a specified mode.
    Takes existing observations and re-runs Step 5, updating SQLite and processed/{doc_id}.json.
    """
    close_repo = False
    if repo is None:
        repo = Repository()
        close_repo = True

    try:
        # Load observations for this document
        doc_obs = repo.list_observations(document_id=doc_id)
        if not doc_obs:
            raise ValueError(f"No observations found in database for document {doc_id}.")

        if progress_callback:
            progress_callback(1, 100, 1, 1, f"Retrieved {len(doc_obs)} claims from database")
            progress_callback(2, 100, len(doc_obs), len(doc_obs), "Validated units and temporal scope")

        pairs_to_evaluate: List[Tuple[Any, Any]] = []

        # 1. Intra-document pairing
        if comparison_mode in ("intra_document", "combined", "all"):
            for i in range(len(doc_obs)):
                for j in range(i + 1, len(doc_obs)):
                    pairs_to_evaluate.append((doc_obs[i], doc_obs[j]))

        # 2. Cross-document pairing
        if comparison_mode in ("cross_document", "combined", "all"):
            comparison_pool = repo.get_comparison_candidates(
                current_doc_id=doc_id,
                comparison_mode="cross_document",
                target_doc_ids=target_doc_ids,
            )
            for new_obs in doc_obs:
                for cand_obs in comparison_pool:
                    pairs_to_evaluate.append((new_obs, cand_obs))

        total_pairs = max(1, len(pairs_to_evaluate))
        if progress_callback:
            progress_callback(3, 0, 0, total_pairs, f"Evaluating {total_pairs} candidate pairs ({comparison_mode.replace('_', ' ')})...")

        # Clear previous relationships for this document
        repo.clear_relationships_for_document(doc_id)

        new_relationships = []
        pairs_evaluated = 0
        step_freq = max(1, min(20, total_pairs // 20))

        for obs_a, obs_b in pairs_to_evaluate:
            pairs_evaluated += 1
            is_cand, cand_type = CandidateMatcher.is_comparable_candidate(obs_a, obs_b)
            if not is_cand:
                if progress_callback and (pairs_evaluated % step_freq == 0 or pairs_evaluated == total_pairs):
                    p_pct = round((pairs_evaluated / total_pairs) * 100)
                    progress_callback(3, p_pct, pairs_evaluated, total_pairs, f"Reconciled {pairs_evaluated}/{total_pairs} pairs ({len(new_relationships)} relations)")
                continue

            rel = reconcile_deterministically(obs_a, obs_b, candidate_type=cand_type)
            if rel:
                repo.save_relationship(rel, session_id=session_id)
                new_relationships.append(rel)

            if progress_callback and (pairs_evaluated % step_freq == 0 or pairs_evaluated == total_pairs):
                p_pct = round((pairs_evaluated / total_pairs) * 100)
                progress_callback(3, p_pct, pairs_evaluated, total_pairs, f"Reconciled {pairs_evaluated}/{total_pairs} pairs ({len(new_relationships)} relations)")

        rel_breakdown = {}
        for r in new_relationships:
            k = r.relationship_type.value
            rel_breakdown[k] = rel_breakdown.get(k, 0) + 1
        breakdown_str = ", ".join(f"{cnt} {k}" for k, cnt in rel_breakdown.items()) or "none"
        print(f"[Pipeline] Re-reconciliation completed for {doc_id} ({comparison_mode}): evaluated {pairs_evaluated} pairs -> generated {len(new_relationships)} relationship(s) ({breakdown_str}).")

        # Update the JSON artifact in processed/ folder
        from backend.processed_manager import PROCESSED_DIR
        file_path = PROCESSED_DIR / f"{doc_id}.json"
        if file_path.exists():
            import json
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["relationships"] = [r.model_dump() for r in new_relationships]
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)

        if progress_callback:
            progress_callback(4, 100, 1, 1, f"Updated processed/{doc_id}.json")

        return {
            "status": "success",
            "document_id": doc_id,
            "comparison_mode": comparison_mode,
            "pairs_evaluated": pairs_evaluated,
            "relationships_generated": len(new_relationships),
            "breakdown": rel_breakdown,
        }
    finally:
        if close_repo:
            repo.close()
