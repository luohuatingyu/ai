'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../backend/static/index.html'), 'utf8');

// 仅隔离 DOM 与网络，真实执行资源筛选、提交、错误保留和任务展示逻辑。
function fixture() {
  const nodes = new Map(), document = { activeElement: null, querySelectorAll: () => [] };
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, { id, value: '', checked: false, hidden: false, disabled: false,
      dataset: {}, handlers: {}, textContent: '', innerHTML: '', children: [],
      addEventListener(key, fn) { this.handlers[key] = fn; },
      contains(element) { return element === this; }, closest() { return null; },
      focus() { document.activeElement = this; }, blur() {}, scrollIntoView() {},
      setAttribute() {}, removeAttribute() {}, querySelectorAll() { return []; } });
    return nodes.get(id);
  }
  for (const match of html.matchAll(/\bid="([^"]+)"[^>]*>/g)) {
    node(match[1]).value = /\bvalue="([^"]*)"/.exec(match[0])?.[1] || '';
  }
  node('taskSelect').value = 'classification'; node('targetSelect').value = 'target';
  node('p_parallel').value = 'single'; node('p_dataset_source').value = 'local';
  node('p_base_model').value = 'org/model'; node('p_reward_model').value = 'org/reward';
  const workers = [
    { worker_id: 'cpu', machine_id: 'server-a', slot: 'cpu0', cpu: { cores: 8 }, gpu: { available: false } },
    { worker_id: 'gpu', machine_id: 'server-b', slot: 'gpu0', gpu: { available: true, name: 'GPU test', vram_mb: 8192 } },
  ].map(worker => ({ ...worker, active: true, enabled: true, state: 'idle' }));
  const state = { datasetId: 'dataset', workerSlots: workers, workerResourcesLoaded: true, workerResourcesError: false, trainSubmitting: false,
    resourceRefreshJobs: new Map(), resourceRefreshTimer: null };
  const requests = [];
  const resourceRefreshRequests = [];
  const context = vm.createContext({ $: node, state, document, console, setTimeout: () => 1, clearTimeout() {},
    esc: value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])),
    toast() {}, selectJob() {}, fmtTime: String, resourceText: value => value.worker_id,
    requestTaskResourceRefresh: (...args) => resourceRefreshRequests.push(args),
    apiJson: async (url, options) => { requests.push({ url, payload: JSON.parse(options.body) }); return { job_id: 'created' }; },
  });
  const updateStartIndex = html.indexOf('function updateStart()');
  const resourceHelperIndex = html.indexOf('function trainingResourceUnavailable');
  const uploadIndex = html.indexOf('/* 上传 */');
  const firstSection = resourceHelperIndex >= 0 && resourceHelperIndex < updateStartIndex
    ? html.slice(resourceHelperIndex, updateStartIndex) + html.slice(updateStartIndex, uploadIndex)
    : html.slice(updateStartIndex, uploadIndex);
  vm.runInContext(firstSection, context);
  vm.runInContext(html.slice(html.indexOf('const taskSelect ='), html.indexOf('const statusText =')), context);
  vm.runInContext(html.slice(html.indexOf('function jobResourceLabel('), html.indexOf('// 仅提供在线')), context);
  vm.runInContext(html.slice(html.indexOf('function renderResourceAllocation('), html.indexOf('// 用最近一次实际执行位置')), context);
  return { context, state, workers, requests, resourceRefreshRequests, $: node };
}

test('空资源池保留自动调度，资源恢复后可选择 CPU/GPU 并显示容量', () => {
  const f = fixture(); f.state.workerSlots = []; f.context.renderTrainingResources(); f.context.updateStart();
  assert.match(f.$('trainingResourceHint').textContent, /没有符合要求/);
  assert.equal(f.$('btnStart').disabled, false);
  f.state.workerSlots = f.workers; f.context.renderTrainingResources();
  assert.match(f.$('trainingResourceSelect').innerHTML, /8 线程/);
  assert.match(f.$('trainingResourceSelect').innerHTML, /8.0 GB/);
});

for (const worker of ['', 'cpu', 'gpu']) {
  test(`请求按选择持久化目标，自动不增加字段：${worker || 'auto'}`, async () => {
    const f = fixture(); f.$('trainingResourceSelect').value = worker;
    await f.$('btnStart').onclick();
    assert.equal(f.requests.length, 1);
    assert.equal(f.requests[0].payload.worker_id, worker || undefined);
    assert.equal(f.state.trainSubmitting, false);
  });
}

test('创建任务成功后立即触发资源协调刷新并限定到指定槽', async () => {
  const f = fixture(); f.$('trainingResourceSelect').value = 'cpu';
  await f.$('btnStart').onclick();
  assert.equal(JSON.stringify(f.resourceRefreshRequests), JSON.stringify([['created', 'starting', ['cpu']]]));
});

test('任务从排队转入运行或终态时触发对应资源协调刷新', () => {
  const f = fixture();
  f.state.jobs = [{ id: 'job-a', status: 'queued', worker_id: 'cpu' }];
  const start = html.indexOf('const terminalJobStatuses');
  const end = html.indexOf('// 只采纳最近一次任务列表响应', start);
  assert.ok(start >= 0 && end > start, '资源状态转换观察器应存在');
  f.context.refreshWorkers = () => {};
  f.context.scheduleResourceRefreshPoll = () => {};
  vm.runInContext(html.slice(start, end), f.context);
  f.context.requestTaskResourceRefresh = (...args) => f.resourceRefreshRequests.push(args);

  f.context.observeResourceJobTransitions([{ id: 'job-a', status: 'running', worker_id: 'cpu' }]);
  f.state.jobs = [{ id: 'job-a', status: 'running', worker_id: 'cpu' }];
  f.context.observeResourceJobTransitions([{ id: 'job-a', status: 'completed', worker_id: 'cpu' }]);
  assert.equal(JSON.stringify(f.resourceRefreshRequests), JSON.stringify([
    ['job-a', 'starting', ['cpu']],
    ['job-a', 'terminal', ['cpu']],
  ]));
});

test('终态刷新等待接口确认目标槽释放，或确认槽已接手其他任务', () => {
  const f = fixture();
  const start = html.indexOf('function isResourceTaskReconciled');
  const end = html.indexOf('function requestTaskResourceRefresh', start);
  assert.ok(start >= 0 && end > start, '资源状态收敛判断应存在');
  vm.runInContext(html.slice(start, end), f.context);
  const reconciliation = { phase: 'terminal', workerIds: new Set(['cpu']) };

  f.state.workerSlots = [{ worker_id: 'cpu', active: true, occupied: true, current_job: 'job-a' }];
  assert.equal(f.context.isResourceTaskReconciled('job-a', reconciliation), false);
  f.state.workerSlots = [{ worker_id: 'cpu', active: true, occupied: true, current_job: null }];
  assert.equal(f.context.isResourceTaskReconciled('job-a', reconciliation), false);
  f.state.workerSlots = [{ worker_id: 'cpu', active: true, occupied: false, current_job: null }];
  assert.equal(f.context.isResourceTaskReconciled('job-a', reconciliation), true);
  f.state.workerSlots = [{ worker_id: 'cpu', active: true, occupied: true, current_job: 'job-b' }];
  assert.equal(f.context.isResourceTaskReconciled('job-a', reconciliation), true);
});

test('首次加载历史任务不触发转换刷新，资源协调最多持续 12 秒', () => {
  const f = fixture();
  f.state.jobs = [];
  const start = html.indexOf('const terminalJobStatuses');
  const end = html.indexOf('// 只采纳最近一次任务列表响应', start);
  assert.ok(start >= 0 && end > start, '资源协调逻辑应存在');
  vm.runInContext(html.slice(start, end), f.context);
  f.context.observeResourceJobTransitions([{ id: 'old', status: 'completed', worker_id: 'cpu' }]);
  assert.equal(f.state.resourceRefreshJobs.size, 0);

  const job = { phase: 'terminal', workerIds: new Set(['cpu']), startedAt: Date.now() - 12001 };
  f.state.resourceRefreshJobs.set('job-a', job);
  f.context.setTimeout = () => 1;
  f.context.clearTimeout = () => {};
  f.context.settleResourceRefreshJobs([]);
  assert.equal(f.state.resourceRefreshJobs.size, 0);
});

for (const unavailable of ['offline', 'disabled', 'degraded', 'removed']) {
  test(`轮询后失效选择保留且阻止提交：${unavailable}`, async () => {
    const f = fixture(); f.$('trainingResourceSelect').value = 'cpu';
    if (unavailable === 'offline') f.workers[0].active = false;
    if (unavailable === 'disabled') f.workers[0].enabled = false;
    if (unavailable === 'degraded') f.workers[0].state = 'degraded';
    if (unavailable === 'removed') f.state.workerSlots = [f.workers[1]];
    f.context.renderTrainingResources(); f.context.updateStart();
    assert.equal(f.$('trainingResourceSelect').value, 'cpu');
    assert.equal(f.$('btnStart').disabled, true);
    await f.$('btnStart').onclick(); assert.equal(f.requests.length, 0);
    f.$('trainingResourceSelect').value = ''; f.context.updateStart();
    assert.equal(f.$('btnStart').disabled, false);
  });
}

test('已有成功快照时资源刷新失败仍可尝试提交，界面说明服务端会实时校验', async () => {
  const f = fixture(); f.$('trainingResourceSelect').value = 'cpu';
  f.state.workerResourcesError = true;
  f.context.renderTrainingResources(); f.context.updateStart();
  assert.equal(f.context.trainingResourceReady(), true);
  assert.equal(f.$('btnStart').disabled, false);
  assert.match(f.$('trainingResourceHint').textContent, /上次成功快照/);
  assert.match(f.$('trainingResourceHint').textContent, /服务端实时校验/);
  await f.$('btnStart').onclick();
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].payload.worker_id, 'cpu');
});

test('首次资源快照尚未成功时仍阻止指定资源提交', async () => {
  const f = fixture(); f.$('trainingResourceSelect').value = 'cpu';
  f.state.workerResourcesLoaded = false;
  f.state.workerResourcesError = true;
  f.context.renderTrainingResources(); f.context.updateStart();
  assert.equal(f.context.trainingResourceReady(), false);
  assert.equal(f.$('btnStart').disabled, true);
  await f.$('btnStart').onclick();
  assert.equal(f.requests.length, 0);
});

test('资源状态恢复后原选择可提交，忙碌槽明确排队', () => {
  const f = fixture(); f.$('trainingResourceSelect').value = 'cpu';
  f.workers[0].active = false; f.context.renderTrainingResources();
  f.workers[0].active = true; f.workers[0].state = 'running'; f.workers[0].current_job = 'busy';
  f.context.renderTrainingResources(); f.context.updateStart();
  assert.equal(f.$('trainingResourceSelect').value, 'cpu');
  assert.equal(f.$('btnStart').disabled, false);
  assert.match(f.$('trainingResourceSelect').innerHTML, /忙碌，排队等待/);
});

test('LLM、聚类与显存边界实时筛选，切换任务不会静默改选', () => {
  const f = fixture(); f.$('trainingResourceSelect').value = 'cpu';
  f.$('taskSelect').value = 'llm_sft'; f.context.updateTaskFields();
  assert.equal(f.context.trainingResourceReady(), false);
  f.$('trainingResourceSelect').value = 'gpu'; f.$('p_min_vram').value = '8192';
  assert.equal(f.context.trainingResourceReady(), true);
  f.$('p_min_vram').value = '8193'; f.$('p_min_vram').handlers.input();
  assert.equal(f.context.trainingResourceReady(), false);
  f.$('taskSelect').value = 'clustering'; f.context.updateTaskFields();
  assert.equal(f.context.trainingResourceReady(), false);
  assert.equal(f.$('trainingResourceSelect').value, 'gpu');
  f.$('trainingResourceSelect').value = 'cpu'; assert.equal(f.context.trainingResourceReady(), true);
});

test('联合训练隐藏单槽选择且不受旧选择失效阻塞', () => {
  const f = fixture(); f.$('trainingResourceSelect').value = 'gone';
  f.$('p_parallel').value = 'parallel'; f.context.renderTrainingResources();
  assert.equal(f.$('trainingResourceField').hidden, true);
  assert.equal(f.$('trainingResourceSelect').disabled, true);
  assert.equal(f.context.trainingResourceReady(), true);
});

test('提交时重复点击和轮询不产生重复任务，失败后保留选择', async () => {
  const f = fixture(); f.$('trainingResourceSelect').value = 'cpu'; let reject;
  f.context.apiJson = async (url, options) => { f.requests.push({ url, options }); return new Promise((_, fail) => { reject = fail; }); };
  const first = f.$('btnStart').onclick(); f.context.updateStart();
  assert.equal(f.$('btnStart').disabled, true);
  await f.$('btnStart').onclick(); assert.equal(f.requests.length, 1);
  reject(new Error('连接失败')); await first;
  assert.equal(f.$('trainingResourceSelect').value, 'cpu');
  assert.equal(f.$('btnStart').disabled, false);
});

test('资源标签转义不可信名称，任务展示指定位置与执行轨迹', () => {
  const f = fixture(); f.workers[0].machine_id = '<img src=x onerror=alert(1)>';
  f.context.renderTrainingResources();
  assert.doesNotMatch(f.$('trainingResourceSelect').innerHTML, /<img/);
  assert.match(f.$('trainingResourceSelect').innerHTML, /&lt;img/);
  const job = { status: 'queued', spec: { resource_hint: { worker_id: 'cpu' } }, segments: [] };
  assert.match(f.context.jobResourceLabel(job), /指定资源 · cpu/);
  f.context.renderResourceAllocation(job); assert.match(f.$('resourceAllocation').innerHTML, /等待指定槽位/);
  job.current_attempt = { worker_id: 'cpu' };
  assert.equal(f.context.jobResourceLabel(job), '资源 · cpu');
});

test('同一机器和槽位标签仍使用 worker 标识区分执行资源', () => {
  const f = fixture();
  Object.assign(f.workers[1], { machine_id: 'server-a', slot: 'cpu0', gpu: { available: false }, worker_id: 'cpu-other' });
  f.context.renderTrainingResources();
  assert.match(f.$('trainingResourceSelect').innerHTML, /server-a \/ cpu0 \(cpu\)/);
  assert.match(f.$('trainingResourceSelect').innerHTML, /server-a \/ cpu0 \(cpu-other\)/);
});

test('训练方式切换时布局样式不能覆盖资源面板的隐藏状态', () => {
  assert.match(html, /#trainingResourceField\[hidden\],\s*#parallelPanel\[hidden\]\s*\{\s*display:\s*none;?\s*\}/);
  const f = fixture();
  f.$('p_parallel').value = 'parallel'; f.context.renderTrainingResources();
  assert.equal(f.$('trainingResourceField').hidden, true);
  f.$('p_parallel').value = 'single'; f.context.renderTrainingResources();
  assert.equal(f.$('trainingResourceField').hidden, false);
});

for (const file of ['index.html', 'remote-management.js']) {
  for (const [raw, label] of [['starting', '准备中'], ['training', '训练中'], ['running', '训练中'], ['reserved', '已占用']]) {
    test(`${file} 将 ${raw} 显示为中文占用状态且停用接单不掩盖执行`, () => {
      const source = fs.readFileSync(path.join(__dirname, '../backend/static', file), 'utf8');
      const start = source.indexOf('const workerStateText =');
      const fn = source.indexOf('function workerSlotCard(');
      const context = vm.createContext({ esc: fixture().context.esc, formatMemory: () => '', workerResourceStats: () => '' });
      vm.runInContext(source.slice(start, source.indexOf('};', start) + 2)
        + '\n' + source.slice(fn, source.indexOf('\n// ', fn)), context);
      const worker = { worker_id: 'cpu', slot: 'cpu0', active: true, enabled: false,
        state: raw, occupied: true, current_job: '<job>', epoch: 1 };
      const rendered = context.workerSlotCard(worker);
      assert.match(rendered, new RegExp(`class="wstate running">${label}`));
      assert.match(rendered, /独占/);
      assert.match(rendered, /&lt;job&gt;/);
      worker.active = false;
      assert.match(context.workerSlotCard(worker), /class="wstate offline">离线/);
    });
  }
}

for (const state of ['starting', 'training', 'reserved']) {
  test(`资源选择器对 ${state} 显示排队，允许提交但不暗示可立即执行`, () => {
    const f = fixture();
    f.workers[0].state = state;
    f.workers[0].occupied = true;
    f.$('trainingResourceSelect').value = 'cpu';
    f.context.renderTrainingResources(); f.context.updateStart();
    assert.match(f.$('trainingResourceSelect').innerHTML, /忙碌，排队等待/);
    assert.equal(f.$('btnStart').disabled, false);
  });
}

// 用可控时间和 HTTP 边界执行真实刷新、状态协调及卡片渲染，避免依赖墙钟等待。
function refreshFixture() {
  const f = fixture(), pending = [], timers = new Map();
  let now = 1000, nextTimer = 0;
  Object.assign(f.state, { jobs: [], workerRequestVersion: 0, remoteServers: [],
    remoteOperations: new Map(), remoteOperationNotified: new Set() });
  Object.assign(f.context, {
    Date: class extends Date { static now() { return now; } }, AbortController,
    setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, { fn, at: now + delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    apiJson(url, options = {}) {
      return new Promise((resolve, reject) => {
        pending.push({ url, options, resolve, reject });
        options.signal?.addEventListener('abort', () => reject(new Error('timeout')), { once: true });
      });
    },
  });
  vm.runInContext(html.slice(html.indexOf('const terminalJobStatuses'), html.indexOf('// 只采纳最近一次任务列表响应')), f.context);
  vm.runInContext(html.slice(html.indexOf('const workerStateText ='), html.indexOf('// 训练页仅刷新资源状态')), f.context);
  vm.runInContext(html.slice(html.indexOf('function renderRetryControls('), html.indexOf('async function retryFailedJob(')), f.context);
  // 清空 HTTP Promise 回调队列，让测试能观察真实异步渲染结果。
  async function flush() { for (let i = 0; i < 15; i++) await Promise.resolve(); }
  // 按截止时间推进定时器，覆盖重复轮询、取消和超时清理。
  async function advance(ms) {
    const target = now + ms;
    while (true) {
      const entry = [...timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
      if (!entry) break;
      const [id, timer] = entry; timers.delete(id); now = timer.at; timer.fn(); await flush();
    }
    now = target; await flush();
  }
  return { ...f, pending, timers, flush, advance };
}

// 构造服务端权威槽位快照，断言页面不会自行改写占用和当前任务。
function resourceSnapshot(currentJob, occupied = Boolean(currentJob), state = currentJob ? 'training' : 'idle') {
  return { queue_depth: 0, workers: [{ worker_id: 'cpu', machine_id: 'machine', slot: 'cpu0',
    active: true, enabled: true, occupied, current_job: currentJob, state }] };
}

test('快速轮询真实更新训练和释放卡片，收敛后清除定时器且只请求 worker', async () => {
  const f = refreshFixture();
  f.context.requestTaskResourceRefresh('a', 'starting', ['cpu']);
  assert.equal(f.pending.length, 1);
  assert.equal(f.pending[0].options.cache, 'no-store');
  f.pending[0].resolve(resourceSnapshot(null)); await f.flush();
  assert.equal(f.state.resourceRefreshJobs.size, 1);
  await f.advance(500);
  f.pending[1].resolve(resourceSnapshot('a')); await f.flush();
  assert.match(f.$('workerGrid').innerHTML, /训练中/);
  assert.equal(f.state.resourceRefreshJobs.size, 0);
  assert.equal(f.timers.size, 0);
  f.context.requestTaskResourceRefresh('a', 'terminal', ['cpu']);
  f.pending[2].resolve(resourceSnapshot('a')); await f.flush();
  assert.match(f.$('workerGrid').innerHTML, /训练中/);
  await f.advance(500);
  f.pending[3].resolve(resourceSnapshot(null)); await f.flush();
  assert.match(f.$('workerGrid').innerHTML, /空闲/);
  assert.equal(f.state.resourceRefreshJobs.size, 0);
  assert.equal(f.timers.size, 0);
  assert.ok(f.pending.every(request => request.url === '/api/workers'));
});

test('常规慢请求返回的旧快照不能结束刚发起的终态协调', async () => {
  const f = refreshFixture();
  f.context.refreshWorkers({ resourcesOnly: true });
  f.context.requestTaskResourceRefresh('a', 'terminal', ['cpu']);
  assert.equal(f.pending.length, 1, '慢请求期间不并发请求资源');
  f.pending[0].resolve(resourceSnapshot(null)); await f.flush();
  assert.equal(f.state.resourceRefreshJobs.size, 1);
  await f.advance(500);
  f.pending[1].resolve(resourceSnapshot('a')); await f.flush();
  assert.equal(f.state.resourceRefreshJobs.size, 1);
  await f.advance(500);
  f.pending[2].resolve(resourceSnapshot('b')); await f.flush();
  assert.equal(f.state.resourceRefreshJobs.size, 0);
  assert.equal(f.state.workerSlots[0].current_job, 'b');
  assert.match(f.$('workerGrid').innerHTML, /训练中/);
});

test('远程目录慢响应不阻塞资源渲染，迟到目录采用最新资源快照', async () => {
  const f = refreshFixture();
  f.context.refreshWorkers();
  assert.deepEqual(f.pending.map(request => request.url), ['/api/remote-servers', '/api/workers']);
  f.pending[1].resolve(resourceSnapshot('a')); await f.flush();
  assert.match(f.$('workerGrid').innerHTML, /训练中/);
  f.context.requestTaskResourceRefresh('a', 'terminal', ['cpu']);
  f.pending[2].resolve(resourceSnapshot(null)); await f.flush();
  f.pending[0].resolve({ servers: [] }); await f.flush();
  assert.match(f.$('workerGrid').innerHTML, /空闲/);
  assert.doesNotMatch(f.$('workerGrid').innerHTML, /训练中/);
});

for (const failure of ['error', 'timeout']) {
  test(`资源请求 ${failure} 保留卡片和选择，有限重试结束后手动刷新可恢复`, async () => {
    const f = refreshFixture();
    f.context.refreshWorkers({ resourcesOnly: true });
    f.pending[0].resolve(resourceSnapshot('a')); await f.flush();
    const previousCard = f.$('workerGrid').innerHTML;
    f.$('trainingResourceSelect').value = 'cpu';
    f.context.requestTaskResourceRefresh('a', 'terminal', ['cpu']);
    if (failure === 'error') { f.pending[1].reject(new Error('offline')); await f.flush(); }
    else await f.advance(5000);
    assert.equal(f.state.workerResourcesError, true);
    assert.equal(f.$('workerGrid').innerHTML, previousCard);
    assert.equal(f.$('trainingResourceSelect').value, 'cpu');
    assert.equal(f.state.resourceRefreshJobs.size, 1, '错误不能让旧快照确认释放');
    await f.advance(20000);
    assert.equal(f.state.resourceRefreshJobs.size, 0);
    assert.equal(f.state.refreshingWorkers, false);
    assert.equal(f.timers.size, 0);
    f.context.refreshWorkers({ resourcesOnly: true });
    f.pending.at(-1).resolve(resourceSnapshot(null)); await f.flush();
    assert.equal(f.state.workerResourcesError, false);
    assert.match(f.$('workerGrid').innerHTML, /空闲/);
  });
}

test('排队超过上限不再快速轮询；联合训练全部槽报告任务后才停止', async () => {
  const f = refreshFixture();
  f.context.requestTaskResourceRefresh('a', 'starting', ['cpu', 'gpu']);
  const one = resourceSnapshot('a');
  f.pending[0].resolve(one); await f.flush();
  assert.equal(f.state.resourceRefreshJobs.size, 1);
  await f.advance(500);
  f.pending[1].resolve({ workers: [...one.workers, { ...one.workers[0], worker_id: 'gpu' }] }); await f.flush();
  assert.equal(f.state.resourceRefreshJobs.size, 0);
  f.context.requestTaskResourceRefresh('queued', 'starting');
  for (let i = 0; i < 24; i++) {
    f.pending.at(-1).resolve(resourceSnapshot(null)); await f.flush(); await f.advance(500);
  }
  assert.equal(f.state.resourceRefreshJobs.size, 0);
  assert.equal(f.timers.size, 0);
  assert.match(f.$('workerGrid').innerHTML, /空闲/);
});

for (const status of ['completed', 'failed', 'cancelled', 'paused']) {
  test(`刚提交的短任务首次列出就是 ${status} 时仍协调资源；恢复后重新协调启动`, async () => {
    const f = refreshFixture();
    f.context.requestTaskResourceRefresh('a', 'starting');
    f.pending[0].resolve(resourceSnapshot(null)); await f.flush();
    f.context.observeResourceJobTransitions([{ id: 'a', status }]);
    assert.equal(f.state.resourceRefreshJobs.get('a').phase, 'terminal');
    f.pending[1].resolve(resourceSnapshot('a')); await f.flush();
    f.state.jobs = [{ id: 'a', status }];
    f.context.observeResourceJobTransitions([{ id: 'a', status: 'running', worker_id: 'cpu' }]);
    assert.equal(f.state.resourceRefreshJobs.get('a').phase, 'starting');
    f.pending[2].resolve(resourceSnapshot('a')); await f.flush();
    assert.equal(f.state.resourceRefreshJobs.size, 0);
  });
}
