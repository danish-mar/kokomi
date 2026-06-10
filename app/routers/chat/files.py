"""Per-workflow file explorer: list, upload, download, zip, mkdir, touch
within a workflow's isolated storage directory."""
import os
import datetime
import shutil

from fastapi import APIRouter, HTTPException, File, UploadFile

router = APIRouter(prefix="/api")


@router.get("/workflows/{run_id}/files")
async def list_workflow_files(run_id: str, path: str = ""):
    """List all files and subdirectories in a workflow's storage directory relative to path."""
    from app.config import DATA_DIR
    base_dir = os.path.join(DATA_DIR, "workflows", run_id)
    if not os.path.isdir(base_dir):
        os.makedirs(base_dir, exist_ok=True)

    # Resolve target directory cleanly
    target_dir = os.path.abspath(os.path.join(base_dir, path.strip("/")))
    if not target_dir.startswith(os.path.abspath(base_dir)):
        raise HTTPException(status_code=400, detail="Directory traversal detected")

    if not os.path.isdir(target_dir):
        os.makedirs(target_dir, exist_ok=True)

    items = []
    for f in os.listdir(target_dir):
        fpath = os.path.join(target_dir, f)
        is_dir = os.path.isdir(fpath)
        items.append({
            "name": f,
            "is_dir": is_dir,
            "size": 0 if is_dir else os.path.getsize(fpath),
            "modified": datetime.datetime.fromtimestamp(os.path.getmtime(fpath)).isoformat()
        })
    return items

@router.post("/workflows/{run_id}/upload")
async def upload_workflow_file(run_id: str, path: str = "", file: UploadFile = File(...)):
    """Upload a file to a workflow's storage directory or subdirectory."""
    from app.config import DATA_DIR
    base_dir = os.path.join(DATA_DIR, "workflows", run_id)
    target_dir = os.path.abspath(os.path.join(base_dir, path.strip("/")))
    if not target_dir.startswith(os.path.abspath(base_dir)):
        raise HTTPException(status_code=400, detail="Invalid path")

    os.makedirs(target_dir, exist_ok=True)
    fpath = os.path.join(target_dir, file.filename)
    with open(fpath, "wb") as buf:
        shutil.copyfileobj(file.file, buf)
    return {"name": file.filename, "size": os.path.getsize(fpath)}

@router.get("/workflows/{run_id}/download")
async def download_workflow_file(run_id: str, filepath: str):
    """Download a file from a workflow's storage directory or subdirectory."""
    from app.config import DATA_DIR
    from fastapi.responses import FileResponse
    base_dir = os.path.join(DATA_DIR, "workflows", run_id)

    # 1. Primary check inside isolated base_dir
    fpath = os.path.abspath(os.path.join(base_dir, filepath.lstrip("/")))
    if fpath.startswith(os.path.abspath(base_dir)) and os.path.isfile(fpath):
        return FileResponse(fpath, filename=os.path.basename(fpath))

    # 2. Resilient check in DATA_DIR, public uploads folder, or relative project path
    proj_root = os.path.abspath(os.path.dirname(os.path.abspath(DATA_DIR)))
    candidates = [
        filepath,
        os.path.join(DATA_DIR, filepath.lstrip("/")),
        os.path.join(DATA_DIR, "uploads", os.path.basename(filepath)),
        os.path.join(DATA_DIR, "workflows", run_id, "uploads", os.path.basename(filepath))
    ]
    for candidate in candidates:
        cand_abs = os.path.abspath(candidate)
        if cand_abs.startswith(proj_root) and os.path.isfile(cand_abs):
            return FileResponse(cand_abs, filename=os.path.basename(cand_abs))

    raise HTTPException(status_code=404, detail=f"File not found: {filepath}")

@router.get("/workflows/{run_id}/download_zip")
async def download_workflow_zip(run_id: str):
    """Download the entire workflow storage directory as a zip file."""
    from app.config import DATA_DIR
    from fastapi.responses import FileResponse
    import shutil
    base_dir = os.path.join(DATA_DIR, "workflows", run_id)
    if not os.path.exists(base_dir):
        raise HTTPException(status_code=404, detail="Workflow directory not found")

    zip_path = os.path.join(DATA_DIR, f"{run_id}_export.zip")
    shutil.make_archive(zip_path.replace('.zip', ''), 'zip', base_dir)

    return FileResponse(zip_path, media_type="application/zip", filename=f"{run_id}_workspace.zip")

@router.post("/workflows/{run_id}/mkdir")
async def make_workflow_dir(run_id: str, data: dict):
    """Create a new folder inside the workflow's storage folder."""
    from app.config import DATA_DIR
    path = data.get("path", "")
    folder_name = data.get("name", "").strip()
    if not folder_name:
        raise HTTPException(status_code=400, detail="Folder name is required")

    base_dir = os.path.join(DATA_DIR, "workflows", run_id)
    target_dir = os.path.abspath(os.path.join(base_dir, path.strip("/"), folder_name))
    if not target_dir.startswith(os.path.abspath(base_dir)):
        raise HTTPException(status_code=400, detail="Invalid path")

    os.makedirs(target_dir, exist_ok=True)
    return {"status": "success", "path": path}

@router.post("/workflows/{run_id}/touch")
async def touch_workflow_file(run_id: str, data: dict):
    """Create an empty file inside the workflow's storage folder."""
    from app.config import DATA_DIR
    path = data.get("path", "")
    filename = data.get("name", "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    base_dir = os.path.join(DATA_DIR, "workflows", run_id)
    target_file = os.path.abspath(os.path.join(base_dir, path.strip("/"), filename))
    if not target_file.startswith(os.path.abspath(base_dir)):
        raise HTTPException(status_code=400, detail="Invalid path")

    os.makedirs(os.path.dirname(target_file), exist_ok=True)
    with open(target_file, "w") as f:
        f.write("")
    return {"status": "success", "path": path}
