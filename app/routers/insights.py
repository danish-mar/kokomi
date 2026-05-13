from fastapi import APIRouter, Query
from app.insights import get_insights_data
from app.storage import load_prefs

router = APIRouter(prefix="/api/insights")

@router.get("")
async def get_insights(view: str = Query("daily", enum=["daily", "monthly"])):
    prefs = load_prefs()
    # Check if insights is enabled (optional, but good for privacy)
    if not prefs.get("insights", True):
        return {"charts": {}, "summary": [], "disabled": True}
    
    return get_insights_data(view=view)
