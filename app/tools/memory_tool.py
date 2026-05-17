from langchain_core.tools import tool
from app.memory import save_memory

def get_memory_tool(character_id: str):
    """Factory to create a memory tool bound to a specific character."""
    
    @tool
    def save_memory_tool(fact: str) -> str:
        """Explicitly save an important fact about the user to your long-term memory.
        Use this when the user shares something they want you to remember forever (e.g. preferences, identity, past events).
        """
        save_memory(character_id, fact)
        return f"Fact remembered: {fact}"
        
    return save_memory_tool
