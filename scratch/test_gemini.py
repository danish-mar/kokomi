import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")
client = genai.Client(api_key=api_key)

models_to_test = ['gemini-2.5-flash', 'gemini-2.0-flash', 'models/gemini-2.5-flash']

for m in models_to_test:
    try:
        response = client.models.generate_content(
            model=m,
            contents="Hello"
        )
        print(f"Success with {m}")
        print(response.text)
    except Exception as e:
        print(f"Error with {m}: {e}")
