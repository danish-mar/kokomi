import os
import uuid
import datetime
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from app.config import SPACES_DIR
from app.storage import load_spaces, save_spaces

router = APIRouter(prefix="/api/spaces")

class SpaceCreate(BaseModel):
    name: str
    description: str = ""
    icon: str = "fa-solid fa-book"

class SpaceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None

@router.get("/")
async def list_spaces():
    spaces = load_spaces()
    return list(spaces.values())

@router.post("/")
async def create_space(s: SpaceCreate):
    spaces = load_spaces()
    sid = f"space_{uuid.uuid4().hex[:8]}"
    spaces[sid] = {
        "id": sid,
        "name": s.name,
        "description": s.description,
        "icon": s.icon,
        "files": [],
        "created_at": datetime.datetime.utcnow().isoformat()
    }
    
    # Create the physical directory for this space's files
    space_path = os.path.join(SPACES_DIR, sid)
    os.makedirs(space_path, exist_ok=True)
    
    save_spaces(spaces)
    return spaces[sid]

@router.get("/{space_id}")
async def get_space(space_id: str):
    spaces = load_spaces()
    if space_id not in spaces:
        raise HTTPException(404, "Space not found")
    return spaces[space_id]

@router.delete("/{space_id}")
async def delete_space(space_id: str):
    spaces = load_spaces()
    if space_id not in spaces:
        raise HTTPException(404, "Space not found")
    del spaces[space_id]
    save_spaces(spaces)
    # Note: We should probably also delete from Qdrant and local files here eventually
    return {"status": "ok"}

@router.post("/{space_id}/files")
async def upload_file_to_space(
    space_id: str,
    file: UploadFile = File(...)
):
    spaces = load_spaces()
    if space_id not in spaces:
        raise HTTPException(404, "Space not found")
        
    space = spaces[space_id]
    
    # Save the file physically
    file_id = f"file_{uuid.uuid4().hex[:8]}"
    ext = os.path.splitext(file.filename)[1].lower() if file.filename else ""
    safe_filename = f"{file_id}{ext}"
    
    file_path = os.path.join(SPACES_DIR, space_id, safe_filename)
    
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)
        
    # Process document into Qdrant RAG
    from app.rag import process_file_to_rag, delete_file_from_rag
    try:
        process_file_to_rag(file_path, space_id, file_id)
    except Exception as e:
        print(f"Error processing RAG: {e}")
        # Note: In production we'd probably want to abort the upload, but let's allow it for now.
    
    new_file = {
        "id": file_id,
        "filename": file.filename,
        "size": len(content),
        "uploaded_at": datetime.datetime.utcnow().isoformat()
    }
    
    space["files"].append(new_file)
    save_spaces(spaces)
    
    return new_file

@router.delete("/{space_id}/files/{file_id}")
async def delete_file_from_space(space_id: str, file_id: str):
    spaces = load_spaces()
    if space_id not in spaces:
        raise HTTPException(404, "Space not found")
        
    space = spaces[space_id]
    original_len = len(space["files"])
    space["files"] = [f for f in space["files"] if f["id"] != file_id]
    
    if len(space["files"]) == original_len:
        raise HTTPException(404, "File not found in space")
        
    save_spaces(spaces)
    
    from app.rag import delete_file_from_rag
    delete_file_from_rag(space_id, file_id)
    
    return {"status": "ok"}
