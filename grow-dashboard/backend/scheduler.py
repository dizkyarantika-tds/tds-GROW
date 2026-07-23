"""Background refresh job for the Game Release radar + Jira status.

Replaces the artifact's "run live Snowflake/Jira queries on every page load"
with: run once on startup, then on a configurable interval
(REFRESH_INTERVAL_HOURS, default daily), storing results in the SQLite cache.
The "↺ Refresh" / "↺ Refresh Jira" buttons in the frontend map to the two
on-demand functions here, so a user can always force an out-of-cycle update
without waiting for the schedule.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .cache import Cache
from .config import Settings
from .jira_client import search_issues_for_games
from .services import radar

logger = logging.getLogger("grow.scheduler")

RADAR_GAMES_KEY = "radar_games"
RADAR_JIRA_KEY = "radar_jira"


class RadarScheduler:
    def __init__(self, cache: Cache, settings: Settings):
        self._cache = cache
        self._settings = settings
        self._scheduler = BackgroundScheduler()

    def refresh_all(self) -> None:
        logger.info("Refreshing Game Release radar (Snowflake + Jira)...")
        games = radar.fetch_games()
        self._cache.set(RADAR_GAMES_KEY, games)
        self._refresh_jira_for(games)
        logger.info("Radar refresh complete: %d games", len(games))

    def refresh_jira_only(self) -> None:
        logger.info("Refreshing Jira status only...")
        cached = self._cache.get(RADAR_GAMES_KEY)
        games = cached[0] if cached else []
        self._refresh_jira_for(games)

    def _refresh_jira_for(self, games: list[dict]) -> None:
        names = [g.get("APPLICATION_NAME") or g.get("ANALYTICAL_NAME") for g in games]
        names = [n for n in names if n]
        issues = search_issues_for_games(names, self._settings)
        self._cache.set(RADAR_JIRA_KEY, issues)

    def start(self) -> None:
        # Populate the cache synchronously before the app starts serving, so
        # the first request doesn't see an empty dashboard.
        try:
            self.refresh_all()
        except Exception:
            logger.exception("Initial radar refresh failed — will retry on schedule")

        self._scheduler.add_job(
            self.refresh_all,
            "interval",
            hours=self._settings.refresh_interval_hours,
            id="radar_refresh",
            replace_existing=True,
        )
        self._scheduler.start()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
