import uvicorn
from server import app

# Entry point for the Kokomi Web Application
if __name__ == "__main__":
    print("🌊 Starting Kokomi Web Interface...")
    print("📍 Point your browser to http://127.0.0.1:8000")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
