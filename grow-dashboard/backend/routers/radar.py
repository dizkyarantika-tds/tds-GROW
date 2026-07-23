"""Serves the cached Game Release radar + Jira status, and exposes manual
refresh endpoints mapping to the frontend's "↺ Refresh" / "↺ Refresh Jira"
buttons.

Route handlers are plain `def` (not `async def`) because they touch the
SQLite cache and, on the refresh endpoints, blocking Snowflake/Jira calls —
FastAPI runs sync path operations in a threadpool automatically, so this
doesn't block the event loop.
"""

from fastapi import APIRouter, Depends, Request

from ..auth import require_user
from ..scheduler import RADAR_GAMES_KEY, RADAR_JIRA_KEY

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


@router.get("/radar")
def get_radar(request: Request):
    cache = request.app.state.cache
    cached = cache.get(RADAR_GAMES_KEY)
    if not cached:
        return {"games": [], "updated_at": None}
    games, updated_at = cached
    return {"games": games, "updated_at": updated_at}


@router.get("/jira")
def get_jira(request: Request):
    cache = request.app.state.cache
    cached = cache.get(RADAR_JIRA_KEY)
    if not cached:
        return {"issues": [], "updated_at": None}
    issues, updated_at = cached
    return {"issues": issues, "updated_at": updated_at}


@router.post("/refresh")
def post_refresh(request: Request):
    request.app.state.scheduler.refresh_all()
    return get_radar(request)


@router.post("/jira/refresh")
def post_jira_refresh(request: Request):
    request.app.state.scheduler.refresh_jira_only()
    return get_jira(request)
