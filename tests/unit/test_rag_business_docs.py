"""
Comprehensive RAG tests using a large, realistic business glossary
(tests/business_docs/novasphere_glossary.md — 19 distinct, deliberately
non-obvious term definitions for the Novasphere dataset) instead of the
2-3 sentence fixtures used elsewhere. Uses the REAL OnnxEmbedder + real
FAISS index throughout (no fake bag-of-words embedder) — the point here
is testing genuine retrieval precision at a realistic document size, not
just that the plumbing doesn't crash.
"""
from pathlib import Path

import pytest

from src.services.rag_service import (
    RagIndex,
    build_session_index,
    retrieve_session_context,
    split_into_chunks,
    drop_session,
    _session_indexes,
)

GLOSSARY_PATH = Path(__file__).resolve().parent.parent / "business_docs" / "novasphere_glossary.md"
GLOSSARY_TEXT = GLOSSARY_PATH.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def clear_sessions():
    yield
    _session_indexes.clear()


@pytest.fixture
def glossary_index():
    documents = [{"filename": "novasphere_glossary.md", "content": GLOSSARY_TEXT}]
    chunks = split_into_chunks(documents)
    index = RagIndex()
    index.build_from_chunks(chunks)
    return index


def test_glossary_chunks_into_many_distinct_pieces(glossary_index):
    """19 glossary paragraphs plus the intro paragraph, each >= 5 words —
    confirms chunking doesn't collapse a large realistic document into one
    blob or drop most of it."""
    assert len(glossary_index.chunks) >= 15


def test_whale_deal_question_retrieves_the_whale_deal_definition(glossary_index):
    result = glossary_index.retrieve_context("What counts as a whale deal?")
    assert "$100,000" in result
    assert "executive review" in result


def test_fiscal_year_question_retrieves_fiscal_calendar_definition(glossary_index):
    result = glossary_index.retrieve_context("When does the fiscal year start at Novasphere?")
    assert "April 1st" in result
    assert "March 31st" in result


def test_champion_question_does_not_retrieve_unrelated_chunks(glossary_index):
    """'champion' and 'contraction event' are unrelated concepts in this
    doc — a champion question should not pull in seat-reduction content."""
    result = glossary_index.retrieve_context("Who counts as a champion at Novasphere?")
    assert "champion" in result.lower() or "reference-able" in result.lower()
    assert "seats value decreased" not in result


def test_similar_sounding_terms_retrieve_distinctly(glossary_index):
    """downsell vs. contraction event are explicitly written to be
    confusable (same doc calls this out directly) — a downsell-specific
    question should surface the downsell paragraph."""
    result = glossary_index.retrieve_context("Is a plan downgrade the same as a downsell?")
    assert "downsell" in result.lower()


def test_unrelated_question_returns_empty_or_no_glossary_leakage(glossary_index):
    """A question with no connection to any glossary term should not drag
    in unrelated business definitions just because top_k forces 3 results
    — this checks the distance threshold is actually filtering, not just
    returning "closest of a bad bunch"."""
    result = glossary_index.retrieve_context("What is the capital of France?")
    assert "whale" not in result.lower()
    assert "fiscal year" not in result.lower()
    assert "champion" not in result.lower()


def test_retrieval_respects_top_k_even_with_many_chunks(glossary_index):
    """19+ chunks exist; default top_k=3 must still cap results, not
    return everything just because more exists."""
    result = glossary_index.retrieve_context("What is NRR and how is it calculated?", top_k=3)
    chunk_count = result.count("\n\n") + 1 if result else 0
    assert chunk_count <= 3


def test_plg_abbreviation_is_resolved_from_glossary(glossary_index):
    result = glossary_index.retrieve_context("What does PLG mean in Novasphere reporting?")
    assert "Product-Led Growth" in result


def test_emerging_vs_core_regions_terminology(glossary_index):
    result = glossary_index.retrieve_context("Which regions are considered Emerging Regions?")
    assert "APAC" in result and "LATAM" in result


class TestSessionIsolationWithConflictingDefinitions:
    """The sharpest possible isolation test: two sessions each define the
    SAME term ("whale deal") differently. If isolation is broken even
    slightly, session A could see session B's conflicting definition."""

    def setup_method(self):
        build_session_index(
            "session-a",
            "A 'whale deal' at Novasphere means any deal with tcv_usd greater than $100,000.",
        )
        build_session_index(
            "session-b",
            "A 'whale deal' at Novasphere means any deal with tcv_usd greater than $500,000, "
            "a much higher bar than most companies use.",
        )

    def test_session_a_only_sees_its_own_definition(self):
        result = retrieve_session_context("session-a", "What is a whale deal?")
        assert "$100,000" in result
        assert "$500,000" not in result

    def test_session_b_only_sees_its_own_definition(self):
        result = retrieve_session_context("session-b", "What is a whale deal?")
        assert "$500,000" in result
        assert "$100,000" not in result

    def test_dropping_session_a_does_not_affect_session_b(self):
        drop_session("session-a")
        assert retrieve_session_context("session-a", "What is a whale deal?") == ""
        assert "$500,000" in retrieve_session_context("session-b", "What is a whale deal?")


def test_large_document_via_build_session_index_end_to_end():
    """No monkeypatching, no shortcuts — the exact call path /upload
    actually uses for a large real-world-sized context document."""
    build_session_index("large-doc-session", GLOSSARY_TEXT, filename="novasphere_glossary.md")
    result = retrieve_session_context("large-doc-session", "What is a land-and-expand account?")
    assert "New Business" in result
    assert "Expansion" in result or "Renewal" in result
