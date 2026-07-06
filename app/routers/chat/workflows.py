"""Multi-agent workflow run lifecycle: create, inspect, collaborate, stop,
restart, recreate, delete, and restart individual task nodes."""
import asyncio
import datetime
import os

from fastapi import APIRouter, HTTPException

from app.workflow import MultiAgentWorkflowEngine, load_workflows, save_workflows

router = APIRouter(prefix="/api")


# ── Recurring schedules ───────────────────────────────────────────────────
# These must be registered ahead of the /workflows/{run_id} routes below:
# FastAPI matches routes in registration order, so GET /workflows/schedules
# would otherwise be swallowed by GET /workflows/{run_id} (run_id="schedules").
@router.get("/workflows/schedules")
async def list_schedules():
    """List all recurring workflow schedules."""
    from app.scheduler import load_schedules
    return list(load_schedules().values())


@router.put("/workflows/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, payload: dict):
    """Patch a schedule's settings (repeat_mode/interval/cron/active)."""
    from app.scheduler import load_schedules, save_schedules, calculate_next_run
    schedules = load_schedules()
    sched = schedules.get(schedule_id)
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")

    recompute = False
    for field in ("repeat_mode", "interval", "cron"):
        if field in payload and payload[field] != sched.get(field):
            sched[field] = payload[field]
            recompute = True
    if "active" in payload:
        sched["active"] = bool(payload["active"])

    if recompute:
        sched["next_run_ts"] = calculate_next_run(sched.get("interval", "daily"), sched.get("cron"))

    schedules[schedule_id] = sched
    save_schedules(schedules)
    return sched


@router.delete("/workflows/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    """Remove a recurring workflow schedule."""
    from app.scheduler import load_schedules, save_schedules
    schedules = load_schedules()
    if schedule_id not in schedules:
        raise HTTPException(status_code=404, detail="Schedule not found")
    schedules.pop(schedule_id)
    save_schedules(schedules)
    return {"status": "deleted"}


@router.post("/workflows/{run_id}/schedule")
async def create_schedule(run_id: str, payload: dict):
    """Create a recurring schedule that re-triggers this workflow run."""
    import uuid
    from app.scheduler import load_schedules, save_schedules, calculate_next_run

    db = load_workflows()
    if run_id not in db:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    state = db[run_id]

    interval = payload.get("interval", "daily")
    cron = payload.get("cron", "")
    schedule_id = uuid.uuid4().hex[:8]
    sched = {
        "id": schedule_id,
        "source_run_id": run_id,
        "run_title": state.get("run_title", "Scheduled Run"),
        "original_prompt": state.get("user_request", ""),
        "repeat_mode": payload.get("repeat_mode", "re_execute"),
        "interval": interval,
        "cron": cron,
        "active": True,
        "created_at": datetime.datetime.now().isoformat(),
        "next_run_ts": calculate_next_run(interval, cron),
        "last_run_ts": None,
        "last_run_id": None,
        "last_run_status": None,
    }

    schedules = load_schedules()
    schedules[schedule_id] = sched
    save_schedules(schedules)
    return sched


@router.get("/workflows")
async def get_workflows():
    """Retrieve all multi-agent LangGraph workflow execution runs."""
    return load_workflows()

@router.get("/workflows/{run_id}")
async def get_workflow_details(run_id: str):
    """Retrieve the high-fidelity state graph for a specific workflow run."""
    db = load_workflows()
    if run_id not in db:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return db[run_id]

@router.post("/workflows")
async def create_workflow_run(payload: dict):
    """Plan a new multi-agent workflow. Returns a reviewable DRAFT (the DAG is
    generated but not executed). The user edits/approves it and calls
    POST /workflows/{run_id}/start to run. Pass auto_start=true to run immediately."""
    query = payload.get("message")
    if not query:
        raise HTTPException(status_code=400, detail="Query message is required")
    run_id = await MultiAgentWorkflowEngine.create_run(query)
    if payload.get("auto_start"):
        db = load_workflows()
        if run_id in db:
            db[run_id]["status"] = "pending"
            save_workflows(db)
        asyncio.create_task(MultiAgentWorkflowEngine.execute_run(run_id))
        return {"run_id": run_id, "status": "pending"}
    return {"run_id": run_id, "status": "draft"}


@router.post("/workflows/{run_id}/start")
async def start_workflow_run(run_id: str):
    """Approve a draft plan and begin execution."""
    db = load_workflows()
    if run_id not in db:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    state = db[run_id]
    if state.get("status") not in ("draft", "pending", "paused"):
        raise HTTPException(status_code=409, detail=f"Cannot start run in status '{state.get('status')}'")
    state["status"] = "pending"
    state["notifications"].append("▶️ Plan approved — starting execution")
    save_workflows(db)
    asyncio.create_task(MultiAgentWorkflowEngine.execute_run(run_id))
    return {"run_id": run_id, "status": "pending"}


@router.put("/workflows/{run_id}/plan")
async def update_workflow_plan(run_id: str, payload: dict):
    """Edit a draft/paused plan's task nodes before (or between) execution.
    Body: { "tasks": [ {task_id, title, description, worker_type, depends_on,
    allowed_tools, checkpoint}, ... ] }. Only allowed while draft or paused."""
    db = load_workflows()
    if run_id not in db:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    state = db[run_id]
    if state.get("status") not in ("draft", "paused"):
        raise HTTPException(status_code=409, detail="Plan can only be edited while draft or paused")

    incoming = payload.get("tasks")
    if not isinstance(incoming, list) or not incoming:
        raise HTTPException(status_code=400, detail="tasks must be a non-empty list")

    existing = {t["task_id"]: t for t in state["tasks"]}
    completed = set(state.get("completed_tasks", []))
    new_tasks = []
    seen = set()
    for t in incoming:
        tid = str(t.get("task_id") or "").strip()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        prev = existing.get(tid, {})
        # Never let an edit mutate a task that already ran.
        if tid in completed and prev:
            new_tasks.append(prev)
            continue
        new_tasks.append({
            **prev,
            "task_id": tid,
            "title": t.get("title", prev.get("title", tid)),
            "description": t.get("description", prev.get("description", "")),
            "worker_type": t.get("worker_type", prev.get("worker_type", "researcher")),
            "depends_on": t.get("depends_on", prev.get("depends_on", [])),
            "allowed_tools": t.get("allowed_tools", prev.get("allowed_tools", [])),
            "checkpoint": bool(t.get("checkpoint", prev.get("checkpoint", False))),
            "checkpoint_cleared": bool(prev.get("checkpoint_cleared", False)),
            "status": prev.get("status", "pending"),
            "retries": prev.get("retries", 0),
            "artifacts": prev.get("artifacts", []),
        })
    if not new_tasks:
        raise HTTPException(status_code=400, detail="No valid tasks after validation")

    state["tasks"] = new_tasks
    if isinstance(state.get("plan"), dict):
        state["plan"]["tasks"] = new_tasks
    state["notifications"].append("✏️ Plan updated")
    save_workflows(db)
    return {"run_id": run_id, "tasks": new_tasks}


@router.post("/workflows/{run_id}/checkpoint/{task_id}")
async def resolve_checkpoint(run_id: str, task_id: str, payload: dict):
    """Approve or reject a checkpoint the run is paused at. Body: { "approve": bool }."""
    approve = bool(payload.get("approve", True))
    try:
        return await MultiAgentWorkflowEngine.clear_checkpoint(run_id, task_id, approve)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

@router.post("/workflows/{run_id}/chat")
async def chat_with_workflow_supervisor(run_id: str, payload: dict):
    """Send a collaborative instruction/message directly to the active workflow's supervisor."""
    from app.workflow import load_workflows, save_workflows, load_prefs, MultiAgentWorkflowEngine
    from app.llm import get_atlas_llm
    from langchain_core.messages import SystemMessage, HumanMessage
    import json

    query = payload.get("message")
    if not query:
        raise HTTPException(status_code=400, detail="Query message is required")

    db = load_workflows()
    if run_id not in db:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    state = db[run_id]

    # Record user message
    state["notifications"].append(f"💬 User: {query}")
    state["debug_logs"].append(f"Received interactive instruction: {query}")
    state.setdefault("collaborative_chat", []).append({
        "role": "user",
        "sender": "User",
        "message": query,
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

    # Auto-start execution if the user request is a start/resume command
    query_lower = query.lower().strip(" .!?,")
    start_keywords = ["start", "run", "execute", "go", "launch", "kick off", "begin", "resume", "play", "start it"]
    if any(k in query_lower for k in start_keywords) and state.get("status") not in ["running"]:
        state["status"] = "pending"
        state["notifications"].append("🤖 Supervisor: Starting/Resuming workflow execution as requested.")
        state.setdefault("collaborative_chat", []).append({
            "role": "assistant",
            "sender": "Supervisor",
            "message": "Understood! I am launching the workflow execution engine now.",
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        db[run_id] = state
        save_workflows(db)
        asyncio.create_task(MultiAgentWorkflowEngine.execute_run(run_id))
        return {"status": "started", "response": "Launching workflow execution engine."}

    # Load LLM
    prefs = load_prefs()
    llm = get_atlas_llm(prefs, streaming=False)

    # Context summary
    tasks_summary = []
    for t in state["tasks"]:
        out_summary = "None"
        if t.get("output"):
            out_summary = json.dumps(t["output"])[:300] + "..." if len(json.dumps(t["output"])) > 300 else json.dumps(t["output"])
        tasks_summary.append({
            "task_id": t["task_id"],
            "title": t["title"],
            "worker_type": t["worker_type"],
            "status": t["status"],
            "output_summary": out_summary
        })

    prompt = (
        "You are the Top-Level Workflow Supervisor. The user is collaborating with you on an active workflow.\n"
        f"Workflow Goal: {state['plan'].get('goal')}\n"
        f"Active Tasks State:\n{json.dumps(tasks_summary, indent=2)}\n\n"
        f"User's Instruction: {query}\n\n"
        "You have two options to respond:\n"
        "1. CONVERSATION: If the user is asking a question, asking for status, or requesting a simple clarification, answer them directly. "
        "Format your answer as a clear markdown explanation.\n"
        "2. APPEND_TASKS: If the user is requesting new actions, modifications, or additions that require specialized worker execution (e.g. searching, writing, exporting PDF, emailing, executing shell commands), define the new task nodes to be appended to the workflow.\n\n"
        "Respond ONLY with a valid JSON matching this schema:\n"
        "{\n"
        '  "response_type": "conversation" or "append_tasks",\n'
        '  "conversation_text": "Your markdown answer (if response_type is conversation)",\n'
        '  "new_tasks": [\n'
        "    {\n"
        '      "task_id": "t_new_1",\n'
        '      "title": "Task title",\n'
        '      "description": "Specific dynamic details for this worker",\n'
        '      "worker_type": "researcher" or "writer" or "pdf_worker" or "email_worker" or "code_worker",\n'
        '      "depends_on": [],  # Specify dependencies. You can depend on existing tasks like \"t1\", \"t2\", etc.\n'
        '      "allowed_tools": ["web_search"],\n'
        '      "success_criteria": "Criteria"\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw_text = response.content.strip()
        # Strip <think> tags from reasoning models if present
        if "<think>" in raw_text:
            import re as _re_think
            raw_text = _re_think.sub(r'<think>.*?</think>', '', raw_text, flags=_re_think.DOTALL).strip()

        clean_text = raw_text
        if "```json" in clean_text:
            clean_text = clean_text.split("```json")[1].split("```")[0].strip()
        elif "```" in clean_text:
            clean_text = clean_text.split("```")[1].split("```")[0].strip()

        try:
            res = json.loads(clean_text)
        except Exception:
            # Fall back gracefully to conversation mode if parsing fails!
            res = {
                "response_type": "conversation",
                "conversation_text": raw_text
            }

        if res.get("response_type") == "conversation":
            text = res.get("conversation_text", "")
            state["notifications"].append(f"🤖 Supervisor: {text}")
            state.setdefault("collaborative_chat", []).append({
                "role": "assistant",
                "sender": "Supervisor",
                "message": text,
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            if state.get("final_result"):
                state["final_result"] = state["final_result"] + "\n\n---\n\n" + text
            else:
                state["final_result"] = text
            db[run_id] = state
            save_workflows(db)
            return {"status": "conversation", "response": text}

        elif res.get("response_type") == "append_tasks" and res.get("new_tasks"):
            new_tasks = res["new_tasks"]
            start_num = len(state["tasks"]) + 1
            for idx, nt in enumerate(new_tasks):
                nt["task_id"] = f"t{start_num + idx}"
                nt["status"] = "pending"
                nt["retries"] = 0
                nt["artifacts"] = []
                state["tasks"].append(nt)
                state["notifications"].append(f"➕ Supervisor added new task: '{nt['title']}' ({nt['worker_type']})")

            state.setdefault("collaborative_chat", []).append({
                "role": "assistant",
                "sender": "Supervisor",
                "message": f"Successfully planned and appended {len(new_tasks)} new specialized execution tasks to the pipeline.",
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })

            # Reset workflow status to pending to execute new tasks!
            state["status"] = "pending"
            db[run_id] = state
            save_workflows(db)

            asyncio.create_task(MultiAgentWorkflowEngine.execute_run(run_id))
            return {"status": "tasks_appended", "count": len(new_tasks)}

    except Exception as e:
        state["notifications"].append(f"⚠️ Supervisor error processing instruction: {str(e)}")
        db[run_id] = state
        save_workflows(db)
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "ignored"}

@router.post("/workflows/{run_id}/stop")
async def stop_workflow(run_id: str):
    from app.workflow import load_workflows as _lw, save_workflows as _sw, MultiAgentWorkflowEngine
    db = _lw()
    if run_id not in db:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if db[run_id]["status"] == "running":
        db[run_id]["status"] = "failed"
        db[run_id]["final_result"] = "Workflow manually stopped by user."
        db[run_id].setdefault("debug_logs", []).append(f"[{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 🛑 Workflow manually stopped")
        _sw(db)

    # Cancel active asyncio Task if running
    active_task = MultiAgentWorkflowEngine.active_tasks.get(run_id)
    if active_task:
        active_task.cancel()
        print(f"🛑 [STOP] Forcefully cancelled active asyncio task for workflow {run_id}")

    return {"status": "stopped"}

@router.post("/workflows/{run_id}/restart")
async def restart_workflow(run_id: str):
    from app.workflow import load_workflows as _lw, save_workflows as _sw, MultiAgentWorkflowEngine
    db = _lw()
    if run_id not in db:
        raise HTTPException(status_code=404, detail="Workflow not found")
    state = db[run_id]
    if state["status"] in ["failed", "completed"]:
        # Restart all incomplete/failed tasks
        failed_set = set(state.get("failed_tasks", []))
        for t in state.get("tasks", []):
            if t["task_id"] in failed_set or t["status"] not in ["completed"]:
                t["status"] = "pending"
                t["retries"] = 0
        state["failed_tasks"] = []
        state["status"] = "pending"
        state["final_result"] = None
        state.setdefault("debug_logs", []).append(f"[{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 🔄 Workflow restarted")
        _sw(db)
        asyncio.create_task(MultiAgentWorkflowEngine.execute_run(run_id))
    return {"status": "restarted"}

@router.post("/workflows/{run_id}/recreate")
async def recreate_workflow(run_id: str):
    from app.workflow import load_workflows as _lw, MultiAgentWorkflowEngine
    db = _lw()
    if run_id not in db:
        raise HTTPException(status_code=404, detail="Workflow not found")
    state = db[run_id]
    prompt = state.get("user_request", "")
    if not prompt:
        raise HTTPException(status_code=400, detail="Original prompt not found for this workflow")

    new_run_id = await MultiAgentWorkflowEngine.create_run(prompt)
    asyncio.create_task(MultiAgentWorkflowEngine.execute_run(new_run_id))
    return {"run_id": new_run_id, "status": "pending"}

@router.delete("/workflows/{run_id}")
async def delete_workflow(run_id: str):
    """Delete a workflow run and its storage directory."""
    from app.workflow import load_workflows as _lw, save_workflows as _sw
    db = _lw()
    if run_id not in db:
        raise HTTPException(status_code=404, detail="Workflow not found")
    wf = db.pop(run_id)
    _sw(db)

    # Cleanup Docker sandbox container if present
    try:
        from app.container import SandboxManager
        SandboxManager.stop_container(run_id)
    except Exception:
        pass
    # Clean storage dir
    sdir = wf.get("storage_dir")
    if sdir and os.path.isdir(sdir):
        import shutil as _shutil
        _shutil.rmtree(sdir, ignore_errors=True)
    return {"status": "deleted"}

@router.post("/workflows/{run_id}/restart-node")
async def restart_workflow_node(run_id: str, payload: dict):
    from app.workflow import load_workflows, save_workflows, MultiAgentWorkflowEngine
    task_id = payload.get("task_id")
    if not task_id:
        raise HTTPException(status_code=400, detail="Task ID is required")

    db = load_workflows()
    if run_id not in db:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    state = db[run_id]

    # Reset target task node
    target_task = None
    for t in state["tasks"]:
        if t["task_id"] == task_id:
            target_task = t
            break

    if not target_task:
        raise HTTPException(status_code=404, detail="Task node not found in this workflow")

    # Reset the task attributes
    target_task["status"] = "pending"
    target_task["retries"] = 0
    target_task.pop("error", None)

    # Remove from lists safely
    if task_id in state.get("failed_tasks", []):
        state["failed_tasks"].remove(task_id)
    if task_id in state.get("completed_tasks", []):
        state["completed_tasks"].remove(task_id)
    if task_id in state.get("running_tasks", []):
        state["running_tasks"].remove(task_id)

    # Reset overall workflow state status to allow running again
    state["status"] = "running"
    state["final_result"] = None
    state.setdefault("notifications", []).append(f"🔄 Restarting task node '{target_task['title']}'...")
    state.setdefault("debug_logs", []).append(f"User requested restart of task node '{target_task['title']}' ({task_id})")

    db[run_id] = state
    save_workflows(db)

    # Launch execution background loop again to continue the run!
    asyncio.create_task(MultiAgentWorkflowEngine.execute_run(run_id))

    return {"ok": True, "status": "running"}
