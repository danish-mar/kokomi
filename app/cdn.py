import os
import logging
import asyncio
import httpx

logger = logging.getLogger("kokomi.cdn")

VENDOR_DIR = "public/static/vendor"
SENTINEL_FILE = os.path.join(VENDOR_DIR, ".download_complete")

# Dictionary mapping local file path relative to VENDOR_DIR -> public CDN URL
ASSETS = {
    # Standalone browser JIT compiler (zero Node dependency on server!)
    "tailwindcss.js": "https://cdn.tailwindcss.com",
    
    # FontAwesome CSS
    "fontawesome/all.min.css": "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css",
    
    # FontAwesome Font Files (stored in webfonts/ relative to the CSS file)
    "webfonts/fa-solid-900.woff2": "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/webfonts/fa-solid-900.woff2",
    "webfonts/fa-regular-400.woff2": "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/webfonts/fa-regular-400.woff2",
    "webfonts/fa-brands-400.woff2": "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/webfonts/fa-brands-400.woff2",
    "webfonts/fa-v4compatibility.woff2": "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/webfonts/fa-v4compatibility.woff2",
    
    # Pyodide Core
    "pyodide.js": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/pyodide.js",
    "pyodide.asm.js": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/pyodide.asm.js",
    "pyodide.asm.wasm": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/pyodide.asm.wasm",
    "python_stdlib.zip": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/python_stdlib.zip",
    "pyodide-lock.json": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/pyodide-lock.json",
    
    # Pyodide Packages (matplotlib, numpy, pandas & dependencies)
    "cycler-0.12.1-py3-none-any.whl": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/cycler-0.12.1-py3-none-any.whl",
    "fonttools-4.51.0-py3-none-any.whl": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/fonttools-4.51.0-py3-none-any.whl",
    "kiwisolver-1.4.5-cp312-cp312-pyodide_2024_0_wasm32.whl": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/kiwisolver-1.4.5-cp312-cp312-pyodide_2024_0_wasm32.whl",
    "matplotlib-3.5.2-cp312-cp312-pyodide_2024_0_wasm32.whl": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/matplotlib-3.5.2-cp312-cp312-pyodide_2024_0_wasm32.whl",
    "matplotlib_pyodide-0.2.2-py3-none-any.whl": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/matplotlib_pyodide-0.2.2-py3-none-any.whl",
    "numpy-1.26.4-cp312-cp312-pyodide_2024_0_wasm32.whl": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/numpy-1.26.4-cp312-cp312-pyodide_2024_0_wasm32.whl",
    "packaging-23.2-py3-none-any.whl": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/packaging-23.2-py3-none-any.whl",
    "pandas-2.2.0-cp312-cp312-pyodide_2024_0_wasm32.whl": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/pandas-2.2.0-cp312-cp312-pyodide_2024_0_wasm32.whl",
    "pillow-10.2.0-cp312-cp312-pyodide_2024_0_wasm32.whl": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/pillow-10.2.0-cp312-cp312-pyodide_2024_0_wasm32.whl",
    "pyparsing-3.1.2-py3-none-any.whl": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/pyparsing-3.1.2-py3-none-any.whl",
    "python_dateutil-2.9.0.post0-py2.py3-none-any.whl": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/python_dateutil-2.9.0.post0-py2.py3-none-any.whl",
    "pytz-2024.1-py2.py3-none-any.whl": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/pytz-2024.1-py2.py3-none-any.whl",
    "six-1.16.0-py2.py3-none-any.whl": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/six-1.16.0-py2.py3-none-any.whl",
    
    # Marked
    "marked.min.js": "https://cdn.jsdelivr.net/npm/marked/marked.min.js",

    # Chart.js — AI-generated inline charts in chat
    "chartjs/chart.umd.min.js": "https://cdn.jsdelivr.net/npm/chart.js@4.4.6/dist/chart.umd.min.js",

    # Mermaid — AI-generated inline diagrams in chat
    "mermaid/mermaid.min.js": "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js",

    # Highlight.js
    "highlight/github-dark.min.css": "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css",
    "highlight/highlight.min.js": "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js",
    
    # Driver.js
    "driver/driver.css": "https://cdn.jsdelivr.net/npm/driver.js@1.0.1/dist/driver.css",
    "driver/driver.js": "https://cdn.jsdelivr.net/npm/driver.js@1.0.1/dist/driver.js.iife.js",
    
    # KaTeX Math Library
    "katex/katex.min.css": "https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css",
    "katex/katex.min.js": "https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js",
}

# ── Canvas editors ────────────────────────────────────────────────────
# Monaco (the editor core VS Code itself is built on) powers the "code"
# canvas; Quill powers the Word-like "document" canvas.
MONACO_VER = "0.52.2"
MONACO_BASE = f"https://cdn.jsdelivr.net/npm/monaco-editor@{MONACO_VER}/min/vs"

# Monaco is an AMD bundle: loader.js pulls the rest in at runtime by relative
# path, so the local copy must mirror the CDN's vs/ tree exactly.
MONACO_CORE = [
    "loader.js",
    "editor/editor.main.js",
    "editor/editor.main.css",
    # Web-worker host + the per-language workers Monaco spawns for smarts.
    "base/worker/workerMain.js",
    "language/typescript/tsMode.js",
    "language/typescript/tsWorker.js",
    "language/json/jsonMode.js",
    "language/json/jsonWorker.js",
    "language/css/cssMode.js",
    "language/css/cssWorker.js",
    "language/html/htmlMode.js",
    "language/html/htmlWorker.js",
    # Icon font referenced by editor.main.css (relative path — keep the tree).
    "base/browser/ui/codicons/codicon/codicon.ttf",
]

# Syntax definitions, loaded on demand per language. Only these languages get
# highlighting offline, so keep the list to what actually shows up in chat.
MONACO_LANGUAGES = [
    "python", "javascript", "typescript", "html", "css", "markdown",
    "sql", "shell", "yaml", "xml", "java", "cpp", "csharp", "go",
    "rust", "php", "ruby", "dockerfile", "ini", "lua", "r", "swift",
    "kotlin", "scss", "graphql", "powershell", "perl", "clojure",
]

for _f in MONACO_CORE:
    ASSETS[f"monaco/vs/{_f}"] = f"{MONACO_BASE}/{_f}"
for _lang in MONACO_LANGUAGES:
    ASSETS[f"monaco/vs/basic-languages/{_lang}/{_lang}.js"] = (
        f"{MONACO_BASE}/basic-languages/{_lang}/{_lang}.js"
    )

# Quill 2 — rich-text engine behind the document canvas (icons are inline SVG,
# so the stylesheet pulls no external fonts).
ASSETS["quill/quill.js"] = "https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.js"
ASSETS["quill/quill.snow.css"] = "https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.snow.css"


# KaTeX relative fonts (stored in fonts/ relative to the CSS file)
KATEX_FONTS = [
    "KaTeX_AMS-Regular.woff2",
    "KaTeX_Caligraphic-Bold.woff2",
    "KaTeX_Caligraphic-Regular.woff2",
    "KaTeX_Fraktur-Bold.woff2",
    "KaTeX_Fraktur-Regular.woff2",
    "KaTeX_Main-Bold.woff2",
    "KaTeX_Main-BoldItalic.woff2",
    "KaTeX_Main-Italic.woff2",
    "KaTeX_Main-Regular.woff2",
    "KaTeX_Math-BoldItalic.woff2",
    "KaTeX_Math-Italic.woff2",
    "KaTeX_SansSerif-Bold.woff2",
    "KaTeX_SansSerif-Italic.woff2",
    "KaTeX_SansSerif-Regular.woff2",
    "KaTeX_Script-Regular.woff2",
    "KaTeX_Size1-Regular.woff2",
    "KaTeX_Size2-Regular.woff2",
    "KaTeX_Size3-Regular.woff2",
    "KaTeX_Size4-Regular.woff2",
    "KaTeX_Typewriter-Regular.woff2"
]

# Populating KaTeX fonts into assets dictionary
for font in KATEX_FONTS:
    ASSETS[f"fonts/{font}"] = f"https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/fonts/{font}"


async def download_file(client: httpx.AsyncClient, relative_path: str, url: str):
    """Download a single asset from URL and save it to the local relative path."""
    dest_path = os.path.join(VENDOR_DIR, relative_path)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    
    logger.info(f"Downloading CDN asset to {relative_path}...")
    try:
        response = await client.get(url, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        
        # Write to temporary file first, then swap to guarantee atomic write
        tmp_path = dest_path + ".tmp"
        with open(tmp_path, "wb") as f:
            f.write(response.content)
        os.replace(tmp_path, dest_path)
        
    except Exception as e:
        logger.error(f"Failed to download {relative_path} from {url}: {e}")
        # Clean up tmp file if it exists
        if os.path.exists(dest_path + ".tmp"):
            os.remove(dest_path + ".tmp")
        raise e


async def download_vendor_assets_if_missing():
    """Triggers first-run CDN download and local caching.

    The sentinel alone is not enough to skip: when a release adds new assets
    (a new editor, a new language), an existing install already has the
    sentinel from a previous run and would never fetch them. So always diff
    ASSETS against what is actually on disk, and only short-circuit when the
    sentinel exists AND nothing is missing.
    """
    os.makedirs(VENDOR_DIR, exist_ok=True)
    missing = {
        rel_path: url
        for rel_path, url in ASSETS.items()
        if not os.path.exists(os.path.join(VENDOR_DIR, rel_path))
    }

    if os.path.exists(SENTINEL_FILE) and not missing:
        logger.info("Local CDN vendor assets are fully cached. Skipping download.")
        return

    logger.info("Initializing CDN self-hosting bundling...")

    async with httpx.AsyncClient() as client:
        tasks = [download_file(client, rel_path, url) for rel_path, url in missing.items()]

        if tasks:
            logger.info(f"Downloading {len(tasks)} vendor assets to {VENDOR_DIR}...")
            # Run all downloads in parallel (with a limit on concurrency to avoid hammering servers)
            sem = asyncio.Semaphore(5)
            
            async def worker(task):
                async with sem:
                    await task
            
            await asyncio.gather(*(worker(t) for t in tasks))
            logger.info("All vendor assets successfully downloaded and cached locally.")
        else:
            logger.info("All specified assets are already present on disk.")
            
        # Create sentinel file to skip subsequent startup checks
        with open(SENTINEL_FILE, "w") as f:
            f.write("Download complete.")
