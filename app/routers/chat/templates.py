"""Available-tools discovery and CRUD for custom worker agent templates."""
import json

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api")


@router.get("/workflow/tools")
async def get_available_tools():
    """Retrieve all available system tools dynamically from the backend."""
    tools = [
        {"id": "web_search", "name": "Web Search", "description": "Search the web using Tavily API for general public information."},
        {"id": "scrape_page", "name": "Scrape Page", "description": "Scrape raw text and table contents from web page URLs."},
        {"id": "fetch_url", "name": "Fetch URL", "description": "Read content directly from standard HTTP URLs."},
        {"id": "memory_search", "name": "Memory Search", "description": "Semantic search inside the active memory space vector databases."},
        {"id": "artifact_read", "name": "Artifact Read", "description": "Read content of existing file drafts in the working directories."},
        {"id": "file_write", "name": "File Write", "description": "Write final output contents or chapters to disk files."},
        {"id": "directory_create", "name": "Directory Create", "description": "Create isolated working folders for data compilation."},
        {"id": "pdf_export", "name": "PDF Export", "description": "Compile markdown contents into ReportLab-styled high-fidelity PDF booklets."},
        {"id": "docx_export", "name": "DOCX Export", "description": "Render structured markdown text into styled Word DOCX documents."},
        {"id": "pptx_export", "name": "PPTX Export", "description": "Compile markdowns into curated, minimalist PowerPoint presentation slides."},
        {"id": "excel_export", "name": "Excel Export", "description": "Convert tabular JSON or CSV dataset tables into styled Excel spreadsheets."},
        {"id": "send_email", "name": "Send Email", "description": "Send summary files, PDFs, or compiled doc suites to the user's inbox."},
        {"id": "browser_automation", "name": "Browser Automation", "description": "Execute browser automation tasks and navigation flows."},
        {"id": "shell_exec", "name": "Shell Exec", "description": "Execute sandboxed CLI calculations or python commands."}
    ]

    try:
        from app.mcp import get_pool_tools
        pool_tools, _, _, _ = get_pool_tools()
        for t in pool_tools:
            tname = t["function"]["name"]
            tdesc = t["function"].get("description") or "MCP-registered external service tool."
            tools.append({
                "id": tname,
                "name": f"MCP: {tname.replace('_', ' ').title()}",
                "description": tdesc
            })
    except Exception as e:
        print(f"[Dynamic Tools] Could not load MCP pool tools: {e}")

    return tools


@router.get("/workflow/templates")
async def get_agent_templates():
    """Retrieve all customized worker templates."""
    from app.workflow import load_templates
    return load_templates()

@router.post("/workflow/templates")
async def create_agent_template(payload: dict):
    """Add a new custom worker template dynamically."""
    from app.workflow import load_templates, save_templates
    name = payload.get("name")
    worker_id = payload.get("id")
    purpose = payload.get("purpose")
    system_prompt = payload.get("system_prompt_template")
    allowed_tools = payload.get("allowed_tools", [])

    if not name or not worker_id or not purpose or not system_prompt:
        raise HTTPException(status_code=400, detail="Missing required template properties")

    templates = load_templates()
    templates[worker_id] = {
        "name": name,
        "purpose": purpose,
        "allowed_tools": allowed_tools,
        "system_prompt_template": system_prompt,
        "expected_output_schema": payload.get("expected_output_schema", {"result": "string"}),
        "timeout": int(payload.get("timeout", 60)),
        "retry_limit": int(payload.get("retry_limit", 2))
    }
    save_templates(templates)
    return {"status": "success", "template_id": worker_id}

@router.delete("/workflow/templates/{template_id}")
async def delete_agent_template(template_id: str):
    """Delete a custom worker template."""
    from app.workflow import load_templates, save_templates
    templates = load_templates()
    if template_id not in templates:
        raise HTTPException(status_code=404, detail="Template not found")
    del templates[template_id]
    save_templates(templates)
    return {"status": "deleted"}

@router.post("/workflow/templates/generate")
async def ai_generate_template(payload: dict):
    """Use the active LLM to generate a worker template from a natural language description."""
    description = payload.get("description", "")
    if not description:
        raise HTTPException(status_code=400, detail="Description is required")

    from app.llm import get_llm
    from app.storage import load_prefs
    from langchain_core.messages import HumanMessage

    prefs = load_prefs()
    llm = get_llm(prefs, streaming=False)

    prompt = (
        "Generate a JSON worker agent template for a multi-agent workflow system.\n"
        f"User description: {description}\n\n"
        "Available tools: web_search, fetch_url, memory_search, artifact_read, file_write, pdf_export, send_email, browser_automation, shell_exec\n\n"
        "Respond ONLY with valid JSON (no markdown fencing):\n"
        '{"id": "snake_case_id", "name": "Human Name", "purpose": "one-liner", '
        '"system_prompt_template": "You are... Task-specific context: {task_description}", '
        '"allowed_tools": ["tool1"], "expected_output_schema": {"result": "string"}, "timeout": 90, "retry_limit": 2}'
    )

    try:
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        raw = resp.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()
        return json.loads(raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI generation failed: {str(e)}")
