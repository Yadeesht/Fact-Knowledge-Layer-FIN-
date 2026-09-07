import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

try:
    import pypdf
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False


class PDFParser:
    """
    Structure-aware, evidence-preserving PDF parser and chunker.
    Follows deterministic rules:
    - Rule 1: Never mix pages (chunks strictly reside on a single page)
    - Rule 2: Never separate a detected table chunk
    - Rule 3: Keep headings attached to subsequent paragraph content (section context tracking)
    - Rule 4: Keep paragraphs together; split only at paragraph boundaries
    """

    def __init__(self, max_chunk_chars: int = 1500):
        self.max_chunk_chars = max_chunk_chars

    def extract_pages(self, pdf_path: Path) -> List[Dict[str, Any]]:
        """
        Extracts text from PDF by page, preserving page numbers (1-indexed).
        """
        pages = []
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found at {pdf_path}")

        if HAS_PYPDF:
            reader = pypdf.PdfReader(str(pdf_path))
            for page_idx, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                pages.append({
                    "page_number": page_idx + 1,
                    "text": text.strip(),
                })
        else:
            pages.append({
                "page_number": 1,
                "text": f"[Extracted text placeholder for {pdf_path.name}]",
            })

        return pages

    def detect_document_metadata(self, pdf_path: Path) -> Dict[str, Any]:
        """
        Infers document type, publisher, and title from first 1-3 pages (Section 2).
        """
        pages = self.extract_pages(pdf_path)
        first_text = pages[0]["text"] if pages else ""

        doc_type = "unknown"
        publisher = "unknown"

        first_lower = first_text.lower()
        if "prospectus" in first_lower:
            doc_type = "prospectus"
        elif "annual report" in first_lower:
            doc_type = "annual_report"
        elif "earnings" in first_lower or "investor presentation" in first_lower:
            doc_type = "presentation"
        elif "economic survey" in first_lower:
            doc_type = "economic_report"
        elif "reserve bank of india" in first_lower or "rbi" in first_lower:
            doc_type = "central_bank_report"
        elif "article iv" in first_lower or "imf" in first_lower:
            doc_type = "imf_consultation"

        if "delhivery" in first_lower:
            publisher = "Delhivery Limited"
        elif "reserve bank of india" in first_lower:
            publisher = "Reserve Bank of India"
        elif "ministry of finance" in first_lower or "economic survey" in first_lower:
            publisher = "Government of India"
        elif "international monetary fund" in first_lower or "imf" in first_lower:
            publisher = "International Monetary Fund"

        return {
            "document_type": doc_type,
            "publisher": publisher,
            "page_count": len(pages),
        }

    def chunk_document(self, pdf_path: Path, document_id: str) -> List[Dict[str, Any]]:
        """
        Splits document into evidence-preserving, structure-aware chunks (Section 8-9).
        """
        pages = self.extract_pages(pdf_path)
        chunks = []
        chunk_idx = 0
        current_section = "General Information"

        for p in pages:
            page_text = p["text"]
            page_num = p["page_number"]

            if not page_text:
                continue

            # Split by double newline into paragraph/block candidates
            raw_blocks = [b.strip() for b in re.split(r"\n\s*\n", page_text) if b.strip()]
            
            # If no double newlines, fallback to line-grouping
            if len(raw_blocks) <= 1:
                raw_blocks = [l.strip() for l in page_text.split("\n") if l.strip()]

            current_chunk_blocks = []
            current_chunk_len = 0

            for block in raw_blocks:
                # Heading detection heuristic (Rule 3)
                is_heading = (
                    len(block) < 120
                    and (
                        block.isupper()
                        or bool(re.match(r"^\d+(\.\d+)*\s+[A-Z]", block))
                        or "table of contents" in block.lower()
                    )
                )

                if is_heading:
                    current_section = block

                # Table detection heuristic (Rule 2)
                num_count = len(re.findall(r"\b\d+[\d,.]*\b", block))
                is_table = num_count >= 4 and ("\t" in block or "   " in block or "\n" in block)

                if is_table:
                    # Flush existing chunk before table
                    if current_chunk_blocks:
                        combined_text = "\n\n".join(current_chunk_blocks)
                        chunks.append({
                            "id": f"chk_{document_id}_p{page_num}_{chunk_idx}",
                            "document_id": document_id,
                            "page_number": page_num,
                            "section": current_section,
                            "content_type": "text",
                            "text": combined_text,
                        })
                        chunk_idx += 1
                        current_chunk_blocks = []
                        current_chunk_len = 0

                    # Save table as atomic chunk
                    chunks.append({
                        "id": f"chk_{document_id}_p{page_num}_{chunk_idx}",
                        "document_id": document_id,
                        "page_number": page_num,
                        "section": current_section,
                        "content_type": "table",
                        "text": block,
                    })
                    chunk_idx += 1
                    continue

                # Paragraph accumulation (Rule 4 & 5)
                block_len = len(block)
                if current_chunk_len + block_len > self.max_chunk_chars and current_chunk_blocks:
                    # Flush chunk at paragraph boundary
                    combined_text = "\n\n".join(current_chunk_blocks)
                    chunks.append({
                        "id": f"chk_{document_id}_p{page_num}_{chunk_idx}",
                        "document_id": document_id,
                        "page_number": page_num,
                        "section": current_section,
                        "content_type": "text",
                        "text": combined_text,
                    })
                    chunk_idx += 1
                    current_chunk_blocks = []
                    current_chunk_len = 0

                current_chunk_blocks.append(block)
                current_chunk_len += block_len

            # Flush remaining blocks on page (Rule 1: never mix pages)
            if current_chunk_blocks:
                combined_text = "\n\n".join(current_chunk_blocks)
                chunks.append({
                    "id": f"chk_{document_id}_p{page_num}_{chunk_idx}",
                    "document_id": document_id,
                    "page_number": page_num,
                    "section": current_section,
                    "content_type": "text",
                    "text": combined_text,
                })
                chunk_idx += 1

        return chunks
