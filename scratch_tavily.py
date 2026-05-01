import json
import asyncio
from langchain_community.tools.tavily_search import TavilySearchResults
import os

os.environ["TAVILY_API_KEY"] = "tvly-test"

async def main():
    try:
        t = TavilySearchResults(max_results=2, name="web_search")
        res = await t.ainvoke({"query": "test"})
        print("TYPE:", type(res))
        print("REPR:", repr(res))
    except Exception as e:
        print("ERROR:", e)

asyncio.run(main())
