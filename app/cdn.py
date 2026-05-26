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
    
    # Pyodide
    "pyodide.js": "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/pyodide.js",
    
    # Marked
    "marked.min.js": "https://cdn.jsdelivr.net/npm/marked/marked.min.js",
    
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
    """Triggers first-run CDN download and local caching."""
    if os.path.exists(SENTINEL_FILE):
        logger.info("Local CDN vendor assets are fully cached. Skipping download.")
        return

    logger.info("Initializing first-run CDN self-hosting bundling...")
    os.makedirs(VENDOR_DIR, exist_ok=True)
    
    async with httpx.AsyncClient() as client:
        tasks = []
        for rel_path, url in ASSETS.items():
            # If the file already exists on disk, we can skip downloading it
            full_path = os.path.join(VENDOR_DIR, rel_path)
            if not os.path.exists(full_path):
                tasks.append(download_file(client, rel_path, url))
        
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
