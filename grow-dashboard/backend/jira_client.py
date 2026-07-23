"""Thin Jira Cloud REST API v3 proxy.

Replaces the artifact's `fetchJiraData()` (which called
`searchJiraIssuesUsingJql` via the Atlassian MCP server) with a direct call to
`POST /rest/api/3/search/jql` using a service/bot account's API token.

Deliberately dumb: this module only runs the batched JQL search and returns
the raw `issues` array. It does NOT reimplement `fuzzyGameKey()` /
`classifyIssue()` / `getJiraStatus()` — that fuzzy-matching logic stays
client-side in frontend/app.js, unchanged, to avoid drift between two
implementations of the same regex-heavy logic.
"""

import httpx

from .config import Settings, get_settings

BATCH_SIZE = 5


def _jql_for_batch(games: list[str], projects: list[str]) -> str:
    project_list = ", ".join(f'"{p}"' for p in projects)
    name_clause = " OR ".join(f'summary ~ "{name}"' for name in games)
    return (
        f"project in ({project_list}) AND ({name_clause}) "
        f"AND created >= -90d ORDER BY created DESC"
    )


def search_issues_for_games(
    game_names: list[str], settings: Settings | None = None
) -> list[dict]:
    settings = settings or get_settings()
    names = [n.strip() for n in game_names if n and n.strip()]
    if not names:
        return []

    url = f"https://{settings.jira_site}/rest/api/3/search/jql"
    auth = (settings.jira_email, settings.jira_api_token)

    all_issues: list[dict] = []
    with httpx.Client(timeout=30.0) as client:
        for i in range(0, len(names), BATCH_SIZE):
            chunk = names[i : i + BATCH_SIZE]
            jql = _jql_for_batch(chunk, settings.jira_project_list)
            body = {
                "jql": jql,
                "fields": ["summary", "issuetype"],
                "maxResults": 50,
            }
            try:
                resp = client.post(url, json=body, auth=auth)
                resp.raise_for_status()
                data = resp.json()
                all_issues.extend(data.get("issues", []))
            except httpx.HTTPError as exc:
                # Mirrors the artifact's per-batch try/catch: one bad batch
                # shouldn't blank out results for every other game.
                print(f"Jira batch {i}-{i + BATCH_SIZE} error: {exc}")

    return all_issues
