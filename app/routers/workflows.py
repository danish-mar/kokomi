from fastapi import APIRouter
import json
import os
from app.config import DATA_DIR

router = APIRouter(prefix="/api/workflows")
WORKFLOWS_FILE = os.path.join(DATA_DIR, "workflows.json")

@router.get("")
async def get_workflows():
    if not os.path.exists(WORKFLOWS_FILE):
        return []
    try:
        with open(WORKFLOWS_FILE, "r") as f:
            return json.load(f)
    except:
        return []

@router.delete("")
async def clear_workflows():
    if os.path.exists(WORKFLOWS_FILE):
        os.remove(WORKFLOWS_FILE)
    return {"ok": True}
