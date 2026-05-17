import uuid
import datetime
import json
from qdrant_client.models import PointStruct, Filter, FieldCondition, MatchValue
from app.rag import qdrant, get_embeddings, ensure_collection
from app.llm import get_llm
from langchain_core.messages import HumanMessage, SystemMessage

MEMORY_COLLECTION = "user_memories"

def save_memory(user_id: str, text: str, metadata: dict = None):
    """Save a concise memory atom to Qdrant."""
    embeddings_model = get_embeddings(task_type="RETRIEVAL_DOCUMENT")
    vector = embeddings_model.embed_query(text)
    
    ensure_collection(MEMORY_COLLECTION, len(vector))
    
    point_id = str(uuid.uuid4())
    payload = {
        "user_id": user_id,
        "text": text,
        "timestamp": datetime.datetime.now().timestamp(),
        **(metadata or {})
    }
    
    qdrant.upsert(
        collection_name=MEMORY_COLLECTION,
        points=[PointStruct(id=point_id, vector=vector, payload=payload)]
    )

def search_memories(user_id: str, query: str, limit: int = 5):
    """Semantic search for relevant memories."""
    try:
        embeddings_model = get_embeddings(task_type="RETRIEVAL_QUERY")
        query_vector = embeddings_model.embed_query(query)
        
        results = qdrant.query_points(
            collection_name=MEMORY_COLLECTION,
            query=query_vector,
            query_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
            limit=limit,
            score_threshold=0.35
        )
        
        return [hit.payload.get("text") for hit in results.points if hit.payload]
    except Exception as e:
        print(f"Memory search error: {e}")
        return []

async def summarize_conversation(messages: list, prefs: dict = None):
    """Extract key facts and preferences from chat history."""
    if not messages or len(messages) < 2:
        return []
        
    llm = get_llm(prefs or {})
    
    chat_text = ""
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        chat_text += f"{role.upper()}: {content}\n\n"
        
    prompt = f"""
    Analyze the following conversation and extract persistent facts about the user's preferences, identity, or important shared information.
    Format each fact as a single, concise sentence. 
    Focus ONLY on information that would be useful for future conversations.
    Example facts:
    - User is allergic to peanuts.
    - User's favorite color is midnight blue.
    - User is a senior developer at Google.
    - User has a dog named Max.
    
    CONVERSATION:
    {chat_text}
    
    Return the facts as a JSON list of strings. If no important facts were shared, return [].
    """ 
    
    try:
        response = await llm.ainvoke([
            SystemMessage(content="You are a memory extraction engine. Return ONLY a JSON list of strings."),
            HumanMessage(content=prompt)
        ])
        
        # Simple JSON extraction
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
            
        facts = json.loads(content)
        return facts if isinstance(facts, list) else []
    except Exception as e:
        print(f"Summarization error: {e}")
        return []
