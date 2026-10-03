"""
One-time script (Phase 5's sentence-transformers -> ONNX swap, see
PHASES.md's Docker-size tradeoff): captured reference embeddings from the
sentence-transformers setup before it was removed, so the ONNX swap could
be checked against real ground truth instead of assumed to be equivalent.
Its output, scripts/reference_embeddings.json, is what
validate_onnx_embedder.py and validate_retrieval_threshold.py check the
new OnnxEmbedder against.

Not part of the shipped app, and not re-runnable as-is anymore --
sentence-transformers/torch/transformers were uninstalled once this
script's output was captured (that was the whole point: they're not
needed in the deployed image). Kept for provenance/reproducibility of how
reference_embeddings.json was produced; re-running it would need
`pip install sentence-transformers` first.
"""
import json

from sentence_transformers import SentenceTransformer

SENTENCES = [
    "The total revenue for the North region was $50,000 last quarter.",
    "North region revenue reached fifty thousand dollars in Q1.",
    "A senior employee is anyone with age greater than 40.",
    "The weather today is sunny with a high of 75 degrees.",
    "In our pipeline, won means a closed-deal, not just a signed contract.",
    "The cat sat quietly on the windowsill in the afternoon sun.",
]

model = SentenceTransformer("all-MiniLM-L6-v2")
embeddings = model.encode(SENTENCES, normalize_embeddings=False)

with open("scripts/reference_embeddings.json", "w") as f:
    json.dump(
        {"sentences": SENTENCES, "embeddings": embeddings.tolist()},
        f,
    )

print(f"Saved {len(SENTENCES)} reference embeddings, dim={embeddings.shape[1]}")
