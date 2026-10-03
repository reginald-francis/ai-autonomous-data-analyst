"""
Real end-to-end test of OnnxEmbedder (Phase 5's swap from sentence-
transformers to onnxruntime + tokenizers — see PHASES.md's Docker-size
tradeoff). Every other RAG test monkeypatches RagIndex._get_model() to a
fake embedder (see test_rag_service.py's docstring) — deliberately, so the
suite never pays a real model-load cost. This file is the one place the
actual bundled ONNX model + tokenizer get exercised, proving the swap
works end-to-end through RagIndex, not just in isolation
(scripts/validate_onnx_embedder.py already checked embedding quality
against the original sentence-transformers model before this landed).
"""
import numpy as np

from src.services.rag_service import OnnxEmbedder, RagIndex


def test_onnx_embedder_produces_normalized_384_dim_vectors():
    embedder = OnnxEmbedder("models/all-MiniLM-L6-v2-onnx")
    vectors = embedder.encode(["revenue grew steadily", "the sky is blue"])

    assert vectors.shape == (2, 384)
    norms = np.linalg.norm(vectors, axis=1)
    np.testing.assert_allclose(norms, 1.0, atol=1e-4)


def test_onnx_embedder_ranks_similar_sentences_closer():
    embedder = OnnxEmbedder("models/all-MiniLM-L6-v2-onnx")
    query, near_duplicate, unrelated = embedder.encode([
        "The total revenue for the North region was $50,000 last quarter.",
        "North region revenue reached fifty thousand dollars in Q1.",
        "The cat sat quietly on the windowsill in the afternoon sun.",
    ])

    sim_near = query @ near_duplicate
    sim_unrelated = query @ unrelated
    assert sim_near > sim_unrelated
    assert sim_near > 0.5


def test_ragindex_end_to_end_with_real_onnx_embedder():
    """No monkeypatching here — build_from_chunks()/retrieve_context() run
    through the real OnnxEmbedder + a real FAISS index, exercising the
    full RagIndex path this app actually uses in production."""
    index = RagIndex()
    index.build_from_chunks([
        {"filename": "glossary.txt", "content": "In our pipeline, won means a closed-deal, not just a signed contract."},
        {"filename": "glossary.txt", "content": "The weather today is sunny with a high of 75 degrees outside."},
    ])

    result = index.retrieve_context("What does won mean in our data?")

    assert "closed-deal" in result
    assert "weather" not in result
