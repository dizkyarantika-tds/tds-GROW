"""Ports the Game Release radar's Snowflake portion from the artifact's
`refresh()` (index.html lines ~821-961): game metadata, Game Server IDs, and
the EPU (events_production_union) liveness check for this/next-week games.

Jira ticket status is fetched separately (see backend/jira_client.py) — this
module only returns the Snowflake side of the `allGames` array the frontend
expects, with GAME_SERVER_ID/EPU/EPU_CHECKED/EPU_ERROR already merged in, the
same shape `renderRadar()`/`renderWeek()` already consume.

Note: the this-week/next-week date math below is a deliberate, minimal
duplicate of the frontend's `getWeekBounds()`/`isThisWeek()`/`isNextWeek()`
(index.html ~497-526) — it has to exist here too because this job runs
unattended on a schedule, with no browser JS available to ask "is this game
launching this week?" before deciding which Game Server IDs to include in the
EPU query. It's the same simple Monday-Sunday algorithm, not a reinterpretation
of it.
"""

from datetime import date, timedelta, timezone, datetime

from ..snowflake_client import run_query

GAME_METADATA_SQL = """
    SELECT
      app.NAME              AS APPLICATION_NAME,
      app.ANALYTICAL_NAME,
      app.PUBLIC_ID,
      TO_CHAR(app.DATE_OF_SOFT_LAUNCH,'YYYY-MM-DD') AS SL_DATE,
      s.NAME                AS STUDIO_NAME,
      COALESCE(abv.DATA:android_bundle_id::VARCHAR, abv.DATA:ios_bundle_id::VARCHAR) AS BUNDLE_ID,
      abv.DATA:android_bundle_id::VARCHAR        AS BUNDLE_ID_ANDROID,
      abv.DATA:ios_bundle_id::VARCHAR            AS BUNDLE_ID_IOS,
      abv.DATA:adjust_ios_app_token::VARCHAR     AS ADJUST_IOS,
      abv.DATA:adjust_android_app_token::VARCHAR AS ADJUST_ANDROID,
      abv.DATA:platforms::VARCHAR                AS PLATFORMS
    FROM TDS_DB.RAW.AS_APPLICATIONS app
    LEFT JOIN TDS_DB.RAW.AS_TEAMS t   ON app.TEAM_ID = t.ID
    LEFT JOIN TDS_DB.RAW.AS_STUDIOS s ON t.STUDIO_ID = s.ID
    LEFT JOIN TDS_DB.RAW.AS_APPLICATION_BUILD_VARIANTS abv ON app.ID = abv.APPLICATION_ID
    LEFT JOIN TDS_DB.RAW.AS_BUILD_VARIANTS bv ON abv.BUILD_VARIANT_ID = bv.ID
    WHERE app.DATE_OF_SOFT_LAUNCH >= DATEADD(day, -30, CURRENT_DATE)
      AND (bv.KEY = 'default' OR bv.KEY IS NULL)
    ORDER BY app.DATE_OF_SOFT_LAUNCH ASC
    LIMIT 100
"""


def _week_bounds(today: date) -> tuple[str, str]:
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


def _next_week_bounds(today: date) -> tuple[str, str]:
    this_monday_str, _ = _week_bounds(today)
    next_monday = date.fromisoformat(this_monday_str) + timedelta(days=7)
    next_sunday = next_monday + timedelta(days=6)
    return next_monday.isoformat(), next_sunday.isoformat()


def _is_this_or_next_week(sl_date: str | None, today: date) -> bool:
    if not sl_date:
        return False
    this_start, this_end = _week_bounds(today)
    next_start, next_end = _next_week_bounds(today)
    return (this_start <= sl_date <= this_end) or (next_start <= sl_date <= next_end)


def fetch_games() -> list[dict]:
    games = run_query(GAME_METADATA_SQL)

    public_ids = [g["PUBLIC_ID"] for g in games if g.get("PUBLIC_ID")]
    gs_map: dict[str, str | None] = {}
    if public_ids:
        placeholders = ",".join("?" for _ in public_ids)
        gs_rows = run_query(
            f"""
            SELECT PUBLIC_ID,
              COALESCE(GS_APP_ID::VARCHAR, ZM_GS_APP_ID::VARCHAR,
                       LDS_GS_APP_ID::VARCHAR, TT_GS_APP_ID::VARCHAR) AS GAME_SERVER_ID
            FROM TDS_DB.RAW.TDS_APP_IDS
            WHERE PUBLIC_ID IN ({placeholders})
            """,
            public_ids,
        )
        gs_map = {r["PUBLIC_ID"]: r["GAME_SERVER_ID"] for r in gs_rows}

    for g in games:
        g["GAME_SERVER_ID"] = gs_map.get(g["PUBLIC_ID"])
        g["EPU"] = None
        g["EPU_CHECKED"] = False
        g["EPU_ERROR"] = None

    today = datetime.now(timezone.utc).date()

    def _numeric_gs_id(g: dict) -> int | None:
        gs_id = g.get("GAME_SERVER_ID")
        if not gs_id:
            return None
        try:
            return int(gs_id)
        except (TypeError, ValueError):
            return None

    in_scope = [
        g
        for g in games
        if _numeric_gs_id(g) is not None and _is_this_or_next_week(g.get("SL_DATE"), today)
    ]
    for g in in_scope:
        g["EPU_CHECKED"] = True

    if in_scope:
        gs_ids = sorted({_numeric_gs_id(g) for g in in_scope})
        placeholders = ",".join("?" for _ in gs_ids)
        try:
            epu_rows = run_query(
                f"""
                SELECT
                  app_id::VARCHAR                                        AS APP_ID,
                  COUNT(*)                                               AS ROW_COUNT,
                  COUNT(DISTINCT name)                                   AS DISTINCT_EVENTS,
                  COUNT(DISTINCT device_model)                           AS DISTINCT_DEVICES,
                  SUM(CASE WHEN name = 'Game_Start' THEN 1 ELSE 0 END)  AS GAME_START_COUNT,
                  SUM(CASE WHEN name = 'Game_End'   THEN 1 ELSE 0 END)  AS GAME_END_COUNT
                FROM TDS_DB.PUBLIC.EVENTS_PRODUCTION_UNION
                WHERE app_id IN ({placeholders})
                  AND CREATED_AT::DATE >= DATEADD(day, -14, CURRENT_DATE)
                GROUP BY app_id
                """,
                gs_ids,
            )
            epu_map = {int(r["APP_ID"]): r for r in epu_rows}
            for g in in_scope:
                g["EPU"] = epu_map.get(_numeric_gs_id(g))
        except Exception as exc:  # noqa: BLE001 - mirrors the artifact's EPU try/catch
            for g in in_scope:
                g["EPU_ERROR"] = str(exc)

    return games
