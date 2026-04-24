import asyncio
from google import genai
from google.genai import types
from app.config import GOOGLE_API_KEY

async def test():
    client = genai.Client(api_key=GOOGLE_API_KEY)
    try:
        async with client.aio.live.connect(model="gemini-2.0-flash-exp") as session:
            print("Connected gemini-2.0-flash-exp!")
    except Exception as e:
        print("Error:", e)

asyncio.run(test())
