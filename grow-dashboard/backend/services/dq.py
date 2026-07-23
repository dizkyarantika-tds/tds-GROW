"""Ports the Data Quality tab's checks from the artifact's `runDQ()`
(index.html lines ~1653-2249) to run server-side, parameterized with bound
values instead of the original's string interpolation.

Each check mirrors one of the original's inline try/catch blocks: if a query
fails, only that check reports an error — the rest still run. Row data and
computed summaries are returned as plain JSON; the frontend's ported
`runDQ()` still builds the visual cards with the existing `mkCard`/`mkTable`/
`renderFlags` helpers, unchanged, so the DQ tab looks and behaves exactly as
before.
"""

from typing import Any, Callable

from ..snowflake_client import run_query

# Same priority list as the artifact's EPU_PRIORITY_EVENTS (index.html ~1546),
# used only to order the EPU check's rows the same way the original did
# client-side before handing them to mkTable().
EPU_PRIORITY_EVENTS = [
    "App_First_Open", "App_Start_Time", "App_Start_Time_First", "App_Update",
    "LAT", "NOT_LAT", "Init_Call_Completed", "Session_Start", "Session_Resume",
    "Session_End", "IAP_Make_Purchase", "DeepLink_Opened", "Notice_Handled",
    "GDPR_Consent", "GDPR_View", "GP_Install_Referrer", "PackageInfo_Android",
    "User_IDs", "DeviceInfo", "InstallSourceInfo", "GP_Init",
    "Remote_Config_Invalid", "Remote_Config_Save_Failed",
    "Remote_Config_Load_Failed", "Remote_Config_Saved", "Game_End",
    "Game_Start", "Game_Suspend", "Game_Resume", "Level_Progression",
    "Player_Progression", "Powerup_Transaction", "Currency_Transaction",
    "Time_Limited_Item_Transaction", "Reward_Received", "Screen_Shown",
    "Screen_Interaction", "User_Funnel", "LiveOpsEvent_Client_ValidationFailed",
]
_PRIORITY_INDEX = {name.lower(): i for i, name in enumerate(EPU_PRIORITY_EVENTS)}


def _f(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _i(v: Any) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def resolve_gs_id(analytical_name: str) -> str | None:
    rows = run_query(
        """
        SELECT COALESCE(GS_APP_ID::VARCHAR, ZM_GS_APP_ID::VARCHAR,
                        LDS_GS_APP_ID::VARCHAR, TT_GS_APP_ID::VARCHAR) AS GS_ID
        FROM TDS_DB.RAW.TDS_APP_IDS
        WHERE PUBLIC_ID = (SELECT PUBLIC_ID FROM TDS_DB.RAW.AS_APPLICATIONS
                           WHERE ANALYTICAL_NAME = ? LIMIT 1)
        """,
        [analytical_name],
    )
    return rows[0]["GS_ID"] if rows else None


def _check_epu(analytical_name: str, days: int, gs_id: str | None) -> dict:
    try:
        gs_id_int = int(gs_id) if gs_id else None
    except (TypeError, ValueError):
        gs_id_int = None
    if gs_id_int is None:
        return {"rows": [], "summaries": [{"text": "No Game Server ID found for this game", "bad": True}], "status": "not_found"}

    rows = run_query(
        """
        WITH total_users AS (
          SELECT PLATFORM, COUNT(DISTINCT user_id) AS TOTAL_DISTINCT_USERS
          FROM TDS_DB.PUBLIC.EVENTS_PRODUCTION_UNION
          WHERE APP_ID = ?
            AND DATE(CREATED_AT) BETWEEN DATEADD('day',-?,CURRENT_DATE) AND DATEADD('day',-1,CURRENT_DATE)
          GROUP BY PLATFORM
        ),
        per_event AS (
          SELECT PLATFORM, NAME AS EVENT_NAME,
            COUNT(*)                  AS NUM_EVENTS,
            COUNT(DISTINCT user_id)   AS DISTINCT_USERS
          FROM TDS_DB.PUBLIC.EVENTS_PRODUCTION_UNION
          WHERE APP_ID = ?
            AND DATE(CREATED_AT) BETWEEN DATEADD('day',-?,CURRENT_DATE) AND DATEADD('day',-1,CURRENT_DATE)
          GROUP BY PLATFORM, NAME
        )
        SELECT p.PLATFORM, p.EVENT_NAME, p.NUM_EVENTS, p.DISTINCT_USERS,
          t.TOTAL_DISTINCT_USERS,
          ROUND(p.DISTINCT_USERS * 100.0 / NULLIF(t.TOTAL_DISTINCT_USERS,0), 1) AS PCT_USERS
        FROM per_event p
        JOIN total_users t ON p.PLATFORM = t.PLATFORM
        ORDER BY p.DISTINCT_USERS DESC
        """,
        [gs_id_int, days, gs_id_int, days],
    )

    def sort_key(r):
        pi = _PRIORITY_INDEX.get((r.get("EVENT_NAME") or "").lower())
        return (pi is None, pi if pi is not None else 0, r.get("PLATFORM") or "")

    rows = sorted(rows, key=sort_key)
    total = sum(_i(r.get("NUM_EVENTS")) for r in rows)
    platforms = sorted({r.get("PLATFORM") for r in rows if r.get("PLATFORM")})
    top1 = max(rows, key=lambda r: _i(r.get("DISTINCT_USERS")), default=None)

    summaries = [
        {"text": f"Total events: {total:,}"},
        {"text": f"Platforms firing: {', '.join(platforms) or '—'}"},
        {"text": f"Top event by users: {top1['EVENT_NAME']} ({top1['PCT_USERS']}% of users)" if top1 else "No events fired", "bad": not top1},
    ]
    return {"rows": rows, "summaries": summaries, "status": "found" if rows else "not_found"}


def _table_match_check(table: str, alias: str, analytical_name: str) -> dict:
    rows = run_query(
        f"""
        SELECT {alias}.APPLICATION_NAME AS app_val, {alias}.TEAM AS team_val, {alias}.STUDIO AS studio_val,
          app.ANALYTICAL_NAME AS as_app, t.NAME AS as_team, s.NAME AS as_studio,
          CASE WHEN {alias}.APPLICATION_NAME = app.ANALYTICAL_NAME THEN '✓' ELSE '✗' END AS check_app,
          CASE WHEN {alias}.TEAM = t.NAME THEN '✓' ELSE '✗' END AS check_team,
          CASE WHEN LOWER({alias}.STUDIO) = LOWER(s.NAME) THEN '✓' ELSE '✗' END AS check_studio
        FROM {table} {alias}
        LEFT JOIN TDS_DB.RAW.AS_APPLICATIONS app ON {alias}.APPLICATION_NAME = app.ANALYTICAL_NAME
        LEFT JOIN TDS_DB.RAW.AS_TEAMS t ON app.TEAM_ID = t.ID
        LEFT JOIN TDS_DB.RAW.AS_STUDIOS s ON t.STUDIO_ID = s.ID
        WHERE {alias}.APPLICATION_NAME = ?
        """,
        [analytical_name],
    )
    if not rows:
        return {"rows": [], "summaries": [{"text": f"App not found in {table}", "bad": True}], "status": "not_found"}
    r0 = rows[0]
    all_pass = r0.get("CHECK_APP") == "✓" and r0.get("CHECK_TEAM") == "✓" and r0.get("CHECK_STUDIO") == "✓"
    if all_pass:
        summaries = [{"text": "app, team, studio — all match"}]
    else:
        summaries = []
        if r0.get("CHECK_APP") != "✓":
            summaries.append({"text": f'app mismatch: "{r0.get("APP_VAL")}" / AS "{r0.get("AS_APP")}"', "bad": True})
        if r0.get("CHECK_TEAM") != "✓":
            summaries.append({"text": f'team mismatch: "{r0.get("TEAM_VAL")}" / AS "{r0.get("AS_TEAM")}"', "bad": True})
        if r0.get("CHECK_STUDIO") != "✓":
            summaries.append({"text": f'studio mismatch: "{r0.get("STUDIO_VAL")}" / AS "{r0.get("AS_STUDIO")}"', "bad": True})
    return {"rows": rows, "summaries": summaries, "status": "pass" if all_pass else "fail"}


def _check_app_name_mapping_gs(analytical_name: str) -> dict:
    rows = run_query(
        """
        SELECT gs.ID AS gs_id, gs.NAME AS gs_name, gs.APP_GROUP AS gs_app_group,
          ids.ANALYTICAL_NAME AS tds_analytical_name,
          ids.GS_APP_ID::VARCHAR AS tds_gs_app_id,
          ids.ZM_GS_APP_ID::VARCHAR AS tds_zm_gs_app_id,
          ids.LDS_GS_APP_ID::VARCHAR AS tds_lds_gs_app_id,
          ids.TT_GS_APP_ID::VARCHAR AS tds_tt_gs_app_id,
          CASE WHEN gs.ID::VARCHAR IN (
            COALESCE(ids.GS_APP_ID::VARCHAR,''), COALESCE(ids.ZM_GS_APP_ID::VARCHAR,''),
            COALESCE(ids.LDS_GS_APP_ID::VARCHAR,''), COALESCE(ids.TT_GS_APP_ID::VARCHAR,'')
          ) THEN '✓' ELSE '✗' END AS check_id,
          CASE WHEN gs.NAME = ids.ANALYTICAL_NAME THEN '✓' ELSE '✗' END AS check_name
        FROM TDS_DB.PUBLIC.APP_NAME_MAPPING_GS gs
        LEFT JOIN TDS_DB.RAW.TDS_APP_IDS ids ON gs.ID::VARCHAR IN (
          COALESCE(ids.GS_APP_ID::VARCHAR,''), COALESCE(ids.ZM_GS_APP_ID::VARCHAR,''),
          COALESCE(ids.LDS_GS_APP_ID::VARCHAR,''), COALESCE(ids.TT_GS_APP_ID::VARCHAR,'')
        )
        WHERE gs.NAME ILIKE ?
        """,
        [f"%{analytical_name}%"],
    )
    if not rows:
        return {"rows": [], "summaries": [{"text": "App not found in APP_NAME_MAPPING_GS — may not be onboarded to Game Server yet", "warn": True}], "status": "not_found"}
    r0 = rows[0]
    id_linked = r0.get("CHECK_ID") == "✓"
    name_matches = r0.get("CHECK_NAME") == "✓"
    if name_matches:
        name_match_text = "Name match (gs.name = analytical_name): Yes"
    else:
        gs_name = r0.get("GS_NAME")
        tds_name = r0.get("TDS_ANALYTICAL_NAME")
        name_match_text = f'Name match (gs.name = analytical_name): No — GS "{gs_name}" / TDS "{tds_name}"'
    summaries = [
        {"text": f'GS ID: {r0.get("GS_ID")} | App Group: {r0.get("GS_APP_GROUP") or "—"}'},
        {"text": f'ID linked in TDS_APP_IDS: {"Yes" if id_linked else "No"}', "warn": not id_linked},
        {"text": name_match_text, "warn": not name_matches},
    ]
    return {"rows": rows, "summaries": summaries, "status": "pass"}


def _check_activity_summary(analytical_name: str, days: int) -> dict:
    rows = run_query(
        """
        SELECT DATE, PLATFORM
        ,COUNT(DISTINCT COUNTRY)        AS distinct_country
        ,COUNT(DISTINCT PLACEMENT_TYPE) AS distinct_placement_type
        ,COUNT(DISTINCT CONNECTION)     AS distinct_connection
        ,COUNT(DISTINCT MEDIA_SOURCE)   AS distinct_media_source
        ,SUM(AD_REVENUE)                AS sum_revenue
        ,SUM(IAP_REVENUE)               AS sum_iap_revenue
        ,SUM(IMPRESSIONS)               AS sum_impression
        ,SUM(REQUESTS)                  AS sum_request
        ,SUM(CLICKS)                    AS sum_clicks
        ,SUM(SPENT)                     AS sum_spent
        ,AVG(DAU_AVG)                   AS dau_avg
        ,AVG(SESSIONS_AVG)              AS sessions_avg
        ,AVG(INSTALLS_AVG)              AS installs_avg
        FROM TDS_DB.PUBLIC.ACTIVITY_SUMMARY_REPORT_NEW
        WHERE APP ILIKE ?
          AND DATE BETWEEN DATEADD('day',-?,CURRENT_DATE) AND DATEADD('day',-1,CURRENT_DATE)
        GROUP BY 1,2 ORDER BY 1,2
        """,
        [f"%{analytical_name}%", days],
    )
    rev = sum(_f(r.get("SUM_REVENUE")) for r in rows)
    latest = str(rows[-1]["DATE"])[:10] if rows else None
    has_iap = any(_f(r.get("SUM_IAP_REVENUE")) > 0 for r in rows)
    summaries = [
        {"text": f"Total ad revenue (L{days}D): ${rev:.2f}", "bad": rev == 0},
        {"text": f"IAP revenue: {'✓ Flowing' if has_iap else '✗ No IAP data'}", "bad": not has_iap, "warn": not has_iap},
        {"text": f"Latest data: {latest}" if latest else "No data found", "bad": not latest},
    ]
    return {"rows": rows, "summaries": summaries, "status": "found" if rows else "not_found"}


def _check_f_user_activity(analytical_name: str, days: int) -> dict:
    rows = run_query(
        """
        WITH base AS (
          SELECT DT, PLATFORM
          ,COUNT(DISTINCT ACCOUNT_UID) AS dau
          ,SUM(NUM_GAME_START) AS total_games
          ,SUM(GAME_LENGTH) AS total_game_time
          ,SUM(NUM_AD_IMPRESSION_INTERSTITIAL) AS fs_imp
          ,SUM(NUM_AD_IMPRESSION_BANNER) AS banner_imp
          ,SUM(NUM_AD_IMPRESSION_REWARDED) AS reward_imp
          FROM TDS_DB.PUBLIC.F_USER_ACTIVITY
          WHERE APPLICATION = ?
            AND DATE(DT) BETWEEN DATEADD('day',-?,CURRENT_DATE) AND DATEADD('day',-1,CURRENT_DATE)
          GROUP BY 1,2
        )
        SELECT DT, PLATFORM, DAU
        ,TOTAL_GAMES/NULLIF(DAU,0)::double AS games_per_dau
        ,TOTAL_GAME_TIME/NULLIF(DAU,0)::double AS game_time_per_dau
        ,FS_IMP/NULLIF(DAU,0)::double AS fs_imp_per_dau
        ,BANNER_IMP/NULLIF(DAU,0)::double AS banner_imp_per_dau
        ,REWARD_IMP/NULLIF(DAU,0)::double AS reward_imp_per_dau
        FROM base ORDER BY 1,2
        """,
        [analytical_name, days],
    )
    peak = max(rows, key=lambda r: _i(r.get("DAU")), default=None)
    avg_games = (sum(_f(r.get("GAMES_PER_DAU")) for r in rows) / len(rows)) if rows else None
    latest = rows[-1]["DT"] if rows else None
    plat = rows[0].get("PLATFORM") if rows else "all"
    summaries = [
        {"text": f'Peak DAU ({plat}): {_i(peak.get("DAU")):,} on {peak.get("DT")}' if peak and peak.get("DAU") else "No DAU data", "bad": not (peak and peak.get("DAU"))},
        {"text": f"Avg games/DAU: {avg_games:.2f}" if avg_games is not None else "Avg games/DAU: —"},
        {"text": f"Latest data: {latest}" if latest else "No data", "bad": not latest},
    ]
    return {"rows": rows, "summaries": summaries, "status": "found" if rows else "not_found"}


def _check_f_installs(analytical_name: str, days: int) -> dict:
    rows = run_query(
        """
        WITH base AS (
          SELECT INSTALL_DATE, PLATFORM
          ,COUNT(DISTINCT COUNTRY_CODE) AS distinct_country
          ,COUNT(DISTINCT SOURCE) AS distinct_source
          ,COUNT(DISTINCT INSTALL_UID) AS num_installs
          ,SUM(CPI_LIBRING) AS total_cpi_libring
          ,SUM(CPI) AS total_cpi
          FROM TDS_DB.PUBLIC.F_INSTALLS
          WHERE APP_NAME = ?
            AND INSTALL_DATE BETWEEN DATEADD('day',-?,CURRENT_DATE) AND DATEADD('day',-1,CURRENT_DATE)
          GROUP BY 1,2
        )
        SELECT INSTALL_DATE, PLATFORM, NUM_INSTALLS, DISTINCT_COUNTRY, DISTINCT_SOURCE
        ,TOTAL_CPI_LIBRING/NULLIF(NUM_INSTALLS,0)::double AS cpi_libring_per_install
        ,TOTAL_CPI
        FROM base ORDER BY 1,2
        """,
        [analytical_name, days],
    )
    total = sum(_i(r.get("NUM_INSTALLS")) for r in rows)
    max_c = max((_i(r.get("DISTINCT_COUNTRY")) for r in rows), default=0)
    has_cpi = any(_f(r.get("TOTAL_CPI")) > 0 for r in rows)
    summaries = [
        {"text": f"Total installs (L{days}D): {total:,}", "bad": total == 0},
        {"text": f"Max country reach in a day: {max_c}"},
        {"text": f"UA spend (CPI): {'✓ Flowing' if has_cpi else '⚠ Not flowing yet'}", "warn": not has_cpi},
    ]
    return {"rows": rows, "summaries": summaries, "status": "found" if rows else "not_found"}


def _check_f_user_revenue(analytical_name: str, days: int) -> dict:
    rows = run_query(
        """
        WITH base AS (
          SELECT DATE, PLATFORM
          ,COUNT(DISTINCT ACCOUNT_UID) AS total_user
          ,SUM(REVENUE) AS total_revenue
          ,SUM(IMPRESSIONS) AS total_impression
          ,COUNT(DISTINCT CASE WHEN PLACEMENT='inter' THEN ACCOUNT_UID END) AS user_inter
          ,SUM(CASE WHEN PLACEMENT='inter' THEN REVENUE END) AS rev_inter
          ,SUM(CASE WHEN PLACEMENT='inter' THEN IMPRESSIONS END) AS imp_inter
          ,COUNT(DISTINCT CASE WHEN PLACEMENT='banner' THEN ACCOUNT_UID END) AS user_banner
          ,SUM(CASE WHEN PLACEMENT='banner' THEN REVENUE END) AS rev_banner
          ,COUNT(DISTINCT CASE WHEN PLACEMENT='reward' THEN ACCOUNT_UID END) AS user_reward
          ,SUM(CASE WHEN PLACEMENT='reward' THEN REVENUE END) AS rev_reward
          FROM TDS_DB.PUBLIC.F_USER_REVENUE
          WHERE APP = ?
            AND DATE BETWEEN DATEADD('day',-?,CURRENT_DATE) AND DATEADD('day',-1,CURRENT_DATE)
          GROUP BY 1,2
        )
        SELECT DATE, PLATFORM
        ,TOTAL_REVENUE
        ,TOTAL_REVENUE/NULLIF(TOTAL_USER,0)::double AS arpdau
        ,TOTAL_REVENUE/NULLIF(TOTAL_IMPRESSION,0)*1000 AS cpm
        ,REV_INTER/NULLIF(USER_INTER,0)::double AS fs_arpdau
        ,REV_BANNER/NULLIF(USER_BANNER,0)::double AS banner_arpdau
        ,REV_REWARD/NULLIF(USER_REWARD,0)::double AS reward_arpdau
        FROM base ORDER BY 1,2
        """,
        [analytical_name, days],
    )
    total_rev = sum(_f(r.get("TOTAL_REVENUE")) for r in rows)
    with_arpdau = [r for r in rows if r.get("ARPDAU")]
    avg_arpdau = (sum(_f(r.get("ARPDAU")) for r in with_arpdau) / len(with_arpdau)) if with_arpdau else None
    total_reward = sum(_f(r.get("REV_REWARD")) for r in rows)
    total_inter = sum(_f(r.get("REV_INTER")) for r in rows)
    top_placement = "Rewarded" if total_reward > total_inter else "Interstitial"
    summaries = [
        {"text": f"Total revenue (L{days}D): ${total_rev:.2f}", "bad": total_rev == 0},
        {"text": f"Avg ARPDAU: ${avg_arpdau:.4f}" if avg_arpdau is not None else "Avg ARPDAU: —"},
        {"text": f"Top placement by revenue: {top_placement}"},
    ]
    return {"rows": rows, "summaries": summaries, "status": "found" if rows else "not_found"}


def _check_release_monitoring(analytical_name: str, days: int) -> dict:
    rows = run_query(
        """
        SELECT DATE(DT) AS DT, PLATFORM
        ,SUM(ACTIVE_USERS) AS active_users
        ,SUM(GAMES) AS total_games
        ,MEDIAN(APPROX_PERCENTILE_ESTIMATE(APP_START_TIME_PERC_ACCUM, 0.5)) AS median_app_load_ms
        ,SUM(RETURN_D1) AS d1_returns
        ,SUM(CRASH_FATAL_COUNT) AS fatal_crashes
        ,SUM(CRASH_FATAL_USERS) AS fatal_crash_users
        ,SUM(CRASH_NON_FATAL_COUNT) AS non_fatal_crashes
        ,SUM(INTERSTITIAL_REVENUE) AS inter_rev
        ,SUM(BANNER_REVENUE) AS banner_rev
        ,SUM(REWARDED_REVENUE) AS reward_rev
        ,SUM(SUCCESSFUL_INTERSTITIAL_REQUESTS)/NULLIF(SUM(INTERSTITIAL_REQUESTS),0)::double AS inter_fill_rate
        ,SUM(SUCCESSFUL_REWARDED_REQUESTS)/NULLIF(SUM(REWARDED_REQUESTS),0)::double AS reward_fill_rate
        FROM TDS_DB.PUBLIC.F_RELEASE_MONITORING_HOURLY
        WHERE APPLICATION_NAME = ?
          AND DATE(DT) BETWEEN DATEADD('day',-?,CURRENT_DATE) AND DATEADD('day',-1,CURRENT_DATE)
        GROUP BY 1,2 ORDER BY 1,2
        """,
        [analytical_name, days],
    )
    total_fatal = sum(_i(r.get("FATAL_CRASHES")) for r in rows)
    with_load = [r for r in rows if r.get("MEDIAN_APP_LOAD_MS")]
    avg_load = (sum(_f(r.get("MEDIAN_APP_LOAD_MS")) for r in with_load) / len(with_load) / 1000) if with_load else None
    with_fill = [r for r in rows if r.get("REWARD_FILL_RATE")]
    latest_fill = _f(with_fill[-1].get("REWARD_FILL_RATE")) * 100 if with_fill else None
    summaries = [
        {"text": f"Total fatal crashes (L{days}D): {total_fatal}", "bad": total_fatal > 10, "warn": 0 < total_fatal <= 10},
        {"text": f"Avg app load time: {avg_load:.1f}s" if avg_load is not None else "Avg app load time: —", "warn": (avg_load or 0) > 5},
        {"text": f"Latest reward fill rate: {latest_fill:.0f}%" if latest_fill is not None else "Latest reward fill rate: —"},
    ]
    return {"rows": rows, "summaries": summaries, "status": "found" if rows else "not_found"}


def _check_tech_performance(analytical_name: str, days: int) -> dict:
    rows = run_query(
        """
        WITH base AS (
          SELECT DATE(EVENT_DATE) AS EVENT_DATE, PLATFORM
          ,COUNT(DISTINCT DEVICE_MODEL) AS distinct_device_model
          ,SUM(COUNT_ACTIVE_USER) AS active_user
          ,SUM(SUM_APP_OPEN_TIME_FIRST)/NULLIF(SUM(COUNT_APP_OPEN_TIME_FIRST),0)::double AS app_open_first_ms
          ,SUM(SUM_APP_OPEN_TIME_RETURNING)/NULLIF(SUM(COUNT_APP_OPEN_TIME_RETURNING),0)::double AS app_open_return_ms
          ,SUM(SUM_MEMORY_FREE)/NULLIF(SUM(COUNT_MEMORY_FREE),0)::double AS memory_free
          ,SUM(SUM_MEMORY_USED)/NULLIF(SUM(COUNT_MEMORY_USED),0)::double AS memory_used
          ,SUM(COUNT_CRASHES) AS total_crash
          FROM TDS_DB.PUBLIC.F_TECH_PERFORMANCE_HOURLY
          WHERE APPLICATION_NAME = ?
            AND DATE(EVENT_DATE) BETWEEN DATEADD('day',-?,CURRENT_DATE) AND DATEADD('day',-1,CURRENT_DATE)
          GROUP BY 1,2
        )
        SELECT * FROM base ORDER BY 1,2
        """,
        [analytical_name, days],
    )
    total_crashes = sum(_i(r.get("TOTAL_CRASH")) for r in rows)
    with_open = [r for r in rows if r.get("APP_OPEN_FIRST_MS")]
    avg_open_first = (sum(_f(r.get("APP_OPEN_FIRST_MS")) for r in with_open) / len(with_open) / 1000) if with_open else None
    max_devices = max((_i(r.get("DISTINCT_DEVICE_MODEL")) for r in rows), default=0)
    summaries = [
        {"text": f"Avg app open time / first launch: {avg_open_first:.1f}s" if avg_open_first is not None else "Avg app open time / first launch: —", "warn": (avg_open_first or 0) > 5},
        {"text": f"Total crashes: {total_crashes:,}", "bad": total_crashes > 100, "warn": 10 < total_crashes <= 100},
        {"text": f"Max distinct device models: {max_devices}"},
    ]
    return {"rows": rows, "summaries": summaries, "status": "found" if rows else "not_found"}


def _check_f_levels(analytical_name: str, days: int) -> dict:
    rows = run_query(
        """
        SELECT ACTIVITY_DATE, PLATFORM
        ,COUNT(DISTINCT LEVEL) AS unique_levels
        ,COUNT(DISTINCT ACCOUNT_UID) AS total_user
        ,MAX(LEVEL) AS max_level
        ,SUM(GAMES_FINISHED) AS total_games_finished
        ,SUM(
          GAME_LENGTH:abandon::int + GAME_LENGTH:complete::int +
          GAME_LENGTH:draw::int + GAME_LENGTH:lost::int + GAME_LENGTH:won::int
        ) AS total_game_length
        ,SUM(CASE WHEN COINS_USAGE IS NOT NULL THEN 1 ELSE NULL END) AS coins_usage_rows
        ,SUM(INTERSTITIAL_IMPRESSIONS) AS inter_imp
        ,SUM(BANNER_IMPRESSIONS) AS banner_imp
        ,SUM(REWARDED_IMPRESSIONS) AS reward_imp
        ,SUM(INTERSTITIAL_REVENUE) AS inter_rev
        ,SUM(BANNER_REVENUE) AS banner_rev
        ,SUM(REWARDED_REVENUE) AS reward_rev
        FROM TDS_DB.PUBLIC.F_LEVELS
        WHERE APPLICATION_NAME = ?
          AND DATE(ACTIVITY_DATE) BETWEEN DATEADD('day',-?,CURRENT_DATE) AND DATEADD('day',-1,CURRENT_DATE)
        GROUP BY 1,2 ORDER BY 1,2
        """,
        [analytical_name, days],
    )
    total_finished = sum(_i(r.get("TOTAL_GAMES_FINISHED")) for r in rows)
    max_uniq_levels = max((_i(r.get("UNIQUE_LEVELS")) for r in rows), default=0)
    coins_rows = sum(_i(r.get("COINS_USAGE_ROWS")) for r in rows)
    summaries = [
        {"text": f"Total games finished (L{days}D): {total_finished:,}", "bad": total_finished == 0},
        {"text": f"Max unique levels in a day: {max_uniq_levels}"},
        {"text": f"Coins usage rows: {coins_rows:,} — economy active" if coins_rows > 0 else "Coins usage rows: 0 — economy not active yet", "warn": coins_rows == 0},
    ]
    return {"rows": rows, "summaries": summaries, "status": "found" if rows else "not_found"}


# Tier 3 — planned, not implemented (matches the artifact's placeholders exactly)
TIER3 = [
    {"group": "QA Related", "tables": ["f_qa_automation", "f_live_telemetry"]},
    {"group": "Experimentation", "tables": ["f_ab_participations"]},
]

# (title, tier, runner) — runner receives (analytical_name, days, gs_id)
_CHECKS: list[tuple[str, int, Callable[[str, int, str | None], dict]]] = [
    ("tds_db.public.events_production_union", 1, lambda name, days, gs_id: _check_epu(name, days, gs_id)),
    ("tds_db.dds.application", 1, lambda name, days, gs_id: _table_match_check("TDS_DB.DDS.APPLICATION", "dds", name)),
    ("tds_db.public.bi_application", 1, lambda name, days, gs_id: _table_match_check("TDS_DB.PUBLIC.BI_APPLICATION", "bi", name)),
    ("tds_db.public.app_name_mapping_gs", 1, lambda name, days, gs_id: _check_app_name_mapping_gs(name)),
    ("tds_db.public.activity_summary_report_new", 2, lambda name, days, gs_id: _check_activity_summary(name, days)),
    ("tds_db.public.f_user_activity", 2, lambda name, days, gs_id: _check_f_user_activity(name, days)),
    ("tds_db.public.f_installs", 2, lambda name, days, gs_id: _check_f_installs(name, days)),
    ("tds_db.public.f_user_revenue", 2, lambda name, days, gs_id: _check_f_user_revenue(name, days)),
    ("tds_db.public.f_release_monitoring_hourly", 2, lambda name, days, gs_id: _check_release_monitoring(name, days)),
    ("tds_db.public.f_tech_performance_hourly", 2, lambda name, days, gs_id: _check_tech_performance(name, days)),
    ("tds_db.public.f_levels", 2, lambda name, days, gs_id: _check_f_levels(name, days)),
]


def run_all_checks(analytical_name: str, days: int) -> dict:
    gs_id = resolve_gs_id(analytical_name)

    checks = []
    for title, tier, runner in _CHECKS:
        try:
            result = runner(analytical_name, days, gs_id)
            checks.append({"title": title, "tier": tier, **result})
        except Exception as exc:  # noqa: BLE001 - isolate one bad check from the rest
            checks.append({"title": title, "tier": tier, "rows": [], "summaries": [{"text": str(exc), "bad": True}], "status": "error"})

    return {"analytical_name": analytical_name, "days": days, "checks": checks, "tier3": TIER3}
