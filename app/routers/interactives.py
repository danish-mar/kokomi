"""
app/routers/interactives.py — Persist interactive component state (checkbox checks, etc.)
"""
from datetime import datetime, timezone
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List
from sqlalchemy import select
from app.db import _session, InteractiveStateRow, j_loads, j_dumps

router = APIRouter(prefix="/api/interactives", tags=["interactives"])


class StatePayload(BaseModel):
    checked: List[int] = []


@router.get("/{iid}")
async def get_state(iid: str):
    async with _session() as session:
        row = await session.get(InteractiveStateRow, iid)
        checked = j_loads(row.state_json) if row else []
        return {"iid": iid, "checked": checked or []}


@router.post("/{iid}")
async def save_state(iid: str, payload: StatePayload):
    now = datetime.now(timezone.utc).isoformat()
    async with _session() as session:
        row = await session.get(InteractiveStateRow, iid)
        if row:
            row.state_json = j_dumps(payload.checked)
            row.updated_at = now
        else:
            session.add(InteractiveStateRow(
                iid=iid,
                state_json=j_dumps(payload.checked),
                updated_at=now,
            ))
    return {"status": "ok", "iid": iid, "checked": payload.checked}
