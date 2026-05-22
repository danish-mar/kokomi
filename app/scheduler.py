import os
import json
import time
import uuid
import datetime
import asyncio
import traceback
from app.config import DATA_DIR
from app.storage import _load, _save

SCHEDULES_FILE = os.path.join(DATA_DIR, "scheduled_workflows.json")
_SCHEDULES_CACHE = {}

def load_schedules() -> dict:
    global _SCHEDULES_CACHE
    if not _SCHEDULES_CACHE:
        if not os.path.exists(SCHEDULES_FILE):
            _SCHEDULES_CACHE = {}
        else:
            data = _load(SCHEDULES_FILE)
            if isinstance(data, dict):
                _SCHEDULES_CACHE = data
            else:
                _SCHEDULES_CACHE = {}
    return _SCHEDULES_CACHE

def save_schedules(data: dict) -> None:
    global _SCHEDULES_CACHE
    _SCHEDULES_CACHE = data
    _save(SCHEDULES_FILE, data)

def calculate_next_run(interval: str, cron_str: str = None) -> float:
    now = time.time()
    if interval == "hourly":
        return now + 3600
    elif interval == "half_day":
        return now + 43200
    elif interval == "daily":
        return now + 86400
    elif interval == "weekly":
        return now + 604800
    elif interval == "monthly":
        return now + 2592000
    elif interval == "custom" and cron_str:
        try:
            from croniter import croniter
            base = datetime.datetime.now()
            iter = croniter(cron_str, base)
            return iter.get_next(datetime.datetime).timestamp()
        except ImportError:
            return now + 86400
    # Default to daily
    return now + 86400

async def execute_scheduled_workflow(schedule_id: str, sched: dict):
    from app.workflow import load_workflows, save_workflows, MultiAgentWorkflowEngine
    
    source_run_id = sched.get("source_run_id")
    repeat_mode = sched.get("repeat_mode", "re_execute")
    prompt = sched.get("original_prompt", "")
    
    db = load_workflows()
    
    new_run_id = None
    try:
        if repeat_mode == "re_execute" and source_run_id in db:
            # Clone plan
            wf_state = db[source_run_id]
            new_run_id = f"wf_{uuid.uuid4().hex[:8]}"
            wf_dir = os.path.join(DATA_DIR, "workflows", new_run_id)
            os.makedirs(wf_dir, exist_ok=True)
            
            # Reset tasks
            cloned_tasks = []
            for t in wf_state.get("tasks", []):
                cloned_tasks.append({
                    "task_id": t["task_id"],
                    "title": t["title"],
                    "description": t["description"],
                    "worker_type": t["worker_type"],
                    "depends_on": t.get("depends_on", []),
                    "allowed_tools": t["allowed_tools"],
                    "success_criteria": t.get("success_criteria", ""),
                    "expected_output_schema": t.get("expected_output_schema", {}),
                    "status": "pending",
                    "retries": 0,
                    "artifacts": []
                })
            
            cloned_state = {
                "run_id": new_run_id,
                "user_id": wf_state.get("user_id", "admin"),
                "user_request": wf_state.get("user_request", ""),
                "run_title": wf_state.get("run_title", "Scheduled Run"),
                "run_icon": wf_state.get("run_icon", "fa-clock"),
                "plan": wf_state.get("plan", {}),
                "tasks": cloned_tasks,
                "ready_queue": [],
                "running_tasks": [],
                "completed_tasks": [],
                "failed_tasks": [],
                "artifacts": [],
                "notifications": [f"⏰ Scheduled re-execution triggered"],
                "collaborative_chat": [{
                    "role": "user",
                    "sender": "System Scheduler",
                    "message": f"Automatically re-executing scheduled workflow plan from trigger template.",
                    "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }],
                "debug_logs": [],
                "final_result": None,
                "status": "pending",
                "created_at": datetime.datetime.now().isoformat(),
                "storage_dir": wf_dir,
                "parent_schedule_id": schedule_id
            }
            
            db[new_run_id] = cloned_state
            save_workflows(db)
            
            # Start background task to run it
            asyncio.create_task(MultiAgentWorkflowEngine.execute_run(new_run_id))
            
        else:
            # Re-generate from prompt
            if prompt:
                new_run_id = await MultiAgentWorkflowEngine.create_run(prompt)
                
                # Tag it with parent schedule
                db = load_workflows()
                if new_run_id in db:
                    db[new_run_id]["parent_schedule_id"] = schedule_id
                    save_workflows(db)
                
                asyncio.create_task(MultiAgentWorkflowEngine.execute_run(new_run_id))
        
        return new_run_id, "success"
    except Exception as e:
        print(f"Error executing scheduled workflow {schedule_id}: {str(e)}")
        traceback.print_exc()
        return None, f"error: {str(e)}"

async def start_scheduler_loop():
    print("⏰ Starting Atlas Workflow background scheduler loop...")
    while True:
        try:
            schedules = load_schedules()
            now = time.time()
            updated = False
            
            for sid, sched in list(schedules.items()):
                if not sched.get("active", True):
                    continue
                
                next_run = sched.get("next_run_ts", 0)
                if next_run <= now:
                    print(f"⏰ Triggering scheduled workflow: '{sched.get('run_title')}' (ID: {sid})")
                    # Run it
                    new_run_id, status = await execute_scheduled_workflow(sid, sched)
                    
                    # Update schedule details
                    sched["last_run_ts"] = now
                    sched["last_run_status"] = status
                    if new_run_id:
                        sched["last_run_id"] = new_run_id
                    
                    # Calculate next runtime
                    sched["next_run_ts"] = calculate_next_run(sched.get("interval", "daily"), sched.get("cron"))
                    updated = True
            
            if updated:
                save_schedules(schedules)
                
        except Exception as e:
            print(f"Error in scheduler background loop: {str(e)}")
            traceback.print_exc()
            
        await asyncio.sleep(30) # check every 30 seconds
