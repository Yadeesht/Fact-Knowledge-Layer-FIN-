import json
import sqlite3
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timezone
from pathlib import Path
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
    ComparabilitySignature,
    NumericComparison,
)


class Repository:
    def __init__(
        self,
        db_conn: Optional[Union[sqlite3.Connection, Path, str]] = None,
        db_path: Optional[Union[Path, str]] = None,
    ):
        target_path = db_path
        if isinstance(db_conn, (Path, str)):
            target_path = db_conn
            db_conn = None

        if db_conn is not None:
            self.conn = db_conn
        elif target_path is not None:
            self.conn = get_db_connection(Path(target_path))
        else:
            self.conn = get_db_connection()

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
            self.conn.execute("DELETE FROM analysis_sessions")


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
        file_hash: Optional[str] = None,
        file_size: Optional[int] = None,
    ):
        now = datetime.utcnow().isoformat()
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO documents (id, filename, dataset, document_type, page_count, file_hash, file_size, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (doc_id, filename, dataset, document_type, page_count, file_hash, file_size, now),
            )

    def get_document_by_hash(self, file_hash: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.execute("SELECT * FROM documents WHERE file_hash = ?", (file_hash,))
        row = cursor.fetchone()
        if not row:
            return None
        return self.get_document_detail(row["id"])

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
        self,
        canonical_name: str,
        entity_type: Optional[str] = None,
        aliases: Optional[List[str]] = None,
        embedding: Optional[List[float]] = None,
    ) -> str:
        with self.conn:
            cursor = self.conn.execute(
                "SELECT id, aliases_json, embedding_json FROM entities WHERE canonical_name = ?", (canonical_name,)
            )
            row = cursor.fetchone()
            aliases_list = aliases or []
            if row:
                updates = []
                params = []
                if aliases_list and row["aliases_json"]:
                    existing_aliases = json.loads(row["aliases_json"])
                    merged = list(set(existing_aliases + aliases_list))
                    updates.append("aliases_json = ?")
                    params.append(json.dumps(merged))
                if embedding and not row["embedding_json"]:
                    updates.append("embedding_json = ?")
                    params.append(json.dumps(embedding))
                if updates:
                    params.append(row["id"])
                    self.conn.execute(f"UPDATE entities SET {', '.join(updates)} WHERE id = ?", tuple(params))
                return row["id"]

            entity_id = f"ent_{canonical_name.lower().replace(' ', '_')}"
            emb_json = json.dumps(embedding) if embedding else None
            self.conn.execute(
                "INSERT INTO entities (id, canonical_name, entity_type, aliases_json, embedding_json) VALUES (?, ?, ?, ?, ?)",
                (entity_id, canonical_name, entity_type, json.dumps(aliases_list), emb_json),
            )
            return entity_id

    def get_or_create_concept(
        self,
        canonical_name: str,
        description: Optional[str] = None,
        aliases: Optional[List[str]] = None,
        embedding: Optional[List[float]] = None,
    ) -> str:
        with self.conn:
            cursor = self.conn.execute(
                "SELECT id, aliases_json, embedding_json FROM concepts WHERE canonical_name = ?", (canonical_name,)
            )
            row = cursor.fetchone()
            aliases_list = aliases or []
            if row:
                updates = []
                params = []
                if aliases_list and row["aliases_json"]:
                    existing_aliases = json.loads(row["aliases_json"])
                    merged = list(set(existing_aliases + aliases_list))
                    updates.append("aliases_json = ?")
                    params.append(json.dumps(merged))
                if embedding and not row["embedding_json"]:
                    updates.append("embedding_json = ?")
                    params.append(json.dumps(embedding))
                if updates:
                    params.append(row["id"])
                    self.conn.execute(f"UPDATE concepts SET {', '.join(updates)} WHERE id = ?", tuple(params))
                return row["id"]

            concept_id = f"cpt_{canonical_name.lower().replace(' ', '_')}"
            emb_json = json.dumps(embedding) if embedding else None
            self.conn.execute(
                "INSERT INTO concepts (id, canonical_name, description, aliases_json, embedding_json) VALUES (?, ?, ?, ?, ?)",
                (concept_id, canonical_name, description, json.dumps(aliases_list), emb_json),
            )
            return concept_id

    def list_all_concepts(self) -> List[Dict[str, Any]]:
        """Returns all concepts stored in the database with their aliases and embeddings."""
        cursor = self.conn.execute("SELECT * FROM concepts")
        results = []
        for r in cursor.fetchall():
            item = dict(r)
            item["aliases"] = json.loads(item["aliases_json"]) if item.get("aliases_json") else []
            item["embedding"] = json.loads(item["embedding_json"]) if item.get("embedding_json") else None
            results.append(item)
        return results

    def list_all_entities(self) -> List[Dict[str, Any]]:
        """Returns all entities stored in the database with their aliases and embeddings."""
        cursor = self.conn.execute("SELECT * FROM entities")
        results = []
        for r in cursor.fetchall():
            item = dict(r)
            item["aliases"] = json.loads(item["aliases_json"]) if item.get("aliases_json") else []
            item["embedding"] = json.loads(item["embedding_json"]) if item.get("embedding_json") else None
            results.append(item)
        return results

    def update_concept_embedding(self, concept_id: str, embedding: List[float]) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE concepts SET embedding_json = ? WHERE id = ?",
                (json.dumps(embedding), concept_id),
            )

    def update_entity_embedding(self, entity_id: str, embedding: List[float]) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE entities SET embedding_json = ? WHERE id = ?",
                (json.dumps(embedding), entity_id),
            )

    # -----------------------------
    # Observation operations
    # -----------------------------
    def save_observation(
        self,
        obs: Observation,
        dataset: str = "general",
        document_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        doc_id = document_id or (obs.evidence[0].document_id if obs.evidence else None)
        entity_id = self.get_or_create_entity(obs.entity.canonical_name, obs.entity.entity_type, obs.entity.aliases)
        concept_id = self.get_or_create_concept(obs.concept.canonical_name, obs.concept.definition)

        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO observations (
                    id, document_id, session_id, entity_id, concept_id, value_type, value, unit,
                    normalized_value, normalized_unit, text_value,
                    period_type, period_start, period_end, period_label,
                    geography, scope_level, consolidation,
                    assertion_status, confidence, needs_review, review_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    obs.id,
                    doc_id,
                    session_id,
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
        document_id: Optional[str] = None,
        session_id: Optional[str] = None,
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
        if document_id:
            query += " AND (o.document_id = ? OR o.id IN (SELECT observation_id FROM evidence WHERE document_id = ?))"
            params.extend([document_id, document_id])
        if session_id:
            query += " AND o.session_id = ?"
            params.append(session_id)

        query += " ORDER BY o.id"
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

    def get_comparison_candidates(
        self,
        current_doc_id: str,
        comparison_mode: str = "cross_document",
        target_doc_ids: Optional[List[str]] = None,
    ) -> List[Observation]:
        """
        Returns scoped candidate observations for reconciliation.
        - 'cross_document': observations from other documents, strictly excluding the current document.
        - If target_doc_ids is provided, limits candidates strictly to those document IDs.
        - Excludes quarantined/needs_review observations.
        """
        query = """
            SELECT o.*, e.canonical_name as entity_name, e.entity_type, e.aliases_json as entity_aliases,
                   c.canonical_name as concept_name, c.description as concept_desc
            FROM observations o
            JOIN entities e ON o.entity_id = e.id
            JOIN concepts c ON o.concept_id = c.id
            WHERE o.needs_review = 0
        """
        params = []
        if comparison_mode == "cross_document":
            query += " AND (o.document_id != ? OR (o.document_id IS NULL AND o.id NOT IN (SELECT observation_id FROM evidence WHERE document_id = ?)))"
            params.extend([current_doc_id, current_doc_id])

        if target_doc_ids:
            placeholders = ",".join("?" for _ in target_doc_ids)
            query += f" AND (o.document_id IN ({placeholders}) OR o.id IN (SELECT observation_id FROM evidence WHERE document_id IN ({placeholders})))"
            params.extend(target_doc_ids + target_doc_ids)

        query += " ORDER BY o.id"
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
    def save_relationship(self, rel: Relationship, session_id: Optional[str] = None) -> None:
        comp_json = json.dumps(rel.comparability.model_dump()) if rel.comparability else None
        num_json = json.dumps(rel.numeric.model_dump()) if rel.numeric else None
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO relationships (
                    id, session_id, observation_a, observation_b, relationship_type,
                    confidence, explanation, reasons_json, comparability_json, numeric_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rel.id,
                    session_id,
                    rel.observation_a,
                    rel.observation_b,
                    rel.relationship_type.value,
                    rel.confidence,
                    rel.explanation,
                    json.dumps(rel.reasons),
                    comp_json,
                    num_json,
                ),
            )

    def clear_relationships_for_document(self, doc_id: str) -> None:
        """
        Clears all relationships involving observations of the specified document.
        """
        with self.conn:
            self.conn.execute(
                """
                DELETE FROM relationships
                WHERE observation_a IN (SELECT id FROM observations WHERE document_id = ?)
                   OR observation_b IN (SELECT id FROM observations WHERE document_id = ?)
                """,
                (doc_id, doc_id),
            )

    def list_relationships(
        self,
        rel_type: Optional[RelationshipType] = None,
        session_id: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> List[Relationship]:
        query = "SELECT * FROM relationships WHERE 1=1"
        params = []
        if rel_type:
            query += " AND relationship_type = ?"
            params.append(rel_type.value)
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if document_id and document_id != "all":
            query += """ AND (
                observation_a IN (SELECT id FROM observations WHERE document_id = ?)
                OR observation_b IN (SELECT id FROM observations WHERE document_id = ?)
            )"""
            params.extend([document_id, document_id])

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
                comparability=ComparabilitySignature(**json.loads(r["comparability_json"]))
                if ("comparability_json" in r.keys() and r["comparability_json"])
                else None,
                numeric=NumericComparison(**json.loads(r["numeric_json"]))
                if ("numeric_json" in r.keys() and r["numeric_json"])
                else None,
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
        rel_dict["comparability"] = (
            json.loads(r["comparability_json"])
            if ("comparability_json" in r.keys() and r["comparability_json"])
            else None
        )
        rel_dict["numeric"] = (
            json.loads(r["numeric_json"])
            if ("numeric_json" in r.keys() and r["numeric_json"])
            else None
        )

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

    # -----------------------------
    # Analysis Sessions
    # -----------------------------
    def create_session(
        self,
        session_id: str,
        name: str,
        document_ids: List[str],
        comparison_mode: str = "cross_document",
        baseline_doc_ids: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = datetime.utcnow().isoformat()
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO analysis_sessions (
                    id, name, description, document_ids_json, comparison_mode,
                    baseline_doc_ids_json, created_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    name,
                    description,
                    json.dumps(document_ids),
                    comparison_mode,
                    json.dumps(baseline_doc_ids or []),
                    now,
                    "active",
                ),
            )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.execute("SELECT * FROM analysis_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        if not row:
            return None
        sess = dict(row)
        sess["document_ids"] = json.loads(sess["document_ids_json"]) if sess["document_ids_json"] else []
        sess["baseline_doc_ids"] = json.loads(sess["baseline_doc_ids_json"]) if sess["baseline_doc_ids_json"] else []
        return sess

    def list_sessions(self) -> List[Dict[str, Any]]:
        cursor = self.conn.execute("SELECT * FROM analysis_sessions ORDER BY created_at DESC")
        sessions = []
        for r in cursor.fetchall():
            s = dict(r)
            s["document_ids"] = json.loads(s["document_ids_json"]) if s["document_ids_json"] else []
            s["baseline_doc_ids"] = json.loads(s["baseline_doc_ids_json"]) if s["baseline_doc_ids_json"] else []
            sessions.append(s)
        return sessions

    def update_session_status(self, session_id: str, status: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE analysis_sessions SET status = ? WHERE id = ?",
                (status, session_id),
            )
