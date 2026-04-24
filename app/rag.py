import os
from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from app.config import GOOGLE_API_KEY
import uuid

# Initialize Qdrant Client (pointing to the local Docker container)
qdrant = QdrantClient(url="http://localhost:6333")

def get_embeddings(task_type: str = "RETRIEVAL_DOCUMENT"):
    """Get embedding model configured for a specific task.
    
    task_type: 
      - "RETRIEVAL_DOCUMENT" when embedding documents for storage
      - "RETRIEVAL_QUERY" when embedding user queries for search
    """
    if not GOOGLE_API_KEY:
        raise ValueError("GOOGLE_API_KEY is required for RAG embeddings")
    from app.storage import load_prefs
    prefs = load_prefs()
    emb_model = prefs.get("embedding_model", "models/gemini-embedding-2")
    
    return GoogleGenerativeAIEmbeddings(
        model=emb_model, 
        google_api_key=GOOGLE_API_KEY,
        task_type=task_type,
    )

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
        chunk_size=2000,
        chunk_overlap=400,
        add_start_index=True,
    )
    
    chunks = splitter.split_documents(docs)
    if not chunks:
        return
        
    # Use RETRIEVAL_DOCUMENT task type for storing documents
    embeddings_model = get_embeddings(task_type="RETRIEVAL_DOCUMENT")
    texts = [c.page_content for c in chunks]
    
    # Embed individually — gemini-embedding-2 can collapse batched
    # embed_documents() into fewer vectors, so we embed one-by-one.
    vectors = []
    for t in texts:
        v = embeddings_model.embed_query(t)
        vectors.append(v)
    
    if vectors:
        vector_size = len(vectors[0])
        ensure_collection(space_id, vector_size)
    
    points = []
    for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
        point_id = str(uuid.uuid4())
        payload = {
            "file_id": file_id,
            "text": chunk.page_content,
            "metadata": chunk.metadata
        }
        points.append(PointStruct(id=point_id, vector=vector, payload=payload))
        
    # Upload in batches
    qdrant.upsert(
        collection_name=space_id,
        points=points
    )

def query_space(space_id: str, query: str, top_k: int = 8) -> list[str]:
    try:
        # Use RETRIEVAL_QUERY task type for searching
        embeddings_model = get_embeddings(task_type="RETRIEVAL_QUERY")
        query_vector = embeddings_model.embed_query(query)
        
        results = qdrant.query_points(
            collection_name=space_id,
            query=query_vector,
            limit=top_k
        )
        
        # Return the retrieved text chunks
        return [hit.payload.get("text", "") for hit in results.points if hit.payload]
    except Exception as e:
        print(f"Error querying Qdrant: {e}")
        return []

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
        """Search the active Knowledge Space for relevant information to answer the user's question using semantic search."""
        results = query_space(space_id, query)
        if not results:
            return "No relevant information found in the knowledge base."
        return "\n\n---\n\n".join(results)
        
    return search_knowledge_base
