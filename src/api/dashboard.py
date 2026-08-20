"""Built-in metrics dashboard served at GET /dashboard.

A self-contained HTML page that fetches /metrics from the same origin,
parses the Prometheus text format, and renders charts using Chart.js
(CDN). No Docker, no external tools required.
"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tunisia Energy RAG — Metrics Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #0f172a; color: #e2e8f0; padding: 24px; }
  h1 { font-size: 1.5rem; margin-bottom: 8px; color: #38bdf8; }
  .subtitle { color: #94a3b8; margin-bottom: 24px; font-size: 0.9rem; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
           gap: 16px; margin-bottom: 32px; }
  .stat { background: #1e293b; border-radius: 12px; padding: 20px;
          border: 1px solid #334155; }
  .stat .label { font-size: 0.75rem; color: #94a3b8; text-transform: uppercase;
                 letter-spacing: 0.05em; }
  .stat .value { font-size: 2rem; font-weight: 700; margin-top: 4px; }
  .stat .value.green { color: #4ade80; }
  .stat .value.blue { color: #38bdf8; }
  .stat .value.purple { color: #a78bfa; }
  .stat .value.orange { color: #fb923c; }
  .stat .value.red { color: #f87171; }
  .charts { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  .chart-card { background: #1e293b; border-radius: 12px; padding: 20px;
                border: 1px solid #334155; }
  .chart-card h3 { font-size: 0.9rem; color: #94a3b8; margin-bottom: 12px; }
  canvas { width: 100% !important; }
  .refresh { color: #64748b; font-size: 0.8rem; margin-top: 16px; text-align: center; }
  @media (max-width: 768px) { .charts { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<h1>⚡ Tunisia Energy RAG — Metrics</h1>
<p class="subtitle">Live Prometheus metrics from <code>/metrics</code> · auto-refreshes every 15s</p>

<div class="stats" id="stats"></div>
<div class="charts">
  <div class="chart-card"><h3>Requests per Endpoint (5min)</h3><canvas id="requestChart"></canvas></div>
  <div class="chart-card"><h3>Request Latency (p50 / p95 / p99)</h3><canvas id="latencyChart"></canvas></div>
  <div class="chart-card"><h3>LLM Token Usage</h3><canvas id="llmChart"></canvas></div>
  <div class="chart-card"><h3>Ingestion Results</h3><canvas id="ingestChart"></canvas></div>
  <div class="chart-card"><h3>Purge Stats</h3><canvas id="purgeChart"></canvas></div>
  <div class="chart-card"><h3>Error Rate (5xx)</h3><canvas id="errorChart"></canvas></div>
</div>
<p class="refresh" id="refreshText">Loading...</p>

<script>
const COLORS = {
  blue: '#38bdf8', green: '#4ade80', purple: '#a78bfa',
  orange: '#fb923c', red: '#f87171', pink: '#f472b6',
  cyan: '#22d3ee', yellow: '#facc15'
};

let charts = {};

function parseMetrics(text) {
  const metrics = {};
  const lines = text.split('\\n');
  for (const line of lines) {
    if (!line || line.startsWith('#')) continue;
    const match = line.match(/^([a-zA-Z_:][a-zA-Z0-9_:]*)\\{?([^}]*)\\}?\\s+(.+)/);
    if (!match) continue;
    const [, name, labelsStr, value] = match;
    if (!metrics[name]) metrics[name] = [];
    const labels = {};
    if (labelsStr) {
      for (const part of labelsStr.split(',')) {
        const [k, v] = part.split('=');
        if (k && v) labels[k.trim()] = v.replace(/"/g, '').trim();
      }
    }
    metrics[name].push({ labels, value: parseFloat(value) });
  }
  return metrics;
}

function sumBy(metrics, name, groupKey) {
  const items = metrics[name] || [];
  const sums = {};
  for (const item of items) {
    const key = item.labels[groupKey] || 'unknown';
    sums[key] = (sums[key] || 0) + item.value;
  }
  return sums;
}

function statValue(metrics, name) {
  const items = metrics[name] || [];
  return items.reduce((s, i) => s + i.value, 0);
}

function renderStats(metrics) {
  const stats = [
    { label: 'Total Requests', value: statValue(metrics, 'http_requests_total'), cls: 'blue' },
    { label: 'Active Requests', value: statValue(metrics, 'active_requests'), cls: 'green' },
    { label: 'LLM Tokens', value: statValue(metrics, 'llm_tokens_total'), cls: 'purple' },
    { label: 'LLM Calls', value: statValue(metrics, 'llm_requests_total'), cls: 'cyan' },
    { label: 'Purge Runs', value: statValue(metrics, 'purge_runs_total'), cls: 'orange' },
    { label: 'Reports Purged', value: statValue(metrics, 'purge_deleted_total'), cls: 'red' },
  ];
  document.getElementById('stats').innerHTML = stats.map(s =>
    `<div class="stat"><div class="label">${s.label}</div><div class="value ${s.cls}">${s.value.toLocaleString()}</div></div>`
  ).join('');
}

function barChart(id, data, color) {
  const ctx = document.getElementById(id);
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(ctx, {
    type: 'bar',
    data: { labels: Object.keys(data), datasets: [{ data: Object.values(data), backgroundColor: color }] },
    options: { responsive: true, plugins: { legend: { display: false } },
               scales: { x: { ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { color: '#1e293b' } },
                         y: { ticks: { color: '#94a3b8' }, grid: { color: '#334155' } } } }
  });
}

function pieChart(id, data, colors) {
  const ctx = document.getElementById(id);
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(ctx, {
    type: 'doughnut',
    data: { labels: Object.keys(data), datasets: [{ data: Object.values(data), backgroundColor: colors }] },
    options: { responsive: true, plugins: { legend: { labels: { color: '#94a3b8' } } } }
  });
}

function renderCharts(metrics) {
  // Requests by endpoint
  const requests = sumBy(metrics, 'http_requests_total', 'path');
  barChart('requestChart', requests, COLORS.blue);

  // LLM tokens
  const tokens = sumBy(metrics, 'llm_tokens_total', 'type');
  barChart('llmChart', tokens, COLORS.purple);

  // Ingestion
  const ingest = sumBy(metrics, 'ingestions_total', 'result');
  if (Object.keys(ingest).length > 0) {
    pieChart('ingestChart', ingest, [COLORS.green, COLORS.orange, COLORS.red]);
  }

  // Purge
  const purgeData = { 'Runs': statValue(metrics, 'purge_runs_total'), 'Deleted': statValue(metrics, 'purge_deleted_total') };
  pieChart('purgeChart', purgeData, [COLORS.orange, COLORS.red]);

  // Latency placeholder (requires histogram parsing)
  const ctx = document.getElementById('latencyChart');
  if (charts['latencyChart']) charts['latencyChart'].destroy();
  const histItems = metrics['http_request_duration_seconds_sum'] || [];
  const countItems = metrics['http_request_duration_seconds_count'] || [];
  const latencyData = {};
  for (const item of histItems) {
    const path = item.labels.path || 'unknown';
    const count = countItems.find(c => c.labels.path === path);
    if (count && count.value > 0) {
      latencyData[path] = (item.value / count.value * 1000).toFixed(1) + 'ms';
    }
  }
  charts['latencyChart'] = new Chart(ctx, {
    type: 'bar',
    data: { labels: Object.keys(latencyData), datasets: [{ data: Object.values(latencyData).map(v => parseFloat(v)), backgroundColor: COLORS.green }] },
    options: { responsive: true, plugins: { legend: { display: false } },
               scales: { x: { ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { color: '#1e293b' } },
                         y: { ticks: { color: '#94a3b8', callback: v => v + 'ms' }, grid: { color: '#334155' } } } }
  });

  // Errors
  const errors = sumBy(metrics, 'http_requests_total', 'status');
  const errorData = {};
  for (const [k, v] of Object.entries(errors)) {
    if (k.startsWith('5')) errorData[k] = v;
  }
  if (Object.keys(errorData).length > 0) {
    barChart('errorChart', errorData, COLORS.red);
  } else {
    const ectx = document.getElementById('errorChart');
    if (charts['errorChart']) charts['errorChart'].destroy();
    charts['errorChart'] = new Chart(ectx, {
      type: 'doughnut',
      data: { labels: ['No errors'], datasets: [{ data: [1], backgroundColor: [COLORS.green] }] },
      options: { responsive: true, plugins: { legend: { labels: { color: '#94a3b8' } } } }
    });
  }
}

async function refresh() {
  try {
    const resp = await fetch('/metrics');
    const text = await resp.text();
    const metrics = parseMetrics(text);
    renderStats(metrics);
    renderCharts(metrics);
    document.getElementById('refreshText').textContent =
      'Last updated: ' + new Date().toLocaleTimeString() + ' · auto-refreshes every 15s';
  } catch (e) {
    document.getElementById('refreshText').textContent = 'Error loading metrics: ' + e.message;
  }
}

refresh();
setInterval(refresh, 15000);
</script>
</body>
</html>
"""
