'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const html = fs.readFileSync(path.join(__dirname, '../backend/static/index.html'), 'utf8');

// 只模拟 DOM 和网络边界，校验、任务状态、请求载荷与响应处理执行真实页面函数。
function fixture() {
  const nodes = new Map();
  const document = { activeElement: null, handlers: {}, body: { classList: { add() {}, remove() {} } },
    addEventListener(name, fn) { this.handlers[name] = fn; } };
  function element(id = '') {
    const node = { id, value: '', textContent: '', hidden: false, disabled: false, checked: false,
      dataset: {}, attributes: {}, children: [], style: {}, handlers: {}, open: false,
      scrollTop: 0, scrollHeight: 500, clientHeight: 100, className: '', markup: '',
      focus() { document.activeElement = this; }, scrollIntoView() {}, blur() {},
      setAttribute(k, v) { this.attributes[k] = String(v); }, removeAttribute(k) { delete this.attributes[k]; },
      addEventListener(k, fn) { this.handlers[k] = fn; },
      contains(el) { return el === this || this.children.includes(el); },
      closest(selector) { return selector === 'details' ? this.details || null : null; },
      querySelector(selector) { return this.children[0] || element(); },
      querySelectorAll() { return this.children; },
      appendChild(child) { return this.insertBefore(child, null); },
      insertBefore(child, reference) {
        this.children = this.children.filter(item => item !== child);
        const index = reference ? this.children.indexOf(reference) : this.children.length;
        this.children.splice(index, 0, child); child.parentElement = this; return child;
      },
      remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(item => item !== this); },
      replaceChildren(...children) { this.children = []; children.forEach(child => this.appendChild(child)); },
    };
    node.classList = { toggle(name, enabled) { const names = new Set(node.className.split(' ')); enabled ? names.add(name) : names.delete(name); node.className = [...names].join(' '); }, add() {}, remove() {} };
    Object.defineProperty(node, 'innerHTML', { get: () => node.markup, set(value) {
      node.markup = value;
      const match = /^<div class="(jobcard[^"]*)"[^>]*data-id="([^"]*)"[^>]*>([\s\S]*)<\/div>$/.exec(value.trim());
      node.children = [];
      if (match) { const card = element(); card.className = match[1]; card.dataset.id = match[2]; card.markup = match[3]; node.appendChild(card); }
    } });
    Object.defineProperty(node, 'firstElementChild', { get: () => node.children[0] });
    return node;
  }
  // 根据真实页面读取默认值，避免测试自行复制业务默认参数。
  for (const match of html.matchAll(/\bid="([^"]+)"[^>]*>/g)) {
    const node = element(match[1]);
    node.value = /\bvalue="([^"]*)"/.exec(match[0])?.[1] || '';
    nodes.set(node.id, node);
  }
  const $ = id => { if (!nodes.has(id)) nodes.set(id, element(id)); return nodes.get(id); };
  const tabs = ['curves', 'record', 'logs', 'result', 'predict'].map(name => { const node = element('detail-tab-' + name); node.dataset.tab = name; nodes.set(node.id, node); return node; });
  document.querySelectorAll = () => tabs;
  document.createElement = () => element();
  $('taskSelect').value = 'classification'; $('targetSelect').value = 'target';
  $('timeColumnSelect').value = 'time'; $('p_parallel').value = 'single'; $('p_dataset_source').value = 'local';
  $('jobStatusFilter').value = 'all'; $('logFollow').checked = true;
  const state = { datasetId: 'dataset', datasets: [], jobs: [], workerSlots: [], jobId: null, logOffset: 0,
    activeTab: 'curves', trainSubmitting: false, detailRequest: 0, jobsRequest: 0, logRequest: 0,
    selectionVersion: 0, logVersion: 0, predictTemplateRequest: 0, logUnread: 0 };
  const requests = [];
  const context = vm.createContext({ $, state, document, window: {}, console,
    esc: value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    taskName: task => task, fmtLoss: String, fmtTime: String, jobResourceLabel: () => '', resourceText: () => '',
    toast() {}, renderResourceAllocation() {}, renderRetryControls() {}, renderCharts() {}, renderResult() {},
    retryFailedJob() {}, confirm: () => false, setTimeout: () => 1, clearTimeout() {}, location: {},
    apiJson: async (url, options) => { requests.push({ url, options }); return { job_id: 'created' }; },
  });
  // 按稳定的页面区段加载函数；不执行初始化轮询，也不替代被测业务逻辑。
  const sections = [
    ['function performanceNumber(', 'function renderCharts('],
    ['function updateStart()', '/* 上传 */'],
    ['const taskSelect =', '/* ---------- 图表 ---------- */'],
    ['/* ---------- 日志 ---------- */', '/* ---------- 结果 ---------- */'],
    ['async function fillPredictTemplate()', '$("btnPredict").onclick'],
    ['/* ---------- Tab 切换 ---------- */', '/* ---------- 启动 ---------- */'],
  ];
  for (const [start, end] of sections) vm.runInContext(html.slice(html.indexOf(start), html.indexOf(end)), context);
  return { context, nodes, $, state, requests, document, tabs };
}

// 可控响应顺序用于复现重复提交、快速切换和轮询竞争。
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }

// 提供最小完整任务记录，断言具体任务名称、状态与进度。
function job(id, status = 'running', name = id) {
  return { id, status, spec: { task: 'classification', target: 'target', dataset_id: 'dataset', dataset_name: name, params: { epochs: 30 } }, progress: { percent: 20 }, metrics: [] };
}

test('提交期间轮询不能解锁按钮，重复点击只创建一个任务', async () => {
  const f = fixture(), pending = deferred();
  f.context.apiJson = async (url, options) => { f.requests.push({ url, options }); return pending.promise; };
  const first = f.$('btnStart').onclick();
  f.context.updateStart();
  assert.equal(f.$('btnStart').disabled, true);
  const second = f.$('btnStart').onclick();
  assert.equal(f.requests.length, 1);
  pending.reject(new Error('离线')); await Promise.all([first, second]);
  assert.equal(f.$('btnStart').disabled, false);
  assert.equal(f.$('p_epochs').value, '30');
  assert.match(f.$('trainFeedback').textContent, /离线/);
});

for (const [field, value] of [['p_epochs', ''], ['p_epochs', '0'], ['p_epochs', '501'], ['p_epochs', '1.5'], ['p_lr', 'NaN'], ['p_lr', '1.1'], ['p_test_split', '0.01']]) {
  test(`非法参数不创建任务并定位字段：${field}=${value}`, async () => {
    const f = fixture(); f.$(field).value = value;
    await f.$('btnStart').onclick();
    assert.equal(f.requests.length, 0);
    assert.equal(f.document.activeElement, f.$(field));
    assert.equal(f.$(field).attributes['aria-invalid'], 'true');
  });
}

test('缺少数据集时说明原因；合法边界及聚类隐藏字段不阻塞', () => {
  const f = fixture(); f.state.datasetId = null; f.context.updateStart();
  assert.equal(f.$('btnStart').disabled, true); assert.match(f.$('trainFeedback').textContent, /数据集/);
  f.state.datasetId = 'dataset'; f.$('p_epochs').value = '500'; f.$('p_hidden_layers').value = '0';
  assert.equal(f.context.getTrainingIssue(), null);
  f.$('taskSelect').value = 'clustering'; f.$('targetSelect').value = ''; f.$('p_hidden_size').value = '';
  assert.equal(f.context.getTrainingIssue(), null);
});

test('高级参数错误展开后定位，修正后恢复就绪', async () => {
  const f = fixture(); f.$('p_hidden_size').value = '0'; f.$('p_hidden_size').details = f.$('trainingAdvanced');
  await f.$('btnStart').onclick(); assert.equal(f.$('trainingAdvanced').open, true);
  f.$('p_hidden_size').value = '128'; f.context.updateStart();
  assert.equal(f.$('btnStart').disabled, false);
});

test('LLM 预训练注意力维度不整除时阻止提交', async () => {
  const f = fixture(); f.$('taskSelect').value = 'llm_pretraining'; f.$('p_model_heads').value = '3';
  await f.$('btnStart').onclick(); assert.equal(f.requests.length, 0);
  assert.match(f.$('trainFeedback').textContent, /整除/);
});

test('搜索和状态筛选组合在完整训练弹窗中工作，支持空列表和恶意名称转义', () => {
  const f = fixture(); f.state.jobs = [job('a', 'running', '<img src=x>'), job('b', 'completed', '销售数据')];
  f.$('jobSearch').value = '销售'; f.$('jobStatusFilter').value = 'completed';
  f.context.renderJobList(); assert.equal(f.$('jobList').children.length, 0);
  assert.equal(f.$('allJobList').children.length, 1); assert.equal(f.$('allJobList').children[0].dataset.id, 'b');
  f.$('jobStatusFilter').value = 'running'; f.context.renderJobList(); assert.match(f.$('allJobList').innerHTML, /没有匹配/);
  f.$('jobSearch').value = ''; f.context.renderJobList(); assert.match(f.$('allJobList').children[0].innerHTML, /&lt;img/);
  f.state.jobs = []; f.context.renderJobList(); assert.match(f.$('allJobList').innerHTML, /暂无训练记录/);
});

test('首次聚焦活跃任务，历史弹窗保留完整卡片并复用焦点', () => {
  const f = fixture(); f.state.jobs = [job('a'), job('b')]; f.state.jobId = 'b';
  assert.equal(f.context.preferredJob(f.state.jobs, null).id, 'a');
  f.state.jobs[0].status = 'completed';
  assert.equal(f.context.preferredJob(f.state.jobs, null).id, 'b');
  assert.equal(f.context.preferredJob(f.state.jobs, 'a').id, 'a');
  f.context.renderJobList(); assert.equal(f.$('jobList').children.length, 1);
  assert.equal(f.$('jobList').children[0].dataset.id, 'b');
  const card = f.$('allJobList').children[1]; card.focus();
  f.state.jobs[1].progress.percent = 60; f.context.renderJobList();
  assert.equal(f.$('allJobList').children[1], card); assert.equal(f.document.activeElement, card);
  assert.equal(card.attributes['aria-pressed'], 'true'); assert.match(card.innerHTML, /60%/);
});

test('历史选择关闭弹窗并切换当前训练，焦点状态和入口数量同步', () => {
  const f = fixture(); f.state.jobs = [job('a', 'completed'), job('b', 'running')];
  f.context.fillPredictTemplate = () => {}; f.context.loadDetail = () => {};
  f.context.openJobsModal();
  assert.equal(f.$('jobsModal').hidden, false); assert.equal(f.$('jobSearch').focus && f.document.activeElement, f.$('jobSearch'));
  assert.equal(f.$('allJobCount').textContent, '2');
  f.$('allJobList').children[0].onclick();
  assert.equal(f.state.jobId, 'a'); assert.equal(f.$('jobsModal').hidden, true);
  assert.equal(f.$('jobList').children[0].dataset.id, 'a');
  assert.equal(f.$('btnOpenJobs').attributes['aria-expanded'], 'false');
});

test('Escape 关闭历史弹窗并恢复入口焦点', () => {
  const f = fixture(); f.context.openJobsModal();
  f.document.handlers.keydown({ key: 'Escape' });
  assert.equal(f.$('jobsModal').hidden, true);
  assert.equal(f.document.activeElement, f.$('btnOpenJobs'));
});

test('训练记录展示配置、完整指标、性能和跨节点执行段，并转义异常文本', () => {
  const f = fixture();
  f.context.renderTrainingRecord({
    ...job('record', 'failed'),
    spec: { task: 'regression', target: '<script>x</script>', dataset_name: 'sales', params: { epochs: 2, nested: { seed: 7 } }, resource_hint: { require_gpu: true } },
    created_at: 10, started_at: 11, finished_at: 15, error: '<img src=x>',
    segments: [{ status: 'completed', from_epoch: 1, to_epoch: 2, worker_id: 'worker-a', started_at: 11, finished_at: 15 }],
    metrics: [{ epoch: 1, train_loss: 0.7, val_r2: 0.2 }, { epoch: 2, train_loss: 0.3, val_r2: 0.8 }],
    performance: [{ epoch: 1, samples_per_second: 120, train_seconds: 0.2 }],
    summary: { duration_sec: 4, final: { val_r2: 0.8 } },
  });
  const record = f.$('trainingRecord').innerHTML;
  for (const value of ['任务 ID', '开始时间', '嵌套', 'worker-a', 'val_r2', 'samples_per_second', '训练结果摘要']) {
    if (value === '嵌套') assert.match(record, /nested/);
    else assert.match(record, new RegExp(value));
  }
  assert.match(record, /&lt;script&gt;/); assert.match(record, /&lt;img src=x&gt;/);
  assert.doesNotMatch(record, /<script>x/); assert.doesNotMatch(record, /<img src=x>/);
});

test('旧任务缺少时间、性能和执行段时记录页提供明确空状态', () => {
  const f = fixture(), legacy = job('legacy', 'completed');
  legacy.spec = { task: 'classification', target: 'target', dataset_name: 'legacy' };
  f.context.renderTrainingRecord(legacy);
  const record = f.$('trainingRecord').innerHTML;
  assert.match(record, /暂无执行记录/); assert.match(record, /暂无逐轮指标/);
  assert.match(record, /没有性能采集记录/); assert.match(record, /该任务未保存参数/);
});

for (const failure of [false, true]) test(`切换后旧详情${failure ? '失败' : '成功'}响应不污染当前任务`, async () => {
  const f = fixture(), pending = deferred(); f.state.jobId = 'a'; f.context.apiJson = () => pending.promise;
  const old = f.context.loadDetail(); f.state.jobId = 'b'; f.$('dName').textContent = '当前 B'; f.$('detail').hidden = false;
  failure ? pending.reject(new Error('网络断开')) : pending.resolve(job('a'));
  await old; assert.equal(f.state.jobId, 'b'); assert.equal(f.$('dName').textContent, '当前 B'); assert.equal(f.$('detail').hidden, false);
});

test('同一任务乱序详情只应用最新响应；当前网络错误保留已有内容并提示', async () => {
  const f = fixture(), first = deferred(), second = deferred(); f.state.jobId = 'a';
  let count = 0; f.context.apiJson = () => (++count === 1 ? first.promise : second.promise);
  const a = f.context.loadDetail(), b = f.context.loadDetail(); second.resolve(job('a', 'completed', '最新')); await b;
  first.resolve(job('a', 'running', '旧')); await a; assert.match(f.$('dName').textContent, /最新/);
  f.context.apiJson = async () => { throw new Error('离线'); }; await f.context.loadDetail();
  assert.equal(f.state.jobId, 'a'); assert.match(f.$('detailFeedback').textContent, /离线/);
});

test('暂停日志跟随保留阅读位置，回到最新恢复跟随并清除未读', async () => {
  const f = fixture(); f.state.jobId = 'a'; f.$('logFollow').checked = false; f.$('logBox').scrollTop = 15;
  f.context.apiJson = async () => ({ lines: ['epoch 1'], next_offset: 1 }); await f.context.tailLogs('a');
  assert.equal(f.$('logBox').scrollTop, 15); assert.match(f.$('logBox').textContent, /epoch 1/);
  assert.match(f.$('logStatus').textContent, /1/); f.context.followLatestLogs();
  assert.equal(f.$('logFollow').checked, true); assert.equal(f.$('logBox').scrollTop, 500); assert.equal(f.state.logUnread, 0);
});

test('同游标并发日志不重复追加；切换任务后的旧日志被丢弃', async () => {
  const f = fixture(), a = deferred(); f.state.jobId = 'a'; let count = 0;
  f.context.apiJson = () => (++count === 1 ? a.promise : Promise.resolve({ lines: ['duplicate'], next_offset: 1 }));
  const p1 = f.context.tailLogs('a'), p2 = f.context.tailLogs('a'); a.resolve({ lines: ['one'], next_offset: 1 });
  await Promise.all([p1, p2]); assert.equal(count, 1); assert.equal(f.$('logBox').textContent, 'one\n');
  const old = deferred(); f.context.apiJson = () => old.promise; const pending = f.context.tailLogs('a');
  f.state.jobId = 'b'; old.resolve({ lines: ['stale'], next_offset: 2 }); await pending; assert.doesNotMatch(f.$('logBox').textContent, /stale/);
});

test('日志轮询明确绕过缓存并避免慢请求重叠', async () => {
  const f = fixture(), pending = deferred(); f.state.jobId = 'a';
  let count = 0, requestOptions;
  f.context.apiJson = (url, options) => { count++; requestOptions = options; return pending.promise; };
  const first = f.context.tailLogs('a');
  const second = f.context.tailLogs('a');
  assert.equal(count, 1);
  assert.equal(requestOptions.cache, 'no-store');
  pending.resolve({ lines: [], next_offset: 0 });
  await Promise.all([first, second]);
  assert.equal(f.state.logLoading, null);
});

test('旧任务日志请求挂起时，新选择仍可继续轮询', async () => {
  const f = fixture(), old = deferred(); f.state.jobId = 'a'; let count = 0;
  f.context.apiJson = url => {
    count++;
    return url.includes('/a/') ? old.promise : Promise.resolve({ lines: ['B epoch 1'], next_offset: 1 });
  };
  const pending = f.context.tailLogs('a');
  f.state.jobId = 'b'; f.state.logVersion++;
  await f.context.tailLogs('b');
  assert.equal(count, 2);
  assert.equal(f.$('logBox').textContent, 'B epoch 1\n');
  old.resolve({ lines: ['stale A'], next_offset: 1 });
  await pending;
  assert.doesNotMatch(f.$('logBox').textContent, /stale A/);
});

test('启动时有独立日志定时器，不依赖详情请求完成', () => {
  assert.match(html, /setInterval\(\(\) => \{ if \(state\.jobId && state\.activeTab === "logs"\) tailLogs\(state\.jobId\); \}, 1000\)/);
});

test('预测模板的旧响应不覆盖新任务或用户正在编辑的内容', async () => {
  const f = fixture(), pending = deferred(); f.state.jobs = [job('a'), job('b')]; f.state.jobId = 'a';
  f.context.apiJson = () => pending.promise; const old = f.context.fillPredictTemplate();
  f.state.jobId = 'b'; f.$('predictInput').value = '{"mine":1}'; pending.resolve({ preview: [{ value: 2 }] }); await old;
  assert.equal(f.$('predictInput').value, '{"mine":1}');
});

test('方向键、Home、End 切换标签并同步焦点及无障碍状态', () => {
  const f = fixture(); let prevented = 0;
  f.tabs[0].handlers.keydown({ key: 'ArrowRight', preventDefault() { prevented++; } });
  assert.equal(f.state.activeTab, 'record'); assert.equal(f.document.activeElement, f.tabs[1]);
  assert.equal(f.tabs[1].attributes['aria-selected'], 'true'); assert.equal(f.$('tab-record').hidden, false);
  f.tabs[1].handlers.keydown({ key: 'End', preventDefault() {} }); assert.equal(f.state.activeTab, 'predict');
  f.tabs[4].handlers.keydown({ key: 'Home', preventDefault() {} }); assert.equal(f.state.activeTab, 'curves'); assert.equal(prevented, 1);
});
