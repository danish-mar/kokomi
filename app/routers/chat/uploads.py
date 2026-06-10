"""File upload endpoint for the chat UI."""
import os
import uuid
import shutil

from fastapi import APIRouter, File, UploadFile, HTTPException

router = APIRouter(prefix="/api")


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file to the data/uploads directory."""
    try:
        file_id = f"{uuid.uuid4()}_{file.filename}"
        file_path = os.path.join("data/uploads", file_id)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        return {
            "id": file_id,
            "filename": file.filename,
            "size": os.path.getsize(file_path),
            "content_type": file.content_type,
            "url": f"/uploads/{file_id}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
