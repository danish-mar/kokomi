from langchain_core.tools import tool
from app.memory import save_memory

def get_memory_tool(character_id: str):
    """Factory to create a memory tool bound to a specific character."""
    
    @tool
    def save_memory_tool(fact: str, importance: float = 4.0) -> str:
        """Explicitly save an important fact about the user to your long-term memory.
        Use this when the user shares something they want you to remember forever (e.g. preferences, identity, past events).
        
        Args:
            fact: The fact to remember, as a concise sentence.
            importance: How important this fact is on a 1.0-5.0 scale.
                5.0 = Critical (allergies, identity, safety)
                4.0 = Important (strong preferences, relationships)
                3.0 = Notable (casual preferences, current projects)
                2.0 = Minor (passing mentions worth noting)
        """
        save_memory(character_id, fact, metadata={"source": "tool"}, importance=importance)
        return f"Remembered [{importance}★]: {fact}"
        
    return save_memory_tool
