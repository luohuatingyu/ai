'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../backend/static/index.html'), 'utf8');

// 只隔离 DOM 和网络，运行实际页面的资源选择、校验和请求构造逻辑。
function fixture(task = 'classification', mode = 'parallel') {
  const nodes = new Map();
  for (const match of html.matchAll(/\bid="([^"]+)"[^>]*>/g)) {
    const id = match[1];
    nodes.set(id, { value: /\bvalue="([^"]*)"/.exec(match[0])?.[1] || '', checked: false,
      hidden: false, disabled: false, dataset: {}, textContent: '', innerHTML: '',
      handlers: {}, addEventListener(event, fn) { this.handlers[event] = fn; },
      contains(element) { return element === this; }, blur() {} });
  }
  nodes.get('taskSelect').value = task;
  nodes.get('p_parallel').value = mode;
  nodes.get('p_dataset_source').value = 'hub';
  nodes.get('p_hub_dataset').value = 'example/corpus';
  nodes.get('p_base_model').value = 'example/model';
  nodes.get('p_reward_model').value = 'example/reward';
  nodes.get('targetSelect').value = 'target';
  nodes.get('timeColumnSelect').value = 'time';
  nodes.get('p_require_gpu').checked = task.startsWith('llm_');
  nodes.get('p_min_vram').value = '8192';
  const workers = ['gpu', 'cpu'].map((worker_id) => ({ worker_id, machine_id: worker_id,
    active: true, enabled: true, parallel: { host: '10.0.0.1', protocol: 1, gloo: true } }));
  const requests = [];
  const state = { datasetId: 'dataset', workerSlots: workers };
  const context = vm.createContext({ document: { activeElement: null },
    $: (id) => nodes.get(id), state,
    esc: (value) => String(value).replaceAll('<', '&lt;'), toast() {}, selectJob() {},
    apiJson: async (url, options) => { requests.push({ url, payload: JSON.parse(options.body) }); return { job_id: 'joint' }; },
  });
  const start = html.slice(html.indexOf('function updateStart()'), html.indexOf('/* 上传 */'));
  const submit = html.slice(html.indexOf('const taskSelect ='), html.indexOf('const statusText ='));
  vm.runInContext(start + '\n' + submit, context);
  const selections = vm.runInContext('parallelSelections', context);
  selections.set('gpu', 3); selections.set('cpu', 1);
  return { context, nodes, requests, workers, selections, state };
}

for (const task of ['classification', 'regression', 'llm_pretraining', 'llm_sft', 'llm_dpo', 'llm_ppo']) {
  test(`联合训练提交包含 CPU 与各节点批大小：${task}`, async () => {
    const f = fixture(task);
    await f.nodes.get('btnStart').onclick();
    assert.equal(f.requests.length, 1);
    const { payload } = f.requests[0];
    assert.deepEqual(payload.parallel_workers, [{ worker_id: 'gpu', batch_size: 3 }, { worker_id: 'cpu', batch_size: 1 }]);
    assert.equal(payload.parallel_timeout, 300);
    assert.equal(payload.require_gpu, false);
    assert.equal(payload.min_vram_mb, 0);
  });
}

test('单节点 LLM 保留 GPU 默认要求，聚类强制回到单节点', async () => {
  const f = fixture('llm_sft', 'single');
  await f.nodes.get('btnStart').onclick();
  assert.equal(f.requests[0].payload.require_gpu, true);
  assert.equal(f.requests[0].payload.parallel_workers, undefined);
  f.nodes.get('taskSelect').value = 'clustering';
  f.nodes.get('p_parallel').value = 'parallel';
  f.context.updateTaskFields();
  assert.equal(f.nodes.get('p_parallel').value, 'single');
  assert.equal(f.nodes.get('parallelPanel').hidden, true);
});

test('节点掉线、停用、未配置通信能力或批大小非法时禁止提交', () => {
  const f = fixture();
  f.context.updateStart();
  assert.equal(f.nodes.get('btnStart').disabled, false);
  for (const key of ['active', 'enabled']) {
    f.workers[1][key] = false; f.context.updateStart();
    assert.equal(f.nodes.get('btnStart').disabled, true);
    f.workers[1][key] = true;
  }
  for (const value of [0, 4097, NaN, 1.5]) {
    f.selections.set('cpu', value); f.context.updateStart();
    assert.equal(f.nodes.get('btnStart').disabled, true);
  }
  f.selections.set('cpu', 1);
  f.workers[1].parallel.host = ''; f.context.updateStart();
  assert.equal(f.nodes.get('btnStart').disabled, true);
  f.workers[1].parallel.host = '10.0.0.1';
  f.workers[1].parallel.gloo = false; f.context.updateStart();
  assert.equal(f.nodes.get('btnStart').disabled, true);
});

test('本机 Compose 计算槽可参与联合训练，缺失远端地址时给出配置入口', () => {
  const f = fixture();
  f.state.workerSlots = [
    { worker_id: 'local-cpu0', machine_id: 'local', slot: 'cpu0', active: true, enabled: true,
      parallel: { host: 'worker', protocol: 1, gloo: true } },
    { worker_id: 'local-cpu1', machine_id: 'local', slot: 'cpu1', active: true, enabled: true,
      parallel: { host: 'worker', protocol: 1, gloo: true } },
    { worker_id: 'remote-cpu0', machine_id: 'remote', slot: 'cpu0', active: true, enabled: true,
      parallel: { host: '', protocol: 1, gloo: true } },
  ];

  f.context.renderParallelWorkers();
  const markup = f.nodes.get('parallelWorkers').innerHTML;
  assert.match(markup, /data-parallel-worker="local-cpu0"[^>]*>/);
  assert.match(markup, /data-parallel-worker="local-cpu1"[^>]*>/);
  assert.match(markup, /data-parallel-worker="remote-cpu0"[^>]*disabled/);
  assert.match(markup, /href="\/server-manager\.html"/);
  assert.match(markup, /缺少可达的联合训练互联地址/);

  f.selections.clear();
  f.selections.set('local-cpu0', 2);
  f.selections.set('local-cpu1', 1);
  f.context.updateStart();
  assert.equal(f.nodes.get('btnStart').disabled, false);
  return f.nodes.get('btnStart').onclick().then(() => {
    assert.deepEqual(f.requests[0].payload.parallel_workers, [
      { worker_id: 'local-cpu0', batch_size: 2 },
      { worker_id: 'local-cpu1', batch_size: 1 },
    ]);
  });
});

test('LLM 限制批大小为 64；编辑中仍更新总批大小且不重建输入框', () => {
  const f = fixture('llm_sft');
  f.selections.set('cpu', 65); f.context.updateStart();
  assert.equal(f.nodes.get('btnStart').disabled, true);
  f.selections.set('cpu', 2);
  f.context.document.activeElement = f.nodes.get('parallelWorkers');
  f.nodes.get('parallelWorkers').innerHTML = 'editing';
  f.context.renderParallelWorkers();
  assert.equal(f.nodes.get('parallelWorkers').innerHTML, 'editing');
  assert.match(f.nodes.get('parallelSummary').textContent, /全局批大小 5/);
});
