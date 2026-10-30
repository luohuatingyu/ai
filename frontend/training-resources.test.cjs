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
  const state = { datasetId: 'dataset', workerSlots: workers, workerResourcesLoaded: true, workerResourcesError: false, trainSubmitting: false };
  const requests = [];
  const context = vm.createContext({ $: node, state, document, console,
    esc: value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])),
    toast() {}, selectJob() {}, fmtTime: String, resourceText: value => value.worker_id,
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
  return { context, state, workers, requests, $: node };
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

for (const unavailable of ['offline', 'disabled', 'degraded', 'removed', 'fetch-failed']) {
  test(`轮询后失效选择保留且阻止提交：${unavailable}`, async () => {
    const f = fixture(); f.$('trainingResourceSelect').value = 'cpu';
    if (unavailable === 'offline') f.workers[0].active = false;
    if (unavailable === 'disabled') f.workers[0].enabled = false;
    if (unavailable === 'degraded') f.workers[0].state = 'degraded';
    if (unavailable === 'removed') f.state.workerSlots = [f.workers[1]];
    if (unavailable === 'fetch-failed') f.state.workerResourcesError = true;
    f.context.renderTrainingResources(); f.context.updateStart();
    assert.equal(f.$('trainingResourceSelect').value, 'cpu');
    assert.equal(f.$('btnStart').disabled, true);
    await f.$('btnStart').onclick(); assert.equal(f.requests.length, 0);
    f.$('trainingResourceSelect').value = ''; f.context.updateStart();
    assert.equal(f.$('btnStart').disabled, false);
  });
}

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
