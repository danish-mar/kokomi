import json
import os
import datetime
import asyncio
from typing import List, Dict, Optional, Any
from collections import defaultdict
from app.config import INSIGHTS_FILE


def log_generation_sync(metadata: Dict[str, Any]):
    """Synchronously append a generation record to the insights log."""
    try:
        # Ensure timestamp is ISO 8601
        if "timestamp" not in metadata:
            metadata["timestamp"] = datetime.datetime.utcnow().isoformat()
        
        with open(INSIGHTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(metadata) + "\n")
    except Exception as e:
        print(f"Error writing to insights log: {e}")

async def log_generation(metadata: Dict[str, Any]):
    """Non-blocking telemetry collection."""
    # We use run_in_executor to avoid blocking the event loop with I/O
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, log_generation_sync, metadata)

def get_insights_data(view: str = "daily", source: str = "all") -> Dict[str, Any]:
    """
    Aggregate telemetry data for charts and tables.
    view: "daily" (rolling 30 days) or "monthly" (rolling 12 months)
    """
    if not os.path.exists(INSIGHTS_FILE):
        return {"charts": {}, "summary": []}

    now = datetime.datetime.utcnow()
    limit_days = 30 if view == "daily" else (12 * 30)
    
    # model -> date_key -> [values]
    tps_data = defaultdict(lambda: defaultdict(list))
    ttft_data = defaultdict(lambda: defaultdict(list))
    context_data = defaultdict(lambda: defaultdict(list))
    
    # model -> total_stats
    summary = defaultdict(lambda: {
        "tps": [], "ttft": [], "context": [], "count": 0
    })

    with open(INSIGHTS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
                
                # Filter by source (default is "chat")
                data_source = data.get("source", "chat")
                if source != "all" and data_source != source:
                    continue
                    
                ts = datetime.datetime.fromisoformat(data["timestamp"])
                
                # Filter by age
                if (now - ts).days > limit_days:
                    continue
                
                if view == "daily":
                    date_key = ts.strftime("%Y-%m-%d")
                else:
                    date_key = ts.strftime("%Y-%m")
                
                model = data.get("model", "unknown")
                
                # TPS
                if data.get("tps") is not None:
                    tps_data[model][date_key].append(data["tps"])
                    summary[model]["tps"].append(data["tps"])
                
                # TTFT
                if data.get("ttft") is not None:
                    ttft_data[model][date_key].append(data["ttft"])
                    summary[model]["ttft"].append(data["ttft"])
                
                # Context
                if data.get("context_used") is not None:
                    context_data[model][date_key].append(data["context_used"])
                    summary[model]["context"].append(data["context_used"])
                
                summary[model]["count"] += 1
                
            except Exception:
                continue

    # Prepare chart payload
    def format_chart(data_map):
        # We need to sort dates and models
        all_dates = sorted(list(set(d for m in data_map.values() for d in m.keys())))
        series = []
        for model, dates in data_map.items():
            vals = []
            for d in all_dates:
                if d in dates:
                    avg = sum(dates[d]) / len(dates[d])
                    vals.append(round(avg, 2))
                else:
                    vals.append(None)
            
            # Label limited data
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
            "context": format_chart(context_data)
        },
        "summary": []
    }

    # Prepare summary table
    for model, stats in summary.items():
        avg_tps = sum(stats["tps"]) / len(stats["tps"]) if stats["tps"] else 0
        avg_ttft = sum(stats["ttft"]) / len(stats["ttft"]) if stats["ttft"] else 0
        avg_context = sum(stats["context"]) / len(stats["context"]) if stats["context"] else 0
        
        payload["summary"].append({
            "model": model,
            "avg_tps": round(avg_tps, 2),
            "avg_ttft": round(avg_ttft, 2),
            "avg_context": round(avg_context, 0),
            "total_gens": stats["count"]
        })
    
    # Sort summary by generation count desc
    payload["summary"].sort(key=lambda x: x["total_gens"], reverse=True)
    
    return payload
