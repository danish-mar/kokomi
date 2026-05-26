import datetime
import asyncio
from typing import Dict, Any
from collections import defaultdict

from sqlalchemy import select
from app.db import _session, InsightRow


# ─────────────────────────────────────────────────────────────────────────────
# Write path — async insert into SQLite
# ─────────────────────────────────────────────────────────────────────────────

async def _insert_insight(metadata: Dict[str, Any]) -> None:
    """Insert a single telemetry record into the insights table."""
    async with _session() as sess:
        sess.add(InsightRow(
            timestamp=metadata.get("timestamp", datetime.datetime.utcnow().isoformat()),
            model=metadata.get("model"),
            source=metadata.get("source"),
            session_id=metadata.get("session_id"),
            tps=metadata.get("tps"),
            ttft=metadata.get("ttft"),
            prompt_tokens=metadata.get("prompt_tokens"),
            completion_tokens=metadata.get("completion_tokens"),
            context_used=metadata.get("context_used"),
        ))


def log_generation_sync(metadata: Dict[str, Any]) -> None:
    """Synchronously insert a generation record (called from thread pool)."""
    try:
        if "timestamp" not in metadata:
            metadata["timestamp"] = datetime.datetime.utcnow().isoformat()
        asyncio.run(_insert_insight(metadata))
    except Exception as e:
        print(f"Error writing insight to SQLite: {e}")


async def log_generation(metadata: Dict[str, Any]) -> None:
    """Non-blocking async telemetry insertion — schedules a background task."""
    if "timestamp" not in metadata:
        metadata["timestamp"] = datetime.datetime.utcnow().isoformat()
    try:
        await _insert_insight(metadata)
    except Exception as e:
        print(f"Error writing insight to SQLite: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Read path — query SQLite
# ─────────────────────────────────────────────────────────────────────────────

def get_insights_data(view: str = "daily", source: str = "all") -> Dict[str, Any]:
    """
    Aggregate telemetry data for charts and tables.
    view: "daily" (rolling 30 days) or "monthly" (rolling 12 months)
    source: "all" | "chat" | "workflow"
    """
    import asyncio

    async def _query():
        async with _session() as sess:
            stmt = select(InsightRow).order_by(InsightRow.id.asc())
            result = await sess.execute(stmt)
            return result.scalars().all()

    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            rows = pool.submit(asyncio.run, _query()).result()
    except RuntimeError:
        rows = asyncio.run(_query())

    if not rows:
        return {"charts": {}, "summary": []}

    now = datetime.datetime.utcnow()
    limit_days = 30 if view == "daily" else (12 * 30)

    tps_data = defaultdict(lambda: defaultdict(list))
    ttft_data = defaultdict(lambda: defaultdict(list))
    context_data = defaultdict(lambda: defaultdict(list))
    summary = defaultdict(lambda: {"tps": [], "ttft": [], "context": [], "count": 0})

    for row in rows:
        try:
            # Source filter
            data_source = row.source or "chat"
            if source != "all" and data_source != source:
                continue

            if not row.timestamp:
                continue
            ts = datetime.datetime.fromisoformat(row.timestamp)
            if (now - ts).days > limit_days:
                continue

            date_key = ts.strftime("%Y-%m-%d") if view == "daily" else ts.strftime("%Y-%m")
            model = row.model or "unknown"

            if row.tps is not None:
                tps_data[model][date_key].append(row.tps)
                summary[model]["tps"].append(row.tps)
            if row.ttft is not None:
                ttft_data[model][date_key].append(row.ttft)
                summary[model]["ttft"].append(row.ttft)
            if row.context_used is not None:
                context_data[model][date_key].append(row.context_used)
                summary[model]["context"].append(row.context_used)
            summary[model]["count"] += 1
        except Exception:
            continue

    def format_chart(data_map):
        all_dates = sorted(set(d for m in data_map.values() for d in m.keys()))
        series = []
        for model, dates in data_map.items():
            vals = []
            for d in all_dates:
                if d in dates:
                    vals.append(round(sum(dates[d]) / len(dates[d]), 2))
                else:
                    vals.append(None)
            label = model
            if len([v for v in vals if v is not None]) < 3:
                label += " (limited data)"
            series.append({
                "name": label,
                "data": vals,
                "is_limited": len([v for v in vals if v is not None]) < 3
            })
        return {"categories": all_dates, "series": series}

    payload = {
        "view": view,
        "charts": {
            "tps": format_chart(tps_data),
            "ttft": format_chart(ttft_data),
            "context": format_chart(context_data),
        },
        "summary": [],
    }

    for model, stats in summary.items():
        payload["summary"].append({
            "model": model,
            "avg_tps": round(sum(stats["tps"]) / len(stats["tps"]), 2) if stats["tps"] else 0,
            "avg_ttft": round(sum(stats["ttft"]) / len(stats["ttft"]), 2) if stats["ttft"] else 0,
            "avg_context": round(sum(stats["context"]) / len(stats["context"]), 0) if stats["context"] else 0,
            "total_gens": stats["count"],
        })

    payload["summary"].sort(key=lambda x: x["total_gens"], reverse=True)
    return payload
