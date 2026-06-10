"""Recurring schedule triggers for workflow runs (create / list / update / delete)."""
import uuid
import datetime

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api")


@router.get("/workflows/schedules")
async def get_all_schedules():
    """Retrieve all active and inactive recurring workflow triggers."""
    from app.scheduler import load_schedules
    return list(load_schedules().values())

@router.post("/workflows/{run_id}/schedule")
async def schedule_workflow_run(run_id: str, payload: dict):
    """Schedule a specific workflow run to repeat periodically."""
    from app.workflow import load_workflows
    from app.scheduler import load_schedules, save_schedules, calculate_next_run

    db = load_workflows()
    if run_id not in db:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    wf = db[run_id]
    repeat_mode = payload.get("repeat_mode", "re_execute")
    interval = payload.get("interval", "daily")
    cron_str = payload.get("cron")

    schedules = load_schedules()
    schedule_id = f"sched_{uuid.uuid4().hex[:8]}"

    next_ts = calculate_next_run(interval, cron_str)

    sched_entry = {
        "id": schedule_id,
        "run_title": wf.get("run_title", "Scheduled Workflow"),
        "original_prompt": wf.get("user_request", ""),
        "source_run_id": run_id,
        "repeat_mode": repeat_mode,
        "interval": interval,
        "cron": cron_str,
        "next_run_ts": next_ts,
        "last_run_ts": None,
        "last_run_status": None,
        "created_at": datetime.datetime.now().isoformat(),
        "active": True
    }

    schedules[schedule_id] = sched_entry
    save_schedules(schedules)
    return sched_entry

@router.put("/workflows/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, payload: dict):
    """Update or toggle an existing workflow schedule."""
    from app.scheduler import load_schedules, save_schedules, calculate_next_run

    schedules = load_schedules()
    if schedule_id not in schedules:
        raise HTTPException(status_code=404, detail="Schedule not found")

    sched = schedules[schedule_id]

    if "active" in payload:
        sched["active"] = bool(payload["active"])
    if "interval" in payload:
        sched["interval"] = payload["interval"]
    if "repeat_mode" in payload:
        sched["repeat_mode"] = payload["repeat_mode"]
    if "cron" in payload:
        sched["cron"] = payload["cron"]

    # Recalculate next execution time if changed or toggled on
    if sched["active"]:
        sched["next_run_ts"] = calculate_next_run(sched["interval"], sched.get("cron"))

    schedules[schedule_id] = sched
    save_schedules(schedules)
    return sched

@router.delete("/workflows/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    """Delete a workflow schedule."""
    from app.scheduler import load_schedules, save_schedules

    schedules = load_schedules()
    if schedule_id not in schedules:
        raise HTTPException(status_code=404, detail="Schedule not found")

    del schedules[schedule_id]
    save_schedules(schedules)
    return {"ok": True}
