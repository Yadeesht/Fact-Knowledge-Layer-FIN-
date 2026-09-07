import json
import sqlite3
from typing import List, Optional, Dict, Any
from datetime import datetime
from backend.db.database import get_db_connection
from backend.models.schema import (
    Observation,
    Relationship,
    Evidence,
    Entity,
    Concept,
    FactValue,
    TimeContext,
    Scope,
    ValueType,
    PeriodType,
    AssertionStatus,
    RelationshipType,
)


class Repository:
    def __init__(self, db_conn: Optional[sqlite3.Connection] = None):
        self.conn = db_conn or get_db_connection()

    def close(self):
        self.conn.close()

    def clear_all_data(self):
        with self.conn:
            self.conn.execute("DELETE FROM relationships")
            self.conn.execute("DELETE FROM evidence")
            self.conn.execute("DELETE FROM observations")
            self.conn.execute("DELETE FROM concepts")
            self.conn.execute("DELETE FROM entities")
            self.conn.execute("DELETE FROM chunks")
            self.conn.execute("DELETE FROM processing_runs")
            self.conn.execute("DELETE FROM documents")


    # -----------------------------
    # Document operations
    # -----------------------------
    def save_document(
        self,
        doc_id: str,
        filename: str,
        dataset: str = "custom",
        document_type: str = "pdf",
        page_count: int = 0,
    ):
        now = datetime.utcnow().isoformat()
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO documents (id, filename, dataset, document_type, page_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (doc_id, filename, dataset, document_type, page_count, now),
            )

    def rollback_document(self, doc_id: str):
        """
        Rolls back any partial data saved for an aborted or cancelled document run.
        Cleans up relationships, evidence, observations, chunks, and the document record.
        """
        with self.conn:
            obs_rows = self.conn.execute(
                "SELECT DISTINCT observation_id FROM evidence WHERE document_id = ?", (doc_id,)
            ).fetchall()
            obs_ids = [r["observation_id"] for r in obs_rows]

            if obs_ids:
                placeholders = ",".join("?" * len(obs_ids))
                self.conn.execute(
                    f"DELETE FROM relationships WHERE observation_a IN ({placeholders}) OR observation_b IN ({placeholders})",
                    obs_ids + obs_ids,
                )
                self.conn.execute("DELETE FROM evidence WHERE document_id = ?", (doc_id,))
                self.conn.execute(
                    f"DELETE FROM observations WHERE id IN ({placeholders}) AND id NOT IN (SELECT observation_id FROM evidence)",
                    obs_ids,
                )
            else:
                self.conn.execute("DELETE FROM evidence WHERE document_id = ?", (doc_id,))

            self.conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
            self.conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    def list_documents(self) -> List[Dict[str, Any]]:
        cursor = self.conn.execute("SELECT * FROM documents ORDER BY created_at DESC, filename")
        results = []
        for row in cursor.fetchall():
            doc = dict(row)
            # Count chunks and observations
            c_cnt = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM chunks WHERE document_id = ?", (doc["id"],)
            ).fetchone()["cnt"]
            o_cnt = self.conn.execute(
                "SELECT COUNT(DISTINCT observation_id) as cnt FROM evidence WHERE document_id = ?",
                (doc["id"],),
            ).fetchone()["cnt"]
            doc["chunk_count"] = c_cnt
            doc["observation_count"] = o_cnt
            results.append(doc)
        return results

    def get_document_detail(self, doc_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
        row = cursor.fetchone()
        if not row:
            return None
        doc = dict(row)
        c_cnt = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM chunks WHERE document_id = ?", (doc_id,)
        ).fetchone()["cnt"]
        o_cnt = self.conn.execute(
            "SELECT COUNT(DISTINCT observation_id) as cnt FROM evidence WHERE document_id = ?",
            (doc_id,),
        ).fetchone()["cnt"]
        doc["chunk_count"] = c_cnt
        doc["observation_count"] = o_cnt
        return doc

    # -----------------------------
    # Chunk operations
    # -----------------------------
    def save_chunk(
        self,
        chunk_id: str,
        document_id: str,
        page_number: int,
        section: Optional[str],
        chunk_type: str,
        text: str,
    ):
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO chunks (id, document_id, page_number, section, chunk_type, text)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (chunk_id, document_id, page_number, section, chunk_type, text),
            )

    def get_chunks_for_document(self, document_id: str) -> List[Dict[str, Any]]:
        cursor = self.conn.execute(
            "SELECT * FROM chunks WHERE document_id = ? ORDER BY page_number, id",
            (document_id,),
        )
        return [dict(r) for r in cursor.fetchall()]

    # -----------------------------
    # Entity & Concept operations
    # -----------------------------
    def get_or_create_entity(
        self, canonical_name: str, entity_type: Optional[str] = None, aliases: Optional[List[str]] = None
    ) -> str:
        with self.conn:
            cursor = self.conn.execute(
                "SELECT id, aliases_json FROM entities WHERE canonical_name = ?", (canonical_name,)
            )
            row = cursor.fetchone()
            aliases_list = aliases or []
            if row:
                if aliases_list and row["aliases_json"]:
                    existing_aliases = json.loads(row["aliases_json"])
                    merged = list(set(existing_aliases + aliases_list))
                    self.conn.execute(
                        "UPDATE entities SET aliases_json = ? WHERE id = ?",
                        (json.dumps(merged), row["id"]),
                    )
                return row["id"]

            entity_id = f"ent_{canonical_name.lower().replace(' ', '_')}"
            self.conn.execute(
                "INSERT INTO entities (id, canonical_name, entity_type, aliases_json) VALUES (?, ?, ?, ?)",
                (entity_id, canonical_name, entity_type, json.dumps(aliases_list)),
            )
            return entity_id

    def get_or_create_concept(
        self, canonical_name: str, description: Optional[str] = None, aliases: Optional[List[str]] = None
    ) -> str:
        with self.conn:
            cursor = self.conn.execute(
                "SELECT id, aliases_json FROM concepts WHERE canonical_name = ?", (canonical_name,)
            )
            row = cursor.fetchone()
            aliases_list = aliases or []
            if row:
                if aliases_list and row["aliases_json"]:
                    existing_aliases = json.loads(row["aliases_json"])
                    merged = list(set(existing_aliases + aliases_list))
                    self.conn.execute(
                        "UPDATE concepts SET aliases_json = ? WHERE id = ?",
                        (json.dumps(merged), row["id"]),
                    )
                return row["id"]

            concept_id = f"cpt_{canonical_name.lower().replace(' ', '_')}"
            self.conn.execute(
                "INSERT INTO concepts (id, canonical_name, description, aliases_json) VALUES (?, ?, ?, ?)",
                (concept_id, canonical_name, description, json.dumps(aliases_list)),
            )
            return concept_id

    # -----------------------------
    # Observation operations
    # -----------------------------
    def save_observation(self, obs: Observation, dataset: str = "general") -> None:
        entity_id = self.get_or_create_entity(obs.entity.canonical_name, obs.entity.entity_type, obs.entity.aliases)
        concept_id = self.get_or_create_concept(obs.concept.canonical_name, obs.concept.definition)

        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO observations (
                    id, entity_id, concept_id, value_type, value, unit,
                    normalized_value, normalized_unit, text_value,
                    period_type, period_start, period_end, period_label,
                    geography, scope_level, consolidation,
                    assertion_status, confidence, needs_review, review_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    obs.id,
                    entity_id,
                    concept_id,
                    obs.value.type.value,
                    obs.value.amount,
                    obs.value.unit,
                    obs.value.normalized_amount,
                    obs.value.normalized_unit,
                    obs.value.text,
                    obs.time.period_type.value,
                    str(obs.time.start_date) if obs.time.start_date else None,
                    str(obs.time.end_date) if obs.time.end_date else None,
                    obs.time.label,
                    obs.scope.geography,
                    obs.scope.level,
                    obs.scope.consolidation,
                    obs.assertion_status.value,
                    obs.confidence,
                    1 if obs.needs_review else 0,
                    obs.review_reason,
                ),
            )

            # Save evidence items
            for ev in obs.evidence:
                ev_id = f"ev_{obs.id}_{ev.page_number}_{abs(hash(ev.quote)) % 10000}"
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO evidence (
                        id, observation_id, document_id, chunk_id, page_number, section, quote, locator_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ev_id,
                        obs.id,
                        ev.document_id,
                        ev.chunk_id,
                        ev.page_number,
                        ev.section,
                        ev.quote,
                        json.dumps(ev.locator) if ev.locator else None,
                    ),
                )

    def get_observation(self, obs_id: str) -> Optional[Observation]:
        cursor = self.conn.execute(
            """
            SELECT o.*, e.canonical_name as entity_name, e.entity_type, e.aliases_json as entity_aliases,
                   c.canonical_name as concept_name, c.description as concept_desc
            FROM observations o
            JOIN entities e ON o.entity_id = e.id
            JOIN concepts c ON o.concept_id = c.id
            WHERE o.id = ?
            """,
            (obs_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        # Fetch evidence
        ev_cursor = self.conn.execute(
            "SELECT * FROM evidence WHERE observation_id = ?", (obs_id,)
        )
        evidence_list = [
            Evidence(
                document_id=erow["document_id"],
                page_number=erow["page_number"],
                section=erow["section"],
                chunk_id=erow["chunk_id"],
                quote=erow["quote"],
                locator=json.loads(erow["locator_json"]) if erow["locator_json"] else None,
            )
            for erow in ev_cursor.fetchall()
        ]

        aliases = json.loads(row["entity_aliases"]) if row["entity_aliases"] else []

        return Observation(
            id=row["id"],
            entity=Entity(canonical_name=row["entity_name"], entity_type=row["entity_type"], aliases=aliases),
            concept=Concept(canonical_name=row["concept_name"], definition=row["concept_desc"]),
            value=FactValue(
                type=ValueType(row["value_type"]),
                amount=row["value"],
                unit=row["unit"],
                normalized_amount=row["normalized_value"],
                normalized_unit=row["normalized_unit"],
                text=row["text_value"],
            ),
            time=TimeContext(
                period_type=PeriodType(row["period_type"]),
                label=row["period_label"],
            ),
            scope=Scope(
                geography=row["geography"],
                level=row["scope_level"],
                consolidation=row["consolidation"],
            ),
            assertion_status=AssertionStatus(row["assertion_status"]),
            confidence=row["confidence"],
            needs_review=bool(row["needs_review"]),
            review_reason=row["review_reason"],
            evidence=evidence_list,
        )

    def list_observations(
        self,
        entity_name: Optional[str] = None,
        concept_name: Optional[str] = None,
        needs_review: Optional[bool] = None,
    ) -> List[Observation]:
        query = """
            SELECT o.*, e.canonical_name as entity_name, e.entity_type, e.aliases_json as entity_aliases,
                   c.canonical_name as concept_name, c.description as concept_desc
            FROM observations o
            JOIN entities e ON o.entity_id = e.id
            JOIN concepts c ON o.concept_id = c.id
            WHERE 1=1
        """
        params = []
        if entity_name:
            query += " AND LOWER(e.canonical_name) = ?"
            params.append(entity_name.lower())
        if concept_name:
            query += " AND LOWER(c.canonical_name) = ?"
            params.append(concept_name.lower())
        if needs_review is not None:
            query += " AND o.needs_review = ?"
            params.append(1 if needs_review else 0)

        cursor = self.conn.execute(query, params)
        obs_rows = cursor.fetchall()

        results = []
        for row in obs_rows:
            ev_cursor = self.conn.execute(
                "SELECT * FROM evidence WHERE observation_id = ?", (row["id"],)
            )
            evidence_list = [
                Evidence(
                    document_id=erow["document_id"],
                    page_number=erow["page_number"],
                    section=erow["section"],
                    chunk_id=erow["chunk_id"],
                    quote=erow["quote"],
                    locator=json.loads(erow["locator_json"]) if erow["locator_json"] else None,
                )
                for erow in ev_cursor.fetchall()
            ]
            aliases = json.loads(row["entity_aliases"]) if row["entity_aliases"] else []

            results.append(
                Observation(
                    id=row["id"],
                    entity=Entity(
                        canonical_name=row["entity_name"], entity_type=row["entity_type"], aliases=aliases
                    ),
                    concept=Concept(
                        canonical_name=row["concept_name"], definition=row["concept_desc"]
                    ),
                    value=FactValue(
                        type=ValueType(row["value_type"]),
                        amount=row["value"],
                        unit=row["unit"],
                        normalized_amount=row["normalized_value"],
                        normalized_unit=row["normalized_unit"],
                        text=row["text_value"],
                    ),
                    time=TimeContext(
                        period_type=PeriodType(row["period_type"]),
                        label=row["period_label"],
                    ),
                    scope=Scope(
                        geography=row["geography"],
                        level=row["scope_level"],
                        consolidation=row["consolidation"],
                    ),
                    assertion_status=AssertionStatus(row["assertion_status"]),
                    confidence=row["confidence"],
                    needs_review=bool(row["needs_review"]),
                    review_reason=row["review_reason"],
                    evidence=evidence_list,
                )
            )
        return results

    # -----------------------------
    # Evidence detail operations
    # -----------------------------
    def get_evidence_by_id(self, evidence_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,))
        row = cursor.fetchone()
        if not row:
            return None
        ev = dict(row)
        ev["locator"] = json.loads(ev["locator_json"]) if ev["locator_json"] else None
        return ev

    # -----------------------------
    # Relationship operations
    # -----------------------------
    def save_relationship(self, rel: Relationship) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO relationships (
                    id, observation_a, observation_b, relationship_type,
                    confidence, explanation, reasons_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rel.id,
                    rel.observation_a,
                    rel.observation_b,
                    rel.relationship_type.value,
                    rel.confidence,
                    rel.explanation,
                    json.dumps(rel.reasons),
                ),
            )

    def list_relationships(
        self, rel_type: Optional[RelationshipType] = None
    ) -> List[Relationship]:
        query = "SELECT * FROM relationships WHERE 1=1"
        params = []
        if rel_type:
            query += " AND relationship_type = ?"
            params.append(rel_type.value)

        cursor = self.conn.execute(query, params)
        rows = cursor.fetchall()
        return [
            Relationship(
                id=r["id"],
                observation_a=r["observation_a"],
                observation_b=r["observation_b"],
                relationship_type=RelationshipType(r["relationship_type"]),
                confidence=r["confidence"],
                explanation=r["explanation"],
                reasons=json.loads(r["reasons_json"]),
            )
            for r in rows
        ]

    def get_relationship_detail(self, rel_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.execute("SELECT * FROM relationships WHERE id = ?", (rel_id,))
        r = cursor.fetchone()
        if not r:
            return None

        rel_dict = dict(r)
        rel_dict["reasons"] = json.loads(r["reasons_json"]) if r["reasons_json"] else []

        obs_a = self.get_observation(r["observation_a"])
        obs_b = self.get_observation(r["observation_b"])

        rel_dict["observation_a_detail"] = obs_a.dict() if obs_a else None
        rel_dict["observation_b_detail"] = obs_b.dict() if obs_b else None
        return rel_dict

    # -----------------------------
    # Free-text Search (§11 GET /search?q=...)
    # -----------------------------
    def search_facts(self, query_str: str) -> Dict[str, Any]:
        term = f"%{query_str.lower().strip()}%"
        # Search entities
        e_cursor = self.conn.execute(
            "SELECT * FROM entities WHERE LOWER(canonical_name) LIKE ? OR LOWER(aliases_json) LIKE ?",
            (term, term),
        )
        matched_entities = [dict(r) for r in e_cursor.fetchall()]

        # Search concepts
        c_cursor = self.conn.execute(
            "SELECT * FROM concepts WHERE LOWER(canonical_name) LIKE ? OR LOWER(description) LIKE ?",
            (term, term),
        )
        matched_concepts = [dict(r) for r in c_cursor.fetchall()]

        # Search observations
        obs_matches = []
        all_obs = self.list_observations()
        for o in all_obs:
            if (
                query_str.lower() in o.entity.canonical_name.lower()
                or query_str.lower() in o.concept.canonical_name.lower()
                or (o.time.label and query_str.lower() in o.time.label.lower())
                or any(query_str.lower() in ev.quote.lower() for ev in o.evidence)
            ):
                obs_matches.append(o.dict())

        return {
            "query": query_str,
            "entities": matched_entities,
            "concepts": matched_concepts,
            "observations": obs_matches,
        }

    # -----------------------------
    # Grouped Facts (§2: Grouped observations referring to same entity & concept)
    # -----------------------------
    def get_grouped_facts(self) -> List[Dict[str, Any]]:
        all_obs = self.list_observations()
        all_rels = self.list_relationships()

        groups: Dict[tuple, List[Observation]] = {}
        for o in all_obs:
            key = (o.entity.canonical_name, o.concept.canonical_name)
            groups.setdefault(key, []).append(o)

        fact_list = []
        for (entity_name, concept_name), obs_group in groups.items():
            # Determine consensus / relationship badge across this group
            obs_ids = {o.id for o in obs_group}
            group_rels = [
                r for r in all_rels
                if r.observation_a in obs_ids and r.observation_b in obs_ids
            ]

            status = "single_source"
            if any(o.needs_review for o in obs_group):
                status = "needs_review"
            elif any(r.relationship_type == RelationshipType.CONTRADICTED for r in group_rels):
                status = "contradicted"
            elif any(r.relationship_type == RelationshipType.CORROBORATED for r in group_rels):
                status = "corroborated"
            elif any(r.relationship_type == RelationshipType.CONTEXTUALIZED for r in group_rels):
                status = "contextualized"

            fact_list.append({
                "fact_key": f"{entity_name}::{concept_name}",
                "entity_name": entity_name,
                "concept_name": concept_name,
                "status": status,
                "observation_count": len(obs_group),
                "observations": [o.dict() for o in obs_group],
                "relationships": [r.dict() for r in group_rels],
            })

        return sorted(fact_list, key=lambda f: (f["status"] != "contradicted", f["entity_name"]))

    # -----------------------------
    # Processing runs
    # -----------------------------
    def log_processing_run(
        self,
        run_id: str,
        document_id: Optional[str],
        status: str,
        extractor_model: str,
        metrics: Dict[str, Any],
        started_at: str,
        completed_at: Optional[str] = None,
        error: Optional[str] = None,
    ):
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO processing_runs (
                    id, document_id, status, extractor_model, metrics_json,
                    started_at, completed_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    document_id,
                    status,
                    extractor_model,
                    json.dumps(metrics),
                    started_at,
                    completed_at,
                    error,
                ),
            )

    def list_processing_runs(self) -> List[Dict[str, Any]]:
        cursor = self.conn.execute("SELECT * FROM processing_runs ORDER BY started_at DESC")
        runs = []
        for r in cursor.fetchall():
            item = dict(r)
            item["metrics"] = json.loads(item["metrics_json"]) if item["metrics_json"] else {}
            runs.append(item)
        return runs
