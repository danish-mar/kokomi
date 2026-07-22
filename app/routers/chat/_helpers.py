"""Shared helpers for the chat conversation endpoints: built-in tool builders
and lazy MCP pool initialization. Used by `conversation.py`."""
from langchain_core.tools import tool

from app.mcp import init_pool, pool_is_stale


@tool
def open_url(url: str) -> str:
    """Open an EXTERNAL destination — a web link or a native app URI — on the user's device.

    Supports standard web links (http/https) and native URI schemes:
    - tel:+91XXXXXXXXXX (Phone dialer)
    - mailto:you@gmail.com (Email client)
    - sms:+91XXXXXXXXXX (SMS app)
    - whatsapp://send?phone=91XXXXXXXXXX (WhatsApp)
    - youtube://watch?v=ID (YouTube app)
    - maps:?q=Location (Maps app)

    Call this ONLY when the user wants to reach a real external destination — a
    website, a phone number, an email address, a location, or an app on their
    device — AND you know the specific target.

    Do NOT call this:
    - for anything that happens inside this app. "Open a canvas", "open a
      document", "open an editor", "show me the code" are NOT this tool — they
      are handled by writing an artifact.
    - to open a placeholder or example address such as example.com. If you do
      not have a real, specific URL, do not call it at all.
    - merely because the word "open" or "play" appears in the request.
    """
    return f"Successfully triggered opening of {url}"


def _get_tavily_tool(prefs: dict):
    """Build a search tool (Tavily or SearxNG) from prefs, or None if not configured."""
    try:
        if not prefs.get("web_search_enabled"):
            return None

        provider = prefs.get("search_provider") or "tavily"

        if provider == "searxng":
            from langchain_core.tools import tool
            import httpx
            import json

            searxng_url = prefs.get("searxng_url") or "http://localhost:8080"
            searxng_url = searxng_url.rstrip("/")

            @tool("web_search")
            def searxng_search_tool(query: str, max_results: int = 5) -> str:
                """Search the web for up-to-date facts on a specific query, specifying the number of results desired (max_results)."""
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

            return searxng_search_tool

        from langchain_core.tools import tool
        from langchain_community.tools.tavily_search import TavilySearchResults
        import json

        api_key = prefs.get("tavily_api_key") or ""
        if not api_key:
            return None
        import os
        os.environ["TAVILY_API_KEY"] = api_key

        @tool("web_search")
        def tavily_search_tool(query: str, max_results: int = 5) -> str:
            """Search the web for up-to-date facts on a specific query, specifying the number of results desired (max_results)."""
            try:
                tavily = TavilySearchResults(max_results=max_results, tavily_api_key=api_key)
                res = tavily.invoke(query)
                return json.dumps(res)
            except Exception as e:
                return f"Tavily search failed: {str(e)}"

        return tavily_search_tool
    except Exception:
        return None


def _get_scrape_tool(prefs: dict):
    """Build a scrape_page tool from prefs, or None if not configured."""
    try:
        if not prefs.get("web_scrape_enabled"):
            return None

        from langchain_core.tools import tool
        import httpx
        from html.parser import HTMLParser

        @tool("scrape_page")
        def scrape_page_tool(url: str) -> str:
            """Scrape a webpage and return clean text content, stripped of JavaScript, CSS, and HTML tags."""
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

        return scrape_page_tool
    except Exception:
        return None


def _get_image_tool(prefs: dict):
    """Build a `search_images` tool (Tavily or SearxNG, per prefs) that returns
    real image URLs for the UI to display as a gallery. None if not configured."""
    try:
        if not prefs.get("image_search_enabled", True):
            return None

        from langchain_core.tools import tool
        import json

        provider = prefs.get("search_provider") or "tavily"
        DESC = (
            "Search the web for IMAGES of a subject (places, landmarks, products, people, "
            "food, animals, etc.). Returns image URLs that are shown to the user automatically "
            "as a gallery. Call this whenever the user asks to see/show a picture of something, "
            "or when images would clearly enrich your answer. Keep `query` short and visual."
        )

        if provider == "searxng":
            import httpx
            searxng_url = (prefs.get("searxng_url") or "http://localhost:8080").rstrip("/")

            @tool("search_images", description=DESC)
            def searxng_image_tool(query: str, count: int = 6) -> str:
                try:
                    n = max(1, min(int(count), 12))
                    params = {"q": query, "format": "json", "categories": "images"}
                    resp = httpx.get(f"{searxng_url}/search", params=params, timeout=12.0)
                    if resp.status_code != 200:
                        resp = httpx.get(f"{searxng_url}/", params=params, timeout=12.0)
                    if resp.status_code != 200:
                        return json.dumps({"query": query, "images": [], "error": f"SearxNG status {resp.status_code}"})
                    images = []
                    for r in (resp.json().get("results") or [])[:n]:
                        src = r.get("img_src") or r.get("thumbnail_src")
                        if not src:
                            continue
                        if src.startswith("//"):
                            src = "https:" + src
                        elif src.startswith("/"):
                            src = searxng_url + src
                        images.append({"url": src, "thumbnail": r.get("thumbnail_src") or src})
                    return json.dumps({"query": query, "images": images})
                except Exception as e:
                    return json.dumps({"query": query, "images": [], "error": str(e)})

            return searxng_image_tool

        # Tavily (default)
        api_key = prefs.get("tavily_api_key") or ""
        if not api_key:
            return None

        @tool("search_images", description=DESC)
        def tavily_image_tool(query: str, count: int = 6) -> str:
            try:
                from tavily import TavilyClient
                n = max(1, min(int(count), 12))
                res = TavilyClient(api_key=api_key).search(
                    query=query, include_images=True, max_results=3,
                )
                images = []
                for im in (res.get("images") or [])[:n]:
                    url = im.get("url") if isinstance(im, dict) else im
                    if url:
                        images.append({"url": url, "thumbnail": url})
                return json.dumps({"query": query, "images": images})
            except Exception as e:
                return json.dumps({"query": query, "images": [], "error": str(e)})

        return tavily_image_tool
    except Exception:
        return None


async def _ensure_pool():
    """Lazily initialize the MCP pool if it's stale or not ready."""
    if pool_is_stale():
        await init_pool()
