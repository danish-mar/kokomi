"""WebSocket feeds that poll workflow state and push live updates to the UI."""
import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.workflow import load_workflows

router = APIRouter(prefix="/api")


@router.websocket("/ws/workflows")
async def ws_workflows_summary(websocket: WebSocket):
    await websocket.accept()
    last_hash = None
    try:
        while True:
            db = load_workflows()

            summary = []
            for rid, wf in db.items():
                summary.append({
                    "run_id": rid,
                    "run_title": wf.get("run_title", rid),
                    "status": wf.get("status", "pending"),
                    "created_at": wf.get("created_at", "")
                })

            summary.sort(key=lambda x: x.get("created_at", ""), reverse=True)

            import json
            current_hash = hash(json.dumps(summary, sort_keys=True))
            if current_hash != last_hash:
                await websocket.send_json({
                    "type": "workflows_list",
                    "workflows": summary
                })
                last_hash = current_hash

            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass

@router.websocket("/ws/workflows/{run_id}")
async def ws_workflow_detail(websocket: WebSocket, run_id: str):
    await websocket.accept()
    last_hash = None
    try:
        while True:
            db = load_workflows()
            if run_id not in db:
                await websocket.send_json({"type": "error", "message": "Workflow not found"})
                await asyncio.sleep(2)
                continue

            wf = db[run_id]
            payload = {
                "run_id": run_id,
                "run_title": wf.get("run_title", ""),
                "status": wf.get("status", "pending"),
                "plan": wf.get("plan", None),
                "tasks": wf.get("tasks", []),
                "artifacts": wf.get("artifacts", []),
                "final_result": wf.get("final_result", ""),
                "notifications": wf.get("notifications", []),
                "collaborative_chat": wf.get("collaborative_chat", []),
                "debug_logs": wf.get("debug_logs", []),

                "run_icon": wf.get("run_icon", ""),
                "created_at": wf.get("created_at", None),
                "started_at": wf.get("started_at", None),
                "completed_at": wf.get("completed_at", None)
            }

            import json
            current_hash = hash(json.dumps(payload, default=str, sort_keys=True))
            if current_hash != last_hash:
                await websocket.send_json({
                    "type": "workflow_detail",
                    "run_id": run_id,
                    "workflow": payload
                })
                last_hash = current_hash

            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
