<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title>Vault explorer v4</title>
  <script>
(function () {
  var k = "vault-explorer-theme";
  var t = localStorage.getItem(k);
  if (t !== "light" && t !== "dark") {
    t = window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  document.documentElement.setAttribute("data-theme", t);
})();
  </script>
  <script>
{{VENDOR_JS}}
  </script>
  <style>
    :root,
    [data-theme="dark"] {
      --bg: #1e1e1e;
      --fg: #d4d4d4;
      --muted: #888;
      --card: #2a2a2a;
      --card-2: #333;
      --border: #3a3a3a;
      --accent: #4a9eff;
      --warn: #ff8a4a;
      --bad: #ff5a5a;
      --good: #4ae87a;
      --surface-inset: #111;
      --code-bg: #333;
      --link-hover: #79b8ff;
      --insight-bg: rgba(74, 158, 255, 0.07);
      --overlay: rgba(0, 0, 0, 0.2);
      --overlay-strong: rgba(0, 0, 0, 0.3);
      --shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
    }
    [data-theme="light"] {
      --bg: #f4f4f5;
      --fg: #18181b;
      --muted: #6b7280;
      --card: #ffffff;
      --card-2: #eef0f2;
      --border: #d8dce0;
      --accent: #2563eb;
      --warn: #ea580c;
      --bad: #dc2626;
      --good: #16a34a;
      --surface-inset: #eef0f2;
      --code-bg: #e8eaed;
      --link-hover: #1d4ed8;
      --insight-bg: rgba(37, 99, 235, 0.08);
      --overlay: rgba(0, 0, 0, 0.06);
      --overlay-strong: rgba(0, 0, 0, 0.08);
      --shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--fg);
      font-size: 14px;
    }
    header { padding: 18px 28px 16px; border-bottom: 1px solid var(--border); }
    .header-inner {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
    }
    h1 { margin: 0 0 4px; font-size: 22px; }
    .subtitle { color: var(--muted); font-size: 13px; }
    .theme-toggle {
      flex-shrink: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      width: 36px;
      height: 36px;
      margin-top: 2px;
      padding: 0;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--card);
      color: var(--fg);
      font-size: 18px;
      line-height: 1;
      cursor: pointer;
      transition: background 0.15s, border-color 0.15s, color 0.15s;
    }
    .theme-toggle:hover {
      border-color: var(--accent);
      color: var(--accent);
    }
    [data-theme="dark"] .theme-icon-light { display: inline; }
    [data-theme="dark"] .theme-icon-dark { display: none; }
    [data-theme="light"] .theme-icon-light { display: none; }
    [data-theme="light"] .theme-icon-dark { display: inline; }

    .one-line {
      padding: 10px 28px;
      background: var(--card);
      border-bottom: 1px solid var(--border);
      font-size: 13px;
    }
    .one-line .label { color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; font-size: 11px; margin-right: 10px; }
    .one-line .added { color: var(--good); }
    .one-line .warn { color: var(--warn); }
    .one-line .dim { color: var(--muted); font-style: italic; }
    .one-line .dot { color: var(--muted); margin: 0 8px; }

    nav.tabs {
      display: flex; gap: 4px; padding: 0 28px;
      background: var(--bg);
      border-bottom: 1px solid var(--border);
      position: sticky; top: 0; z-index: 10;
    }
    .tab-btn {
      background: transparent; color: var(--muted);
      border: none; padding: 12px 16px; cursor: pointer;
      font-size: 13px; border-bottom: 2px solid transparent;
      transition: color 0.15s, border-color 0.15s;
    }
    .tab-btn:hover { color: var(--fg); }
    .tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }

    main { padding: 24px 28px 32px; }
    .panel { display: none; }
    .panel.active { display: block; }

    h2.section {
      margin: 28px 0 14px;
      font-size: 13px; font-weight: 600;
      text-transform: uppercase; letter-spacing: 1px;
      color: var(--muted);
      padding-bottom: 6px;
      border-bottom: 1px solid var(--border);
    }
    h2.section:first-child { margin-top: 0; }

    /* Info icon + tooltip */
    .info-icon {
      display: inline-block;
      margin-left: 6px;
      width: 14px; height: 14px;
      border-radius: 50%;
      background: var(--border);
      color: var(--fg);
      text-align: center;
      font-size: 10px;
      font-weight: 600;
      line-height: 14px;
      cursor: help;
      position: relative;
      vertical-align: middle;
      text-transform: none;       /* override parent uppercase */
      letter-spacing: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .info-icon:hover { background: var(--accent); color: var(--bg); }
    .info-icon:hover::after {
      content: attr(data-tip);
      position: absolute;
      bottom: calc(100% + 8px);
      left: 50%;
      transform: translateX(-50%);
      background: var(--card-2);
      color: var(--fg);
      padding: 10px 12px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 400;
      font-style: normal;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      text-transform: none;       /* critical: don't inherit uppercase */
      letter-spacing: 0;
      white-space: pre-wrap;
      width: 280px;
      z-index: 1000;
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
      line-height: 1.5;
      text-align: left;
    }
    .info-icon:hover::before {
      content: "";
      position: absolute;
      bottom: 100%;
      left: 50%;
      transform: translateX(-50%);
      border: 5px solid transparent;
      border-top-color: var(--card-2);
      z-index: 1001;
    }

    .hero-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 14px;
    }
    .hero {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 20px 22px;
    }
    .hero .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
    .hero .value { font-size: 38px; font-weight: 600; margin: 6px 0 4px; line-height: 1; }
    .hero .delta { font-size: 12px; color: var(--muted); }

    .metrics-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 12px;
    }
    .metric {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 14px;
    }
    .metric .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
    .metric .value { font-size: 22px; font-weight: 500; margin: 4px 0 2px; }
    .metric .value.text { font-size: 15px; line-height: 1.3; }
    .metric .delta { font-size: 11px; color: var(--muted); }
    .delta.up { color: var(--good); }
    .delta.down { color: var(--warn); }

    .charts-2 {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
      gap: 16px;
    }
    .charts-4 {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 14px;
    }
    .chart-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px 16px;
    }
    .chart-card h3 {
      margin: 0 0 10px;
      font-size: 13px;
      font-weight: 500;
    }
    .chart-card .hint { color: var(--muted); font-size: 11px; margin-top: 6px; }
    canvas { max-height: 220px; }
    canvas.tall { max-height: 280px; }

    /* History tab */
    .pulse { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 24px; }
    .pulse-col { padding: 14px 18px; background: var(--card); border-radius: 8px; border-left: 3px solid var(--accent); }
    .pulse-col.empty { border-left-color: var(--muted); }
    .pulse-col.warn { border-left-color: var(--warn); }
    .pulse-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
    .pulse-title { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted); }
    .pulse-ts { color: var(--muted); font-size: 11px; }
    .pulse-empty-msg { color: var(--muted); font-style: italic; font-size: 13px; }
    .pulse-items { list-style: none; padding: 0; margin: 0; }
    .pulse-items li { padding: 2px 0; font-size: 13px; }
    .pulse-items li::before { content: ""; display: inline-block; width: 14px; }
    .pulse-items li.added::before { content: "+"; color: var(--good); font-weight: 600; }
    .pulse-items li.removed::before { content: "−"; color: var(--bad); font-weight: 600; }
    .pulse-items li.warn::before { content: "⚠"; color: var(--warn); }
    .pulse-items li.metric::before { content: "↑"; color: var(--good); }
    .pulse-items li.metric-down::before { content: "↓"; color: var(--warn); }
    .pulse-items li .badge { color: var(--muted); font-size: 11px; margin-left: 6px; }

    .diff-history { list-style: none; padding: 0; margin: 0; }
    .diff-history > li { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 14px 18px; margin-bottom: 10px; }
    .diff-history .when { font-size: 12px; color: var(--muted); margin-bottom: 4px; }
    .diff-history .what { font-weight: 500; margin-bottom: 6px; }
    .diff-history .items { list-style: none; padding: 0; margin: 0; }
    .diff-history .items li { font-size: 13px; padding: 1px 0; }
    .diff-history .items li::before { content: "+"; color: var(--good); display: inline-block; width: 14px; }
    .diff-history .items li.warn::before { content: "⚠"; color: var(--warn); }
    .diff-history .items li.removed::before { content: "−"; color: var(--bad); }

    /* Maps */
    .map-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 16px;
    }
    @media (max-width: 1100px) {
      .map-grid { grid-template-columns: 1fr; }
    }
    .map-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px 16px;
      display: flex;
      flex-direction: column;
      position: relative;     /* anchor for tooltip overflow */
    }
    .map-card h3 { margin: 0 0 8px; font-size: 13px; font-weight: 500; }
    .map-iframe {
      width: 100%;
      height: 380px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--surface-inset);
    }
    .map-canvas-wrapper {
      width: 100%;
      height: 380px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--surface-inset);
      padding: 10px;
      position: relative;
    }
    .map-canvas-wrapper canvas { max-height: 360px !important; }
    .map-card.wide { grid-column: 1 / -1; }
    .wordcloud-wrapper {
      width: 100%;
      min-height: 380px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--surface-inset);
      padding: 24px 28px;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: center;
      gap: 4px 14px;
      line-height: 1.1;
    }
    .wordcloud-wrapper span {
      display: inline-block;
      font-weight: 500;
      white-space: nowrap;
      transition: transform 0.15s, opacity 0.15s;
      opacity: 0.92;
    }
    .wordcloud-wrapper span:hover {
      transform: scale(1.08);
      opacity: 1;
      cursor: default;
    }

    .map-insight {
      background: var(--insight-bg);
      border-left: 3px solid var(--accent);
      padding: 10px 14px;
      margin-top: 12px;
      border-radius: 0 6px 6px 0;
      font-size: 13px;
      line-height: 1.5;
    }
    .map-insight .badge {
      display: inline-block;
      background: var(--accent);
      color: var(--bg);
      padding: 1px 7px;
      border-radius: 4px;
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-right: 8px;
      vertical-align: middle;
    }
    .map-insight ul { margin: 6px 0 0; padding-left: 18px; }
    .map-insight li { margin: 2px 0; }
    /* Glossary */
    details.glossary {
      margin-top: 32px;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 18px;
    }
    details.glossary summary {
      cursor: pointer;
      font-size: 13px;
      font-weight: 500;
      padding: 6px 0;
    }
    details.glossary dl { margin: 12px 0 4px; }
    details.glossary dt { font-weight: 600; color: var(--accent); font-size: 13px; margin-top: 14px; }
    details.glossary dd { margin: 4px 0 0 0; color: var(--fg); font-size: 13px; line-height: 1.55; }
    details.glossary .formula {
      display: block;
      background: var(--overlay-strong);
      padding: 8px 12px;
      margin: 6px 0;
      border-radius: 4px;
      font-family: "SF Mono", Menlo, monospace;
      font-size: 12px;
      color: var(--accent);
    }
    details.glossary .example {
      color: var(--muted);
      font-style: italic;
      font-size: 12px;
      margin-top: 4px;
    }

    code { background: var(--code-bg); padding: 1px 5px; border-radius: 3px; font-size: 12px; }
    .note { color: var(--muted); font-size: 12px; padding: 10px 14px; background: var(--card); border-left: 3px solid var(--accent); border-radius: 4px; margin-bottom: 18px; }

    /* Obsidian wikilink */
    .obsidian-link {
      color: var(--accent);
      text-decoration: none;
      border-bottom: 1px dotted var(--accent);
      transition: color 0.15s, border-color 0.15s;
    }
    .obsidian-link:hover { color: var(--link-hover); border-bottom-color: var(--link-hover); }

    /* Health score block */
    .health-block {
      display: grid;
      grid-template-columns: 200px 1fr;
      gap: 20px;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px 22px;
      margin-bottom: 24px;
      align-items: center;
    }
    .health-score {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 4px;
      padding: 8px;
      border-radius: 8px;
      background: var(--overlay);
    }
    .health-score .value {
      font-size: 56px;
      font-weight: 700;
      line-height: 1;
    }
    .health-score .grade {
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 1px;
      font-weight: 600;
      color: var(--muted);
      margin-top: 4px;
    }
    .health-score.good .value { color: var(--good); }
    .health-score.warn .value { color: var(--warn); }
    .health-score.bad .value { color: var(--bad); }
    .health-breakdown {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .health-row {
      display: grid;
      grid-template-columns: 130px 1fr 60px;
      gap: 10px;
      align-items: center;
      font-size: 12px;
    }
    .health-row .label { color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; font-size: 11px; }
    .health-row .bar {
      height: 8px;
      background: var(--overlay-strong);
      border-radius: 4px;
      overflow: hidden;
      position: relative;
    }
    .health-row .bar-fill {
      height: 100%;
      border-radius: 4px;
      transition: width 0.3s;
    }
    .health-row .bar-fill.good { background: var(--good); }
    .health-row .bar-fill.warn { background: var(--warn); }
    .health-row .bar-fill.bad { background: var(--bad); }
    .health-row .num { text-align: right; color: var(--fg); font-weight: 500; font-variant-numeric: tabular-nums; }

    /* Range selector */
    .range-selector {
      display: inline-flex;
      gap: 2px;
      background: var(--card-2);
      padding: 3px;
      border-radius: 6px;
      margin-bottom: 18px;
    }
    .range-btn {
      background: transparent;
      color: var(--muted);
      border: none;
      padding: 6px 14px;
      font-size: 12px;
      border-radius: 4px;
      cursor: pointer;
      transition: background 0.15s, color 0.15s;
    }
    .range-btn:hover { color: var(--fg); }
    .range-btn.active {
      background: var(--accent);
      color: var(--bg);
    }
  </style>
</head>
<body>

<header>
  <div class="header-inner">
    <div>
      <h1>Vault explorer</h1>
      <div class="subtitle" id="header-subtitle"></div>
    </div>
    <button type="button" id="theme-toggle" class="theme-toggle" aria-label="Переключить тему" title="Переключить тему">
      <span class="theme-icon theme-icon-light" aria-hidden="true">☀</span>
      <span class="theme-icon theme-icon-dark" aria-hidden="true">☾</span>
    </button>
  </div>
</header>

<script id="dashboard-data" type="application/json">{{DATA_JSON}}</script>

<div class="one-line" id="one-line"></div>

<nav class="tabs">
  <button class="tab-btn active" data-panel="overview">Обзор</button>
  <button class="tab-btn" data-panel="timeseries">Динамика</button>
  <button class="tab-btn" data-panel="history">История изменений</button>
  <button class="tab-btn" data-panel="maps">Карты</button>
</nav>

<main>

  <!-- ═════════ OVERVIEW ═════════ -->
  <section class="panel active" id="overview">

    <h2 class="section">Здоровье vault <span class="info-icon" data-tip="Агрегированная оценка vault'а 0-100. Считается из 5 компонент: orphans (доля сирот), density (плотность связей), Q (Louvain modularity), ready (доля зрелых страниц), domain coverage. Каждая компонента нормируется на 0-100 и взвешивается. Считается без LLM, формула детерминированная.">i</span></h2>
    <div class="health-block">
      <div class="health-score" id="health-score">
        <div class="value">—</div>
        <div class="grade">vault health</div>
      </div>
      <div class="health-breakdown" id="health-breakdown"></div>
    </div>

    <h2 class="section">Главное</h2>
    <div class="hero-grid">
      <div class="hero">
        <div class="label">Страниц <span class="info-icon" data-tip="Все wiki-страницы во vault: idea + entity + domain + question + mind. Не включает meta-страницы (index, log, cache) и raw-источники.">i</span></div>
        <div class="value" id="hero-pages-value">—</div>
        <div class="delta" id="hero-pages-delta">—</div>
      </div>
      <div class="hero">
        <div class="label">Wikilinks <span class="info-icon" data-tip="Валидные двойные-скобочные ссылки между страницами ([[X]]). Не считает red-links. Чем больше — тем плотнее vault.">i</span></div>
        <div class="value" id="hero-wikilinks-value">—</div>
        <div class="delta" id="hero-wikilinks-delta">—</div>
      </div>
      <div class="hero">
        <div class="label">Доменов <span class="info-icon" data-tip="Навигационные хабы (MOC) в wiki/domains/. Создаются при пороге N=10 страниц с одним тегом области.">i</span></div>
        <div class="value" id="hero-domains-value">—</div>
        <div class="delta" id="hero-domains-delta">—</div>
      </div>
      <div class="hero">
        <div class="label">Сирот <span class="info-icon" data-tip="Страницы, на которые никто не ссылается (нет входящих wikilinks). Высокое число = vault несвязный. Норма ≤ 2 при 60 страниц.">i</span></div>
        <div class="value" id="hero-orphans-value">—</div>
        <div class="delta" id="hero-orphans-delta">—</div>
      </div>
    </div>

    <h2 class="section">Распределения</h2>
    <div class="charts-4">
      <div class="chart-card">
        <h3>По типам страниц <span class="info-icon" data-tip="Какая доля контента — концепции (idea), персоны/статьи/модели (entity), сохранённые ответы (question), авторские мысли (mind), навигационные хабы (domain).">i</span></h3>
        <canvas id="dist-types"></canvas>
      </div>
      <div class="chart-card">
        <h3>По доменам <span class="info-icon" data-tip="Распределение страниц по primary domain (первый домен в frontmatter — самый специфичный).">i</span></h3>
        <canvas id="dist-domains"></canvas>
      </div>
      <div class="chart-card">
        <h3>По provenance <span class="info-icon" data-tip="Откуда контент: ingest = реальный источник из raw/, study = training knowledge модели, brainstorm = реплики пользователя, save = из чата, manual = ручная правка.">i</span></h3>
        <canvas id="dist-provenance"></canvas>
      </div>
      <div class="chart-card">
        <h3>По статусам <span class="info-icon" data-tip="Стадия проработки страницы: evaluation = непроверенная, in-progress = в работе, ready = подкреплена связями и источниками.">i</span></h3>
        <canvas id="dist-status"></canvas>
      </div>
    </div>

    <h2 class="section">Связность и топология</h2>
    <div class="metrics-grid">
      <div class="metric">
        <div class="label">Самая связанная <span class="info-icon" data-tip="Страница с максимальным числом входящих wikilinks. Это якорная концепция — на неё ссылается большая часть vault.">i</span></div>
        <div class="value text" id="m-most-connected-value">—</div>
        <div class="delta" id="m-most-connected-delta">—</div>
      </div>
      <div class="metric">
        <div class="label">Топ-мост <span class="info-icon" data-tip="Страница с максимальным participation coefficient P. Её wikilinks ведут в разные сообщества Louvain — она держит междисциплинарные связи. Улучшение такой страницы бьёт сразу по нескольким темам.">i</span></div>
        <div class="value text" id="m-top-bridge-value">—</div>
        <div class="delta" id="m-top-bridge-delta">—</div>
      </div>
      <div class="metric">
        <div class="label">Плотность связей <span class="info-icon" data-tip="Отношение wikilinks / pages. Чем выше, тем плотнее vault цитирует сам себя. Молодая база: 3-5; зрелая: 6-10; пере-связанная: >15.">i</span></div>
        <div class="value" id="m-density-value">—</div>
        <div class="delta" id="m-density-delta">—</div>
      </div>
      <div class="metric">
        <div class="label">Q (Louvain) <span class="info-icon" data-tip="Модулярность разбиения на сообщества Louvain. 0.3-0.7 — норма для содержательных графов. Q < 0.3 — структура слабая или связи распределены равномерно.">i</span></div>
        <div class="value" id="m-Q-value">—</div>
        <div class="delta" id="m-Q-delta">—</div>
      </div>
      <div class="metric">
        <div class="label">Сообществ ≥ 3 <span class="info-icon" data-tip="Число сообществ Louvain размера ≥ 3 страниц. Сколько у вас явных «кустов» связанных тем. Сообщества меньше 3 — обычно шум.">i</span></div>
        <div class="value" id="m-communities-value">—</div>
        <div class="delta" id="m-communities-delta">—</div>
      </div>
      <div class="metric">
        <div class="label">Узлов-мостов <span class="info-icon" data-tip="Страниц с participation P > 0.3 — структурно важных, держащих связи между несколькими сообществами.">i</span></div>
        <div class="value" id="m-bridges-value">—</div>
        <div class="delta" id="m-bridges-delta">—</div>
      </div>
    </div>

    <h2 class="section">Семантика</h2>
    <div class="metrics-grid">
      <div class="metric">
        <div class="label">Медиана близости <span class="info-icon" data-tip="Медиана попарных косинусных расстояний между эмбеддингами всех страниц. Растёт = vault становится семантически когерентнее. Падает = добавились разнородные страницы.">i</span></div>
        <div class="value" id="m-sim-p50-value">—</div>
        <div class="delta" id="m-sim-p50-delta">—</div>
      </div>
      <div class="metric">
        <div class="label">75-й перцентиль <span class="info-icon" data-tip="Близость, которую превышают 25% пар. Показывает плотность связности «лучшей половины» страниц.">i</span></div>
        <div class="value" id="m-sim-p75-value">—</div>
        <div class="delta" id="m-sim-p75-delta">—</div>
      </div>
      <div class="metric">
        <div class="label">95-й перцентиль <span class="info-icon" data-tip="Близость, которую превышают 5% самых тесных пар. Если падает — самые близкие пары стали менее похожи, либо появились дубли.">i</span></div>
        <div class="value" id="m-sim-p95-value">—</div>
        <div class="delta" id="m-sim-p95-delta">—</div>
      </div>
      <div class="metric">
        <div class="label">Самая близкая пара <span class="info-icon" data-tip="Две страницы с максимальной семантической близостью. Если > 0.9 — возможные дубли, кандидаты на merge.">i</span></div>
        <div class="value text" id="m-closest-pair-value">—</div>
        <div class="delta" id="m-closest-pair-delta">—</div>
      </div>
      <div class="metric">
        <div class="label">Самая изолированная <span class="info-icon" data-tip="Страница, чья максимальная близость с любой другой меньше всех. Семантически уникальная — либо новая фронтирная тема, либо сирота вне домена.">i</span></div>
        <div class="value text" id="m-isolated-value">—</div>
        <div class="delta" id="m-isolated-delta">—</div>
      </div>
    </div>

    <details class="glossary">
      <summary>📖 Глоссарий метрик с формулами</summary>

      <dl>
        <dt>Wikilinks</dt>
        <dd>
          Двойные-скобочные ссылки между страницами вики: <code>[[X]]</code>. Считаются только валидные — target существует во vault. Frontmatter-ссылки (<code>related</code>, <code>domain</code>, <code>sources</code>) считаются. Red-links (на несуществующие страницы) — нет.
          <div class="example">Пример: 390 wikilinks на 62 страницы = плотность 6.3 ссылки на страницу.</div>
        </dd>

        <dt>Плотность связей</dt>
        <dd>
          Простая мера связности — отношение wikilinks к страницам.
          <span class="formula">density = wikilinks / pages</span>
          Молодая база: 3-5. Зрелая: 6-10. Пере-связанная (тревожный сигнал, возможно поверхностные ссылки): >15.
        </dd>

        <dt>Сироты (orphans)</dt>
        <dd>
          Страницы без входящих wikilinks. Не значит «удалить» — может быть свежесозданная страница, на которую ещё не успели сослаться. Но рост числа сирот = vault теряет связность. Норма ≤ 2 при ~60 страниц.
        </dd>

        <dt>Косинусная близость</dt>
        <dd>
          Косинус угла между эмбеддингами двух страниц.
          <span class="formula">cos(A, B) = (A · B) / (‖A‖ · ‖B‖)</span>
          От 0 (ортогональны) до 1 (идентичны). Считается на всех C(N,2) парах wiki-страниц. По выборке считаются перцентили p50/p75/p95.
        </dd>

        <dt>Avg internal cosine (когерентность домена)</dt>
        <dd>
          Средняя попарная близость страниц одного домена.
          <span class="formula">coherence(D) = (2 / |D|(|D|−1)) · Σ cos(a, b) для a, b ∈ D</span>
          Высокое значение (>0.6) — плотный когерентный кластер. Низкое (<0.5) — домен размазан по нескольким темам, возможно стоит разбить на под-домены.
        </dd>

        <dt>Сообщества (Louvain communities)</dt>
        <dd>
          Группы страниц с плотными связями внутри и слабыми наружу. Алгоритм Louvain находит их по топологии wikilinks (не по семантике). Сравнение с доменами — диагностика структуры vault: если сообщества совпадают с доменами, vault структурно здоров.
        </dd>

        <dt>Модулярность Q (Louvain)</dt>
        <dd>
          Численная оценка качества разбиения графа на сообщества.
          <span class="formula">Q = (1 / 2m) · Σ [A_ij − k_i·k_j / 2m] · δ(c_i, c_j)</span>
          где <code>m</code> — число рёбер, <code>A_ij</code> — adjacency matrix, <code>k_i</code> — степень узла i, <code>δ</code> — индикатор «один кластер». Стремится к 1 когда внутри-кластерных рёбер много, между-кластерных — мало. Содержательный диапазон 0.3-0.7.
        </dd>

        <dt>Participation coefficient P</dt>
        <dd>
          Для каждой страницы: насколько её wikilinks распределены между сообществами.
          <span class="formula">P_i = 1 − Σ_c (k_i^c / k_i)²</span>
          где <code>k_i</code> — общая степень узла, <code>k_i^c</code> — число рёбер в сообщество c. P = 0: все ссылки в одном сообществе. P → 1: равномерно по нескольким. Узлы-мосты определяются как P > 0.3.
          <div class="example">Пример: [[Machine Learning]] имеет 8 wikilinks: 3 в сообщество A, 3 в B, 2 в C → P ≈ 0.66.</div>
        </dd>

        <dt>Provenance</dt>
        <dd>
          Маркер происхождения страницы во frontmatter:
          <ul style="margin: 6px 0; padding-left: 20px;">
            <li><code>ingest</code> — синтезировано из реального источника в <code>raw/</code></li>
            <li><code>study</code> — из training knowledge модели (учебная сессия)</li>
            <li><code>brainstorm</code> — из реплик пользователя (mind-сессия)</li>
            <li><code>save</code> — зафайлировано из чата</li>
            <li>пусто — ручная правка пользователя в Obsidian</li>
          </ul>
          Используется для оценки «насколько vault опирается на реальные источники vs знания модели».
        </dd>

        <dt>Status</dt>
        <dd>
          Стадия проработки страницы во frontmatter:
          <ul style="margin: 6px 0; padding-left: 20px;">
            <li><code>evaluation</code> — непроверенная (типично для study-страниц)</li>
            <li><code>in-progress</code> — суть описана, не хватает связей или примеров</li>
            <li><code>ready</code> — подкреплена связями и источниками</li>
          </ul>
        </dd>
      </dl>
    </details>
  </section>

  <!-- ═════════ TIME-SERIES ═════════ -->
  <section class="panel" id="timeseries">

    <div class="range-selector" id="range-selector">
      <button class="range-btn" data-range="7">7 дней</button>
      <button class="range-btn" data-range="30">30 дней</button>
      <button class="range-btn" data-range="90">90 дней</button>
      <button class="range-btn active" data-range="all">Всё время</button>
    </div>

    <h2 class="section">Масштаб</h2>
    <div class="charts-2">
      <div class="chart-card">
        <h3>Страниц во времени (по типам) <span class="info-icon" data-tip="Stacked area: domain, entity, idea, mind, unassigned. Видно, какой тип растёт быстрее. Если unassigned (без домена) накапливается — нужно классифицировать страницы.">i</span></h3>
        <canvas id="ts-pages-stack" class="tall"></canvas>
      </div>
      <div class="chart-card">
        <h3>Размер доменов <span class="info-icon" data-tip="Сколько страниц в каждом домене. Расхождение скоростей роста показывает, какие области интенсивно развиваются.">i</span></h3>
        <canvas id="ts-dom-sizes" class="tall"></canvas>
      </div>
    </div>

    <h2 class="section">Связность</h2>
    <div class="charts-2">
      <div class="chart-card">
        <h3>Wikilinks и сироты <span class="info-icon" data-tip="Две оси: wikilinks (растущая связность) и сироты (изолированные страницы). Рост сирот — сигнал для /lint.">i</span></h3>
        <canvas id="ts-connectivity" class="tall"></canvas>
      </div>
      <div class="chart-card">
        <h3>Плотность связей <span class="info-icon" data-tip="wikilinks / pages. Если растёт быстрее, чем число страниц — vault уплотняется. Если падает — новые страницы недостаточно связаны.">i</span></h3>
        <canvas id="ts-density" class="tall"></canvas>
      </div>
    </div>

    <h2 class="section">Топология</h2>
    <div class="charts-2">
      <div class="chart-card">
        <h3>Модулярность Q и число сообществ <span class="info-icon" data-tip="Q (Louvain) — глобальная оценка чёткости разбиения на сообщества. Растёт = структура устаканивается. Бары — число сообществ ≥ 3 страниц.">i</span></h3>
        <canvas id="ts-topology" class="tall"></canvas>
      </div>
      <div class="chart-card">
        <h3>Узлов-мостов <span class="info-icon" data-tip="Страниц с participation coefficient P > 0.3, держащих междисциплинарные связи. Стабильно ≥ 3 — здоровая структура. Падение до 0-1 — фрагментация.">i</span></h3>
        <canvas id="ts-bridges" class="tall"></canvas>
      </div>
    </div>

    <h2 class="section">Семантика</h2>
    <div class="charts-2">
      <div class="chart-card">
        <h3>Распределение попарной близости <span class="info-icon" data-tip="Три перцентиля косинусной близости. p50 (медиана) — типичная пара. p95 — самые близкие пары (рост близок к 1 = появились дубли). Падение медианы при добавлении страниц — нормально.">i</span></h3>
        <canvas id="ts-similarity" class="tall"></canvas>
      </div>
      <div class="chart-card">
        <h3>Когерентность доменов <span class="info-icon" data-tip="Avg internal cosine для каждого домена. >0.6 — плотный кластер. Падение для одного домена — он размывается, возможно стоит разбить.">i</span></h3>
        <canvas id="ts-dom-cohesion" class="tall"></canvas>
      </div>
    </div>
  </section>

  <!-- ═════════ HISTORY ═════════ -->
  <section class="panel" id="history">

    <h2 class="section">Сводка</h2>
    <div class="pulse">
      <div class="pulse-col" id="pulse-last-turn"></div>
      <div class="pulse-col" id="pulse-24h"></div>
    </div>

    <h2 class="section">Хронология</h2>
    <div class="note">Каждая запись — один turn с непустой delta. Источник: <code>wiki/meta/snapshots/history.jsonl</code>.</div>
    <ul class="diff-history" id="diff-history-list"></ul>
  </section>

  <!-- ═════════ MAPS ═════════ -->
  <section class="panel" id="maps">
    <div class="note">4 разных визуализации структуры vault. Карты обновляются раз в сутки или явной командой <code>/snapshot</code>. Под каждой картой — LLM-инсайт для конкретного состояния базы и краткое пояснение что показано.</div>

    <div class="map-grid">

      <!-- 1. UMAP × domains -->
      <div class="map-card">
        <h3>UMAP × домены <span class="info-icon" data-tip="Близость на экране = семантическая близость эмбеддингов (UMAP-проекция 4096-d → 2D). Цвет = primary domain. Линии = wikilinks. Если страница оторвалась от своего цветового кластера — её эмбеддинг ушёл в чужую семантическую область.">i</span></h3>
        <iframe class="map-iframe" id="iframe-umap"></iframe>
        <div class="map-insight" id="insight-umap"></div>
      </div>

      <!-- 2. Force-graph × communities -->
      <div class="map-card">
        <h3>Force-graph × сообщества Louvain <span class="info-icon" data-tip="Force-directed (fcose layout): близость на экране = плотность wikilink-связей. Цвет = primary domain. Размер узла = √(число связей). Облака = сообщества Louvain. Золотая обводка = узлы-мосты (participation P > 0.3). Hover на узле подсвечивает 1-hop соседей.">i</span></h3>
        <iframe class="map-iframe" id="iframe-graph"></iframe>
        <div class="map-insight" id="insight-graph"></div>
      </div>

      <!-- 3. Sankey -->
      <div class="map-card">
        <h3>Sankey: домены ↔ сообщества <span class="info-icon" data-tip="Толщина потока = число страниц домена, попавших в данное сообщество Louvain. Идеальный случай — каждый домен течёт в ровно одно сообщество (domain ≈ topology). Расщеплённые потоки = междисциплинарные домены или ошибки в domain-классификации.">i</span></h3>
        <div class="map-canvas-wrapper"><canvas id="map-sankey"></canvas></div>
        <div class="map-insight" id="insight-sankey"></div>
      </div>

      <!-- 4. Treemap -->
      <div class="map-card">
        <h3>Treemap: домен → тип страницы <span class="info-icon" data-tip="Площадь прямоугольника = число страниц. Группировка: сначала по primary domain, внутри — по типу (idea / entity / mind / domain / unassigned). Видно структурный профиль каждой области: какой домен из чего состоит.">i</span></h3>
        <div class="map-canvas-wrapper"><canvas id="map-treemap"></canvas></div>
        <div class="map-insight" id="insight-treemap"></div>
      </div>

      <!-- 5. Word cloud -->
      <div class="map-card wide">
        <h3>Облако слов <span class="info-icon" data-tip="Размер слова = частота встречаемости в текстах всех wiki-страниц. Парсятся idea / entity / question / mind / domain (без frontmatter, code-blocks, wikilinks). Отфильтрованы stop-words (русские + английские) и короткие токены (< 3 символов).">i</span></h3>
        <div class="wordcloud-wrapper" id="map-wordcloud"></div>
        <div class="map-insight" id="insight-wordcloud"></div>
      </div>

    </div>
  </section>

</main>

<script>
// ════════════════════════════════════════════════════════════════════
// Data — loaded from inline JSON block written by update_dashboard.py
// ════════════════════════════════════════════════════════════════════
const _dashboardData = JSON.parse(document.getElementById("dashboard-data").textContent);

const history = _dashboardData.history || [];
const NOW = _dashboardData.now ? new Date(_dashboardData.now) : new Date();
const provenance = _dashboardData.provenance || {};
const statuses = _dashboardData.statuses || {};
const sankeyData = _dashboardData.sankey || [];
const treemapData = _dashboardData.treemap || [];
const wordcloudData = _dashboardData.wordcloud || [];
const insights = _dashboardData.insights || {};

// Setup dynamic iframe sources for dated maps
if (_dashboardData.umap_iframe) {
  document.getElementById("iframe-umap").src = _dashboardData.umap_iframe;
}
if (_dashboardData.graph_iframe) {
  document.getElementById("iframe-graph").src = _dashboardData.graph_iframe;
}

// Inject insight blocks (HTML strings written by knowledge_map.py for heavy
// renders; empty placeholders for light updates between heavy refreshes).
function setInsight(elementId, html) {
  const el = document.getElementById(elementId);
  if (!el) return;
  if (html && html.trim()) {
    el.innerHTML = `<span class="badge">💡 Инсайт</span>${html}`;
  } else {
    el.innerHTML = `<span class="badge" style="background: var(--muted);">💡 Инсайт</span><span style="color: var(--muted); font-style: italic;">Будет сгенерирован при следующем /snapshot.</span>`;
  }
}
setInsight("insight-umap", insights.umap);
setInsight("insight-graph", insights.graph);
setInsight("insight-sankey", insights.sankey);
setInsight("insight-treemap", insights.treemap);
setInsight("insight-wordcloud", insights.wordcloud);

// ════════════════════════════════════════════════════════════════════
// Header subtitle + Overview cards from data
// ════════════════════════════════════════════════════════════════════
function fillCard(idValue, idDelta, value, delta, deltaClass) {
  const v = document.getElementById(idValue);
  const d = document.getElementById(idDelta);
  if (v) v.innerHTML = value;
  if (d) {
    d.innerHTML = delta;
    d.className = "delta" + (deltaClass ? " " + deltaClass : "");
  }
}

// Header subtitle
const sub = document.getElementById("header-subtitle");
if (sub) {
  const lastTs = history.length ? history[history.length - 1].ts.replace("T", " ") : "—";
  const total = history.length ? history[history.length - 1].pages : 0;
  const turnsToday = history.filter(h =>
    new Date(h.ts).toDateString() === NOW.toDateString()
  ).length;
  sub.textContent = `обновлено ${lastTs} · ${total} страниц · ${turnsToday} turn'ов сегодня`;
}

// Overview cards from data.cards
const cards = _dashboardData.cards || {};
function renderCards() {
  const c = cards;
  if (c.pages)         fillCard("hero-pages-value",      "hero-pages-delta",      c.pages.value,      c.pages.delta,      c.pages.delta_class);
  if (c.wikilinks)     fillCard("hero-wikilinks-value",  "hero-wikilinks-delta",  c.wikilinks.value,  c.wikilinks.delta,  c.wikilinks.delta_class);
  if (c.domains)       fillCard("hero-domains-value",    "hero-domains-delta",    c.domains.value,    c.domains.delta,    c.domains.delta_class);
  if (c.orphans)       fillCard("hero-orphans-value",    "hero-orphans-delta",    c.orphans.value,    c.orphans.delta,    c.orphans.delta_class);

  if (c.most_connected) fillCard("m-most-connected-value", "m-most-connected-delta", c.most_connected.value, c.most_connected.delta);
  if (c.top_bridge)    fillCard("m-top-bridge-value",    "m-top-bridge-delta",    c.top_bridge.value,    c.top_bridge.delta);
  if (c.density)       fillCard("m-density-value",       "m-density-delta",       c.density.value,       c.density.delta, c.density.delta_class);
  if (c.Q)             fillCard("m-Q-value",             "m-Q-delta",             c.Q.value,             c.Q.delta,       c.Q.delta_class);
  if (c.communities)   fillCard("m-communities-value",   "m-communities-delta",   c.communities.value,   c.communities.delta);
  if (c.bridges)       fillCard("m-bridges-value",       "m-bridges-delta",       c.bridges.value,       c.bridges.delta);

  if (c.sim_p50)       fillCard("m-sim-p50-value",       "m-sim-p50-delta",       c.sim_p50.value,       c.sim_p50.delta, c.sim_p50.delta_class);
  if (c.sim_p75)       fillCard("m-sim-p75-value",       "m-sim-p75-delta",       c.sim_p75.value,       c.sim_p75.delta, c.sim_p75.delta_class);
  if (c.sim_p95)       fillCard("m-sim-p95-value",       "m-sim-p95-delta",       c.sim_p95.value,       c.sim_p95.delta, c.sim_p95.delta_class);
  if (c.closest_pair)  fillCard("m-closest-pair-value",  "m-closest-pair-delta",  c.closest_pair.value,  c.closest_pair.delta);
  if (c.isolated)      fillCard("m-isolated-value",      "m-isolated-delta",      c.isolated.value,      c.isolated.delta);
}
renderCards();

// ════════════════════════════════════════════════════════════════════
// One-line + pulse + history (unchanged)
// ════════════════════════════════════════════════════════════════════
function renderOneLine() {
  const last = history[history.length - 1];
  const lastTs = new Date(last.ts);
  const minAgo = Math.round((NOW - lastTs) / 60000);
  const d = last.delta;
  const isQuiet = !d || (d.pages_added.length === 0 && d.wikilinks === 0);
  const cutoff = new Date(NOW.getTime() - 24 * 3600 * 1000);
  const within = history.filter(h => new Date(h.ts) > cutoff && h.delta);
  const added24h = within.reduce((s, h) => s + h.delta.pages_added.length, 0);
  const links24h = within.reduce((s, h) => s + h.delta.wikilinks, 0);
  const orphans24h = within.reduce((s, h) => s + h.delta.orphans, 0);
  let part1 = isQuiet
    ? `<span class="label">Последний turn</span><span class="dim">${last.trigger} · ${minAgo} мин назад · без изменений</span>`
    : `<span class="label">Последний turn</span>${last.trigger} (${minAgo} мин) · <span class="added">+${d.pages_added.length} стр.</span> · <span class="added">+${d.wikilinks} wikilinks</span>`;
  let part2;
  if (added24h === 0 && links24h === 0) {
    part2 = `<span class="dim">за 24ч изменений нет</span>`;
  } else {
    const a = added24h ? `<span class="added">+${added24h} стр.</span>` : "";
    const l = links24h ? `<span class="added">+${links24h} wikilinks</span>` : "";
    const o = orphans24h > 0 ? `<span class="warn">+${orphans24h} сирот ⚠</span>` : "";
    part2 = `<span class="label">За 24ч</span>${[a, l, o].filter(Boolean).join(" · ")}`;
  }
  document.getElementById("one-line").innerHTML = part1 + `<span class="dot">·</span>` + part2;
}
renderOneLine();

function fmtTs(d) { return d.toISOString().replace("T", " ").slice(0, 16); }

function renderPulseHistory() {
  const last = history[history.length - 1];
  const minAgo = Math.round((NOW - new Date(last.ts)) / 60000);
  const d = last.delta;
  const isEmpty = !d || (d.pages_added.length === 0 && d.pages_removed.length === 0 && d.wikilinks === 0 && d.orphans === 0);
  const leftEl = document.getElementById("pulse-last-turn");
  leftEl.innerHTML = `<div class="pulse-head"><span class="pulse-title">С последнего turn'а</span><span class="pulse-ts">${minAgo} мин назад · ${last.trigger}</span></div>${isEmpty ? `<div class="pulse-empty-msg">Изменений в vault нет — был не-модифицирующий turn.</div>` : renderDeltaItems(d)}`;
  if (isEmpty) leftEl.classList.add("empty");

  const cutoff = new Date(NOW.getTime() - 24 * 3600 * 1000);
  const within = history.filter(h => new Date(h.ts) > cutoff && h.delta);
  const rightEl = document.getElementById("pulse-24h");
  if (within.length === 0) {
    rightEl.classList.add("empty");
    rightEl.innerHTML = `<div class="pulse-head"><span class="pulse-title">За последние 24ч</span><span class="pulse-ts">тихо</span></div><div class="pulse-empty-msg">Никаких изменений за сутки.</div>`;
    return;
  }
  const agg = { pages_added: [], pages_removed: [], wikilinks: 0, orphans: 0, Q: 0, Q_present: false };
  for (const h of within) {
    agg.pages_added.push(...h.delta.pages_added);
    agg.pages_removed.push(...h.delta.pages_removed);
    agg.wikilinks += h.delta.wikilinks;
    agg.orphans += h.delta.orphans;
    if (h.delta.Q !== null) { agg.Q += h.delta.Q; agg.Q_present = true; }
  }
  if (agg.orphans > 2) rightEl.classList.add("warn");
  rightEl.innerHTML = `<div class="pulse-head"><span class="pulse-title">За последние 24ч</span><span class="pulse-ts">с ${fmtTs(cutoff)}</span></div>${renderDayAgg(agg)}`;
}
function renderDeltaItems(d) {
  const items = [];
  for (const name of d.pages_added) items.push(`<li class="added">${name}</li>`);
  for (const name of d.pages_removed) items.push(`<li class="removed">${name}</li>`);
  if (d.wikilinks) items.push(`<li class="metric">${d.wikilinks > 0 ? "+" : ""}${d.wikilinks} wikilinks</li>`);
  if (d.orphans > 0) items.push(`<li class="warn">+${d.orphans} сирот</li>`);
  if (d.Q !== null && d.Q !== 0) items.push(`<li class="${d.Q > 0 ? "metric" : "metric-down"}">Q: ${d.Q > 0 ? "+" : ""}${d.Q.toFixed(3)}</li>`);
  return `<ul class="pulse-items">${items.join("")}</ul>`;
}
function renderDayAgg(agg) {
  const items = [];
  if (agg.pages_added.length > 0) {
    const preview = agg.pages_added.slice(0, 3).map(n => `<b>${n}</b>`).join(", ");
    const more = agg.pages_added.length > 3 ? `, +${agg.pages_added.length - 3} ещё` : "";
    items.push(`<li class="added">${agg.pages_added.length} страниц <span class="badge">${preview}${more}</span></li>`);
  }
  if (agg.wikilinks) items.push(`<li class="metric">+${agg.wikilinks} wikilinks</li>`);
  if (agg.orphans > 0) items.push(`<li class="warn">+${agg.orphans} сирот</li>`);
  if (agg.Q_present) items.push(`<li class="metric">Q: ${agg.Q > 0 ? "+" : ""}${agg.Q.toFixed(3)}</li>`);
  return `<ul class="pulse-items">${items.join("")}</ul>`;
}
renderPulseHistory();

function renderDiffHistory() {
  const ul = document.getElementById("diff-history-list");
  const items = [...history].reverse().filter(h => h.delta).map(h => {
    const d = h.delta;
    const isEmpty = d.pages_added.length === 0 && d.pages_removed.length === 0 && d.wikilinks === 0;
    if (isEmpty && h.trigger.includes("query")) return null;
    const bullets = [];
    for (const n of d.pages_added) bullets.push(`<li>${n}</li>`);
    for (const n of d.pages_removed) bullets.push(`<li class="removed">${n}</li>`);
    if (d.wikilinks) bullets.push(`<li>${d.wikilinks > 0 ? "+" : ""}${d.wikilinks} wikilinks</li>`);
    if (d.orphans > 0) bullets.push(`<li class="warn">+${d.orphans} сирот</li>`);
    if (d.Q !== null && d.Q !== 0) bullets.push(`<li>Q: ${d.Q > 0 ? "+" : ""}${d.Q.toFixed(3)}</li>`);
    return `<li><div class="when">${h.ts.replace("T", " ")}</div><div class="what">${h.trigger}</div><ul class="items">${bullets.join("")}</ul></li>`;
  }).filter(Boolean);
  ul.innerHTML = items.join("");
}
renderDiffHistory();

document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(btn.dataset.panel).classList.add("active");
  });
});

// ════════════════════════════════════════════════════════════════════
// Theme — localStorage + Chart.js resync
// ════════════════════════════════════════════════════════════════════
const THEME_STORAGE_KEY = "vault-explorer-theme";
const chartInstances = [];

function getTheme() {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

function chartThemeTokens() {
  const light = getTheme() === "light";
  return light
    ? { muted: "#6b7280", border: "#d8dce0", tooltipBg: "#ffffff", tooltipFg: "#18181b", tooltipBorder: "#d8dce0", treemapBorder: "#f4f4f5", treemapLabel: "#18181b" }
    : { muted: "#888", border: "#3a3a3a", tooltipBg: "#333", tooltipFg: "#d4d4d4", tooltipBorder: "#4a4a4a", treemapBorder: "#1e1e1e", treemapLabel: "#1e1e1e" };
}

function applyChartTheme() {
  const t = chartThemeTokens();
  Chart.defaults.color = t.muted;
  Chart.defaults.borderColor = t.border;
  Object.assign(Chart.defaults.plugins.tooltip, {
    backgroundColor: t.tooltipBg,
    titleColor: t.tooltipFg,
    bodyColor: t.tooltipFg,
    borderColor: t.tooltipBorder,
  });
  chartInstances.forEach(chart => {
    if (chart.config.type === "treemap" && chart.data.datasets[0]) {
      chart.data.datasets[0].borderColor = t.treemapBorder;
      chart.data.datasets[0].labels.color = t.treemapLabel;
    }
    chart.update("none");
  });
}

function setTheme(theme) {
  const next = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem(THEME_STORAGE_KEY, next);
  applyChartTheme();
}

document.getElementById("theme-toggle").addEventListener("click", () => {
  setTheme(getTheme() === "dark" ? "light" : "dark");
});

// ════════════════════════════════════════════════════════════════════
// Charts — careful with defaults (don't overwrite plugin.tooltip)
// ════════════════════════════════════════════════════════════════════
Chart.defaults.interaction.mode = "index";
Chart.defaults.interaction.intersect = false;
Object.assign(Chart.defaults.plugins.tooltip, {
  borderWidth: 1,
  padding: 10,
  cornerRadius: 6,
});
applyChartTheme();

function registerChart(chart) {
  chartInstances.push(chart);
  return chart;
}

const palette = {
  // Page types
  domain: "#a8a8a8", entity: "#4a9eff", idea: "#4ae87a", mind: "#ff4ad8",
  question: "#ffd54a", unassigned: "#ff8a4a",
  // Provenance
  ingest: "#4a9eff", study: "#4ae87a", brainstorm: "#ff4ad8",
  save: "#ffd54a", manual: "#a8a8a8",
  // Statuses (idea/entity/question) and mind statuses
  evaluation: "#ff8a4a", "in-progress": "#ffd54a", ready: "#4ae87a",
  draft: "#ff4ad8", stable: "#4ae87a", deprecated: "#a8a8a8",
  // Synthetic community labels used by Sankey
  "Comm A": "#4a9eff", "Comm B": "#4ae87a", "Comm C": "#ffd54a", "Comm D": "#ff6b6b",
};
// Fallback palette for keys not in the explicit map (e.g. real domain
// names, new statuses). Index-cycled across the chart's data points.
const FALLBACK_COLORS = ["#4a9eff", "#4ae87a", "#ffd54a", "#ff6b6b", "#ff4ad8", "#ff8a4a", "#79b8ff", "#6bcb6b", "#a8a8a8"];
function colorFor(key, index) {
  return palette[key] || FALLBACK_COLORS[index % FALLBACK_COLORS.length];
}
function colorsFor(keys) {
  return keys.map((k, i) => colorFor(k, i));
}
const latest = history[history.length - 1] || { types: {}, domains: {}, cohesion: {} };
// Domain names from current state — used for time-series line colors
const domainKeys = Object.keys(latest.domains || {});

// ════════════════════════════════════════════════════════════════════
// Health score — deterministic, no LLM
// ════════════════════════════════════════════════════════════════════
function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

function computeHealth(h) {
  const total = h.pages;
  const orphans_ratio = h.orphans / total;
  const density = h.wikilinks / total;
  const ready_ratio = (statuses.ready || 0) / total;
  const with_domain = total - (h.types.unassigned || 0);
  const domain_ratio = with_domain / total;

  // 0..100 component scores
  const orphan_score = clamp(100 - orphans_ratio * 1000, 0, 100);          // 10% orphans → 0
  const density_score = clamp(100 - Math.abs(density - 7) * 14, 0, 100);   // sweet spot ≈ 7
  const Q_score = h.Q !== null ? clamp((h.Q / 0.7) * 100, 0, 100) : 50;
  const ready_score = clamp(ready_ratio * 100, 0, 100);
  const domain_score = clamp(domain_ratio * 100, 0, 100);

  // Weighted total
  const total_score = Math.round(
    orphan_score * 0.25 + density_score * 0.15 + Q_score * 0.25 +
    ready_score * 0.20 + domain_score * 0.15
  );

  return {
    total: total_score,
    rows: [
      { key: "orphans",  label: "Сироты",       score: Math.round(orphan_score),  detail: `${h.orphans} / ${total}` },
      { key: "density",  label: "Плотность",    score: Math.round(density_score), detail: density.toFixed(2) },
      { key: "modular",  label: "Q (Louvain)",  score: Math.round(Q_score),       detail: h.Q !== null ? h.Q.toFixed(3) : "—" },
      { key: "ready",    label: "Зрелых",       score: Math.round(ready_score),   detail: `${Math.round(ready_ratio * 100)}%` },
      { key: "domain",   label: "Классифицир.", score: Math.round(domain_score),  detail: `${Math.round(domain_ratio * 100)}%` },
    ],
  };
}
function gradeColor(score) {
  if (score >= 80) return "good";
  if (score >= 60) return "warn";
  return "bad";
}
function renderHealth() {
  const h = computeHealth(latest);
  const scoreEl = document.getElementById("health-score");
  scoreEl.className = "health-score " + gradeColor(h.total);
  scoreEl.querySelector(".value").textContent = h.total;
  const breakdown = document.getElementById("health-breakdown");
  breakdown.innerHTML = h.rows.map(r => `
    <div class="health-row">
      <div class="label">${r.label}</div>
      <div class="bar"><div class="bar-fill ${gradeColor(r.score)}" style="width: ${r.score}%"></div></div>
      <div class="num">${r.score} <span style="color: var(--muted); font-size: 10px;">(${r.detail})</span></div>
    </div>
  `).join("");
}
renderHealth();

// ════════════════════════════════════════════════════════════════════
// Range selector for time-series
// ════════════════════════════════════════════════════════════════════
let currentRange = "all";
const tsCharts = {};

function filterHistoryByRange(range) {
  if (range === "all") return history;
  const days = parseInt(range, 10);
  const cutoff = new Date(NOW.getTime() - days * 24 * 3600 * 1000);
  return history.filter(h => new Date(h.ts) >= cutoff);
}

// Distributions — no border on doughnut (matches page bg)
registerChart(new Chart(document.getElementById("dist-types"), {
  type: "doughnut",
  data: { labels: Object.keys(latest.types), datasets: [{ data: Object.values(latest.types), backgroundColor: colorsFor(Object.keys(latest.types)), borderWidth: 0 }] },
  options: { responsive: true, plugins: { legend: { position: "right", labels: { boxWidth: 10, font: { size: 11 } } } } },
}));
registerChart(new Chart(document.getElementById("dist-domains"), {
  type: "bar",
  data: { labels: Object.keys(latest.domains), datasets: [{ data: Object.values(latest.domains), backgroundColor: colorsFor(Object.keys(latest.domains)), borderWidth: 0 }] },
  options: { responsive: true, plugins: { legend: { display: false } }, indexAxis: "y", scales: { x: { beginAtZero: true } } },
}));
registerChart(new Chart(document.getElementById("dist-provenance"), {
  type: "doughnut",
  data: { labels: Object.keys(provenance), datasets: [{ data: Object.values(provenance), backgroundColor: colorsFor(Object.keys(provenance)), borderWidth: 0 }] },
  options: { responsive: true, plugins: { legend: { position: "right", labels: { boxWidth: 10, font: { size: 11 } } } } },
}));
registerChart(new Chart(document.getElementById("dist-status"), {
  type: "bar",
  data: { labels: Object.keys(statuses), datasets: [{ data: Object.values(statuses), backgroundColor: colorsFor(Object.keys(statuses)), borderWidth: 0 }] },
  options: { responsive: true, plugins: { legend: { display: false } }, indexAxis: "y", scales: { x: { beginAtZero: true } } },
}));

// Time-series — rendered through a single function so range-selector
// can rebuild them on the fly.
function renderTimeseries(filtered) {
  if (filtered.length === 0) return;
  const labels = filtered.map(h => h.ts.slice(5, 16));

  function build(id, config) {
    if (tsCharts[id]) {
      const idx = chartInstances.indexOf(tsCharts[id]);
      if (idx >= 0) chartInstances.splice(idx, 1);
      tsCharts[id].destroy();
    }
    tsCharts[id] = registerChart(new Chart(document.getElementById(id), config));
  }

  build("ts-pages-stack", {
    type: "line",
    data: { labels, datasets: ["domain","entity","idea","question","mind","unassigned"].map((t, i) => ({ label: t, data: filtered.map(h => (h.types || {})[t] || 0), backgroundColor: colorFor(t, i) + "55", borderColor: colorFor(t, i), fill: true, tension: 0.25, pointRadius: 2, pointHoverRadius: 6 })) },
    options: { responsive: true, plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } } }, scales: { y: { stacked: true, beginAtZero: true } } },
  });
  build("ts-dom-sizes", {
    type: "line",
    data: { labels, datasets: domainKeys.map((d, i) => ({ label: d, data: filtered.map(h => (h.domains || {})[d] || 0), borderColor: colorFor(d, i), tension: 0.25, pointRadius: 2, pointHoverRadius: 6 })) },
    options: { responsive: true, plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } } } },
  });
  build("ts-connectivity", {
    type: "line",
    data: { labels, datasets: [
      { label: "Wikilinks", data: filtered.map(h => h.wikilinks), borderColor: "#4a9eff", backgroundColor: "#4a9eff22", fill: true, yAxisID: "y", tension: 0.25, pointRadius: 2, pointHoverRadius: 6 },
      { label: "Сироты", data: filtered.map(h => h.orphans), borderColor: "#ff8a4a", yAxisID: "y1", tension: 0.25, pointRadius: 2, pointHoverRadius: 6 }
    ] },
    options: { responsive: true, plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } } }, scales: { y: { position: "left", title: { display: true, text: "wikilinks" } }, y1: { position: "right", beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: "orphans" } } } },
  });
  build("ts-density", {
    type: "line",
    data: { labels, datasets: [{ label: "wikilinks / page", data: filtered.map(h => parseFloat((h.wikilinks / h.pages).toFixed(2))), borderColor: "#4ae87a", backgroundColor: "#4ae87a22", fill: true, tension: 0.25, pointRadius: 2, pointHoverRadius: 6 }] },
    options: { responsive: true, plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } } } },
  });
  build("ts-topology", {
    type: "bar",
    data: { labels, datasets: [
      { type: "line", label: "Q", data: filtered.map(h => h.Q), borderColor: "#4ae87a", yAxisID: "y", tension: 0.25, pointRadius: 2, pointHoverRadius: 6 },
      { type: "bar", label: "Сообществ ≥ 3", data: filtered.map(h => h.communities), backgroundColor: "#4a9eff55", borderWidth: 0, yAxisID: "y1" }
    ] },
    options: { responsive: true, plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } } }, scales: { y: { position: "left", min: 0.6, max: 0.7, title: { display: true, text: "Q" } }, y1: { position: "right", beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: "сообществ" } } } },
  });
  build("ts-bridges", {
    type: "line",
    data: { labels, datasets: [{ label: "узлов-мостов", data: filtered.map(h => h.bridges), borderColor: "#ffd54a", backgroundColor: "#ffd54a22", fill: true, tension: 0.25, pointRadius: 2, pointHoverRadius: 6 }] },
    options: { responsive: true, plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } } } },
  });
  build("ts-similarity", {
    type: "line",
    data: { labels, datasets: [
      { label: "медиана", data: filtered.map(h => h.sim_p50), borderColor: "#4a9eff", tension: 0.25, pointRadius: 2, pointHoverRadius: 6 },
      { label: "p75", data: filtered.map(h => h.sim_p75), borderColor: "#4ae87a", tension: 0.25, pointRadius: 2, pointHoverRadius: 6 },
      { label: "p95", data: filtered.map(h => h.sim_p95), borderColor: "#ff8a4a", tension: 0.25, pointRadius: 2, pointHoverRadius: 6 }
    ] },
    options: { responsive: true, plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } } }, scales: { y: { min: 0.3, max: 0.8 } } },
  });
  build("ts-dom-cohesion", {
    type: "line",
    data: { labels, datasets: domainKeys.map((d, i) => ({ label: d, data: filtered.map(h => (h.cohesion || {})[d] ?? null), borderColor: colorFor(d, i), tension: 0.25, pointRadius: 2, pointHoverRadius: 6 })) },
    options: { responsive: true, plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } } }, scales: { y: { min: 0.4, max: 0.7, title: { display: true, text: "avg cosine" } } } },
  });
}
renderTimeseries(history);

// Range selector handlers
document.querySelectorAll("#range-selector .range-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#range-selector .range-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentRange = btn.dataset.range;
    renderTimeseries(filterHistoryByRange(currentRange));
  });
});

// ════════════════════════════════════════════════════════════════════
// Sankey & Treemap — heavy artifacts. Render only when data is present;
// otherwise the chart libs throw on empty datasets and abort the rest
// of the script.
// ════════════════════════════════════════════════════════════════════
function showPlaceholder(canvasId, label) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const wrap = canvas.parentElement;
  wrap.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--muted);font-style:italic;font-size:13px;text-align:center;padding:20px;">${label}</div>`;
}

if (sankeyData && sankeyData.length > 0) {
  try {
    // Pre-assign colors so each domain and each community gets its own
    // tone (palette + indexed fallback). Without this, all flows render
    // as the same grey because palette has no entries for full domain
    // names like "Machine Learning" or for "Сообщество N" labels.
    const sankeyColor = {};
    [...new Set(sankeyData.map(d => d.from))].forEach((k, i) => { sankeyColor[k] = colorFor(k, i); });
    [...new Set(sankeyData.map(d => d.to))].forEach((k, i) => { sankeyColor[k] = colorFor(k, i + 4); });

    registerChart(new Chart(document.getElementById("map-sankey"), {
      type: "sankey",
      data: {
        datasets: [{
          label: "Domain → Community flow",
          data: sankeyData,
          colorFrom: c => sankeyColor[c.dataset.data[c.dataIndex].from] || "#888",
          colorTo: c => sankeyColor[c.dataset.data[c.dataIndex].to] || "#888",
          colorMode: "gradient",
          borderWidth: 0,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "nearest", intersect: true },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => `${ctx.raw.from} → ${ctx.raw.to}: ${ctx.raw.flow} страниц` } } },
      },
    }));
  } catch (e) {
    console.error("Sankey render failed", e);
    showPlaceholder("map-sankey", "Sankey не отрисовался");
  }
} else {
  showPlaceholder("map-sankey", "Данные появятся после следующего /snapshot");
}

// Word cloud uses wordcloudData loaded from _dashboardData above.
const cloudColors = ["#4a9eff", "#4ae87a", "#ff4ad8", "#ffd54a", "#ff8a4a", "#ff6b6b"];

// CSS-based word cloud — no canvas, no library quirks, always renders.
// Font-size is linear interpolation of frequency between 12px and 60px.
// Words are shuffled so small and large mix randomly across the layout.
function renderWordCloud() {
  const wrap = document.getElementById("map-wordcloud");
  if (!wrap) return;
  const max = Math.max(...wordcloudData.map(([, f]) => f));
  const min = Math.min(...wordcloudData.map(([, f]) => f));
  const shuffled = [...wordcloudData].sort(() => Math.random() - 0.5);
  wrap.innerHTML = shuffled.map(([word, freq]) => {
    const t = (freq - min) / (max - min);     // 0..1
    const size = Math.round(13 + t * 47);      // 13..60 px
    const color = cloudColors[Math.floor(Math.random() * cloudColors.length)];
    return `<span style="font-size:${size}px;color:${color};" title="${word} · ${freq}">${word}</span>`;
  }).join("");
}
renderWordCloud();

// ════════════════════════════════════════════════════════════════════
// Wikilinks [[X]] → obsidian:// links inside insight & history blocks
// ════════════════════════════════════════════════════════════════════
const VAULT_NAME = _dashboardData.vault_name || "llm-wiki";
function linkifyWikilinks(rootSelector) {
  document.querySelectorAll(rootSelector).forEach(el => {
    el.innerHTML = el.innerHTML.replace(/\[\[([^\]|#]+?)(?:\|([^\]]+))?\]\]/g,
      (_, target, alias) => {
        const enc = encodeURIComponent(target);
        const label = alias || target;
        return `<a class="obsidian-link" href="obsidian://open?vault=${VAULT_NAME}&file=${enc}" target="_blank">${label}</a>`;
      });
  });
}
linkifyWikilinks(".map-insight");

if (treemapData && treemapData.length > 0) {
  try {
    const treemapTheme = chartThemeTokens();
    registerChart(new Chart(document.getElementById("map-treemap"), {
      type: "treemap",
      data: {
        datasets: [{
          tree: treemapData,
          key: "value",
          groups: ["domain", "type"],
          borderColor: treemapTheme.treemapBorder,
          borderWidth: 2,
          spacing: 1,
          backgroundColor: ctx => {
            if (ctx.type !== "data") return "transparent";
            const o = ctx.raw._data;
            // Same color per domain — index by position in domainKeys + 1
            // for stability (so 'Machine Learning' is always #4a9eff etc.).
            const idx = domainKeys.indexOf(o.domain);
            return colorFor(o.domain, idx >= 0 ? idx : 0);
          },
          labels: {
            display: true,
            color: treemapTheme.treemapLabel,
            font: { size: 11, weight: 500 },
            formatter: ctx => {
              const o = ctx.raw._data;
              if (!o) return "";
              if (o.type) return `${o.type}\n${o.value}`;
              return `${o.domain}`;
            },
            align: "center",
            position: "middle",
          },
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "nearest", intersect: true },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => { const o = ctx.raw._data; return o ? `${o.domain} / ${o.type}: ${o.value} страниц` : ""; } } } },
      },
    }));
  } catch (e) {
    console.error("Treemap render failed", e);
    showPlaceholder("map-treemap", "Treemap не отрисовался");
  }
} else {
  showPlaceholder("map-treemap", "Данные появятся после следующего /snapshot");
}
</script>

</body>
</html>
