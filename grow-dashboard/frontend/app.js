// ════════════════════════════════════════════════
// STATE
// ════════════════════════════════════════════════
let allGames       = [];
let jiraData       = {}; // normalizedGameKey → { story, tasks: {etl, dds, dashboard, crashlytics} }
let weekGamesMap   = {}; // APPLICATION_NAME → full game object (populated by renderWeek)
let selectedStudios = new Set(); // empty = all shown (populated after load)

// ════════════════════════════════════════════════
// API HELPERS — replace window.cowork.callMcpTool with fetch() against
// this app's own backend (see backend/routers/*.py)
// ════════════════════════════════════════════════
async function apiGet(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} failed: ${res.status} ${await res.text()}`);
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${path} failed: ${res.status} ${await res.text()}`);
  return res.json();
}

// ════════════════════════════════════════════════
// HELPERS — week/date math, badges (unchanged from the original artifact)
// ════════════════════════════════════════════════
function getWeekBounds() {
  const now = new Date();
  const day = now.getDay();
  const mon = new Date(now);
  mon.setDate(now.getDate() - (day === 0 ? 6 : day - 1));
  const sun = new Date(mon); sun.setDate(mon.getDate() + 6);
  const iso = d => d.toISOString().split('T')[0];
  const lbl = d => d.toLocaleDateString('en-GB', { day:'numeric', month:'short' });
  return { start: iso(mon), end: iso(sun), label: `${lbl(mon)} – ${lbl(sun)} ${sun.getFullYear()}` };
}

function isThisWeek(d) {
  const { start, end } = getWeekBounds();
  return d && d >= start && d <= end;
}

function getNextWeekBounds() {
  const { start } = getWeekBounds();
  const nextMon = new Date(start + 'T12:00:00Z');
  nextMon.setUTCDate(nextMon.getUTCDate() + 7);
  const nextSun = new Date(nextMon);
  nextSun.setUTCDate(nextMon.getUTCDate() + 6);
  const iso = d => d.toISOString().split('T')[0];
  return { start: iso(nextMon), end: iso(nextSun) };
}

function isNextWeek(d) {
  const { start, end } = getNextWeekBounds();
  return d && d >= start && d <= end;
}

function isPast(slDate) {
  if (!slDate) return false;
  const { start } = getWeekBounds();
  // Only "released" if SL_DATE is before this week's Monday
  return slDate < start;
}

function whenBadge(slDate) {
  if (isThisWeek(slDate)) return '<span class="badge b-purple">This Week</span>';
  if (isPast(slDate)) return '<span class="badge b-green">Released</span>';
  if (isNextWeek(slDate)) return '<span class="badge b-blue">Following Week</span>';
  return '<span class="badge b-gray">Upcoming</span>';
}

function fmtDate(s) {
  if (!s) return '—';
  return new Date(s + 'T12:00:00Z').toLocaleDateString('en-GB', { day:'numeric', month:'short', year:'numeric' });
}

function fmtDateRange(startIso, endIso) {
  const s = new Date(startIso + 'T12:00:00Z');
  const e = new Date(endIso   + 'T12:00:00Z');
  const sDay = s.getUTCDate();
  const eDay = e.getUTCDate();
  const sMonth = s.toLocaleDateString('en-GB', { month: 'short', timeZone: 'UTC' });
  const eMonth = e.toLocaleDateString('en-GB', { month: 'short', timeZone: 'UTC' });
  const eYear  = e.getUTCFullYear();
  if (s.getUTCMonth() === e.getUTCMonth()) {
    return `${sDay}–${eDay} ${eMonth} ${eYear}`;
  }
  return `${sDay} ${sMonth} – ${eDay} ${eMonth} ${eYear}`;
}

function inferGroup(name) {
  const n = (name || '').toLowerCase();
  if (n.includes('sort'))   return 'Sort';
  if (n.includes('block'))  return 'Blocks';
  if (n.includes('card'))   return 'Cards';
  if (n.includes('tile'))   return 'Tiles';
  if (n.includes('casino') || n.includes('slot')) return 'Casino';
  if (n.includes('iap'))    return 'IAP';
  return 'Others';
}


// ─── Jira helpers (unchanged) ───────────────────
function fuzzyGameKey(name) {
  // Lowercase, strip subtitle after ":", dashes/underscores → spaces, collapse whitespace
  return (name || '')
    .split(':')[0]
    .toLowerCase()
    .replace(/[-_]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function extractSummaryParts(summary) {
  // "(Game) - (description)" → { gameName, desc }
  const idx = summary.indexOf(' - ');
  if (idx === -1) return { gameName: summary.trim(), desc: '' };
  return { gameName: summary.substring(0, idx).trim(), desc: summary.substring(idx + 3).trim() };
}

function hasTerm(str, terms) {
  // All terms must appear (case-insensitive, dashes treated as spaces)
  const lower = (str || '').toLowerCase().replace(/[-_]/g, ' ');
  return terms.every(t => lower.includes(t.toLowerCase()));
}

function classifyIssue(entry, issue, desc) {
  // Normalize: lowercase, collapse dots/dashes/underscores/spaces to single space
  const n = (desc || '').toLowerCase().replace(/[.\-_]/g, ' ').replace(/\s+/g, ' ').trim();
  const isStory = (issue.fields.issuetype?.name || '').toLowerCase() === 'story';

  // ETL — "Add New App to ETLs" (matches "ETL" and "ETLs", story or task)
  if (/\betl/i.test(n)) {
    if (isStory) {
      entry.story = true;
      if (issue.key && !entry.storyKey) entry.storyKey = issue.key; // store for partial creation
    }
    else entry.tasks.etl = true;
  }
  // Crashlytics — "Add to Crashlytics Pipeline"
  if (/crashlytics/i.test(n)) entry.tasks.crashlytics = true;
  // BI Dashboard — "Review and Add to BI-Supported Dashboard"
  if (/dashboard/i.test(n)) entry.tasks.dashboard = true;
  // dds.application — "Add New App to dds.application"
  if (/\bdds\b/i.test(n) && /\bapplication\b/i.test(n)) entry.tasks.dds = true;
}

function getJiraStatus(applicationName, analyticalName) {
  const key    = fuzzyGameKey(applicationName);
  const altKey = fuzzyGameKey(analyticalName);
  const entry  = jiraData[key] || jiraData[altKey] || null;

  if (!entry) return { level: 'fail', issues: ['No tickets found'] };

  const missing = [];
  if (!entry.story)             missing.push('ETL story');
  if (!entry.tasks.etl)         missing.push('ETL task');
  if (!entry.tasks.crashlytics) missing.push('Crashlytics task');
  if (!entry.tasks.dashboard)   missing.push('BI Dashboard task');
  if (!entry.tasks.dds)         missing.push('dds.application task');

  if (missing.length === 0) return { level: 'pass' };
  return { level: 'warn', issues: missing.map(m => `Missing: ${m}`) };
}

function jiraBadge(applicationName, analyticalName) {
  const s = getJiraStatus(applicationName, analyticalName);
  if (s.level === 'pass') return '<span class="badge b-green">✓ Jira</span>';
  if (s.level === 'warn') {
    const tip = s.issues.join('\n');
    return `<span class="badge b-yellow" title="${tip}" style="cursor:help">⚠ Jira</span>`;
  }
  return '<span class="badge b-red">✗ Jira</span>';
}

// Builds the same jiraData shape the original fetchJiraData() produced, but
// from a raw issues array the backend already fetched (see GET /api/jira) —
// the fuzzy-matching/classification logic itself is unchanged.
function buildJiraData(issues, games) {
  const keyMap = {};
  games.forEach(g => {
    const gameKey = fuzzyGameKey(g.APPLICATION_NAME);
    const altKey  = fuzzyGameKey(g.ANALYTICAL_NAME);
    keyMap[gameKey] = gameKey;
    if (altKey && !keyMap[altKey]) keyMap[altKey] = gameKey;
  });

  const data = {};
  (issues || []).forEach(issue => {
    const { gameName, desc } = extractSummaryParts(issue.fields?.summary || '');
    const ticketKey = fuzzyGameKey(gameName);
    const gameKey = keyMap[ticketKey];
    if (!gameKey) return;

    if (!data[gameKey]) data[gameKey] = {
      gameName,
      story: false,
      storyKey: null,
      tasks: { etl: false, crashlytics: false, dashboard: false, dds: false }
    };
    classifyIssue(data[gameKey], issue, desc);
  });
  return data;
}

function getMissing(g) {
  const m = [];
  if (!g.ANALYTICAL_NAME)  m.push('Analytical Name');
  if (!g.GAME_SERVER_ID)   m.push('Game Server ID');
  if (!g.ADJUST_IOS)       m.push('Adjust iOS');
  if (!g.ADJUST_ANDROID)   m.push('Adjust Android');
  if (!g.BUNDLE_ID)        m.push('Bundle ID');
  return m;
}

function isAdjustWarn(g) {
  return !!(g.ADJUST_IOS && g.ADJUST_ANDROID && g.ADJUST_IOS === g.ADJUST_ANDROID);
}

function ck(val, warn) {
  if (warn) return '<span class="ck ck-warn" title="iOS = Android — verify">⚠</span>';
  return val
    ? '<span class="ck ck-ok">✓</span>'
    : '<span class="ck ck-fail">✗</span>';
}

function epuStatus(g) {
  // Returns { level: 'none'|'testing'|'live', label, tooltip }
  if (!g.GAME_SERVER_ID) return { level: 'na' };
  if (!g.EPU_CHECKED)    return { level: 'unknown' };
  if (!g.EPU)            return { level: 'none', label: '✗ No events', tooltip: 'No events in last 14 days' };
  const devices   = parseInt(g.EPU.DISTINCT_DEVICES || 0);
  const gameStart = parseInt(g.EPU.GAME_START_COUNT || 0);
  const gameEnd   = parseInt(g.EPU.GAME_END_COUNT   || 0);
  const rows      = parseInt(g.EPU.ROW_COUNT || 0).toLocaleString();
  const evtTypes  = parseInt(g.EPU.DISTINCT_EVENTS || 0);
  const tip       = `${rows} rows · ${evtTypes} event types · ${devices} device models · ${gameStart} Game_Start · ${gameEnd} Game_End`;
  if (devices > 5 && gameStart >= 5 && gameEnd >= 5) {
    return { level: 'live', label: `Live · ${devices} dev`, tooltip: tip };
  }
  return { level: 'testing', label: `Testing · ${devices} dev`, tooltip: tip };
}

function epuCell(g) {
  if (!g.GAME_SERVER_ID) return '<span style="color:#9ca3af;font-size:12px">—</span>';
  if (!g.EPU_CHECKED)    return '<span style="color:#9ca3af;font-size:12px">—</span>'; // outside this/next week
  if (g.EPU_ERROR)       return `<span class="badge b-red" title="${g.EPU_ERROR}">⚠ EPU error</span>`;
  const s = epuStatus(g);
  if (s.level === 'none')    return `<span class="badge b-red" title="${s.tooltip}">${s.label}</span>`;
  if (s.level === 'testing') return `<span class="badge b-yellow" title="${s.tooltip}">🧪 ${s.label}</span>`;
  if (s.level === 'live')    return `<span class="badge b-green" title="${s.tooltip}">✓ ${s.label}</span>`;
  return '<span class="badge b-gray" title="Query unavailable">?</span>';
}

function statusBadge(g, hasJira) {
  if (!g.ANALYTICAL_NAME) return '<span class="badge b-red">Blocked</span>';
  const missing = getMissing(g);
  const warn    = isAdjustWarn(g);
  if (missing.length > 0 || warn) return '<span class="badge b-yellow">Needs Review</span>';
  if (!hasJira)                   return '<span class="badge b-blue">Action Needed</span>';
  return '<span class="badge b-green">Ready ✓</span>';
}

function setStatus(msg, state) {
  document.getElementById('status-text').textContent = msg;
  const d = document.getElementById('status-dot');
  d.className = 'dot' + (state === 'loading' ? ' loading' : state === 'error' ? ' error' : '');
}

function switchTab(name) {
  const names = ['radar','week','dq'];
  document.querySelectorAll('.tab-btn').forEach((b,i) => b.classList.toggle('active', names[i] === name));
  document.querySelectorAll('.tab-pane').forEach((p,i) => p.classList.toggle('active', names[i] === name));
}

// ════════════════════════════════════════════════
// LOAD DATA — read the backend's cache (populated on a schedule, see
// backend/scheduler.py), with manual "↺ Refresh" buttons forcing an
// immediate recompute instead of waiting for the schedule.
// ════════════════════════════════════════════════
function applyRadarPayload(payload) {
  allGames = payload.games || [];
  if (payload.updated_at) {
    const t = new Date(payload.updated_at).toLocaleString('en-GB', { day:'numeric', month:'short', hour:'2-digit', minute:'2-digit' });
    document.getElementById('last-updated').textContent = `Updated ${t}`;
  }
}

function applyJiraPayload(payload) {
  jiraData = buildJiraData(payload.issues || [], allGames);
}

async function loadAll() {
  setStatus('Loading...', 'loading');
  document.getElementById('radar-table').innerHTML = '<div class="loading-state"><div class="spinner"></div> Loading...</div>';
  document.getElementById('week-table').innerHTML  = '<div class="loading-state"><div class="spinner"></div></div>';
  document.getElementById('radar-chips').style.display = 'none';
  document.getElementById('radar-issues').innerHTML = '';

  try {
    const radarPayload = await apiGet('/api/radar');
    applyRadarPayload(radarPayload);
  } catch (e) {
    allGames = [];
    document.getElementById('radar-table').innerHTML =
      `<div class="empty-state">⚠️ Could not load radar data — ${e.message}</div>`;
    document.getElementById('week-table').innerHTML =
      `<div class="empty-state">⚠️ Could not load radar data</div>`;
    setStatus('Error loading data', 'error');
    return;
  }

  try {
    const jiraPayload = await apiGet('/api/jira');
    applyJiraPayload(jiraPayload);
  } catch (e) {
    jiraData = {};
    console.warn('Jira load error:', e);
  }

  setStatus(`${allGames.length} game${allGames.length !== 1 ? 's' : ''} loaded`, 'ok');
  renderRadar();
  renderWeek();
  populateDQSelect();
}

async function refresh() {
  setStatus('Refreshing...', 'loading');
  const btn = document.querySelector('.status-bar .btn-refresh');
  if (btn) btn.disabled = true;
  try {
    const payload = await apiPost('/api/refresh');
    applyRadarPayload(payload);
    try {
      const jiraPayload = await apiGet('/api/jira');
      applyJiraPayload(jiraPayload);
    } catch (e) {
      console.warn('Jira load error after refresh:', e);
    }
    setStatus(`${allGames.length} game${allGames.length !== 1 ? 's' : ''} loaded`, 'ok');
    renderRadar();
    renderWeek();
    populateDQSelect();
  } catch (e) {
    setStatus(`Refresh failed — ${e.message}`, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function refreshJira() {
  const btn = document.getElementById('refresh-jira-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Checking Jira...'; }
  try {
    const payload = await apiPost('/api/jira/refresh');
    applyJiraPayload(payload);
    renderWeek();
    renderRadar();
  } catch(e) {
    console.warn('Jira refresh error:', e);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '↺ Refresh Jira'; }
  }
}

// ════════════════════════════════════════════════
// STUDIO FILTER (unchanged)
// ════════════════════════════════════════════════
function renderStudioChips(chipsElId, filterRowId) {
  const studios = [...new Set(allGames.map(g => g.STUDIO_NAME || 'Unknown'))].sort();
  const chipsEl = document.getElementById(chipsElId);
  if (!chipsEl) return;
  chipsEl.innerHTML = studios.map(s => `
    <button class="studio-chip ${selectedStudios.has(s) ? 'on' : 'off'}"
      data-studio="${s.replace(/&/g,'&amp;').replace(/"/g,'&quot;')}">${s}</button>
  `).join('');
  chipsEl.querySelectorAll('.studio-chip').forEach(btn => {
    btn.addEventListener('click', () => toggleStudio(btn.dataset.studio));
  });
  const row = document.getElementById(filterRowId);
  if (row) row.style.display = 'flex';
}

function initStudioFilter() {
  const studios = [...new Set(allGames.map(g => g.STUDIO_NAME || 'Unknown'))].sort();
  // Default: all ON except Ludios
  if (selectedStudios.size === 0) {
    studios.forEach(s => { if (!s.toLowerCase().includes('ludio')) selectedStudios.add(s); });
  }
  renderStudioChips('studio-chips', 'studio-filter');
  renderStudioChips('week-studio-chips', 'week-studio-filter');
}

function toggleStudio(name) {
  if (selectedStudios.has(name)) selectedStudios.delete(name);
  else selectedStudios.add(name);
  initStudioFilter();
  renderRadar();
  renderWeek();
}

// ════════════════════════════════════════════════
// ISSUES PANEL — this week + next week with problems (unchanged)
// ════════════════════════════════════════════════
function getGameIssues(g) {
  const jiraStatus   = getJiraStatus(g.APPLICATION_NAME, g.ANALYTICAL_NAME);
  const hasJira      = jiraStatus.level === 'pass';
  const aWarn   = isAdjustWarn(g);
  const issues  = [];

  // Blocked
  if (!g.ANALYTICAL_NAME)
    issues.push({ text: 'No analytical name set', type: 'ip-block' });

  // Data missing
  if (!g.GAME_SERVER_ID)
    issues.push({ text: 'Game Server ID not found', type: 'ip-warn' });
  if (!g.ADJUST_IOS && !aWarn)
    issues.push({ text: 'Adjust iOS token missing', type: 'ip-warn' });
  if (!g.ADJUST_ANDROID && !aWarn)
    issues.push({ text: 'Adjust Android token missing', type: 'ip-warn' });
  if (aWarn)
    issues.push({ text: 'iOS and Android Adjust tokens are the same', type: 'ip-warn' });
  if (!g.BUNDLE_ID)
    issues.push({ text: 'Bundle ID missing', type: 'ip-warn' });

  // Events production union
  if (g.EPU_CHECKED && !g.EPU)
    issues.push({ text: 'No events in the last 14 days', type: 'ip-warn' });
  if (g.EPU_CHECKED && g.EPU) {
    const s = epuStatus(g);
    if (s.level === 'testing')
      issues.push({ text: 'Events look like internal testing only', type: 'ip-warn' });
  }

  // Action needed
  if (jiraStatus.level === 'fail')
    issues.push({ text: 'Jira tickets not created', type: 'ip-action' });
  else if (jiraStatus.level === 'warn')
    issues.push({ text: `Jira incomplete: ${jiraStatus.issues.join('; ')}`, type: 'ip-action' });
  return issues;
}

function renderIssuesPanel(visible) {
  const urgent = visible.filter(g => isThisWeek(g.SL_DATE) || isNextWeek(g.SL_DATE));
  const withIssues = urgent.map(g => ({ g, issues: getGameIssues(g) })).filter(x => x.issues.length > 0);

  const el = document.getElementById('radar-issues');
  if (withIssues.length === 0) { el.innerHTML = ''; return; }

  const sections = withIssues.map(({ g, issues }) => {
    const bullets = issues.map(i => `
      <li class="issue-bullet ${i.type}">
        <span class="ib-dot"></span>
        <span class="ib-text">${i.text}</span>
      </li>`).join('');

    return `
      <div class="issue-section">
        <div class="issue-game-header">
          <span class="issue-game-name">${g.APPLICATION_NAME}</span>
          ${whenBadge(g.SL_DATE)}
          <span style="font-size:11px;color:#6b7280">${fmtDate(g.SL_DATE)}</span>
        </div>
        <ul class="issue-bullets">${bullets}</ul>
      </div>`;
  }).join('');

  el.innerHTML = `
    <div class="issues-card">
      <div class="issues-card-header">
        <span class="issues-card-title">Needs Attention (${withIssues.length} game${withIssues.length !== 1 ? 's' : ''})</span>
      </div>
      ${sections}
    </div>`;
}

// ════════════════════════════════════════════════
// RENDER — GAME RADAR (unchanged)
// ════════════════════════════════════════════════
function renderRadar() {
  initStudioFilter(); // must run first so selectedStudios is populated before filtering
  const visible = allGames.filter(g => selectedStudios.has(g.STUDIO_NAME || 'Unknown'));

  const upcoming = visible.filter(g => !isPast(g.SL_DATE));
  const released = visible.filter(g => isPast(g.SL_DATE));

  function buildRows(games) {
    return games.map(g => {
      const jiraStatus   = getJiraStatus(g.APPLICATION_NAME, g.ANALYTICAL_NAME);
      const hasJira      = jiraStatus.level === 'pass';
      const aWarn        = isAdjustWarn(g);
      return `<tr>
        <td>
          <div class="game-name">${g.APPLICATION_NAME || '—'}</div>
          <div class="${g.ANALYTICAL_NAME ? 'game-sub' : 'game-no-an'}">${g.ANALYTICAL_NAME || '⚠ No analytical name'}</div>
        </td>
        <td style="white-space:nowrap">${g.STUDIO_NAME || '—'}</td>
        <td style="white-space:nowrap">${whenBadge(g.SL_DATE)}<div style="font-size:11px;color:#6b7280;margin-top:3px">${fmtDate(g.SL_DATE)}</div></td>
        <td class="center">${ck(g.ANALYTICAL_NAME)}</td>
        <td class="center">${ck(g.GAME_SERVER_ID)}</td>
        <td class="center">${ck(g.ADJUST_IOS, aWarn)}</td>
        <td class="center">${ck(g.ADJUST_ANDROID, aWarn)}</td>
        <td class="center">${ck(g.BUNDLE_ID)}</td>
        <td class="center">${epuCell(g)}</td>
        <td class="center">${jiraBadge(g.APPLICATION_NAME, g.ANALYTICAL_NAME)}</td>
        <td>${statusBadge(g, hasJira)}</td>
      </tr>`;
    }).join('');
  }

  const tableHeader = `<table>
      <thead><tr>
        <th>App Name</th>
        <th>Studio</th>
        <th>Soft Launch</th>
        <th title="Analytical Name">AN</th>
        <th title="Game Server ID">GS ID</th>
        <th title="Adjust Token iOS">iOS Adj</th>
        <th title="Adjust Token Android">And Adj</th>
        <th title="Bundle ID">Bundle</th>
        <th title="Events in events_production_union (last 14 days) · Testing = ≤5 device models · Live = >5 devices + ≥5 Game_Start + ≥5 Game_End">Events</th>
        <th title="Jira AS Story">Jira</th>
        <th>Status</th>
      </tr></thead>
      <tbody>`;

  // ── Upcoming table
  const thisWeekN = upcoming.filter(g => isThisWeek(g.SL_DATE)).length;
  const nextWeekN = upcoming.filter(g => isNextWeek(g.SL_DATE)).length;
  const readyN    = upcoming.filter(g => !getMissing(g).length && !isAdjustWarn(g) && getJiraStatus(g.APPLICATION_NAME, g.ANALYTICAL_NAME).level === 'pass').length;
  document.getElementById('radar-chips').innerHTML = `
    <span class="badge b-purple">📅 ${thisWeekN} this week</span>
    <span class="badge b-blue">🗓 ${nextWeekN} next week</span>
    <span class="badge b-green">✓ ${readyN} ready</span>
    <span class="badge b-gray">${upcoming.length} shown</span>`;
  document.getElementById('radar-chips').style.display = 'flex';

  document.getElementById('radar-table').innerHTML = upcoming.length === 0
    ? '<div class="empty-state">No upcoming games match the current studio filter</div>'
    : tableHeader + buildRows(upcoming) + `</tbody></table>`;

  // ── Released table
  const releasedCard = document.getElementById('released-card');
  if (released.length > 0) {
    releasedCard.style.display = '';
    const liveN = released.filter(g => epuStatus(g).level === 'live').length;
    document.getElementById('released-chips').innerHTML = `
      <span class="badge b-green">${released.length} released</span>
      <span class="badge b-green">${liveN} live EPU</span>`;
    document.getElementById('released-table').innerHTML = tableHeader + buildRows(released) + `</tbody></table>`;
  } else {
    releasedCard.style.display = 'none';
  }

  renderIssuesPanel(visible);
}

// ════════════════════════════════════════════════
// RENDER — ETL PROCESS (read-only: reporting only, no Jira ticket creation)
// Sections: This Week / Following Week / Now Live
// ════════════════════════════════════════════════
function renderWeek() {
  renderStudioChips('week-studio-chips', 'week-studio-filter');

  const visible = allGames.filter(g => selectedStudios.has(g.STUDIO_NAME || 'Unknown'));
  const thisWeekGames = visible.filter(g => isThisWeek(g.SL_DATE));
  const nextWeekGames = visible.filter(g => isNextWeek(g.SL_DATE));
  // Now Live = all games released in the last 30 days (before this week)
  const _thirtyAgo = new Date();
  _thirtyAgo.setUTCDate(_thirtyAgo.getUTCDate() - 30);
  const _thirtyAgoStr = _thirtyAgo.toISOString().split('T')[0];
  const { start: _weekStart } = getWeekBounds();
  const nowLiveGames = visible.filter(g =>
    g.SL_DATE && g.SL_DATE < _weekStart && g.SL_DATE >= _thirtyAgoStr
  );
  // Count ALL games with live EPU (including this/next week) for accurate chip
  const allLiveEPUCount = visible.filter(g => epuStatus(g).level === 'live').length;

  const allInScope = [...thisWeekGames, ...nextWeekGames, ...nowLiveGames];
  const totalNeedJira = allInScope.filter(g => getJiraStatus(g.APPLICATION_NAME, g.ANALYTICAL_NAME).level !== 'pass').length;

  // Summary chips
  const chipsEl = document.getElementById('week-summary-chips');
  if (chipsEl) {
    chipsEl.innerHTML = `
      <span class="badge b-purple">${thisWeekGames.length} this week</span>
      <span class="badge b-blue">${nextWeekGames.length} following week</span>
      <span class="badge b-green">${allLiveEPUCount} live EPU</span>
      <span class="badge b-red">${totalNeedJira} need Jira</span>`;
  }

  if (allInScope.length === 0) {
    document.getElementById('week-table').innerHTML = '<div class="empty-state">🎉 No games in scope for Phase 2</div>';
    return;
  }

  function renderSection(games, sectionTitle, borderColor) {
    if (games.length === 0) return '';
    const rows = games.map(g => {
      const jiraStatus = getJiraStatus(g.APPLICATION_NAME, g.ANALYTICAL_NAME);
      const missing    = getMissing(g);
      const aWarn      = isAdjustWarn(g);
      const dataIssues = [...missing, ...(aWarn ? ['Adjust tokens identical'] : [])];
      const epu = epuStatus(g);
      const epuBadge = epu.level === 'live'    ? '<span class="badge b-green">✓ Live</span>'
                     : epu.level === 'testing' ? '<span class="badge b-yellow">Testing</span>'
                     : epu.level === 'none'    ? '<span class="badge b-red">No events</span>'
                     : '<span class="badge b-gray">—</span>';

      return `<tr>
        <td>
          <div class="game-name">${g.APPLICATION_NAME}</div>
          <div class="game-sub">${g.ANALYTICAL_NAME || '—'}</div>
        </td>
        <td style="white-space:nowrap">${fmtDate(g.SL_DATE)}</td>
        <td>${g.STUDIO_NAME || '—'}</td>
        <td>${epuBadge}</td>
        <td>${jiraBadge(g.APPLICATION_NAME, g.ANALYTICAL_NAME)}</td>
        <td>${dataIssues.length === 0
          ? '<span class="badge b-green">Complete</span>'
          : dataIssues.slice(0,2).map(i=>`<span class="badge b-red" style="font-size:10px">${i}</span>`).join(' ')}</td>
      </tr>`;
    }).join('');

    return `
      <div style="margin:16px 18px 0; border-radius:8px; border:1px solid ${borderColor}25; overflow:hidden;">
        <div style="padding:10px 16px; font-size:12px; font-weight:700; color:${borderColor}; background:${borderColor}0d; border-bottom:1px solid ${borderColor}25;">
          ${sectionTitle}
        </div>
        <div class="overflow-x">
          <table>
            <thead><tr>
              <th>App</th><th>Soft Launch</th><th>Studio</th>
              <th>EPU</th><th>Jira</th><th>Data Gaps</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>`;
  }

  const wb  = getWeekBounds();
  const nwb = getNextWeekBounds();
  const thisWeekLabel = `This Week (${fmtDateRange(wb.start, wb.end)})`;
  const nextWeekLabel = `Following Week (${fmtDateRange(nwb.start, nwb.end)})`;

  weekGamesMap = {};
  [...thisWeekGames, ...nextWeekGames, ...nowLiveGames].forEach(g => {
    weekGamesMap[g.APPLICATION_NAME] = g;
  });

  const weekTableEl = document.getElementById('week-table');
  weekTableEl.innerHTML =
    renderSection(thisWeekGames, thisWeekLabel, '#7c3aed') +
    renderSection(nextWeekGames, nextWeekLabel, '#1d4ed8') +
    (nowLiveGames.length > 0 ? renderSection(nowLiveGames, 'Now Live — Released (Last 30 Days)', '#0f766e') : '') +
    '<div style="height:16px"></div>';
}

// ════════════════════════════════════════════════
// DATA QUALITY
// ════════════════════════════════════════════════
let dqCheckData = {};
let _dqCardId   = 0;

function fmtVal(v) {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'string') {
    if (v.match(/^\d{4}-\d{2}-\d{2}/)) return v.substring(0, 10);
    const n = Number(v);
    if (isNaN(n)) return v;
    if (!v.includes('.')) return n.toLocaleString();
    return n.toFixed(2);
  }
  const n = Number(v);
  if (isNaN(n)) return String(v);
  if (Number.isInteger(n)) return n.toLocaleString();
  return n.toFixed(2);
}

function renderFlags(rows, skipCols) {
  if (!rows || rows.length === 0) return '';
  const upper = (skipCols || []).map(c => c.toUpperCase());
  const metricCols = Object.keys(rows[0] || {}).filter(k => !upper.includes(k.toUpperCase()));
  const flags = [];
  for (const col of metricCols) {
    if (flags.length >= 5) break;
    const allEmpty = rows.every(r => {
      const v = r[col];
      return v === null || v === undefined || v === '' || v === '0' || Number(v) === 0;
    });
    if (allEmpty) flags.push(col);
  }
  if (flags.length === 0) return '';
  return `<div class="dq-flags-inner">` +
    flags.map(f => `<span class="dq-flag">No data: ${f.toLowerCase().replace(/_/g,' ')}</span>`).join('') +
    `</div>`;
}

function refreshAllCards(platform) {
  Object.entries(dqCheckData).forEach(([id, data]) => {
    let rows = data.rows;
    const hasPlatformCol = rows.some(r => r.PLATFORM !== undefined);
    if (platform && platform !== 'all' && hasPlatformCol) {
      rows = rows.filter(r => (r.PLATFORM || '').toLowerCase() === platform);
    }
    const flagsEl = document.getElementById('dqflags_' + id);
    if (flagsEl) flagsEl.innerHTML = renderFlags(rows, data.skipCols);
  });
}

function filterDQPlatform(p, btn) {
  document.querySelectorAll('.pf-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  document.querySelectorAll('#dq-results tr[data-platform]').forEach(row => {
    row.style.display = (p === 'all' || row.dataset.platform === p) ? '' : 'none';
  });
  refreshAllCards(p);
}

function populateDQSelect() {
  const sel = document.getElementById('dq-game');
  sel.innerHTML = '<option value="">Select a game...</option>';

  const dqThirtyAgo = new Date();
  dqThirtyAgo.setUTCDate(dqThirtyAgo.getUTCDate() - 30);
  const dqThirtyAgoStr = dqThirtyAgo.toISOString().split('T')[0];
  const dqWeekStart = getWeekBounds().start;

  const upcoming = allGames.filter(g => g.ANALYTICAL_NAME && (!g.SL_DATE || g.SL_DATE >= dqWeekStart));
  const released = allGames.filter(g => g.ANALYTICAL_NAME && g.SL_DATE && g.SL_DATE < dqWeekStart && g.SL_DATE >= dqThirtyAgoStr);

  function addGroup(label, games) {
    if (games.length === 0) return;
    const grp = document.createElement('optgroup');
    grp.label = label;
    games.forEach(g => {
      const o = document.createElement('option');
      o.value       = g.ANALYTICAL_NAME;
      o.textContent = `${g.APPLICATION_NAME}  (${g.ANALYTICAL_NAME})`;
      grp.appendChild(o);
    });
    sel.appendChild(grp);
  }

  addGroup('Upcoming', upcoming);
  addGroup('Released — Last 30 Days', released);
}

function badgeForStatus(status) {
  if (status === 'found' || status === 'pass') return '<span class="badge b-green">✓ Pass</span>';
  if (status === 'not_found') return '<span class="badge b-yellow">⚠ Not found</span>';
  if (status === 'fail') return '<span class="badge b-red">✗ Fail</span>';
  if (status === 'error') return '<span class="badge b-red">Error</span>';
  return '<span class="badge b-gray">—</span>';
}

function columnsFor(title) {
  // Column sets per check table — mirrors the original artifact's per-check
  // mkTable() column lists exactly.
  const cols = {
    'tds_db.public.events_production_union': [
      {key:'PLATFORM',label:'Platform'},{key:'EVENT_NAME',label:'Event Name'},
      {key:'NUM_EVENTS',label:'Total Events'},{key:'DISTINCT_USERS',label:'Distinct Users'},
      {key:'TOTAL_DISTINCT_USERS',label:'Total Users'},{key:'PCT_USERS',label:'% Users'}
    ],
    'tds_db.dds.application': [
      {key:'DDS_APP',label:'dds_app'},{key:'DDS_TEAM',label:'dds_team'},{key:'DDS_STUDIO',label:'dds_studio'},
      {key:'AS_APP',label:'as_app'},{key:'AS_TEAM',label:'as_team'},{key:'AS_STUDIO',label:'as_studio'},
      {key:'CHECK_APP',label:'check_app'},{key:'CHECK_TEAM',label:'check_team'},{key:'CHECK_STUDIO',label:'check_studio'}
    ],
    'tds_db.public.bi_application': [
      {key:'BI_APP',label:'bi_app'},{key:'BI_TEAM',label:'bi_team'},{key:'BI_STUDIO',label:'bi_studio'},
      {key:'AS_APP',label:'as_app'},{key:'AS_TEAM',label:'as_team'},{key:'AS_STUDIO',label:'as_studio'},
      {key:'CHECK_APP',label:'check_app'},{key:'CHECK_TEAM',label:'check_team'},{key:'CHECK_STUDIO',label:'check_studio'}
    ],
    'tds_db.public.app_name_mapping_gs': [
      {key:'GS_ID',label:'gs_id'},{key:'GS_NAME',label:'gs_name'},{key:'GS_APP_GROUP',label:'gs_app_group'},
      {key:'TDS_ANALYTICAL_NAME',label:'tds_analytical_name'},
      {key:'TDS_GS_APP_ID',label:'tds_gs_app_id'},{key:'TDS_ZM_GS_APP_ID',label:'tds_zm_gs_app_id'},
      {key:'TDS_LDS_GS_APP_ID',label:'tds_lds_gs_app_id'},{key:'TDS_TT_GS_APP_ID',label:'tds_tt_gs_app_id'},
      {key:'CHECK_ID',label:'check_id'},{key:'CHECK_NAME',label:'check_name'}
    ],
    'tds_db.public.activity_summary_report_new': [
      {key:'DATE',label:'Date'},{key:'PLATFORM',label:'Platform'},
      {key:'DISTINCT_COUNTRY',label:'Countries'},{key:'DISTINCT_PLACEMENT_TYPE',label:'Placement Types'},
      {key:'DISTINCT_CONNECTION',label:'Connections'},{key:'DISTINCT_MEDIA_SOURCE',label:'Media Sources'},
      {key:'DAU_AVG',label:'DAU Avg'},{key:'SESSIONS_AVG',label:'Sessions Avg'},{key:'INSTALLS_AVG',label:'Installs Avg'},
      {key:'SUM_REVENUE',label:'Ad Revenue'},{key:'SUM_IAP_REVENUE',label:'IAP Revenue'},
      {key:'SUM_IMPRESSION',label:'Impressions'},{key:'SUM_REQUEST',label:'Requests'},
      {key:'SUM_CLICKS',label:'Clicks'},{key:'SUM_SPENT',label:'Spent'}
    ],
    'tds_db.public.f_user_activity': [
      {key:'DT',label:'Date'},{key:'PLATFORM',label:'Platform'},{key:'DAU',label:'DAU'},
      {key:'GAMES_PER_DAU',label:'Games/DAU'},{key:'GAME_TIME_PER_DAU',label:'Game Time/DAU'},
      {key:'FS_IMP_PER_DAU',label:'FS Imp/DAU'},{key:'BANNER_IMP_PER_DAU',label:'Banner Imp/DAU'},{key:'REWARD_IMP_PER_DAU',label:'Reward Imp/DAU'}
    ],
    'tds_db.public.f_installs': [
      {key:'INSTALL_DATE',label:'Date'},{key:'PLATFORM',label:'Platform'},{key:'NUM_INSTALLS',label:'Installs'},
      {key:'DISTINCT_COUNTRY',label:'Countries'},{key:'DISTINCT_SOURCE',label:'Sources'},
      {key:'CPI_LIBRING_PER_INSTALL',label:'CPI Libring/Install'},{key:'TOTAL_CPI',label:'Total CPI'}
    ],
    'tds_db.public.f_user_revenue': [
      {key:'DATE',label:'Date'},{key:'PLATFORM',label:'Platform'},
      {key:'TOTAL_REVENUE',label:'Revenue'},{key:'ARPDAU',label:'ARPDAU'},{key:'CPM',label:'CPM'},
      {key:'FS_ARPDAU',label:'FS ARPDAU'},{key:'BANNER_ARPDAU',label:'Banner ARPDAU'},{key:'REWARD_ARPDAU',label:'Reward ARPDAU'}
    ],
    'tds_db.public.f_release_monitoring_hourly': [
      {key:'DT',label:'Date'},{key:'PLATFORM',label:'Platform'},{key:'ACTIVE_USERS',label:'Active Users'},
      {key:'TOTAL_GAMES',label:'Games'},{key:'D1_RETURNS',label:'D1 Returns'},
      {key:'FATAL_CRASHES',label:'Fatal Crashes'},{key:'FATAL_CRASH_USERS',label:'Fatal Crash Users'},{key:'NON_FATAL_CRASHES',label:'Non-Fatal'},
      {key:'MEDIAN_APP_LOAD_MS',label:'App Load (ms)'},
      {key:'INTER_REV',label:'Inter Rev'},{key:'BANNER_REV',label:'Banner Rev'},{key:'REWARD_REV',label:'Reward Rev'},
      {key:'INTER_FILL_RATE',label:'Inter Fill'},{key:'REWARD_FILL_RATE',label:'Reward Fill'}
    ],
    'tds_db.public.f_tech_performance_hourly': [
      {key:'EVENT_DATE',label:'Date'},{key:'PLATFORM',label:'Platform'},{key:'ACTIVE_USER',label:'Active Users'},
      {key:'DISTINCT_DEVICE_MODEL',label:'Device Models'},
      {key:'APP_OPEN_FIRST_MS',label:'Open (First) ms'},{key:'APP_OPEN_RETURN_MS',label:'Open (Return) ms'},
      {key:'MEMORY_FREE',label:'Memory Free'},{key:'MEMORY_USED',label:'Memory Used'},
      {key:'TOTAL_CRASH',label:'Crashes'}
    ],
    'tds_db.public.f_levels': [
      {key:'ACTIVITY_DATE',label:'Date'},{key:'PLATFORM',label:'Platform'},
      {key:'TOTAL_USER',label:'Users'},{key:'UNIQUE_LEVELS',label:'Unique Levels'},{key:'MAX_LEVEL',label:'Max Level'},
      {key:'TOTAL_GAMES_FINISHED',label:'Games Finished'},{key:'TOTAL_GAME_LENGTH',label:'Total Game Length'},
      {key:'COINS_USAGE_ROWS',label:'Coins Usage Rows'},
      {key:'INTER_IMP',label:'Inter Imp'},{key:'BANNER_IMP',label:'Banner Imp'},{key:'REWARD_IMP',label:'Reward Imp'},
      {key:'INTER_REV',label:'Inter Rev'},{key:'BANNER_REV',label:'Banner Rev'},{key:'REWARD_REV',label:'Reward Rev'}
    ],
  };
  return cols[title] || (title === 'tds_db.public.events_production_union' ? cols[title] : null);
}

function skipColsFor(title) {
  const skip = {
    'tds_db.public.events_production_union': ['PLATFORM','EVENT_NAME','TOTAL_DISTINCT_USERS'],
    'tds_db.public.activity_summary_report_new': ['DATE','PLATFORM'],
    'tds_db.public.f_user_activity': ['DT','PLATFORM'],
    'tds_db.public.f_installs': ['INSTALL_DATE','PLATFORM'],
    'tds_db.public.f_user_revenue': ['DATE','PLATFORM'],
    'tds_db.public.f_release_monitoring_hourly': ['DT','PLATFORM'],
    'tds_db.public.f_tech_performance_hourly': ['EVENT_DATE','PLATFORM'],
    'tds_db.public.f_levels': ['ACTIVITY_DATE','PLATFORM'],
  };
  return skip[title] || null;
}

function mkTable(rows, cols) {
  if (!rows || rows.length === 0 || !cols) return '<p style="color:#9ca3af;font-size:12px;padding:8px 14px">No rows returned.</p>';
  const hdr = cols.map(c => `<th>${c.label}</th>`).join('');
  const body = rows.map(r => {
    const plat = (r.PLATFORM || '').toLowerCase();
    const attr = plat ? ` data-platform="${plat}"` : '';
    return `<tr${attr}>${cols.map(c => `<td>${fmtVal(r[c.key])}</td>`).join('')}</tr>`;
  }).join('');
  return `<table><thead><tr>${hdr}</tr></thead><tbody>${body}</tbody></table>`;
}

function mkCard(title, badge, summaries, tableHtml, checkData) {
  const id = 'dqchk_' + (_dqCardId++);
  if (checkData) dqCheckData[id] = checkData;
  const sHtml = summaries.map(s =>
    `<span class="dq-summary${s.warn?' warn':s.bad?' bad':''}">${s.text}</span>`
  ).join('');
  const flagsHtml = checkData ? renderFlags(checkData.rows, checkData.skipCols) : '';
  return `
    <div class="dq-check" data-check-id="${id}">
      <div class="dq-check-header">
        <span class="dq-check-title">${title}</span>${badge}
      </div>
      <div class="dq-summaries" id="dqsums_${id}">${sHtml}</div>
      <div class="dq-flags" id="dqflags_${id}">${flagsHtml}</div>
      <div class="dq-table-wrap">${tableHtml}</div>
    </div>`;
}

async function runDQ(force) {
  const analyticalName = document.getElementById('dq-game').value;
  const days = parseInt(document.getElementById('dq-days').value);
  if (!analyticalName) { alert('Please select a game.'); return; }

  const el = document.getElementById('dq-results');
  el.innerHTML = '<div class="loading-state"><div class="spinner"></div> Running all checks...</div>';
  dqCheckData = {};
  _dqCardId   = 0;

  let payload;
  try {
    payload = await apiPost('/api/dq/run', { analytical_name: analyticalName, days, force: !!force });
  } catch (e) {
    el.innerHTML = `<div class="empty-state">⚠️ ${e.message}</div>`;
    return;
  }

  let html = '';
  let lastTier = null;
  const tierLabels = { 1: '🔴 Tier 1 — Presence &amp; Consistency', 2: '🟡 Tier 2 — Metrics' };

  (payload.checks || []).forEach(check => {
    if (check.tier !== lastTier) {
      html += `<div class="tier-header">${tierLabels[check.tier] || `Tier ${check.tier}`}</div>`;
      lastTier = check.tier;
    }
    const cols = columnsFor(check.title);
    const skipCols = skipColsFor(check.title);
    html += mkCard(
      check.title,
      badgeForStatus(check.status),
      check.summaries || [],
      mkTable(check.rows, cols),
      skipCols ? { rows: check.rows, skipCols } : null
    );
  });

  html += `<div class="tier-header">⚪ Tier 3 — Further Enhancement (Planned)</div>`;
  (payload.tier3 || []).forEach(g => {
    g.tables.forEach(t => {
      html += `<div class="dq-check">
        <div class="dq-check-header">
          <span class="dq-check-title">tds_db.public.${t}</span>
          <span class="badge b-gray">${g.group}</span>
          <span class="badge b-gray">Planned</span>
        </div>
        <div class="dq-placeholder">Check not yet implemented — queued for future enhancement.</div>
      </div>`;
    });
  });

  const cacheNote = payload.cached ? ' · cached' : ' · live';
  el.innerHTML = `<div class="chips">
    <span class="badge b-gray">L${days}D · ${analyticalName}${cacheNote}</span>
  </div>` + html;

  const pfBar = document.getElementById('dq-platform-filter');
  if (pfBar) pfBar.style.display = 'flex';
  document.querySelectorAll('.pf-btn').forEach(b => b.classList.remove('active'));
  const allBtn = document.querySelector('.pf-btn[onclick*="all"]');
  if (allBtn) allBtn.classList.add('active');
}

// ════════════════════════════════════════════════
// AUTH — show who's logged in, provide a logout link
// ════════════════════════════════════════════════
async function loadUserBadge() {
  try {
    const user = await apiGet('/auth/me');
    document.getElementById('user-badge').innerHTML =
      `${user.email} · <a href="/auth/logout">Sign out</a>`;
  } catch (e) {
    // Not logged in — the "/" route already redirects to /auth/login in this
    // case, so this is just a defensive no-op.
  }
}

// ════════════════════════════════════════════════
// INIT
// ════════════════════════════════════════════════
loadUserBadge();
loadAll();
