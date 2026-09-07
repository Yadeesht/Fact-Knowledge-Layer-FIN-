import json
import os
from pathlib import Path
from typing import Dict, Any, List
from backend.models.schema import Observation, Relationship
from backend.db.repository import Repository

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "processed"


def ensure_processed_dir() -> Path:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    return PROCESSED_DIR


def save_to_processed_folder(
    doc_id: str,
    doc_meta: Dict[str, Any],
    chunks: List[Dict[str, Any]],
    observations: List[Observation],
    relationships: List[Relationship],
) -> Path:
    ensure_processed_dir()
    file_path = PROCESSED_DIR / f"{doc_id}.json"

    payload = {
        "document": doc_meta,
        "chunks": chunks,
        "observations": [o.dict() for o in observations],
        "relationships": [r.dict() for r in relationships],
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    return file_path


def load_all_processed_into_db(repo: Repository) -> int:
    ensure_processed_dir()
    json_files = list(PROCESSED_DIR.glob("*.json"))
    
    # Always reset database state to mirror the processed/ folder
    repo.clear_all_data()

    if not json_files:
        return 0

    loaded_count = 0
    for file_path in json_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            doc = data.get("document", {})
            if doc and "id" in doc:
                repo.save_document(
                    doc_id=doc["id"],
                    filename=doc.get("filename", file_path.name),
                    dataset=doc.get("dataset", "processed"),
                    document_type=doc.get("document_type", "pdf"),
                    page_count=doc.get("page_count", 0),
                )

            for chk in data.get("chunks", []):
                repo.save_chunk(
                    chunk_id=chk["id"],
                    document_id=chk["document_id"],
                    page_number=chk["page_number"],
                    section=chk.get("section"),
                    chunk_type=chk.get("chunk_type", "text"),
                    text=chk["text"],
                )

            for obs_dict in data.get("observations", []):
                obs = Observation(**obs_dict)
                repo.save_observation(obs, dataset=doc.get("dataset", "processed"))

            for rel_dict in data.get("relationships", []):
                rel = Relationship(**rel_dict)
                repo.save_relationship(rel)

            loaded_count += 1
        except Exception as e:
            print(f"Error loading processed JSON {file_path.name}: {e}")

    return loaded_count
