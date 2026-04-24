import uvicorn
from app import app  # noqa: F401

# Entry point for the Kokomi Web Application
if __name__ == "__main__":
    print("🌊 Starting Kokomi Web Interface...")
    print("📍 Point your browser to http://0.0.0.0:8000")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True, timeout_graceful_shutdown=2)
