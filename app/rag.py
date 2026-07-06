import os
import json
from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from app.config import GOOGLE_API_KEY, NVIDIA_API_KEY
import uuid
from app.config import QDRANT_URL, JSON_DIR, SPACES_DIR

# Initialize Qdrant Client (pointing to the configured Qdrant instance)
qdrant = QdrantClient(url=QDRANT_URL)

# Global cache for embedding clients to avoid re-initialization latency
_EMBEDDING_MODEL_CACHE = {}

# Per-space record of which embedding model produced its vectors. Changing the
# embedding model/provider silently invalidates old vectors (same dimension, but
# a different vector space → ~0 similarity), so we stamp the identity here and
# detect mismatches instead of returning empty results with no explanation.
_RAG_META_FILE = os.path.join(JSON_DIR, "rag_meta.json")


class _EmbeddingClient:
    """Provider-agnostic single-text embedder. Encapsulates provider + the
    document/query distinction so callers just call embed_one()."""

    def __init__(self, provider: str, model_name: str):
        self.provider = provider
        self.model_name = model_name
        self.id = f"{provider}:{model_name}"
        self._doc = None   # document/passage embedder
        self._query = None  # query embedder

    def _google(self, task_type: str):
        if not GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY is required for Google RAG embeddings")
        return GoogleGenerativeAIEmbeddings(
            model=self.model_name, google_api_key=GOOGLE_API_KEY, task_type=task_type,
        )

    def _nvidia(self):
        if not NVIDIA_API_KEY:
            raise ValueError("NVIDIA_API_KEY is required for NVIDIA NIM embeddings")
        from langchain_nvidia_ai_endpoints import NVIDIAEmbeddings
        # NVIDIAEmbeddings picks input_type per method (embed_documents=passage,
        # embed_query=query), so one instance serves both roles.
        return NVIDIAEmbeddings(model=self.model_name, api_key=NVIDIA_API_KEY, truncate="END")

    def embed_one(self, text: str, is_query: bool = False) -> list[float]:
        if self.provider == "nvidia":
            if self._doc is None:
                self._doc = self._nvidia()
            # embed_documents tags input_type=passage; embed_query tags =query
            return self._doc.embed_query(text) if is_query else self._doc.embed_documents([text])[0]
        # default: google
        if is_query:
            if self._query is None:
                self._query = self._google("RETRIEVAL_QUERY")
            return self._query.embed_query(text)
        if self._doc is None:
            self._doc = self._google("RETRIEVAL_DOCUMENT")
        return self._doc.embed_query(text)


def _current_embedding_client() -> _EmbeddingClient:
    """Build (cached) the embedding client for the currently-configured provider."""
    from app.storage import load_prefs
    prefs = load_prefs()
    provider = (prefs.get("embedding_provider") or "google").lower()
    if provider == "nvidia":
        model_name = prefs.get("nvidia_embedding_model", "nvidia/nv-embedqa-e5-v5")
    else:
        provider = "google"
        model_name = prefs.get("embedding_model", "gemini-embedding-001")
    key = f"{provider}:{model_name}"
    if key not in _EMBEDDING_MODEL_CACHE:
        _EMBEDDING_MODEL_CACHE[key] = _EmbeddingClient(provider, model_name)
    return _EMBEDDING_MODEL_CACHE[key]


# ── Per-space embedding-identity registry ────────────────────────────
def _load_rag_meta() -> dict:
    try:
        with open(_RAG_META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_rag_meta(meta: dict) -> None:
    try:
        with open(_RAG_META_FILE, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    except Exception as e:
        print(f"Failed to persist rag_meta: {e}")


def _set_space_embedding_id(space_id: str, embedding_id: str) -> None:
    meta = _load_rag_meta()
    meta[space_id] = embedding_id
    _save_rag_meta(meta)


def space_needs_reindex(space_id: str) -> bool:
    """True when a space has vectors that were embedded with a different model
    than the one currently configured (so retrieval would silently return junk).

    Legacy spaces created before the identity registry existed have no stamp; if
    they hold vectors we treat them as needing re-index too, since their model is
    unknown and almost certainly differs from the current default."""
    try:
        if qdrant.count(space_id).count == 0:
            return False
    except Exception:
        return False  # collection doesn't exist yet — nothing to reindex
    stored = _load_rag_meta().get(space_id)
    return stored != _current_embedding_client().id


# Backwards-compatible shim: some callers may still import get_embeddings.
def get_embeddings(task_type: str = "RETRIEVAL_DOCUMENT"):
    return _current_embedding_client()

def ensure_collection(collection_name: str, vector_size: int):
    collections = qdrant.get_collections().collections
    exists = any(c.name == collection_name for c in collections)
    
    if exists:
        # Check if dimensions match
        info = qdrant.get_collection(collection_name)
        current_dim = info.config.params.vectors.size
        if current_dim != vector_size:
            print(f"Dimension mismatch for {collection_name}: {current_dim} vs {vector_size}. Recreating...")
            qdrant.delete_collection(collection_name)
            exists = False
            
    if not exists:
        qdrant.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )

def process_file_to_rag(file_path: str, space_id: str, file_id: str):
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext == '.pdf':
        loader = PyPDFLoader(file_path)
    elif ext in ['.docx', '.doc']:
        loader = Docx2txtLoader(file_path)
    elif ext in ['.txt', '.md', '.csv']:
        loader = TextLoader(file_path, encoding='utf-8')
    else:
        raise ValueError(f"Unsupported file format: {ext}")
        
    docs = loader.load()
    
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=150,
        add_start_index=True,
    )
    
    chunks = splitter.split_documents(docs)
    if not chunks:
        return

    embedder = _current_embedding_client()
    # Embed individually — some models collapse batched embed_documents() into
    # fewer vectors, so we embed one-by-one for a stable 1:1 chunk→vector mapping.
    vectors = [embedder.embed_one(c.page_content, is_query=False) for c in chunks]
    if not vectors:
        return

    # Create the collection BEFORE upserting (fixes the empty-collection race).
    ensure_collection(space_id, len(vectors[0]))

    points = []
    for chunk, vector in zip(chunks, vectors):
        points.append(PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={"file_id": file_id, "text": chunk.page_content, "metadata": chunk.metadata},
        ))

    qdrant.upsert(collection_name=space_id, points=points)
    # Record which embedding model these vectors belong to.
    _set_space_embedding_id(space_id, embedder.id)

def query_space(space_id: str, query: str, top_k: int = 5,
                score_threshold: float = 0.2) -> list[str]:
    # NOTE on threshold: with gemini-embedding-001, relevant paraphrased queries
    # score ~0.35–0.45 while unrelated content sits near ~0.04, so 0.2 cleanly
    # separates signal from noise. The old 0.4 default silently dropped genuine
    # near-misses (e.g. 0.388) and returned nothing.
    try:
        # Guard: if the space was embedded with a different model, its vectors are
        # incompatible with the current query embedding (often a different vector
        # dimension → a hard 400 from Qdrant). Short-circuit with a clear warning
        # instead of firing a doomed query.
        if space_needs_reindex(space_id):
            print(f"[RAG] Space '{space_id}' was embedded with a different model "
                  f"({_load_rag_meta().get(space_id)}) than the active one "
                  f"({_current_embedding_client().id}); it needs re-indexing. "
                  f"Skipping retrieval until then.")
            return []

        query_vector = _current_embedding_client().embed_one(query, is_query=True)

        results = qdrant.query_points(
            collection_name=space_id,
            query=query_vector,
            limit=top_k,
            score_threshold=score_threshold,
        )

        # Return only relevant chunks, truncated to avoid dumping entire docs
        chunks = []
        for hit in results.points:
            if not hit.payload:
                continue
            text = hit.payload.get("text", "").strip()
            if text:
                if len(text) > 600:
                    text = text[:600] + "…"
                chunks.append(text)
        if not chunks:
            print(f"[RAG] query_space('{space_id}') returned 0 chunks "
                  f"(threshold={score_threshold}). Top raw scores may be below threshold.")
        return chunks
    except Exception as e:
        print(f"Error querying Qdrant: {e}")
        return []


def reindex_space(space_id: str) -> dict:
    """Rebuild a space's vectors from its retained source files using the current
    embedding model. Drops the old (incompatible) collection first. Returns a
    small summary. Files live in SPACES_DIR/<space_id>/<file_id><ext>."""
    space_dir = os.path.join(SPACES_DIR, space_id)
    if not os.path.isdir(space_dir):
        raise ValueError(f"No stored files for space '{space_id}' — cannot re-index")

    # Drop the stale collection and its identity stamp so it is rebuilt cleanly.
    try:
        qdrant.delete_collection(space_id)
    except Exception:
        pass
    meta = _load_rag_meta()
    meta.pop(space_id, None)
    _save_rag_meta(meta)

    processed, errors = [], []
    for fname in sorted(os.listdir(space_dir)):
        fpath = os.path.join(space_dir, fname)
        if not os.path.isfile(fpath):
            continue
        file_id = os.path.splitext(fname)[0]  # "file_1b8478a7"
        try:
            process_file_to_rag(fpath, space_id, file_id)
            processed.append(fname)
        except Exception as e:
            errors.append({"file": fname, "error": str(e)})

    count = 0
    try:
        count = qdrant.count(space_id).count
    except Exception:
        pass
    return {"space_id": space_id, "reindexed_files": processed,
            "errors": errors, "vector_count": count,
            "embedding_id": _current_embedding_client().id}

def delete_file_from_rag(space_id: str, file_id: str):
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    try:
        qdrant.delete(
            collection_name=space_id,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="file_id",
                        match=MatchValue(value=file_id)
                    )
                ]
            )
        )
    except Exception as e:
        print(f"Error deleting from Qdrant: {e}")

def get_space_tool(space_id: str):
    from langchain_core.tools import tool
    
    @tool
    def search_knowledge_base(query: str) -> str:
        """Search the active Knowledge Space for relevant information to answer the user's question using semantic search. Returns only the most relevant excerpts."""
        results = query_space(space_id, query)
        if not results:
            return "No relevant information found in the knowledge base."
        # Provide numbered excerpts for clarity
        formatted = []
        for i, r in enumerate(results, 1):
            formatted.append(f"[Excerpt {i}]\n{r}")
        return "\n\n".join(formatted)
        
    return search_knowledge_base
