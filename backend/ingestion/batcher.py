import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class LLMBatch:
    batch_id: str
    document_id: str
    section: str
    page_start: int
    page_end: int
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    total_chars: int = 0

    def format_for_llm(self) -> str:
        """
        Formats the batch into a structured representation with section headers,
        explicit chunk boundary tags, chunk IDs, page numbers, and content types.
        Provides the LLM with surrounding narrative and tabular context together.
        """
        lines = [
            f"=== BATCH {self.batch_id} | SECTION: {self.section} | PAGES: {self.page_start} - {self.page_end} ===",
            ""
        ]
        for chk in self.chunks:
            c_type = chk.get("chunk_type") or chk.get("content_type", "text")
            c_id = chk.get("id", "")
            c_page = chk.get("page_number", 0)
            c_sec = chk.get("section") or self.section
            
            lines.append(f'[CHUNK id="{c_id}" page={c_page} type="{c_type}" section="{c_sec}"]')
            lines.append(chk.get("text", "").strip())
            lines.append("[/CHUNK]")
            lines.append("")

        return "\n".join(lines)


class HierarchicalBatcher:
    """
    Partitions atomic chunks into semantically coherent LLM processing units (§5).
    Rules:
    1. Semantic Locality: Keep paragraphs, tables, and explanations of the same section together.
    2. Table Context Preservation: Tables are kept atomic and packaged with their preceding and following context.
    3. Section Boundaries: Major new section headings trigger a clean batch boundary if the current batch has sufficient size.
    4. Budgeting: Limit batches to ~12,000–18,000 characters (approx. 3,000–4,500 tokens) or max 6–8 pages.
    """

    def __init__(
        self,
        max_batch_chars: int = 15000,
        max_batch_pages: int = 6,
        min_section_split_chars: int = 3500,
    ):
        self.max_batch_chars = max_batch_chars
        self.max_batch_pages = max_batch_pages
        self.min_section_split_chars = min_section_split_chars

    def build_batches(
        self,
        chunks: List[Dict[str, Any]],
        document_id: str = "doc",
    ) -> List[LLMBatch]:
        """
        Groups a list of atomic chunk dictionaries into LLMBatch objects.
        """
        if not chunks:
            return []

        batches: List[LLMBatch] = []
        current_chunks: List[Dict[str, Any]] = []
        current_chars = 0
        current_section = chunks[0].get("section", "General")
        batch_counter = 1

        def flush_current():
            nonlocal current_chunks, current_chars, current_section, batch_counter
            if not current_chunks:
                return

            pages = [c.get("page_number", 1) for c in current_chunks]
            batch = LLMBatch(
                batch_id=f"batch_{batch_counter:02d}",
                document_id=document_id,
                section=current_section,
                page_start=min(pages),
                page_end=max(pages),
                chunks=list(current_chunks),
                total_chars=current_chars,
            )
            batches.append(batch)
            batch_counter += 1
            current_chunks = []
            current_chars = 0

        for idx, chk in enumerate(chunks):
            text = chk.get("text", "").strip()
            if not text:
                continue

            chk_len = len(text)
            chk_page = chk.get("page_number", 1)
            chk_sec = chk.get("section") or current_section
            chk_type = chk.get("chunk_type") or chk.get("content_type", "text")
            is_table = chk_type == "table"

            # Check if section changed
            section_changed = (
                chk_sec != current_section
                and current_section != "General Information"
                and chk_sec != "General Information"
            )

            # Check page span if we added this chunk
            if current_chunks:
                current_min_page = min(c.get("page_number", chk_page) for c in current_chunks)
                would_exceed_pages = (chk_page - current_min_page) >= self.max_batch_pages
            else:
                would_exceed_pages = False

            # Condition 1: Exceeds character or page budget
            would_exceed_chars = (current_chars + chk_len) > self.max_batch_chars

            # Condition 2: Section changed and current batch is reasonably sized
            should_split_section = section_changed and (current_chars >= self.min_section_split_chars)

            # Condition 3: Chunk is a large table and adding it exceeds budget
            is_large_table_overflow = is_table and would_exceed_chars and (current_chars > 0)

            if should_split_section or would_exceed_chars or would_exceed_pages or is_large_table_overflow:
                flush_current()
                current_section = chk_sec

            current_chunks.append(chk)
            current_chars += chk_len

            # Update current section if new one appears and we haven't flushed
            if chk_sec and chk_sec != "General Information":
                current_section = chk_sec

        flush_current()
        return batches
