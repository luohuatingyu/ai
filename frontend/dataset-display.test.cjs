'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../backend/static/dataset-manager.html'), 'utf8');

// 仅替代浏览器 DOM 和 HTTP 边界，执行页面实际的渲染、筛选、异步及焦点逻辑。
function fixture() {
  const nodes = new Map(), requests = [];
  const document = { activeElement: null, addEventListener() {} };
  function element(id = '') {
    const classes = new Set();
    const node = { id, innerHTML: '', textContent: '', value: '', hidden: false, disabled: false,
      isConnected: true, children: [], dataset: {}, attributes: {},
      focus() { document.activeElement = node; },
      appendChild(child) { this.children.push(child); return child; },
      replaceChildren(...children) { this.children = children; this.innerHTML = ''; },
      setAttribute(key, value) { this.attributes[key] = value; },
      addEventListener() {}, remove() {},
      getClientRects() { return this.hidden ? [] : [{}]; },
      contains(child) { return child === this || this.children.includes(child); },
      querySelector() { return this.children[0] || null; },
      querySelectorAll() { return this.children; },
      classList: { add(name) { classes.add(name); }, remove(name) { classes.delete(name); }, contains(name) { return classes.has(name); } },
    };
    return node;
  }
  for (const match of html.split('<script>')[0].matchAll(/\bid="([^"]+)"/g)) nodes.set(match[1], element(match[1]));
  document.body = element('body');
  document.createElement = () => element();
  document.getElementById = id => nodes.get(id);
  document.querySelector = () => [...nodes.values()].find(node => node.classList.contains('is-open')) || null;
  document.querySelectorAll = () => [...nodes.values()].filter(node => node.classList.contains('is-open'));
  for (const id of ['datasetModal', 'processingModal']) nodes.get(id).children.push(element(id + '-close'));
  const reply = { list: [], detail: {}, error: null, pending: null };
  const context = vm.createContext({ document, window: { setTimeout() {}, confirm: () => false }, console,
    fetch: async (url, options = {}) => {
      requests.push({ url, ...options });
      if (reply.pending && url === '/api/datasets/a') return reply.pending;
      if (reply.error && url === '/api/datasets/a') return { ok: false, status: 503, json: async () => ({ detail: reply.error }) };
      return { ok: true, json: async () => url === '/api/datasets' ? { total: reply.list.length, datasets: reply.list } : reply.detail };
    },
  });
  vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], context);
  vm.runInContext('globalThis.setDatasets = (items) => { datasets = items; }; globalThis.setTags = (tags) => { selectedTags = tags; };', context);
  return { context, nodes, requests, reply, document, element };
}

// 构造包含规模、字段和标签的真实 API 数据结构，按用例只替换必要字段。
function dataset(extra = {}) {
  return { id: 'a', name: '客户.csv', rows: 1234, file_size: 2048, columns: ['年龄', '地区'],
    tags: ['业务'], created_at: 1700000000, ...extra };
}

for (const [status, label, pending] of [['remote', '待下载', true], ['downloading', '下载中', true], ['ready', '已缓存', false], ['error', '下载失败', true], ['unknown', '待下载', true]]) {
  test(`Hub ${status} 展示明确状态及真实/未知规模，渲染不拉取数据`, () => {
    const f = fixture();
    const card = f.context.createDatasetCard(dataset({ source: 'huggingface', cache_status: status }));
    assert.match(card.innerHTML, new RegExp(label));
    assert.match(card.innerHTML, /Hugging Face/);
    assert.equal((card.innerHTML.match(/<dd>—<\/dd>/g) || []).length, pending ? 3 : 0);
    if (status === 'downloading') assert.equal((card.innerHTML.match(/ disabled/g) || []).length, 3);
    if (status === 'error') assert.match(card.innerHTML, /重试下载/);
    if (!pending) assert.match(card.innerHTML, /1,234/);
    assert.equal(f.requests.length, 0);
  });
}

test('本地数据、旧 row_count 与真实零值兼容，操作位于字段之后', () => {
  const f = fixture();
  const card = f.context.createDatasetCard(dataset({ rows: undefined, row_count: 10, file_size: 0 }));
  assert.match(card.innerHTML, /本地数据集/); assert.match(card.innerHTML, /可使用/);
  assert.match(card.innerHTML, /<dd>10<\/dd>/); assert.match(card.innerHTML, /0 B/);
  assert.ok(card.innerHTML.indexOf('dataset-actions') > card.innerHTML.indexOf('dataset-fields'));
  assert.doesNotMatch(card.innerHTML, /data-action="download"/);
  const zero = f.context.datasetMetrics({ rows: 0, columns: [], file_size: 0 });
  assert.equal((zero.match(/<dd>0<\/dd>/g) || []).length, 2);
});

test('长名称、超多字段、标签及恶意文本保留信息且安全转义', () => {
  const f = fixture(), attack = '"><img src=x onerror=alert(1)>';
  const card = f.context.createDatasetCard(dataset({ name: '超长'.repeat(100) + attack, description: attack,
    columns: [attack, 'b', 'c', 'd', 'e', 'f'], tags: [attack, '2', '3', '4'], last_error: attack,
    source: 'huggingface', cache_status: 'error', hub: { revision: attack, split: attack } }));
  assert.doesNotMatch(card.innerHTML, /<img/); assert.match(card.innerHTML, /&lt;img/);
  assert.match(card.innerHTML, /另 2 个字段/); assert.match(card.innerHTML, />\+1<\/span>/);
  assert.match(card.innerHTML, /超长超长/);
});

test('缺失名称、字段和规模时提供占位信息', () => {
  const f = fixture();
  const card = f.context.createDatasetCard({ id: 'missing' });
  assert.match(card.innerHTML, /未命名数据集/); assert.match(card.innerHTML, /暂无字段信息/);
  assert.equal((card.innerHTML.match(/<dd>—<\/dd>/g) || []).length, 2);
});

test('关键词匹配标签并组合筛选；清除后恢复数量及搜索焦点', () => {
  const f = fixture();
  f.context.setDatasets([dataset({ tags: ['销售'] }), dataset({ id: 'b', name: '库存.csv', tags: ['仓储'] })]);
  f.nodes.get('searchInput').value = '销售'; f.context.filterDatasets();
  assert.equal(f.nodes.get('datasetGrid').children.length, 1);
  assert.equal(f.nodes.get('datasetResults').textContent, '显示 1 / 2 个数据集');
  f.context.setTags(['仓储']); f.context.filterDatasets();
  assert.match(f.nodes.get('datasetGrid').innerHTML, /没有匹配/);
  assert.equal(f.nodes.get('clearDatasetFilters').hidden, false);
  f.context.clearDatasetFilters();
  assert.equal(f.nodes.get('datasetGrid').children.length, 2);
  assert.equal(f.nodes.get('clearDatasetFilters').hidden, true);
  assert.equal(f.document.activeElement, f.nodes.get('searchInput'));
  assert.equal(f.requests.length, 0);
});

test('空列表提供导入指引，读取列表不会访问详情或 materialize', async () => {
  const f = fixture(); await f.context.loadDatasets();
  assert.match(f.nodes.get('datasetGrid').innerHTML, /还没有数据集/);
  assert.equal(f.nodes.get('datasetResults').textContent, '显示 0 / 0 个数据集');
  f.reply.list = [dataset({ source: 'huggingface', cache_status: 'remote' })];
  await f.context.loadDatasets();
  assert.equal(f.nodes.get('datasetGrid').children.length, 1);
  assert.ok(f.requests.every(request => request.url === '/api/datasets'));
});

test('明确重试通过 materialize 接口下载并刷新为已缓存', async () => {
  const f = fixture(), button = f.element();
  f.reply.list = [dataset({ source: 'huggingface', cache_status: 'ready' })];
  await f.context.materializeDataset('a', button);
  assert.equal(f.requests[0].url, '/api/datasets/a/materialize'); assert.equal(f.requests[0].method, 'POST');
  assert.match(f.nodes.get('datasetGrid').children[0].innerHTML, /已缓存/);
});

test('详情优先展示预览，保留长文本、换行、完整来源及所有字段', async () => {
  const f = fixture(), value = '<script>alert(1)</script>\n' + '长文本'.repeat(200);
  f.reply.detail = dataset({ source: 'huggingface', cache_status: 'ready', preview: [{ 年龄: 0, 地区: value }],
    column_types: { 年龄: 'int64', 地区: 'object' }, hub: { revision: 'a'.repeat(40), split: 'train' } });
  await f.context.showDatasetDetail('a');
  const content = f.nodes.get('modalBody').innerHTML;
  assert.ok(content.indexOf('数据预览') < content.indexOf('字段与数据集信息'));
  assert.match(content, /前 1 行/); assert.match(content, /int64/); assert.match(content, /a{40}/);
  assert.doesNotMatch(content, /<script>/); assert.match(content, /&lt;script&gt;/);
  assert.ok(content.includes('长文本'.repeat(200)));
  assert.match(content, /class="preview-cell">0<\/div>/);
});

test('无预览行和请求失败分别提供可理解的反馈', async () => {
  const f = fixture(); f.reply.detail = dataset();
  await f.context.showDatasetDetail('a'); assert.match(f.nodes.get('modalBody').innerHTML, /暂无可预览/);
  f.reply.error = '网络连接失败，请重试';
  await f.context.showDatasetDetail('a'); assert.match(f.nodes.get('modalBody').textContent, /网络连接失败/);
});

test('关闭加载中的详情后，迟到响应不会重开弹窗或覆盖页面', async () => {
  const f = fixture(); let resolve;
  f.reply.pending = new Promise(done => { resolve = done; });
  const pending = f.context.showDatasetDetail('a');
  f.context.closeModal('datasetModal');
  resolve({ ok: true, json: async () => dataset({ preview: [{ 年龄: 1 }] }) });
  await pending;
  assert.equal(f.nodes.get('datasetModal').classList.contains('is-open'), false);
  assert.equal(f.nodes.get('modalBody').innerHTML, '');
  assert.equal(f.requests.length, 1);
});

test('弹窗循环键盘焦点，Escape 解锁背景并恢复入口或搜索框', () => {
  const f = fixture(), trigger = f.element('trigger'), last = f.element('last');
  const modal = f.nodes.get('datasetModal'), first = modal.children[0]; modal.children.push(last);
  trigger.focus(); f.context.openModal('datasetModal');
  assert.equal(f.document.activeElement, first);
  let prevented = 0;
  f.context.handleModalKeydown({ key: 'Tab', shiftKey: true, preventDefault() { prevented++; } });
  assert.equal(f.document.activeElement, last);
  f.context.handleModalKeydown({ key: 'Tab', shiftKey: false, preventDefault() { prevented++; } });
  assert.equal(f.document.activeElement, first); assert.equal(prevented, 2);
  f.context.handleModalKeydown({ key: 'Escape', preventDefault() {} });
  assert.equal(f.document.activeElement, trigger); assert.equal(f.document.body.classList.contains('has-dataset-modal'), false);
  f.context.openModal('datasetModal'); trigger.isConnected = false; f.context.closeModal('datasetModal');
  assert.equal(f.document.activeElement, f.nodes.get('searchInput'));
});
