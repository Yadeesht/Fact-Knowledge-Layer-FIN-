import pytest
from backend.ingestion.batcher import HierarchicalBatcher, LLMBatch
from backend.ingestion.extractor import resolve_chunk_for_observation, extract_observations_from_batch, extract_observations_from_text


def test_hierarchical_batcher_grouping():
    chunks = [
        {
            "id": "chk_doc_p1_0",
            "page_number": 1,
            "section": "Overview",
            "content_type": "text",
            "text": "Delhivery is an Indian logistics and supply chain company founded in 2011.",
        },
        {
            "id": "chk_doc_p1_1",
            "page_number": 1,
            "section": "Overview",
            "content_type": "text",
            "text": "The company provides express parcel delivery, PTL freight, and supply chain solutions.",
        },
        {
            "id": "chk_doc_p2_2",
            "page_number": 2,
            "section": "Financial Performance",
            "content_type": "text",
            "text": "In FY24, Delhivery reported significant revenue improvements across express parcel segments.",
        },
        {
            "id": "chk_doc_p2_3",
            "page_number": 2,
            "section": "Financial Performance",
            "content_type": "table",
            "text": "Metric | FY24 | FY23\nRevenue from operations | 8,142.16 | 7,224.81\nEBITDA | 127.3 | (452.1)",
        },
        {
            "id": "chk_doc_p2_4",
            "page_number": 2,
            "section": "Financial Performance",
            "content_type": "text",
            "text": "Revenue increased 18.2% primarily driven by express parcel volumes reaching 740 million.",
        },
    ]

    batcher = HierarchicalBatcher(max_batch_chars=5000, max_batch_pages=4, min_section_split_chars=100)
    batches = batcher.build_batches(chunks, document_id="doc_test")

    assert len(batches) >= 1
    # Check that formatted text contains expected chunk tags
    formatted = batches[0].format_for_llm()
    assert '[CHUNK id="chk_doc_p1_0"' in formatted
    assert '[/CHUNK]' in formatted


def test_resolve_chunk_for_observation():
    batch_chunks = [
        {
            "id": "chk_101",
            "page_number": 5,
            "section": "Income Statement",
            "text": "Revenue from contracts with customers reached 8,142.16 Cr in FY24.",
        },
        {
            "id": "chk_102",
            "page_number": 6,
            "section": "Operational KPIs",
            "text": "Express parcel shipment volumes expanded to 740 million parcels in FY24.",
        },
    ]

    # 1. Exact chunk_id resolution
    chk_id, page, sec = resolve_chunk_for_observation("chk_102", 6, "arbitrary quote", batch_chunks)
    assert chk_id == "chk_102"
    assert page == 6

    # 2. Quote substring fallback resolution
    chk_id, page, sec = resolve_chunk_for_observation(None, None, "contracts with customers reached 8,142.16", batch_chunks)
    assert chk_id == "chk_101"
    assert page == 5

    # 3. Page fallback resolution
    chk_id, page, sec = resolve_chunk_for_observation("chk_nonexistent", 6, "some missing quote", batch_chunks)
    assert chk_id == "chk_102"
    assert page == 6


def test_extract_observations_from_text_wrapper():
    """Verify that extract_observations_from_text seamlessly delegates to the unified batch extractor."""
    obs = extract_observations_from_text(
        text="Delhivery reported revenue from operations of 8,142.16 crore in FY24.",
        page_number=1,
        document_id="doc_test",
        chunk_id="chk_test_1",
    )
    assert len(obs) >= 1
    assert obs[0].evidence[0].chunk_id == "chk_test_1"
    assert obs[0].evidence[0].page_number == 1
