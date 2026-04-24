import asyncio
from google import genai
from google.genai import types
from app.config import GOOGLE_API_KEY

async def test():
    client = genai.Client(api_key=GOOGLE_API_KEY, http_options={"api_version": "v1alpha"})
    try:
        async with client.aio.live.connect(model="gemini-2.5-flash") as session:
            print("Connected!")
    except Exception as e:
        print("Error:", e)

asyncio.run(test())
