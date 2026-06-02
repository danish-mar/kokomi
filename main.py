import uvicorn
from app import app  # noqa: F401
from app.config import VERSION, RELEASE_NAME

# Entry point for the Kokomi Web Application
if __name__ == "__main__":
    print(f"🌊 Starting Kokomi Web Interface : ver : {VERSION}-{RELEASE_NAME}...")
    print("📍 Point your browser to http://0.0.0.0:8000")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True, reload_excludes=["data/*", "data/**/*"], timeout_graceful_shutdown=2)
