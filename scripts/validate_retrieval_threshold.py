"""
Follow-up to validate_onnx_embedder.py: the raw ranking check flagged two
mismatches, both swaps among near-zero similarity pairs. The question that
actually matters is whether either model's retrieve_context() would make
a different retrieve/don't-retrieve decision for any pair, given the
real Settings.rag_distance_threshold (1.5, i.e. cosine similarity > 0.25
for L2-normalized vectors: squared_L2 = 2 - 2*cos_sim).
"""
import json

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from validate_onnx_embedder import encode, cosine_sim_matrix

THRESHOLD_L2 = 1.5
THRESHOLD_COSINE = 1 - (THRESHOLD_L2 / 2)  # 0.25

with open("scripts/reference_embeddings.json") as f:
    ref = json.load(f)

sentences = ref["sentences"]
ref_embeddings = np.array(ref["embeddings"])
ref_norm = ref_embeddings / np.linalg.norm(ref_embeddings, axis=1, keepdims=True)
ref_sim = cosine_sim_matrix(ref_norm)

tokenizer = Tokenizer.from_file("models/all-MiniLM-L6-v2-onnx/tokenizer.json")
session = ort.InferenceSession("models/all-MiniLM-L6-v2-onnx/model_quantized.onnx")
onnx_embeddings = encode(sentences, tokenizer, session)
onnx_sim = cosine_sim_matrix(onnx_embeddings)

print(f"Retrieval cosine-similarity cutoff (from rag_distance_threshold=1.5): {THRESHOLD_COSINE}")
print()

n = len(sentences)
mismatches = 0
for i in range(n):
    for j in range(n):
        if i == j:
            continue
        ref_would_retrieve = ref_sim[i, j] > THRESHOLD_COSINE
        onnx_would_retrieve = onnx_sim[i, j] > THRESHOLD_COSINE
        if ref_would_retrieve != onnx_would_retrieve:
            mismatches += 1
            print(f"MISMATCH: [{i}]->[{j}] ref_sim={ref_sim[i,j]:.3f} (retrieve={ref_would_retrieve}) "
                  f"onnx_sim={onnx_sim[i,j]:.3f} (retrieve={onnx_would_retrieve})")
        elif ref_would_retrieve:
            print(f"Both retrieve [{i}]->[{j}]: ref_sim={ref_sim[i,j]:.3f} onnx_sim={onnx_sim[i,j]:.3f}")

print()
print(f"Retrieve/don't-retrieve decision mismatches: {mismatches} out of {n*(n-1)} ordered pairs")
