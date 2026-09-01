"""The `load_skill` tool — the on-demand half of progressive disclosure.

The system prompt carries only skill names and summaries (see
`app.skills.skills_prompt_block`); this is how the model pulls in the actual
instructions when it decides one is relevant.
"""
import asyncio

from langchain_core.tools import tool

from app.skills import get_skill, load_skills, MAX_BODY_CHARS


def get_skill_tool():
    """Returns the load_skill tool, or None when no skills are installed —
    there's no point advertising a tool that can only ever fail."""
    if not load_skills():
        return None

    @tool
    async def load_skill(name: str) -> str:
        """Load the full instructions for one of your available skills.

        Call this BEFORE starting work that matches a skill listed in your
        prompt, then follow the instructions it returns.

        Args:
            name: The skill's name, exactly as listed in your available skills.
        """
        # Reading from disk is blocking; on the single event loop that would
        # stall every other request for the duration.
        skill = await asyncio.to_thread(get_skill, name)
        if not skill:
            available = ", ".join(s["name"] for s in load_skills()) or "none"
            return f"No skill named '{name}'. Available skills: {available}."
        if not skill.get("enabled", True):
            return f"The skill '{skill['name']}' is currently disabled."

        body = (skill.get("body") or "").strip()
        if not body:
            return f"The skill '{skill['name']}' has no instructions in it yet."
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + "\n\n[...skill truncated...]"

        return f"# Skill: {skill['name']}\n\n{body}"

    return load_skill
