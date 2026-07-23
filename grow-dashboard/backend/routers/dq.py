"""Data Quality tab endpoint — cached per (analytical_name, days) combo with
the same configurable TTL as the radar/Jira refresh, manually overridable via
`force`. See services/dq.py for the actual check logic.
"""

from fastapi import APIRouter, Depends, Request

from ..auth import require_user
from ..config import get_settings
from ..models import DQRunRequest
from ..services import dq

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


def _cache_key(analytical_name: str, days: int) -> str:
    return f"dq:{analytical_name}:{days}"


@router.post("/dq/run")
def run_dq(body: DQRunRequest, request: Request):
    cache = request.app.state.cache
    settings = get_settings()
    key = _cache_key(body.analytical_name, body.days)

    if not body.force:
        cached = cache.get(key)
        if cached:
            payload, updated_at = cached
            if cache.is_fresh(updated_at, settings.refresh_interval_hours):
                return {**payload, "updated_at": updated_at, "cached": True}

    result = dq.run_all_checks(body.analytical_name, body.days)
    updated_at = cache.set(key, result)
    return {**result, "updated_at": updated_at, "cached": False}
