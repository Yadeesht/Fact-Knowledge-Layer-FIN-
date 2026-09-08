import sqlite3
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = ROOT_DIR / os.getenv("DATABASE_PATH", "backend/data/knowledge.db")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    dataset TEXT NOT NULL,
    document_type TEXT,
    page_count INTEGER,
    file_hash TEXT UNIQUE,
    file_size INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    section TEXT,
    chunk_type TEXT,
    text TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE,
    entity_type TEXT,
    aliases_json TEXT,
    embedding_json TEXT
);

CREATE TABLE IF NOT EXISTS concepts (
    id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE,
    description TEXT,
    aliases_json TEXT,
    embedding_json TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    document_id TEXT,
    session_id TEXT,
    entity_id TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    value_type TEXT NOT NULL,
    value REAL,
    unit TEXT,
    normalized_value REAL,
    normalized_unit TEXT,
    text_value TEXT,
    period_type TEXT,
    period_start TEXT,
    period_end TEXT,
    period_label TEXT,
    geography TEXT,
    scope_level TEXT,
    consolidation TEXT,
    assertion_status TEXT,
    confidence REAL,
    needs_review INTEGER DEFAULT 0,
    review_reason TEXT,
    FOREIGN KEY(document_id) REFERENCES documents(id),
    FOREIGN KEY(entity_id) REFERENCES entities(id),
    FOREIGN KEY(concept_id) REFERENCES concepts(id)
);

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    chunk_id TEXT,
    page_number INTEGER NOT NULL,
    section TEXT,
    quote TEXT NOT NULL,
    locator_json TEXT,
    FOREIGN KEY(observation_id) REFERENCES observations(id),
    FOREIGN KEY(document_id) REFERENCES documents(id),
    FOREIGN KEY(chunk_id) REFERENCES chunks(id)
);

CREATE TABLE IF NOT EXISTS relationships (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    observation_a TEXT NOT NULL,
    observation_b TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    explanation TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    comparability_json TEXT,
    numeric_json TEXT,
    FOREIGN KEY(observation_a) REFERENCES observations(id),
    FOREIGN KEY(observation_b) REFERENCES observations(id)
);

CREATE TABLE IF NOT EXISTS analysis_sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    document_ids_json TEXT NOT NULL,
    comparison_mode TEXT NOT NULL,
    baseline_doc_ids_json TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processing_runs (
    id TEXT PRIMARY KEY,
    document_id TEXT,
    status TEXT NOT NULL,
    extractor_model TEXT,
    metrics_json TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT
);
"""


def get_db_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    conn = get_db_connection(db_path)
    with conn:
        conn.executescript(SCHEMA_SQL)

        # Migrations for existing DB instances if columns are missing
        cursor = conn.cursor()

        # Documents file_hash and file_size
        cursor.execute("PRAGMA table_info(documents)")
        columns = [row["name"] for row in cursor.fetchall()]
        if "file_hash" not in columns:
            cursor.execute("ALTER TABLE documents ADD COLUMN file_hash TEXT")
        if "file_size" not in columns:
            cursor.execute("ALTER TABLE documents ADD COLUMN file_size INTEGER")

        # Chunks chunk_type
        cursor.execute("PRAGMA table_info(chunks)")
        columns = [row["name"] for row in cursor.fetchall()]
        if "chunk_type" not in columns:
            cursor.execute("ALTER TABLE chunks ADD COLUMN chunk_type TEXT")

        # Entities aliases_json and embedding_json
        cursor.execute("PRAGMA table_info(entities)")
        columns = [row["name"] for row in cursor.fetchall()]
        if "aliases_json" not in columns:
            cursor.execute("ALTER TABLE entities ADD COLUMN aliases_json TEXT")
        if "embedding_json" not in columns:
            cursor.execute("ALTER TABLE entities ADD COLUMN embedding_json TEXT")

        # Concepts aliases_json and embedding_json
        cursor.execute("PRAGMA table_info(concepts)")
        columns = [row["name"] for row in cursor.fetchall()]
        if "aliases_json" not in columns:
            cursor.execute("ALTER TABLE concepts ADD COLUMN aliases_json TEXT")
        if "embedding_json" not in columns:
            cursor.execute("ALTER TABLE concepts ADD COLUMN embedding_json TEXT")

        # Observations document_id and session_id
        cursor.execute("PRAGMA table_info(observations)")
        columns = [row["name"] for row in cursor.fetchall()]
        if "document_id" not in columns:
            cursor.execute("ALTER TABLE observations ADD COLUMN document_id TEXT")
        if "session_id" not in columns:
            cursor.execute("ALTER TABLE observations ADD COLUMN session_id TEXT")

        # Relationships session_id, comparability_json and numeric_json
        cursor.execute("PRAGMA table_info(relationships)")
        columns = [row["name"] for row in cursor.fetchall()]
        if "session_id" not in columns:
            cursor.execute("ALTER TABLE relationships ADD COLUMN session_id TEXT")
        if "comparability_json" not in columns:
            cursor.execute("ALTER TABLE relationships ADD COLUMN comparability_json TEXT")
        if "numeric_json" not in columns:
            cursor.execute("ALTER TABLE relationships ADD COLUMN numeric_json TEXT")

    conn.close()
