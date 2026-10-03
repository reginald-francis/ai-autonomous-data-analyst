"""
One-time validation: encodes the same sentences captured in
reference_embeddings.json (from the real sentence-transformers model)
using the new ONNX + tokenizers pipeline, and checks that pairwise cosine
similarity ordering matches — the actual thing that matters for RAG
retrieval quality, not exact vector equality (quantization shifts values
slightly; it must not shift *rankings*).
"""
import json

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MODEL_DIR = "models/all-MiniLM-L6-v2-onnx"


def mean_pool_and_normalize(last_hidden_state: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    mask = attention_mask[..., None].astype(np.float32)  # (batch, seq, 1)
    summed = (last_hidden_state * mask).sum(axis=1)  # (batch, dim)
    counts = np.clip(mask.sum(axis=1), a_min=1e-9, a_max=None)  # (batch, 1)
    mean_pooled = summed / counts
    norms = np.linalg.norm(mean_pooled, axis=1, keepdims=True)
    return mean_pooled / np.clip(norms, a_min=1e-9, a_max=None)


def encode(sentences: list, tokenizer: Tokenizer, session: ort.InferenceSession) -> np.ndarray:
    tokenizer.enable_padding()
    encodings = tokenizer.encode_batch(sentences)
    input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
    token_type_ids = np.array([e.type_ids for e in encodings], dtype=np.int64)

    outputs = session.run(
        None,
        {"input_ids": input_ids, "attention_mask": attention_mask, "token_type_ids": token_type_ids},
    )
    last_hidden_state = outputs[0]
    return mean_pool_and_normalize(last_hidden_state, attention_mask)


def cosine_sim_matrix(vectors: np.ndarray) -> np.ndarray:
    return vectors @ vectors.T  # already L2-normalized


with open("scripts/reference_embeddings.json") as f:
    ref = json.load(f)

sentences = ref["sentences"]
ref_embeddings = np.array(ref["embeddings"])
ref_norm = ref_embeddings / np.linalg.norm(ref_embeddings, axis=1, keepdims=True)
ref_sim = cosine_sim_matrix(ref_norm)

tokenizer = Tokenizer.from_file(f"{MODEL_DIR}/tokenizer.json")
session = ort.InferenceSession(f"{MODEL_DIR}/model_quantized.onnx")
onnx_embeddings = encode(sentences, tokenizer, session)
onnx_sim = cosine_sim_matrix(onnx_embeddings)

print("Sentences:")
for i, s in enumerate(sentences):
    print(f"  [{i}] {s}")

print("\nReference (sentence-transformers) similarity matrix:")
print(np.round(ref_sim, 3))
print("\nONNX (quantized) similarity matrix:")
print(np.round(onnx_sim, 3))

print("\nMax absolute difference in similarity values:", np.max(np.abs(ref_sim - onnx_sim)))

# The actual thing that matters: for each sentence, does the *ranking* of
# which other sentences it's most similar to match between the two models?
ref_rankings = np.argsort(-ref_sim, axis=1)
onnx_rankings = np.argsort(-onnx_sim, axis=1)
rankings_match = np.array_equal(ref_rankings, onnx_rankings)
print("Similarity rankings identical between models:", rankings_match)

if not rankings_match:
    for i in range(len(sentences)):
        if not np.array_equal(ref_rankings[i], onnx_rankings[i]):
            print(f"  Ranking differs for sentence [{i}]: ref={ref_rankings[i].tolist()} onnx={onnx_rankings[i].tolist()}")
