'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const html = fs.readFileSync(path.join(__dirname, '../backend/static/index.html'), 'utf8');

// 使用真实渲染逻辑，只隔离 DOM 和 Chart.js 绘制边界。
function fixture() {
  const nodes = new Map();
  const $ = id => {
    if (!nodes.has(id)) nodes.set(id, { textContent: '', previousElementSibling: { textContent: '' } });
    return nodes.get(id);
  };
  function Chart(node, config) { Object.assign(this, config); this.update = () => {}; this.resize = () => {}; }
  Chart.defaults = { font: {}, plugins: { legend: { labels: {} } } };
  const context = vm.createContext({ $, Chart, state: { charts: {} }, console });
  vm.runInContext(html.slice(html.indexOf('function ensureCharts()'), html.indexOf('/* ---------- 日志 ---------- */')), context);
  vm.runInContext(html.slice(html.indexOf('function resourceSparkline('), html.indexOf('// 渲染单个 CPU/GPU')), context);
  return { context, $ };
}

test('未知、零值与非有限资源值使用不同展示', () => {
  const { context: c } = fixture();
  for (const value of [undefined, null, NaN, Infinity, -1, '99', '<script>']) assert.equal(c.performanceNumber(value), '—');
  assert.equal(c.performanceNumber(0, 1, '%'), '0.0%');
  assert.equal(c.performanceNumber(42.25, 1), '42.3');
});

test('资源采样包含明确口径，过期与离线不会显示实时', () => {
  const { context: c } = fixture();
  assert.match(c.workerResourceStats({}), /等待采样/);
  const worker = { active: true, gpu: { available: true }, resources: { latest: {
    sampled_at: Date.now() / 1000, host_cpu_percent: 25, process_cpu_cores: .5, torch_threads: 2,
    rss_mb: 128, host_available_mb: 1024, gpu: { utilization_percent: 0, used_mb: 1000, total_mb: 8000 },
  }, history: [] } };
  let result = c.workerResourceStats(worker);
  assert.match(result, /实时采样/); assert.match(result, /主机 CPU 25.0%/);
  assert.match(result, /进程使用 0.50 核/); assert.match(result, /GPU 0.0%/);
  worker.active = false;
  assert.match(c.workerResourceStats(worker), /采样已过期/);
  worker.active = true; worker.resources.latest.sampled_at -= 30;
  assert.match(c.workerResourceStats(worker), /采样已过期/);
});

test('曲线缺测断开，恶意文本和非有限值不进入 SVG', () => {
  const { context: c } = fixture();
  const points = [10, null, 20, '<script>', NaN, 30].map((value, i) => ({ sampled_at: i + 1, gpu_percent: value }));
  const svg = c.resourceSparkline(points, 'gpu_percent', '#fff');
  assert.doesNotMatch(svg, /script|NaN|Infinity/);
  assert.equal((svg.match(/M/g) || []).length, 3);
  assert.equal(c.resourceSparkline([], 'gpu_percent', '#fff'), '');
});

test('吞吐、阶段曲线与旧任务切换保持数据和单位一致', () => {
  const { context: c, $ } = fixture();
  const job = { spec: { task: 'llm_sft' }, metrics: [], performance: [{ epoch: 1, scope: 'group',
    samples_per_second: 10, tokens_per_second: 200, step_ms: 30, train_seconds: 2,
    validation_seconds: .5, checkpoint_save_seconds: .1, checkpoint_publish_seconds: .2 }] };
  c.renderPerformanceSummary(job); c.renderCharts(job);
  assert.match($('performanceSummary').textContent, /联合任务总吞吐 200.0 有效 tokens\/s/);
  assert.equal(c.state.charts.throughput.data.datasets[0].data[0], 200);
  assert.equal(c.state.charts.phases.data.datasets[3].data[0], .2);
  c.renderPerformanceSummary({});
  c.renderCharts({ spec: { task: 'classification' }, metrics: [] });
  assert.match($('performanceSummary').textContent, /尚无性能记录/);
  assert.equal(c.state.charts.throughput.data.datasets[0].data.length, 0);
  assert.equal(c.state.charts.phases.data.datasets[0].data.length, 0);
  assert.match($('throughputTitle').textContent, /samples\/s/);
});

for (const activeChart of ['loss', 'metric', 'throughput', 'phases']) test(`曲线 ${activeChart} 的空状态使用自身数据，缺失图表组件仍可恢复`, () => {
  const { context: c, $ } = fixture(); c.state.activeChart = activeChart;
  const job = { spec: { task: 'classification' }, metrics: [{ epoch: 1, train_loss: 0.5, val_accuracy: 0.8 }], performance: [] };
  const Chart = c.Chart; c.Chart = undefined; c.renderCharts(job);
  assert.match($('chartNote').textContent, /未加载/);
  c.Chart = Chart; c.renderCharts(job);
  assert.equal($('chartNote').textContent, ['throughput', 'phases'].includes(activeChart) ? '暂无性能数据；历史任务可能未采集。' : '');
  job.metrics = []; c.renderCharts(job);
  assert.match($('chartNote').textContent, /暂无/);
  assert.equal(c.state.charts.loss.data.datasets[0].data.length, 0);
});
