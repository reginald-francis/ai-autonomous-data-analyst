import numpy as np
import logging

from src.config import get_settings

logger = logging.getLogger(__name__)


class OnnxEmbedder:
    """Drop-in replacement for SentenceTransformer's .encode(texts) ->
    np.ndarray interface, backed by onnxruntime + tokenizers instead of
    torch + transformers (Phase 5 — see PHASES.md's Docker-size tradeoff).
    Loads the model bundled under model_dir at build time — never
    downloaded at runtime, so this works with a read-only container
    filesystem and has no HuggingFace network dependency in production.

    Bypassing SentenceTransformer's wrapper means its mean-pooling +
    L2-normalization postprocessing has to be replicated by hand here;
    quality-parity against the original model was verified in
    scripts/validate_onnx_embedder.py and
    scripts/validate_retrieval_threshold.py before this replaced it.
    """

    def __init__(self, model_dir: str):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._tokenizer = Tokenizer.from_file(f"{model_dir}/tokenizer.json")
        self._tokenizer.enable_padding()
        self._session = ort.InferenceSession(f"{model_dir}/model_quantized.onnx")

    def encode(self, texts: list) -> np.ndarray:
        encodings = self._tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        token_type_ids = np.array([e.type_ids for e in encodings], dtype=np.int64)

        outputs = self._session.run(
            None,
            {"input_ids": input_ids, "attention_mask": attention_mask, "token_type_ids": token_type_ids},
        )
        last_hidden_state = outputs[0]
        return self._mean_pool_and_normalize(last_hidden_state, attention_mask)

    @staticmethod
    def _mean_pool_and_normalize(last_hidden_state: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
        mask = attention_mask[..., None].astype(np.float32)
        summed = (last_hidden_state * mask).sum(axis=1)
        counts = np.clip(mask.sum(axis=1), a_min=1e-9, a_max=None)
        mean_pooled = summed / counts
        norms = np.linalg.norm(mean_pooled, axis=1, keepdims=True)
        return mean_pooled / np.clip(norms, a_min=1e-9, a_max=None)


def split_into_chunks(documents: list) -> list:
    all_chunks = []
    for doc in documents:
        paragraphs = doc["content"].split('\n\n')
        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            # if len(paragraph) > 50:  # This threshold can be adjusted based on your needs
            if len(paragraph.split()) >= 5:
                all_chunks.append({"filename": doc["filename"], "content": paragraph})
    return all_chunks


class RagIndex:
    """Owns the embedding model, FAISS index, and chunk store as instance
    state instead of module globals. The OnnxEmbedder is constructed
    lazily in _get_model() on first actual use (build or retrieve), not at
    import time — importing this module (or anything that imports it, e.g.
    every agent) doesn't pay the model-load cost just to run."""

    def __init__(self, settings=None):
        self._settings = settings if settings is not None else get_settings()
        self._model = None
        self.index = None
        self.chunks = []

    def _get_model(self):
        if self._model is None:
            logger.info(
                f"Loading embedding model: {self._settings.embedding_model} "
                f"(ONNX, from {self._settings.embedding_model_path})"
            )
            self._model = OnnxEmbedder(self._settings.embedding_model_path)
        return self._model

    def build_from_chunks(self, chunks: list) -> None:
        """Embed an already-chunked document list and build this instance's
        FAISS index from it. self.chunks stays [] / self.index stays None
        when there are no usable chunks (e.g. every paragraph in the source
        document was too short to survive split_into_chunks) — the existing
        "index not built yet" guard in retrieve_context() already returns ""
        safely for that case."""
        self.chunks = chunks

        if not chunks:
            logger.warning("No usable chunks to index — RAG index left empty")
            self.index = None
            return

        import faiss

        embeddings = self._get_model().encode([chunk["content"] for chunk in chunks])
        embeddings = np.array(embeddings).astype('float32')
        logger.info(f"Embeddings generated with shape: {embeddings.shape}")

        dimension = embeddings.shape[1]
        index = faiss.IndexFlatL2(dimension)  # Euclidean distance index best for small datasets, can switch to IndexIVFFlat for larger datasets
        index.add(embeddings)
        self.index = index

        logger.info(f"FAISS index built with {self.index.ntotal} vectors")

    def retrieve_context(self, question: str, top_k: int = None) -> str:
        if top_k is None:
            top_k = self._settings.rag_top_k

        if self.index is None:
            logger.warning("FAISS index not built yet")
            return ""

        question_embedding = self._get_model().encode([question])
        question_embedding = np.array(question_embedding).astype('float32')

        distances, indices = self.index.search(question_embedding, top_k)

        relevant_chunks = []
        for i, distance in zip(indices[0], distances[0]):
            if 0 <= i < len(self.chunks) and distance < self._settings.rag_distance_threshold:
                relevant_chunks.append(self.chunks[i]["content"])

        return "\n\n".join(relevant_chunks)


_session_indexes: dict[str, RagIndex] = {}


def build_session_index(session_id: str, text: str, filename: str = "context.txt") -> None:
    """Chunk and embed one user-uploaded business-context document into a
    fresh, session-scoped RagIndex. Called only when a session's /upload
    request actually includes a context document — a session that never
    uploads one never gets an entry here, so it costs nothing (no embedding
    model load, no FAISS index)."""
    documents = [{"filename": filename, "content": text}]
    chunks = split_into_chunks(documents)

    index = RagIndex()
    index.build_from_chunks(chunks)
    logger.info(f"Session {session_id}: indexed {len(chunks)} chunk(s) from context document")

    _session_indexes[session_id] = index


def retrieve_session_context(session_id: str, question: str, top_k: int = None) -> str:
    """Retrieve chunks from the given session's own context document, or ""
    if that session never uploaded one. Session isolation is by construction:
    each session's RagIndex only ever contains that session's own chunks."""
    index = _session_indexes.get(session_id)
    if index is None:
        return ""
    return index.retrieve_context(question, top_k)


def drop_session(session_id: str) -> None:
    """Discard a session's RAG index, if it has one. Called from
    session_service's cleanup path so session-scoped RAG data is deleted on
    the same TTL/cleanup cadence as the session's uploaded CSV and DB files."""
    if _session_indexes.pop(session_id, None) is not None:
        logger.info(f"Deleted session RAG index: {session_id}")


def format_context_block(rag_context: str) -> str:
    """Formats retrieve_session_context()'s result for splicing into a
    prompt — the labeled "Additional business context" section every
    agent's prompt (planner/python/sql) includes. Returns "" when there's
    nothing to add (no session document, or nothing relevant to this
    question), instead of every prompt paying for an empty labeled section
    every single call — real token savings against Groq's tight 8K TPM
    ceiling (see CLAUDE.md), and one less ambiguous "is this section
    empty on purpose?" signal for the model to parse."""
    if not rag_context:
        return ""
    return f"\n\nAdditional business context:\n{rag_context}"