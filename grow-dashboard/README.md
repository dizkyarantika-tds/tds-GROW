# GROW Dashboard

Standalone, read-only rebuild of the "GROW" (Game Release Onboarding Workflow) Cowork
artifact. Runs as a normal web app — no Claude/Cowork session required — behind Google
Workspace login, restricted to `@tripledotstudios.com`.

Three tabs:
- **Game Release** — upcoming/released games radar (Snowflake) with completeness
  checks and Jira ticket status.
- **ETL Process** — this-week / next-week / now-live games still missing Jira
  tickets. Read-only in this version (no ticket creation).
- **Data Quality** — on-demand, per-game checks across ~10 Snowflake tables.

Game Release + Jira status refresh on a background schedule (default daily,
configurable) with a manual "↺ Refresh" override. Data Quality results are cached per
`(game, day-range)` combo with the same TTL, also manually overridable.

## Local setup

1. `cp .env.example .env` and fill in real values (see below for what you need).
2. Put your Snowflake private key at the path referenced by
   `SNOWFLAKE_PRIVATE_KEY_PATH` (default `./secrets/snowflake_key.p8`) — this repo's
   `.gitignore` already excludes `*.pem`/`*.key`, but double check before committing.
3. `docker compose up --build`
4. Open `http://localhost:8000`.

Without Docker: `pip install -r backend/requirements.txt`, then
`uvicorn backend.main:app --reload --port 8000` from the project root (needs the same
`.env` loaded, e.g. via `python-dotenv` or exporting vars manually).

## What to hand your Data Engineering / IT team

**Snowflake:**
- A read-only service account (e.g. `SVC_GROW_DASHBOARD`) with `SELECT` on:
  - `TDS_DB.RAW.AS_APPLICATIONS`, `AS_TEAMS`, `AS_STUDIOS`,
    `AS_APPLICATION_BUILD_VARIANTS`, `AS_BUILD_VARIANTS`, `TDS_APP_IDS`
  - `TDS_DB.PUBLIC.EVENTS_PRODUCTION_UNION`, `ACTIVITY_SUMMARY_REPORT_NEW`,
    `BI_APPLICATION`, `APP_NAME_MAPPING_GS`, `F_USER_ACTIVITY`, `F_INSTALLS`,
    `F_USER_REVENUE`, `F_RELEASE_MONITORING_HOURLY`, `F_TECH_PERFORMANCE_HOURLY`,
    `F_LEVELS`
  - `TDS_DB.DDS.APPLICATION`
- A dedicated small/x-small warehouse (so this app's queries don't compete with
  other workloads and cost is attributable).
- Key-pair auth set up for the service account (see [Snowflake key-pair auth
  docs](https://docs.snowflake.com/en/user-guide/key-pair-auth)).

**Jira:**
- An API token generated from a **service/bot Atlassian account** (not a personal
  account) with read access to the `AS` and `DS` projects.

**Google Workspace:**
- An OAuth 2.0 client ID (Google Cloud Console) with the redirect URI set to
  `https://<final-hostname>/auth/callback` once hosting is decided.

**Hosting:**
- Not yet decided — this app is containerized (`Dockerfile`) so it should run on
  whatever platform is chosen (VM, Kubernetes, ECS, internal PaaS, etc.). All
  configuration is via environment variables (see `.env.example`), so no code
  changes should be needed regardless of the target.

## Environment variables

See `.env.example` for the full list with comments. Key one to know:
`REFRESH_INTERVAL_HOURS` controls how often the background refresh runs and how long
Data Quality results stay cached — change it any time without a code change.
