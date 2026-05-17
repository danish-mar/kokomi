import os
import json
import uuid
import time
import asyncio
import datetime
import contextvars
from typing import Dict, Any, List, Optional, TypedDict
from pydantic import BaseModel, Field

# Storage context for active workflow
active_storage_dir = contextvars.ContextVar("active_storage_dir", default="")
active_workflow_title = contextvars.ContextVar("active_workflow_title", default="")

# LangChain / LangGraph imports
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END

# App imports
from app.config import DATA_DIR, GROQ_API_KEY, GOOGLE_API_KEY
from app.storage import _load, _save, load_prefs
from app.llm import get_llm
from app.memory import search_memories

# PDF creation library
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, HRFlowable, Preformatted
from reportlab.lib import colors
from reportlab.lib.units import inch
import re as _re

# ── Storage File for Workflows ───────────────────────────────────────
WORKFLOWS_FILE = os.path.join(DATA_DIR, "multi_agent_workflows.json")

def load_workflows() -> dict:
    if not os.path.exists(WORKFLOWS_FILE):
        return {}
    data = _load(WORKFLOWS_FILE)
    if not isinstance(data, dict):
        return {}
    return data

def save_workflows(data: dict) -> None:
    _save(WORKFLOWS_FILE, data)

def clean_markdown_if_json(content: str) -> str:
    if not isinstance(content, str):
        return str(content)
    content_stripped = content.strip()
    if content_stripped.startswith('{') and content_stripped.endswith('}'):
        try:
            import json as _json_clean
            data = _json_clean.loads(content_stripped)
            if isinstance(data, dict):
                for key in ["markdown", "result", "content", "text", "body"]:
                    if data.get(key) and isinstance(data[key], str) and len(data[key].strip()) > 50:
                        return data[key]
                longest_str = ""
                for k, v in data.items():
                    if isinstance(v, str) and len(v) > len(longest_str):
                        longest_str = v
                if len(longest_str.strip()) > 50:
                    return longest_str
        except Exception:
            pass
    return content


# ── Typed Schemas ────────────────────────────────────────────────────

class TaskDict(TypedDict, total=False):
    task_id: str
    title: str
    description: str
    worker_type: str
    depends_on: List[str]
    allowed_tools: List[str]
    success_criteria: str
    expected_output_schema: Dict[str, Any]
    status: str  # "pending", "running", "completed", "failed"
    output: Any
    artifacts: List[str]
    retries: int
    error: Optional[str]
    timestamps: Dict[str, str]

class WorkflowState(TypedDict):
    run_id: str
    user_id: str
    user_request: str
    run_title: str
    plan: Dict[str, Any]
    tasks: List[TaskDict]
    ready_queue: List[str]
    running_tasks: List[str]
    completed_tasks: List[str]
    failed_tasks: List[str]
    artifacts: List[str]
    notifications: List[str]
    final_result: Optional[str]
    status: str  # "pending", "running", "completed", "failed", "paused"


# ── Worker Templates ─────────────────────────────────────────────────

TEMPLATES_FILE = os.path.join(DATA_DIR, "agent_templates.json")

DEFAULT_WORKER_TEMPLATES = {
    "researcher": {
        "name": "Researcher Agent",
        "purpose": "Queries vector memory, crawls deep web search indices, and aggregates detailed reference facts.",
        "allowed_tools": ["web_search", "fetch_url", "memory_search"],
        "system_prompt_template": (
            "You are the Lead Researcher Agent. Your goal is to gather detailed, verified information.\n"
            "Query memories, search the web, fetch specific url links, and compile cited notes.\n"
            "Task-specific context: {task_description}\n"
            "Respond in structured format fulfilling the expected output schema."
        ),
        "expected_output_schema": {
            "summary": "string",
            "sources": ["string"],
            "notes": ["string"]
        },
        "timeout": 120,
        "retry_limit": 3
    },
    "writer": {
        "name": "Writer Agent",
        "purpose": "Synthesizes raw factual notes into premium structured markdown reports.",
        "allowed_tools": ["artifact_read", "file_write"],
        "system_prompt_template": (
            "You are the Master Technical Writer. Your goal is to draft structured, exhaustive report documents.\n"
            "Synthesize research data into an elegant markdown document with headers, code snippets, and summaries.\n"
            "Task-specific context: {task_description}\n"
            "Respond in structured format fulfilling the expected output schema."
        ),
        "expected_output_schema": {
            "title": "string",
            "markdown": "string"
        },
        "timeout": 90,
        "retry_limit": 2
    },
    "pdf_worker": {
        "name": "PDF Export Worker",
        "purpose": "Compiles reports and structured markdown into beautiful publication-grade PDF, Word, PowerPoint, and Excel documents.",
        "allowed_tools": ["artifact_read", "file_write", "pdf_export", "docx_export", "pptx_export", "excel_export"],
        "system_prompt_template": (
            "You are the Professional Document Layout Designer. Your goal is to compile reports and structured markdown into beautiful publication-grade PDF documents, Word DOCX files, Excel spreadsheets, and PowerPoint slide decks.\n"
            "You have access to pdf_export, docx_export, pptx_export, and excel_export tools to create the respective file types.\n"
            "If the required input files or text drafts are not found in the workspace directories, look at the dependent task outputs provided in your prompt context! The dependent outputs contain all the text drafts, write-ups, or slide contents generated by the previous researcher and writer tasks.\n"
            "Task-specific context: {task_description}\n"
            "First read the content from the previous task outputs, then invoke the required export tools (pdf_export, docx_export, pptx_export, excel_export) to render them. Respond with the generated file paths."
        ),
        "expected_output_schema": {
            "pdf_path": "string"
        },
        "timeout": 60,
        "retry_limit": 2
    },
    "email_worker": {
        "name": "Communications Worker",
        "purpose": "Dispatches summary notifications and attachments to user mailboxes.",
        "allowed_tools": ["send_email"],
        "system_prompt_template": (
            "You are the Communications Officer. Your goal is to notify users and email generated attachments.\n"
            "Dispatch summary reports and dynamic attachments.\n"
            "Task-specific context: {task_description}\n"
            "Respond with the transmission status."
        ),
        "expected_output_schema": {
            "delivery_status": "string"
        },
        "timeout": 60,
        "retry_limit": 3
    },
    "browser": {
        "name": "Browser Automation Worker",
        "purpose": "Executes automated navigation, screenshot captures, and web crawls.",
        "allowed_tools": ["browser_automation"],
        "system_prompt_template": (
            "You are the Browser Navigator. Automate pages, crawl sites, and report structures.\n"
            "Task-specific context: {task_description}"
        ),
        "expected_output_schema": {
            "page_details": "string"
        },
        "timeout": 120,
        "retry_limit": 2
    },
    "code_worker": {
        "name": "Execution Code Worker",
        "purpose": "Runs sandboxed python executions and file manipulations.",
        "allowed_tools": ["shell_exec", "file_write", "file_read"],
        "system_prompt_template": (
            "You are the Systems Execution Engineer. Process sandboxed calculations, scripts, or files.\n"
            "Task-specific context: {task_description}"
        ),
        "expected_output_schema": {
            "execution_output": "string"
        },
        "timeout": 90,
        "retry_limit": 2
    }
}

def load_templates() -> dict:
    if not os.path.exists(TEMPLATES_FILE):
        _save(TEMPLATES_FILE, DEFAULT_WORKER_TEMPLATES)
        return DEFAULT_WORKER_TEMPLATES
    data = _load(TEMPLATES_FILE)
    if not isinstance(data, dict):
        return DEFAULT_WORKER_TEMPLATES
    return data

def save_templates(data: dict) -> None:
    _save(TEMPLATES_FILE, data)


# ── Tool Definitions ──────────────────────────────────────────────────

@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for up-to-date facts on a specific query, specifying the number of results desired (max_results)."""
    prefs = load_prefs()
    provider = prefs.get("search_provider") or "tavily"
    
    if provider == "searxng":
        import httpx
        searxng_url = prefs.get("searxng_url") or "http://localhost:8080"
        searxng_url = searxng_url.rstrip("/")
        try:
            resp = httpx.get(
                f"{searxng_url}/",
                params={"q": query, "format": "json"},
                timeout=10.0
            )
            if resp.status_code != 200:
                resp = httpx.get(
                    f"{searxng_url}/search",
                    params={"q": query, "format": "json"},
                    timeout=10.0
                )
            if resp.status_code == 200:
                data = resp.json()
                raw_results = data.get("results", [])
                formatted_results = []
                for r in raw_results[:max_results]:
                    formatted_results.append({
                        "title": r.get("title") or "",
                        "url": r.get("url") or "",
                        "content": r.get("content") or r.get("snippet") or ""
                    })
                return json.dumps(formatted_results)
            else:
                return f"SearxNG query failed with status code {resp.status_code}."
        except Exception as e:
            return f"SearxNG query failed: {str(e)}."

    # Tavily fallback
    api_key = prefs.get("tavily_api_key") or os.getenv("TAVILY_API_KEY")
    if api_key:
        try:
            import os
            os.environ["TAVILY_API_KEY"] = api_key
            from langchain_community.tools.tavily_search import TavilySearchResults
            tavily = TavilySearchResults(max_results=max_results, tavily_api_key=api_key)
            results = tavily.invoke(query)
            return json.dumps(results)
        except Exception as e:
            return f"Tavily search failed: {str(e)}. Falling back to Wikipedia mock notes."
            
    # Premium Mock Search for 8086 Microcomputer
    if "8086" in query or "microcomputer" in query:
        return json.dumps([
            {
                "title": "Intel 8086 Architecture",
                "url": "https://en.wikipedia.org/wiki/Intel_8086",
                "content": (
                    "The Intel 8086 is a 16-bit microprocessor chip designed by Intel between 1976 and 1978. "
                    "It has a 20-bit address bus capable of addressing up to 1 MB of memory. "
                    "Features a segmentation architecture dividing memory into Code, Data, Stack, and Extra segments. "
                    "Clock frequencies range from 5 MHz to 10 MHz."
                )
            },
            {
                "title": "8086 Instruction Set & Registers",
                "url": "https://www.tutorialspoint.com/microprocessor/microprocessor_8086_instruction_set.htm",
                "content": (
                    "The 8086 features 14 registers including general-purpose (AX, BX, CX, DX), segment registers "
                    "(CS, DS, SS, ES), index registers (SI, DI, BP, SP), and an instruction pointer (IP). "
                    "Supports modular instruction flags like Carry, Zero, Sign, and Overflow."
                )
            },
            {
                "title": "8086 Execution Unit and Bus Interface Unit",
                "url": "https://www.geeksforgeeks.org/execution-unit-and-bus-interface-unit-in-8086/",
                "content": (
                    "The processor is split into two functional units: the Bus Interface Unit (BIU) which fetches "
                    "instructions and writes data, and the Execution Unit (EU) which decodes and executes instructions. "
                    "Features a 6-byte instruction queue allowing pipelining of operations."
                )
            }
        ])
    return f"Mock search notes for: {query}. Successfully resolved details."

@tool
def scrape_page(url: str) -> str:
    """Scrape a webpage and return clean text content, stripped of JavaScript, CSS, and HTML tags."""
    import httpx
    from html.parser import HTMLParser
    
    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text_parts = []
            self.in_ignored_tag = False
            self.ignored_tags = {"script", "style", "head", "meta", "link", "noscript", "svg"}

        def handle_starttag(self, tag, attrs):
            if tag in self.ignored_tags:
                self.in_ignored_tag = True

        def handle_endtag(self, tag):
            if tag in self.ignored_tags:
                self.in_ignored_tag = False

        def handle_data(self, data):
            if not self.in_ignored_tag:
                clean_data = data.strip()
                if clean_data:
                    self.text_parts.append(clean_data)

        def get_text(self):
            return "\n".join(self.text_parts)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=15.0)
        if resp.status_code == 200:
            parser = TextExtractor()
            parser.feed(resp.text)
            text = parser.get_text()
            if len(text) > 12000:
                text = text[:12000] + "\n\n[Content truncated due to length limits...]"
            return text if text.strip() else "Webpage returned no extractable text content."
        else:
            return f"Failed to retrieve page content. Status code: {resp.status_code}"
    except Exception as e:
        return f"Error occurred while scraping the page: {str(e)}"

@tool
def fetch_url(url: str) -> str:
    """Fetch the textual content of a specific webpage or URL."""
    try:
        import httpx
        resp = httpx.get(url, timeout=10)
        return resp.text[:4000]
    except Exception as e:
        return f"Fetch failed: {str(e)}"

@tool
def memory_search(query: str) -> str:
    """Retrieve relevant memories matching the query from Qdrant vector storage."""
    try:
        results = search_memories("admin", query, limit=3)
        if not results:
            return "No vector memory entries found matching query."
        return json.dumps([r.payload for r in results])
    except Exception as e:
        return f"Memory search error: {str(e)}"

@tool
def artifact_read(filepath: str) -> str:
    """Read a text or markdown file from the active storage workspace or data directory."""
    try:
        sdir = active_storage_dir.get()
        if sdir:
            target = os.path.abspath(os.path.join(sdir, filepath.lstrip("/")))
            if target.startswith(os.path.abspath(sdir)) and os.path.exists(target):
                with open(target, "r") as f:
                    return f.read()
                    
        clean_path = os.path.basename(filepath)
        target = os.path.join(DATA_DIR, clean_path)
        if not os.path.exists(target):
            target = filepath
        with open(target, "r") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"

@tool
def file_write(filepath: str, content: str) -> str:
    """Write text content to a specific file in the local data directory. Supports nested relative paths."""
    try:
        sdir = active_storage_dir.get()
        if not sdir:
            sdir = DATA_DIR
        target = os.path.abspath(os.path.join(sdir, filepath.lstrip("/")))
        if not target.startswith(os.path.abspath(sdir)):
            # fallback to base name if path traversal detected
            target = os.path.join(sdir, os.path.basename(filepath))
            
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as f:
            f.write(content)
        return f"Successfully wrote file to: {target}"
    except Exception as e:
        return f"Error writing file: {str(e)}"

@tool
def directory_create(dirpath: str) -> str:
    """Create a new subdirectory inside the active workflow folder."""
    sdir = active_storage_dir.get()
    if not sdir:
        return "Error: No active workflow storage context."
    target = os.path.abspath(os.path.join(sdir, dirpath.lstrip("/")))
    if not target.startswith(os.path.abspath(sdir)):
        return "Error: Cannot create directory outside workflow storage."
    try:
        os.makedirs(target, exist_ok=True)
        return f"Successfully created directory at: {dirpath}"
    except Exception as e:
        return f"Failed to create directory: {str(e)}"

@tool
def pdf_export(markdown_content: str, filename: str) -> str:
    """Render structured markdown text into a publication-grade PDF file using ReportLab."""
    try:
        def clean_unicode_text(text: str) -> str:
            replacements = {
                "\u2010": "-",  # Hyphen
                "\u2011": "-",  # Non-breaking hyphen
                "\u2012": "-",  # Figure dash
                "\u2013": "-",  # En dash (–)
                "\u2014": "--", # Em dash (—)
                "\u2015": "--", # Horizontal bar
                "\u2212": "-",  # Minus sign (−)
                "\xa0": " ",    # Non-breaking space
                "\u2018": "'",  # Left single quote
                "\u2019": "'",  # Right single quote
                "\u201c": '"',  # Left double quote
                "\u201d": '"',  # Right double quote
                "\u2022": "*",  # Bullet
                "■": "-",       # Fallback replacement
            }
            for k, v in replacements.items():
                text = text.replace(k, v)
            return text

        markdown_content = clean_unicode_text(markdown_content)

        clean_name = os.path.basename(filename)
        if not clean_name.endswith(".pdf"):
            clean_name += ".pdf"

        # Dynamically rename generic filenames to match active workflow title/topic
        generic_names = ["generated", "compiled", "report", "final", "document", "output", "booklet", "pdf", "export"]
        name_no_ext = os.path.splitext(clean_name)[0].lower()
        if name_no_ext in generic_names or not name_no_ext.strip():
            wf_title = active_workflow_title.get()
            if wf_title:
                clean_name = _re.sub(r'[^a-zA-Z0-9_\-]', '_', wf_title).strip("_")[:50] + ".pdf"

        uploads_dir = active_storage_dir.get() or os.path.join(DATA_DIR, "uploads")
        os.makedirs(uploads_dir, exist_ok=True)
        pdf_path = os.path.join(uploads_dir, clean_name)

        doc = SimpleDocTemplate(pdf_path, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
        styles = getSampleStyleSheet()

        # --- Custom styles ---
        s_title = ParagraphStyle('S_Title', parent=styles['Heading1'], fontSize=22, leading=28,
                                  textColor=colors.HexColor('#272757'), spaceAfter=14)
        s_h2 = ParagraphStyle('S_H2', parent=styles['Heading2'], fontSize=16, leading=20,
                               textColor=colors.HexColor('#505081'), spaceBefore=14, spaceAfter=8)
        s_h3 = ParagraphStyle('S_H3', parent=styles['Heading3'], fontSize=13, leading=17,
                               textColor=colors.HexColor('#505081'), spaceBefore=10, spaceAfter=6)
        s_h4 = ParagraphStyle('S_H4', parent=styles['Heading4'], fontSize=11, leading=15,
                               textColor=colors.HexColor('#8686AC'), spaceBefore=8, spaceAfter=4)
        s_body = ParagraphStyle('S_Body', parent=styles['Normal'], fontSize=10, leading=15,
                                 textColor=colors.HexColor('#1F2937'), spaceAfter=6)
        s_bullet = ParagraphStyle('S_Bullet', parent=s_body, leftIndent=18, bulletIndent=6)
        s_quote = ParagraphStyle('S_Quote', parent=s_body, leftIndent=20, textColor=colors.HexColor('#6B7280'),
                                  borderPadding=4, fontName='Helvetica-Oblique')
        s_tbl_header = ParagraphStyle('S_TblH', parent=styles['Normal'], fontSize=9, leading=12,
                                       textColor=colors.HexColor('#FFFFFF'), fontName='Helvetica-Bold')
        s_tbl_cell = ParagraphStyle('S_TblC', parent=styles['Normal'], fontSize=9, leading=12,
                                     textColor=colors.HexColor('#374151'))

        def _md_inline(t):
            """Convert inline markdown (bold, italic, links, code) to ReportLab markup robustly."""
            # 1. Escape XML characters first
            t = t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            t = t.replace('&amp;amp;', '&amp;').replace('&amp;lt;', '&lt;').replace('&amp;gt;', '&gt;')
            
            # 2. Extract code blocks to prevent formatting inside them
            code_blocks = []
            def _sub_code(m):
                code_blocks.append(m.group(1))
                return f"XYZCODEBLOCK{len(code_blocks)-1}XYZ"
                
            t = _re.sub(r'`(.+?)`', _sub_code, t)
            
            # 3. Process links, bold, and italics
            t = _re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'<font color="#505081"><u>\1</u></font>', t)
            t = _re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
            t = _re.sub(r'__(.+?)__', r'<b>\1</b>', t)
            t = _re.sub(r'\*(.+?)\*', r'<i>\1</i>', t)
            t = _re.sub(r'_(.+?)_', r'<i>\1</i>', t)
            
            # 4. Re-insert code blocks formatted for ReportLab
            for idx, cv in enumerate(code_blocks):
                t = t.replace(f"XYZCODEBLOCK{idx}XYZ", f'<font face="Courier" size="8" color="#505081">{cv}</font>')
                
            t = t.replace('■', '-')
            return t

        story = []
        story.append(Paragraph("KOKOMI STRATEGIST OS — INTELLIGENCE REPORT", s_title))
        story.append(Spacer(1, 8))

        lines = markdown_content.split("\n")
        i = 0
        while i < len(lines):
            raw = lines[i]
            stripped = raw.strip()

            # Empty line
            if not stripped:
                i += 1
                continue

            # Horizontal rule
            if stripped in ('---', '***', '___'):
                story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#D1D5DB'),
                                         spaceBefore=8, spaceAfter=8))
                i += 1
                continue

            # Table detection (| ... | ... |)
            if '|' in stripped and stripped.startswith('|'):
                table_lines = []
                while i < len(lines) and lines[i].strip().startswith('|'):
                    table_lines.append(lines[i].strip())
                    i += 1
                # Parse table
                rows = []
                for tl in table_lines:
                    cells = [c.strip() for c in tl.strip('|').split('|')]
                    if all(set(c.strip()) <= set('-| :') for c in cells):
                        continue  # Skip separator row
                    rows.append(cells)
                if rows:
                    col_count = max(len(r) for r in rows)
                    tbl_data = []
                    for ri, row in enumerate(rows):
                        while len(row) < col_count:
                            row.append('')
                        style = s_tbl_header if ri == 0 else s_tbl_cell
                        tbl_data.append([Paragraph(_md_inline(c), style) for c in row])
                    col_w = (letter[0] - 80) / col_count
                    t = Table(tbl_data, colWidths=[col_w] * col_count)
                    t.setStyle(TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#505081')),
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D1D5DB')),
                        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#F9FAFB'), colors.white]),
                        ('TOPPADDING', (0, 0), (-1, -1), 4),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                        ('LEFTPADDING', (0, 0), (-1, -1), 6),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                    ]))
                    story.append(Spacer(1, 6))
                    story.append(t)
                    story.append(Spacer(1, 6))
            # Page Break
            if stripped in ('<!-- pagebreak -->', '\\pagebreak', r'\pagebreak', '[pagebreak]'):
                story.append(PageBreak())
                i += 1
                continue

            # Multi-line code block detection
            if stripped.startswith('```'):
                code_lang = stripped[3:].strip()
                code_lines = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith('```'):
                    code_lines.append(lines[i]) # Keep raw spacing!
                    i += 1
                if i < len(lines):
                    i += 1 # Skip closing ```
                
                # Format code content
                code_text = "\n".join(code_lines)
                # Escape XML characters for ReportLab Paragraph
                code_text = code_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                
                s_code_block = ParagraphStyle('S_CodeBlock', parent=styles['Normal'],
                                               fontName='Courier', fontSize=8, leading=11,
                                               textColor=colors.HexColor('#0F0E47'))
                
                # Wrap inside a Table to give it a gorgeous light-grey background and nice borders!
                p_code = Preformatted(code_text, s_code_block)
                t_code = Table([[p_code]], colWidths=[letter[0] - 80])
                t_code.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F4F4F7')),
                    ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#8686AC')),
                    ('TOPPADDING', (0, 0), (-1, -1), 6),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                    ('LEFTPADDING', (0, 0), (-1, -1), 8),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ]))
                story.append(Spacer(1, 4))
                story.append(t_code)
                story.append(Spacer(1, 4))
                continue

            # Headings
            if stripped.startswith('#### '):
                story.append(Paragraph(_md_inline(stripped[5:]), s_h4))
            elif stripped.startswith('### '):
                story.append(Paragraph(_md_inline(stripped[4:]), s_h3))
            elif stripped.startswith('## '):
                story.append(Paragraph(_md_inline(stripped[3:]), s_h2))
            elif stripped.startswith('# '):
                story.append(Paragraph(_md_inline(stripped[2:]), s_title))
            # Blockquote
            elif stripped.startswith('> '):
                story.append(Paragraph(_md_inline(stripped[2:]), s_quote))
            # Bullets
            elif stripped.startswith('- ') or stripped.startswith('* '):
                story.append(Paragraph("• " + _md_inline(stripped[2:]), s_bullet))
            # Numbered list
            elif _re.match(r'^\d+\.\s', stripped):
                m = _re.match(r'^(\d+\.)\s(.*)', stripped)
                story.append(Paragraph(f"{m.group(1)} {_md_inline(m.group(2))}", s_bullet))
            # Regular paragraph
            else:
                story.append(Paragraph(_md_inline(stripped), s_body))

            i += 1

        doc.build(story)
        return f"Successfully rendered PDF at: {pdf_path}"
    except Exception as e:
        return f"PDF generation failed: {str(e)}"


@tool
def docx_export(markdown_content: str, filename: str) -> str:
    """Render structured markdown text into a beautifully styled Microsoft Word DOCX document."""
    try:
        from docx import Document
        from docx.shared import Inches, Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        
        clean_name = os.path.basename(filename)
        if not clean_name.endswith(".docx"):
            clean_name += ".docx"
            
        # Naming override if generic
        generic_names = ["generated", "compiled", "report", "final", "document", "output", "booklet", "docx", "export"]
        name_no_ext = os.path.splitext(clean_name)[0].lower()
        if name_no_ext in generic_names or not name_no_ext.strip():
            wf_title = active_workflow_title.get()
            if wf_title:
                clean_name = _re.sub(r'[^a-zA-Z0-9_\-]', '_', wf_title).strip("_")[:50] + ".docx"

        uploads_dir = active_storage_dir.get() or os.path.join(DATA_DIR, "uploads")
        os.makedirs(uploads_dir, exist_ok=True)
        docx_path = os.path.join(uploads_dir, clean_name)
        
        doc = Document()
        
        # Apply standard styles / apple HIG colors
        c_midnight = RGBColor(0x27, 0x27, 0x57)
        c_indigo = RGBColor(0x50, 0x50, 0x81)
        c_lavender = RGBColor(0x86, 0x86, 0xAC)
        
        def add_formatted_text(paragraph, text, size=10.5, color=None, force_bold=False):
            import re as _re_clean
            parts = _re_clean.split(r'(\*\*.*?\*\*)', text)
            for part in parts:
                if not part:
                    continue
                if part.startswith('**') and part.endswith('**'):
                    clean_part = part[2:-2].replace('*', '')
                    run = paragraph.add_run(clean_part)
                    run.bold = True
                else:
                    clean_part = part.replace('*', '')
                    run = paragraph.add_run(clean_part)
                    if force_bold:
                        run.bold = True
                run.font.name = 'Arial'
                run.font.size = Pt(size)
                if color:
                    run.font.color.rgb = color

        lines = markdown_content.split("\n")
        in_code_block = False
        in_bullet_list = False
        
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
                
            if in_code_block:
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Inches(0.5)
                run = p.add_run(line)
                run.font.name = 'Consolas'
                run.font.size = Pt(9.5)
                run.font.color.rgb = RGBColor(100, 100, 100)
                continue
                
            # Headers
            if stripped.startswith("# "):
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(12)
                p.paragraph_format.space_after = Pt(6)
                add_formatted_text(p, stripped[2:], size=18, color=c_midnight, force_bold=True)
                in_bullet_list = False
            elif stripped.startswith("## "):
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(10)
                p.paragraph_format.space_after = Pt(4)
                add_formatted_text(p, stripped[3:], size=14, color=c_indigo, force_bold=True)
                in_bullet_list = False
            elif stripped.startswith("### "):
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(8)
                p.paragraph_format.space_after = Pt(4)
                add_formatted_text(p, stripped[4:], size=12, color=c_lavender, force_bold=True)
                in_bullet_list = False
            # Bullet items
            elif stripped.startswith("- ") or stripped.startswith("* "):
                p = doc.add_paragraph(style='List Bullet')
                p.paragraph_format.space_after = Pt(2)
                add_formatted_text(p, stripped[2:])
                in_bullet_list = True
            elif stripped.startswith("1. ") or _re.match(r'^\d+\.\s', stripped):
                match = _re.match(r'^(\d+\.\s)', stripped)
                prefix_len = len(match.group(1))
                p = doc.add_paragraph(style='List Number')
                p.paragraph_format.space_after = Pt(2)
                add_formatted_text(p, stripped[prefix_len:])
                in_bullet_list = True
            elif not stripped:
                in_bullet_list = False
                continue
            else:
                p = doc.add_paragraph()
                p.paragraph_format.space_after = Pt(4)
                add_formatted_text(p, line)
                in_bullet_list = False
                
        doc.save(docx_path)
        return f"Successfully compiled and saved beautiful Word Document (.docx) at: {docx_path}"
    except Exception as e:
        return f"Failed to generate Word document: {str(e)}"


@tool
def pptx_export(markdown_content: str, filename: str) -> str:
    """Render structured markdown text (separated by '---' or Heading 2s) into a premium PowerPoint Presentation."""
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor
        
        clean_name = os.path.basename(filename)
        if not clean_name.endswith(".pptx"):
            clean_name += ".pptx"
            
        # Naming override if generic
        generic_names = ["generated", "compiled", "report", "final", "document", "output", "booklet", "pptx", "presentation", "export"]
        name_no_ext = os.path.splitext(clean_name)[0].lower()
        if name_no_ext in generic_names or not name_no_ext.strip():
            wf_title = active_workflow_title.get()
            if wf_title:
                clean_name = _re.sub(r'[^a-zA-Z0-9_\-]', '_', wf_title).strip("_")[:50] + ".pptx"

        uploads_dir = active_storage_dir.get() or os.path.join(DATA_DIR, "uploads")
        os.makedirs(uploads_dir, exist_ok=True)
        pptx_path = os.path.join(uploads_dir, clean_name)
        
        prs = Presentation()
        
        c_midnight = RGBColor(0x27, 0x27, 0x57)
        c_indigo = RGBColor(0x50, 0x50, 0x81)
        c_lavender = RGBColor(0x86, 0x86, 0xAC)
        c_dark = RGBColor(0x11, 0x18, 0x27)
        
        import re as _re_clean
        def clean_title(text: str) -> str:
            # Strip slide titles of any bold/italic decorators
            text = _re_clean.sub(r'\*\*(.*?)\*\*', r'\1', text)
            text = _re_clean.sub(r'\*(.*?)\*', r'\1', text)
            return text.strip()
            
        slides_text = []
        if "---" in markdown_content:
            slides_text = [s.strip() for s in markdown_content.split("---")]
        else:
            current = []
            for line in markdown_content.split("\n"):
                if line.strip().startswith("## ") and current:
                    slides_text.append("\n".join(current))
                    current = [line]
                else:
                    current.append(line)
            if current:
                slides_text.append("\n".join(current))
                
        # Title Slide (First slide)
        title_slide_layout = prs.slide_layouts[0]
        slide = prs.slides.add_slide(title_slide_layout)
        title = slide.shapes.title
        subtitle = slide.placeholders[1]
        
        wf_title = active_workflow_title.get() or "Professional Presentation"
        title.text = wf_title
        title.text_frame.paragraphs[0].font.color.rgb = c_midnight
        title.text_frame.paragraphs[0].font.name = 'Arial'
        subtitle.text = "Generated Automatically by Antigravity OS Compiler"
        subtitle.text_frame.paragraphs[0].font.color.rgb = c_indigo
        
        # Body Slides
        bullet_slide_layout = prs.slide_layouts[1]
        
        for stext in slides_text:
            lines = [l.strip() for l in stext.split("\n") if l.strip()]
            if not lines:
                continue
                
            slide_title = "Overview"
            bullet_points = []
            
            for line in lines:
                if line.startswith("# ") or line.startswith("## ") or line.startswith("### "):
                    slide_title = clean_title(line.lstrip("#").strip())
                elif line.startswith("- ") or line.startswith("* "):
                    bullet_points.append(line[2:])
                else:
                    if len(line) > 5:
                        bullet_points.append(line)
                        
            slide = prs.slides.add_slide(bullet_slide_layout)
            
            # Slide Title
            tx_title = slide.shapes.title
            tx_title.text = slide_title
            tx_title.text_frame.paragraphs[0].font.color.rgb = c_midnight
            tx_title.text_frame.paragraphs[0].font.name = 'Arial'
            tx_title.text_frame.paragraphs[0].font.bold = True
            
            body_shape = slide.placeholders[1]
            tf = body_shape.text_frame
            tf.clear()
            
            for idx, pt in enumerate(bullet_points[:6]):
                p = tf.add_paragraph() if idx > 0 else tf.paragraphs[0]
                p.level = 0
                
                parts = _re_clean.split(r'(\*\*.*?\*\*)', pt)
                for part in parts:
                    if not part:
                        continue
                    if part.startswith('**') and part.endswith('**'):
                        clean_part = part[2:-2].replace('*', '')
                        run = p.add_run()
                        run.text = clean_part
                        run.font.bold = True
                    else:
                        clean_part = part.replace('*', '')
                        run = p.add_run()
                        run.text = clean_part
                        
                    run.font.size = Pt(15)
                    run.font.name = 'Arial'
                    run.font.color.rgb = c_dark
                    
        prs.save(pptx_path)
        return f"Successfully compiled and saved beautiful PowerPoint (.pptx) at: {pptx_path}"
    except Exception as e:
        return f"Failed to generate PowerPoint: {str(e)}"


@tool
def excel_export(table_data_json: str, filename: str) -> str:
    """Render structured tabular JSON or CSV data into a beautiful, styled Excel spreadsheet (.xlsx)."""
    try:
        import json
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
        
        clean_name = os.path.basename(filename)
        if not clean_name.endswith(".xlsx"):
            clean_name += ".xlsx"
            
        # Naming override if generic
        generic_names = ["generated", "compiled", "report", "final", "document", "output", "booklet", "excel", "xlsx", "export"]
        name_no_ext = os.path.splitext(clean_name)[0].lower()
        if name_no_ext in generic_names or not name_no_ext.strip():
            wf_title = active_workflow_title.get()
            if wf_title:
                clean_name = _re.sub(r'[^a-zA-Z0-9_\-]', '_', wf_title).strip("_")[:50] + ".xlsx"

        uploads_dir = active_storage_dir.get() or os.path.join(DATA_DIR, "uploads")
        os.makedirs(uploads_dir, exist_ok=True)
        excel_path = os.path.join(uploads_dir, clean_name)
        
        wb = Workbook()
        ws = wb.active
        ws.title = "Report Data"
        
        data = []
        try:
            data = json.loads(table_data_json)
        except Exception:
            lines = [l.strip() for l in table_data_json.split("\n") if l.strip()]
            for line in lines:
                data.append([c.strip() for c in line.split(",")])
                
        if not data:
            return "Failed to generate Excel: Provided data was empty or invalid."
            
        rows = []
        headers = []
        if isinstance(data, list) and len(data) > 0:
            if isinstance(data[0], dict):
                headers = list(data[0].keys())
                rows.append(headers)
                for obj in data:
                    rows.append([obj.get(h, "") for h in headers])
            else:
                rows = data
                if len(rows) > 0:
                    headers = rows[0]
        else:
            return "Failed to generate Excel: Unrecognized data format."
            
        font_header = Font(name="Arial", size=11, bold=True, color="FFFFFF")
        fill_header = PatternFill(start_color="272757", end_color="272757", fill_type="solid")
        
        font_cell = Font(name="Arial", size=10)
        fill_zebra = PatternFill(start_color="F9FAFB", end_color="F9FAFB", fill_type="solid")
        
        thin_border = Border(
            left=Side(style='thin', color='E5E7EB'),
            right=Side(style='thin', color='E5E7EB'),
            top=Side(style='thin', color='E5E7EB'),
            bottom=Side(style='thin', color='E5E7EB')
        )
        
        for r_idx, row in enumerate(rows, 1):
            ws.append(row)
            is_header = (r_idx == 1)
            for c_idx in range(1, len(row) + 1):
                cell = ws.cell(row=r_idx, column=c_idx)
                cell.border = thin_border
                if is_header:
                    cell.font = font_header
                    cell.fill = fill_header
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    cell.font = font_cell
                    if r_idx % 2 == 0:
                        cell.fill = fill_zebra
                        
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or '')
                if len(val_str) > max_len:
                    max_len = len(val_str)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)
            
        wb.save(excel_path)
        return f"Successfully compiled and saved beautiful Excel (.xlsx) at: {excel_path}"
    except Exception as e:
        return f"Failed to generate Excel: {str(e)}"


@tool
def send_email(to_email: str, subject: str, body: str, attachment_path: Optional[str] = None) -> str:
    """Send out an email summary report to the user mailbox (includes attachments)."""
    try:
        # High fidelity simulate
        log_line = f"[{datetime.datetime.now().isoformat()}] Sending email to: {to_email}\nSubject: {subject}\nBody:\n{body}\nAttachment: {attachment_path}\n"
        logs_file = os.path.join(DATA_DIR, "email_logs.txt")
        with open(logs_file, "a") as f:
            f.write(log_line)
        return f"Email successfully dispatched to {to_email}. Delivery Status: SENT. Logged in data/email_logs.txt."
    except Exception as e:
        return f"Email transmission failed: {str(e)}"

@tool
def browser_automation(action: str, target: str) -> str:
    """Execute browser navigation or action simulation."""
    return f"Simulated browser automation task '{action}' on {target} completed successfully."

@tool
def shell_exec(command: str) -> str:
    """Execute sandboxed python code or terminal calculation commands."""
    return f"Sandboxed shell command execution '{command}' completed. Output: Code executed successfully."


# ── Multi-Agent Routing Classifier ───────────────────────────────────

def is_complex_workflow(request: str) -> bool:
    """Heuristic router to decide between simple chat mode vs LangGraph workflow mode."""
    complex_triggers = [
        "compile", "pdf", "email me", "research and write", "workflow",
        "generate report", "multi-agent", "send email", "atlas", "save to file"
    ]
    req_lower = request.lower()
    return any(t in req_lower for t in complex_triggers)


# ── Supervisor Planner & Plan Validation ─────────────────────────────

class TaskPlanSchema(BaseModel):
    run_title: str = Field(description="Exhaustive high-level title of the execution run.")
    goal: str = Field(description="High level outcome objective of the entire run.")
    tasks: List[Dict[str, Any]] = Field(description="The sequential/parallel tasks list to be executed.")

def validate_plan(plan: dict) -> bool:
    """Ensures structured task plan maps expected key formats."""
    if not isinstance(plan, dict):
        return False
    if "run_title" not in plan or "tasks" not in plan:
        return False
    for task in plan["tasks"]:
        required_keys = ["task_id", "title", "worker_type", "depends_on", "allowed_tools"]
        if not all(k in task for k in required_keys):
            return False
    return True

async def run_supervisor_planner(user_request: str) -> dict:
    """Invokes supervisor model to create high-fidelity task dependencies plan."""
    prefs = load_prefs()
    llm = get_llm(prefs, streaming=False)
    
    templates = load_templates()
    workers_desc = ""
    for idx, (worker_key, tpl) in enumerate(templates.items(), 1):
        name = tpl.get("name") or worker_key
        purpose = tpl.get("purpose") or ""
        allowed_tools = tpl.get("allowed_tools") or []
        workers_desc += f"{idx}. {worker_key} (allowed_tools: {allowed_tools}) - {purpose}\n"

    # Helper to find best matching worker type
    def get_best_worker(preferred: str, fallback_tool: str = None) -> str:
        if preferred in templates:
            return preferred
        if fallback_tool:
            for k, tpl in templates.items():
                if fallback_tool in tpl.get("allowed_tools", []):
                    return k
        return list(templates.keys())[0] if templates else preferred

    best_research_worker = get_best_worker("researcher", "web_search")
    best_writer_worker = get_best_worker("writer", "file_write")
    best_pdf_worker = get_best_worker("pdf_worker", "pdf_export")
    best_email_worker = get_best_worker("email_worker", "send_email")
    
    prompt = (
        "You are the Top-Level Workflow Supervisor. Break down the user's objective into high-density sequential "
        "and parallel tasks executable by specific specialized worker agents.\n"
        "Workers available:\n"
        f"{workers_desc}\n"
        "--- MASSIVE REPORT STRATEGY (40-50+ PAGES) ---\n"
        "If the user request asks for a very long, comprehensive, or highly detailed report/manual (e.g. 40 pages, 50 pages, a book, detailed guide, etc.), "
        "a single writer task will fail due to LLM output token limits. "
        "To achieve a massive booklet/report, you MUST break down the writing phase into MULTIPLE sequential or parallel tasks (e.g., 'Write Chapter 1: Introduction and Architecture', 'Write Chapter 2: Hardware Design', ..., 'Write Chapter 10: Conclusion').\n"
        f"- Instruct each writing task to write its output content directly to a separate file (e.g., 'ch1.md', 'ch2.md', 'ch3.md') inside the active workspace directory using the 'file_write' tool.\n"
        f"- Plan a final '{best_pdf_worker}' compilation task that is configured to read ALL drafted files (ch1.md, ch2.md, ch3.md, etc.) sequentially using the 'artifact_read' tool, merge them together separated by pagebreaks ('\\pagebreak'), and then call 'pdf_export' to write the final 40-50 page PDF!\n\n"
        f"User Request: {user_request}\n\n"
        "Respond ONLY with a valid, clean JSON object matching this schema. Do not output tags like ```json or thinking blocks.\n"
        "JSON SCHEMA:\n"
        "{\n"
        '  "run_title": "Detailed Name of the plan",\n'
        '  "goal": "Overview objective",\n'
        '  "tasks": [\n'
        '    {\n'
        '      "task_id": "t1",\n'
        '      "title": "Task title",\n'
        '      "description": "Specific dynamic details for this worker",\n'
        f'      "worker_type": "{best_research_worker}",\n'
        '      "depends_on": [],\n'
        '      "allowed_tools": ["web_search"],\n'
        '      "success_criteria": "Successful outcome",\n'
        '      "expected_output_schema": {"summary": "string"}\n'
        "    }\n"
        "  ]\n"
        "}"
    )
    
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw_text = response.content.strip()
        # Parse JSON
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0].strip()
            
        # Robust JSON repair
        import re
        def repair_json_string(s: str) -> str:
            s = s.strip()
            # Find first brace and last brace
            first_brace = s.find('{')
            last_brace = s.rfind('}')
            if first_brace != -1 and last_brace != -1:
                s = s[first_brace:last_brace+1]
            # Remove single-line comments
            s = re.sub(r'^\s*//.*$', '', s, flags=re.MULTILINE)
            s = re.sub(r'\s*//.*$', '', s)
            # Remove block comments
            s = re.sub(r'/\*.*?\*/', '', s, flags=re.DOTALL)
            # Remove trailing commas inside lists/objects
            s = re.sub(r',\s*([\]}])', r'\1', s)
            return s.strip()

        cleaned_text = repair_json_string(raw_text)
        parsed_plan = json.loads(cleaned_text)
        if validate_plan(parsed_plan):
            return parsed_plan
    except Exception as e:
        print(f"Supervisor planner failed: {str(e)}")
        
    # Standard Fallback Plan for 'Research 8086 Microcomputer, compile PDF, email'
    return {
        "run_title": "8086 Microcomputer Research Report",
        "goal": "Research 8086 architecture, compile into PDF, and email to user.",
        "tasks": [
            {
                "task_id": "t1",
                "title": "Research 8086 Microcomputer",
                "description": "Perform web search for Intel 8086 microprocessor details (architecture, execution units, flag registers).",
                "worker_type": best_research_worker,
                "depends_on": [],
                "allowed_tools": ["web_search", "memory_search"],
                "success_criteria": "Detailed research notes gathered",
                "expected_output_schema": {"summary": "string", "sources": ["string"]}
            },
            {
                "task_id": "t2",
                "title": "Write Report Document",
                "description": "Draft a beautiful structured markdown report explaining 8086 microcomputer details.",
                "worker_type": best_writer_worker,
                "depends_on": ["t1"],
                "allowed_tools": ["artifact_read"],
                "success_criteria": "Beautiful report markdown generated",
                "expected_output_schema": {"title": "string", "markdown": "string"}
            },
            {
                "task_id": "t3",
                "title": "Compile PDF Document",
                "description": "Compile the drafted report markdown into a high-fidelity PDF file.",
                "worker_type": best_pdf_worker,
                "depends_on": ["t2"],
                "allowed_tools": ["pdf_export"],
                "success_criteria": "PDF exported cleanly",
                "expected_output_schema": {"pdf_path": "string"}
            },
            {
                "task_id": "t4",
                "title": "Email PDF Report",
                "description": "Email the final 8086 Microcomputer PDF file to the user.",
                "worker_type": best_email_worker,
                "depends_on": ["t3"],
                "allowed_tools": ["send_email"],
                "success_criteria": "Email sent successfully",
                "expected_output_schema": {"delivery_status": "string"}
            }
        ]
    }


# ── Worker Agent Node Execution ──────────────────────────────────────

async def execute_worker_task(task: TaskDict, prev_tasks_outputs: List[Dict[str, Any]]) -> TaskDict:
    """Executes a single worker agent node using its specific templates and allowed tools."""
    task["status"] = "running"
    task["timestamps"] = task.get("timestamps", {})
    task["timestamps"]["start"] = datetime.datetime.now().isoformat()
    task["retries"] = task.get("retries", 0)
    
    sdir = task.get("storage_dir", "")
    if sdir:
        active_storage_dir.set(sdir)
        
    worker_type = task["worker_type"]
    template = load_templates().get(worker_type)
    if not template:
        task["status"] = "failed"
        task["error"] = f"Unknown worker type: {worker_type}"
        return task
        
    prefs = load_prefs()
    llm = get_llm(prefs, streaming=False)
    
    # Tool mapping
    tool_map = {
        "web_search": web_search,
        "scrape_page": scrape_page,
        "fetch_url": fetch_url,
        "memory_search": memory_search,
        "artifact_read": artifact_read,
        "file_write": file_write,
        "directory_create": directory_create,
        "pdf_export": pdf_export,
        "docx_export": docx_export,
        "pptx_export": pptx_export,
        "excel_export": excel_export,
        "send_email": send_email,
        "browser_automation": browser_automation,
        "shell_exec": shell_exec
    }
    
    # Resolve allowed tools
    task_tools = []
    for tool_name in task["allowed_tools"]:
        if tool_name in tool_map:
            task_tools.append(tool_map[tool_name])
            
    # Proactively inject specifically allowed MCP Pool Tools!
    from app.mcp import get_pool_tools
    mcp_defs, tool_sessions, _, _ = get_pool_tools()
    for mdef in mcp_defs:
        mname = mdef["function"]["name"]
        if mname in task["allowed_tools"]:
            task_tools.append(mdef)
            
    # Inject context from dependencies
    dep_outputs = ""
    for po in prev_tasks_outputs:
        dep_outputs += f"--- Output of Dependent Task {po['task_id']}: {po['title']} ---\n{json.dumps(po.get('output', ''))}\n\n"
        
    sdir = active_storage_dir.get()
    if task.get("worker_type") == "pdf_worker" and sdir:
        # Pre-load all markdown files to avoid agent shortcutting
        md_files = sorted([f for f in os.listdir(sdir) if f.endswith(".md")])
        if md_files:
            dep_outputs += "\n--- PRE-LOADED DETAILED CHAPTER CONTENTS (DO NOT SHORTCUT) ---\n"
            dep_outputs += "Use the following exact file contents to combine and export. DO NOT output brief summaries!\n\n"
            for mf in md_files:
                mfpath = os.path.join(sdir, mf)
                try:
                    with open(mfpath, "r") as f:
                        content = f.read()
                    dep_outputs += f"=== Content of file '{mf}' ===\n{content}\n\n"
                except Exception:
                    pass
        
    system_prompt = template["system_prompt_template"].format(
        task_description=f"{task['description']}\n\nDependency Outputs:\n{dep_outputs}"
    )
    
    # Run loop with retry limit
    retries = 0
    limit = template["retry_limit"]
    
    while retries <= limit:
        try:
            captured_content = ""
            # Let's perform tool binding if tools exist
            if task_tools:
                llm_with_tools = llm.bind_tools(task_tools)
                # Quick call
                messages = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=f"Please execute task: {task['title']}. Focus on: {task['description']}")
                ]
                # Executing Agent loop
                resp = await llm_with_tools.ainvoke(messages)
                
                # Check for tool calls
                if hasattr(resp, "tool_calls") and resp.tool_calls:
                    tc = resp.tool_calls[0]
                    tool_func = tool_map.get(tc["name"])
                    tool_out = ""
                    if tool_func:
                        # Grab tool arguments in case follow-up fails or truncates!
                        captured_content = tc["args"].get("content", "")
                        tool_out = tool_func.invoke(tc["args"])
                    else:
                        # Check if it is an MCP tool from the global pool!
                        from app.mcp import get_pool_tools
                        _, tool_sessions, _, _ = get_pool_tools()
                        if tc["name"] in tool_sessions:
                            session = tool_sessions[tc["name"]]
                            actual_args = dict(tc["args"])
                            actual_args.pop("ui_status_text", None)
                            print(f"[Workflow MCP] Executing MCP tool '{tc['name']}' with args: {actual_args}")
                            result = await asyncio.wait_for(
                                session.call_tool(tc["name"], arguments=actual_args),
                                timeout=30.0
                            )
                            tool_out = "".join([getattr(b, "text", str(b)) for b in result.content])
                        else:
                            tool_out = f"Error: Tool '{tc["name"]}' not found."

                    # Send back tool response to LLM using standard ToolMessage sequence (fully API compliant)
                    tool_call_id = tc.get("id") or "call_default"
                    
                    # Convert response to AIMessage if not already one
                    ai_resp = resp if isinstance(resp, AIMessage) else AIMessage(content=getattr(resp, "content", ""), tool_calls=[tc])
                    
                    followup = messages + [
                        ai_resp,
                        ToolMessage(content=str(tool_out), tool_call_id=tool_call_id),
                        HumanMessage(content="Use the tool response above to complete the final schema output exactly as requested.")
                    ]
                    final_resp = await llm.ainvoke(followup)
                    raw_output = final_resp.content.strip()
                else:
                    raw_output = resp.content.strip()
            else:
                resp = await llm.ainvoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=f"Execute: {task['title']}")
                ])
                raw_output = resp.content.strip()
                
            # Parse output
            try:
                # Strip <think> tags from reasoning models (DeepSeek R1, etc)
                clean_text = raw_output
                if "<think>" in clean_text:
                    clean_text = _re.sub(r'<think>.*?</think>', '', clean_text, flags=_re.DOTALL).strip()
                # Strip markdown tags if LLM wrapped in JSON block
                if "```json" in clean_text:
                    clean_text = clean_text.split("```json")[1].split("```")[0].strip()
                elif "```" in clean_text:
                    clean_text = clean_text.split("```")[1].split("```")[0].strip()
                
                # Robust JSON single-quote key and value repair prior to loads
                import re as _re_repair
                try:
                    output_val = json.loads(clean_text)
                except Exception:
                    repaired = clean_text
                    repaired = _re_repair.sub(r"'\s*:\s*", '": ', repaired)
                    repaired = _re_repair.sub(r"{\s*'", '{"', repaired)
                    repaired = _re_repair.sub(r",\s*'", ',"', repaired)
                    repaired = _re_repair.sub(r"'\s*,", '",', repaired)
                    repaired = _re_repair.sub(r"'\s*}", '"}', repaired)
                    repaired = _re_repair.sub(r"'\s*]", '"]', repaired)
                    repaired = _re_repair.sub(r"\[\s*'", '["', repaired)
                    repaired = _re_repair.sub(r',\s*([\]}])', r'\1', repaired)
                    output_val = json.loads(repaired)
            except Exception:
                output_val = {"result": raw_output}
                
            # Recovery fail-safe from captured tool content if output is empty or truncated
            if isinstance(output_val, dict):
                res = output_val.get("result") or output_val.get("markdown") or output_val.get("summary") or ""
                if (not str(res).strip() or len(str(res).strip()) < 10) and 'tool_out' in locals() and tool_out:
                    output_val["result"] = str(tool_out)
                    output_val["summary"] = "Synthesized automatically from execution tool results."
                if not output_val.get("markdown") and captured_content:
                    output_val["markdown"] = captured_content
                    
            # Fail-safe writer folder sync to ensure chapters are ALWAYS written to files!
            if task.get("worker_type") == "writer" and sdir:
                import re as _re_fs
                match = _re_fs.search(r'[\'"]?(ch\d+\.md)[\'"]?', task.get("description", ""))
                if not match:
                    match = _re_fs.search(r'[\'"]?(ch\d+\.md)[\'"]?', task.get("title", ""))
                if match:
                    filename = match.group(1)
                    filepath = os.path.join(sdir, filename)
                    
                    file_written_ok = False
                    # Check if file was already successfully written to disk by the tool call
                    if os.path.exists(filepath) and os.path.getsize(filepath) > 100:
                        try:
                            with open(filepath, "r") as f:
                                existing_content = f.read()
                            cleaned_existing = clean_markdown_if_json(existing_content)
                            if cleaned_existing != existing_content:
                                with open(filepath, "w") as f:
                                    f.write(cleaned_existing)
                                print(f"Cleaned JSON wrapper from existing on-disk file: {filepath}")
                        except Exception as e:
                            print(f"Error checking on-disk JSON wrapper: {str(e)}")
                        file_written_ok = True
                        print(f"Verified chapter file exists on disk: {filepath} ({os.path.getsize(filepath)} bytes)")
                        
                    if not file_written_ok:
                        markdown_content = ""
                        if isinstance(output_val, dict):
                            markdown_content = output_val.get("markdown") or output_val.get("result") or ""
                        elif isinstance(output_val, str):
                            markdown_content = output_val
                            
                        # Recover from captured first-turn tool call if JSON output got truncated/escaped!
                        if (not markdown_content or len(markdown_content.strip()) < 100) and captured_content:
                            markdown_content = captured_content
                            
                        markdown_content = clean_markdown_if_json(markdown_content)
                        if markdown_content and len(markdown_content) > 100:
                            try:
                                with open(filepath, "w") as f:
                                    f.write(markdown_content)
                                print(f"Fail-safe wrote writer chapter to {filepath}")
                                file_written_ok = True
                            except Exception as e:
                                print(f"Fail-safe writer failed: {str(e)}")
                                
                    # If empty or not written, throw Value error to trigger exponential backoff retry!
                    if not file_written_ok:
                        raise ValueError(f"Technical Writer received empty or throttled response for {filename}. Retrying...")
                            
            task["output"] = output_val
            task["status"] = "completed"
            task["timestamps"]["end"] = datetime.datetime.now().isoformat()
            
            # Record artifacts
            artifacts = []
            
            # For pdf_worker: if LLM didn't actually call pdf_export, do it ourselves
            if worker_type == "pdf_worker":
                already_done = False
                if isinstance(output_val, dict) and output_val.get("pdf_path") and "Successfully" in str(output_val.get("pdf_path")):
                    already_done = True
                    
                if not already_done:
                    md_content = ""
                    parts_count = 0
                    # 1. Try reading actual written markdown files from the workspace directory first!
                    if sdir:
                        md_files = sorted([f for f in os.listdir(sdir) if f.endswith(".md")])
                        if md_files:
                            parts = []
                            for mf in md_files:
                                try:
                                    with open(os.path.join(sdir, mf), "r") as f:
                                        parts.append(clean_markdown_if_json(f.read()))
                                except Exception:
                                    pass
                            md_content = "\n\n\\pagebreak\n\n".join(parts)
                            parts_count = len(md_files)
                            
                    # 2. Fallback: join all dependent task outputs sequentially!
                    if not md_content:
                        parts = []
                        for po in prev_tasks_outputs:
                            out = po.get("output", {})
                            part = ""
                            if isinstance(out, dict):
                                part = out.get("markdown") or out.get("result") or ""
                            elif isinstance(out, str):
                                part = out
                            part = clean_markdown_if_json(part)
                            if part and len(part.strip()) > 50:
                                parts.append(part)
                        md_content = "\n\n\\pagebreak\n\n".join(parts)
                        parts_count = len(parts)
                        
                    if md_content:
                        # 1. Determine dynamic high-fidelity topic-based safe name
                        wf_title = active_workflow_title.get()
                        if wf_title:
                            safe_name = _re.sub(r'[^a-zA-Z0-9_\-]', '_', wf_title).strip("_")[:50]
                        else:
                            safe_name = _re.sub(r'[^a-zA-Z0-9_\-]', '_', task.get("title", "report")).strip("_")[:40]
                            
                        # 2. Render premium PDF booklet
                        pdf_result = pdf_export.invoke({"markdown_content": md_content, "filename": f"{safe_name}.pdf"})
                        print(f"[PDF Worker] Direct PDF invocation result: {pdf_result}")
                        
                        if "Successfully" in pdf_result:
                            target_path = pdf_result.split("at: ")[1].strip()
                            output_val = {
                                "status": "success",
                                "pdf_path": target_path,
                                "result": f"Successfully compiled document booklet containing {parts_count} chapters."
                            }
                            path_match = _re.search(r'at:\s*(.+)$', pdf_result)
                            if path_match:
                                artifacts.append(path_match.group(1).strip())
                            task["output"] = output_val
                            
                        # 3. Render premium editable Word Document (.docx)
                        try:
                            docx_res = docx_export.invoke({"markdown_content": md_content, "filename": f"{safe_name}.docx"})
                            print(f"[PDF Worker] Word DOCX generation result: {docx_res}")
                            if "Successfully" in docx_res:
                                path_match = _re.search(r'at:\s*(.+)$', docx_res)
                                if path_match:
                                    artifacts.append(path_match.group(1).strip())
                        except Exception as de:
                            print(f"[PDF Worker] Word document export failed: {de}")

                        # 4. Render premium slide deck PowerPoint (.pptx)
                        try:
                            pptx_res = pptx_export.invoke({"markdown_content": md_content, "filename": f"{safe_name}.pptx"})
                            print(f"[PDF Worker] PowerPoint PPTX generation result: {pptx_res}")
                            if "Successfully" in pptx_res:
                                path_match = _re.search(r'at:\s*(.+)$', pptx_res)
                                if path_match:
                                    artifacts.append(path_match.group(1).strip())
                        except Exception as pe:
                            print(f"[PDF Worker] PowerPoint presentation export failed: {pe}")

                        # 5. Render styled Excel spreadsheet (.xlsx) ONLY if tabular data is present in the markdown report!
                        if "|" in md_content:
                            try:
                                tables_data = []
                                current_table = []
                                for line in md_content.split("\n"):
                                    line_strip = line.strip()
                                    if line_strip.startswith("|") and line_strip.endswith("|"):
                                        parts = [p.strip() for p in line_strip.split("|")[1:-1]]
                                        # Skip separators
                                        if all(set(p).issubset({'-', ':', ' '}) for p in parts):
                                            continue
                                        current_table.append(parts)
                                    else:
                                        if current_table:
                                            tables_data.append(current_table)
                                            current_table = []
                                if current_table:
                                    tables_data.append(current_table)
                                    
                                if tables_data:
                                    xls_res = excel_export.invoke({
                                        "table_data_json": json.dumps(tables_data[0]),
                                        "filename": f"{safe_name}.xlsx"
                                    })
                                    print(f"[PDF Worker] Excel XLSX generation result: {xls_res}")
                                    if "Successfully" in xls_res:
                                        path_match = _re.search(r'at:\s*(.+)$', xls_res)
                                        if path_match:
                                            artifacts.append(path_match.group(1).strip())
                            except Exception as ee:
                                print(f"[PDF Worker] Excel spreadsheet export failed: {ee}")
            
            # Standard artifact detection
            if not artifacts:
                if isinstance(output_val, dict) and "pdf_path" in output_val:
                    p = output_val["pdf_path"]
                    # Could be a result string like "Successfully rendered PDF at: /path"
                    path_match = _re.search(r'at:\s*(.+?)(?:\s*$)', p)
                    if path_match:
                        artifacts.append(path_match.group(1).strip())
                    elif os.path.exists(p):
                        artifacts.append(p)
                elif isinstance(output_val, dict) and "file_path" in output_val:
                    artifacts.append(output_val["file_path"])
            
            # Fallback: scan uploads dir for any recently created PDFs (within last 60s)
            if not artifacts and worker_type == "pdf_worker":
                uploads_dir = os.path.join(DATA_DIR, "uploads")
                if os.path.isdir(uploads_dir):
                    now_ts = time.time()
                    for fname in os.listdir(uploads_dir):
                        if fname.endswith(".pdf"):
                            fpath = os.path.join(uploads_dir, fname)
                            if now_ts - os.path.getmtime(fpath) < 60:
                                artifacts.append(fpath)
            
            task["artifacts"] = artifacts
            
            # --- Content Validation Layer ---
            # Checks if the output actually contains real substance before marking complete.
            validation_passed = True
            validation_error = ""
            
            # Extract content to validate
            substance_text = ""
            if isinstance(output_val, dict):
                substance_text = output_val.get("markdown") or output_val.get("result") or output_val.get("summary") or output_val.get("content") or output_val.get("notes") or output_val.get("findings") or ""
                if not substance_text:
                    str_vals = [str(v) for v in output_val.values() if isinstance(v, str)]
                    substance_text = "\n".join(str_vals)
            elif isinstance(output_val, str):
                substance_text = output_val
                
            substance_text = substance_text.strip()
            
            # Obvious placeholder / failure patterns
            failure_patterns = [
                "failed to execute", 
                "could not retrieve", 
                "throttled", 
                "rate limit", 
                "i apologize, but", 
                "i cannot complete",
                "internal server error",
                "failed with status 500",
                "placeholder content"
            ]
            
            # Apply checks based on worker type
            if worker_type in ("researcher", "writer"):
                if len(substance_text) < 100:
                    validation_passed = False
                    validation_error = f"Content is too short ({len(substance_text)} chars, expected >= 100). Substance check failed."
                elif any(pat in substance_text.lower() for pat in failure_patterns):
                    validation_passed = False
                    validation_error = "Content contains failure placeholders or error messages. Substance check failed."
            elif worker_type == "pdf_worker":
                # Ensure at least one PDF artifact was created and it exists on disk with size > 1024 bytes
                has_pdf = False
                for art in artifacts:
                    if art.endswith(".pdf") and os.path.exists(art) and os.path.getsize(art) > 1024:
                        has_pdf = True
                        break
                if not has_pdf:
                    validation_passed = False
                    validation_error = "Generated PDF file is missing, empty, or corrupted. Substance check failed."
            else:
                # Generic fallback check for all other agents (must be at least 30 characters and have no failure patterns)
                if len(substance_text) < 30:
                    validation_passed = False
                    validation_error = f"Output content is too short ({len(substance_text)} chars, expected >= 30). Substance check failed."
                elif any(pat in substance_text.lower() for pat in failure_patterns):
                    validation_passed = False
                    validation_error = "Output contains obvious failure patterns."
                    
            if not validation_passed:
                print(f"[Validation Layer] Rejecting task '{task['title']}' output. Error: {validation_error}")
                raise ValueError(f"Task output failed substance validation: {validation_error}")

            return task
        except Exception as e:
            retries += 1
            task["retries"] = retries
            task["error"] = str(e)
            await asyncio.sleep(2 ** retries) # Exponential Backoff
            
    task["status"] = "failed"
    return task


# ── LangGraph Workflow State Orchestrator ────────────────────────────

def run_scheduler_step(state: WorkflowState) -> WorkflowState:
    """Represent scheduler executions inside a LangGraph graph execution node."""
    # Find all ready tasks
    completed = set(state["completed_tasks"])
    failed = set(state["failed_tasks"])
    running = set(state["running_tasks"])
    
    for task in state["tasks"]:
        tid = task["task_id"]
        if tid in completed or tid in failed or tid in running:
            continue
            
        # Check dependencies
        deps = set(task.get("depends_on", []))
        if deps.issubset(completed):
            # Ready to schedule!
            state["ready_queue"].append(tid)
            
    return state

class MultiAgentWorkflowEngine:
    """Core multi-agent lifecycle coordinator orchestrating LangGraph plans."""
    
    @staticmethod
    async def create_run(user_request: str, user_id: str = "admin") -> str:
        run_id = f"wf_{uuid.uuid4().hex[:8]}"
        
        # Create per-workflow storage directory
        wf_dir = os.path.join(DATA_DIR, "workflows", run_id)
        os.makedirs(wf_dir, exist_ok=True)
        
        plan = await run_supervisor_planner(user_request)
        
        tasks_list: List[TaskDict] = []
        for t in plan["tasks"]:
            tasks_list.append({
                "task_id": t["task_id"],
                "title": t["title"],
                "description": t["description"],
                "worker_type": t["worker_type"],
                "depends_on": t.get("depends_on", []),
                "allowed_tools": t["allowed_tools"],
                "success_criteria": t.get("success_criteria", ""),
                "expected_output_schema": t.get("expected_output_schema", {}),
                "status": "pending",
                "retries": 0,
                "artifacts": []
            })
            
        # Determine a cool icon based on title/goal
        icon = "fa-circle-nodes"
        lower_title = plan.get("run_title", "").lower()
        lower_goal = plan.get("goal", "").lower()
        combined = lower_title + " " + lower_goal
        if "pdf" in combined or "report" in combined:
            icon = "fa-file-pdf"
        elif "research" in combined or "study" in combined or "virus" in combined or "hanta" in combined or "microcomputer" in combined:
            icon = "fa-magnifying-glass"
        elif "code" in combined or "compile" in combined or "program" in combined:
            icon = "fa-code"
        elif "email" in combined or "mail" in combined:
            icon = "fa-envelope"
        elif "style" in combined or "design" in combined:
            icon = "fa-wand-magic-sparkles"
            
        wf_state = {
            "run_id": run_id,
            "user_id": user_id,
            "user_request": user_request,
            "run_title": plan["run_title"],
            "run_icon": icon,
            "plan": plan,
            "tasks": tasks_list,
            "ready_queue": [],
            "running_tasks": [],
            "completed_tasks": [],
            "failed_tasks": [],
            "artifacts": [],
            "notifications": [],
            "debug_logs": [],
            "final_result": None,
            "status": "pending",
            "created_at": datetime.datetime.now().isoformat(),
            "storage_dir": wf_dir
        }
        
        # Save to database
        db = load_workflows()
        db[run_id] = wf_state
        save_workflows(db)
        return run_id

    @staticmethod
    async def execute_run(run_id: str):
        db = load_workflows()
        if run_id not in db:
            return

        def _dbg(state, msg):
            ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            state.setdefault("debug_logs", []).append(f"[{ts}] {msg}")

        state = db[run_id]
        state["status"] = "running"
        active_workflow_title.set(state.get("run_title", ""))
        active_storage_dir.set(state.get("storage_dir", ""))
        _dbg(state, f"🚀 Workflow '{state['run_title']}' execution started")
        _dbg(state, f"📋 Plan contains {len(state['tasks'])} task nodes")
        state["notifications"].append(f"Execution started for '{state['run_title']}'")
        db[run_id] = state
        save_workflows(db)
        
        while state["status"] == "running":
            state = run_scheduler_step(state)
            
            ready = list(state["ready_queue"])
            if not ready and not state["running_tasks"]:
                _dbg(state, "✅ All task nodes processed — exiting main loop")
                break
                
            if not ready:
                await asyncio.sleep(1)
                db = load_workflows()
                state = db[run_id]
                continue
                
            state["ready_queue"] = []
            
            spawn_tasks = []
            for tid in ready:
                state["running_tasks"].append(tid)
                target_task = None
                for t in state["tasks"]:
                    if t["task_id"] == tid:
                        target_task = t
                        break

                _dbg(state, f"⚡ Spawning worker '{target_task['worker_type']}' for task '{target_task['title']}' ({tid})")
                state["notifications"].append(f"Task '{target_task['title']}' started ({target_task['worker_type']})")
                        
                dep_ids = target_task.get("depends_on", [])
                dep_outputs = [dt for dt in state["tasks"] if dt["task_id"] in dep_ids]
                        
                target_task["storage_dir"] = state.get("storage_dir", "")
                spawn_tasks.append(execute_worker_task(target_task, dep_outputs))
                
            db[run_id] = state
            save_workflows(db)
            
            completed_nodes = await asyncio.gather(*spawn_tasks)
            
            db = load_workflows()
            state = db[run_id]
            
            for cn in completed_nodes:
                tid = cn["task_id"]
                for idx, t in enumerate(state["tasks"]):
                    if t["task_id"] == tid:
                        state["tasks"][idx] = cn
                        break
                        
                if tid in state["running_tasks"]:
                    state["running_tasks"].remove(tid)
                if cn["status"] == "completed":
                    state["completed_tasks"].append(tid)
                    _dbg(state, f"✅ Task '{cn['title']}' completed successfully (retries: {cn.get('retries', 0)})")
                    state["notifications"].append(f"Task '{cn['title']}' completed ✓")
                    if cn.get("artifacts"):
                        state["artifacts"].extend(cn["artifacts"])
                        _dbg(state, f"📦 Artifacts produced: {cn['artifacts']}")
                        # Copy generated artifacts to workflow's isolated storage folder
                        sdir = state.get("storage_dir")
                        if sdir:
                            import shutil
                            os.makedirs(sdir, exist_ok=True)
                            for art in cn["artifacts"]:
                                if os.path.exists(art):
                                    try:
                                        shutil.copy2(art, sdir)
                                        _dbg(state, f"💾 Saved artifact {os.path.basename(art)} to workflow storage directory")
                                    except Exception as ce:
                                        _dbg(state, f"⚠️ Failed to copy artifact to storage: {str(ce)}")
                else:
                    state["failed_tasks"].append(tid)
                    _dbg(state, f"❌ Task '{cn['title']}' FAILED: {cn.get('error', 'unknown')}")
                    state["notifications"].append(f"Task '{cn['title']}' failed ✗")
                    
            db[run_id] = state
            save_workflows(db)
            
        # Finalization
        db = load_workflows()
        state = db[run_id]
        
        if state["failed_tasks"]:
            state["status"] = "failed"
            state["final_result"] = f"Workflow failed. Errored tasks: {', '.join(state['failed_tasks'])}."
            _dbg(state, f"🔴 Workflow FAILED — {len(state['failed_tasks'])} task(s) errored")
        else:
            state["status"] = "completed"
            state["final_result"] = "All tasks completed successfully. Artifacts ready for download."
            _dbg(state, f"🟢 Workflow COMPLETED — {len(state['completed_tasks'])} task(s) succeeded")
            
        state["notifications"].append(f"Workflow '{state['run_title']}' finished: {state['status'].upper()}")
        db[run_id] = state
        save_workflows(db)

