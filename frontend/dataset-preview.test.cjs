'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const html = fs.readFileSync(path.join(__dirname, '../backend/static/index.html'), 'utf8');

// 仅隔离 DOM 与 HTTP 边界，执行页面真实的列表、列选择和预览交互。
function fixture() {
  const nodes = new Map(), requests = [];
  // 模拟选择框的默认选项与清空行为，避免掩盖旧列残留问题。
  function element(id = '', tag = 'div', attrs = '') {
    let markup = '';
    const node = { id, tag, value: '', textContent: '', children: [], attributes: {},
      hidden: /\bhidden\b/.test(attrs), disabled: /\bdisabled\b/.test(attrs),
      appendChild(child) { this.children.push(child); if (tag === 'select' && this.children.length === 1) this.value = child.value; },
      setAttribute(key, value) { this.attributes[key] = String(value); },
      dispatchEvent(event) { return this['on' + event.type]?.({ target: this }); },
    };
    for (const match of attrs.matchAll(/([\w-]+)="([^"]*)"/g)) node.attributes[match[1]] = match[2];
    Object.defineProperty(node, 'innerHTML', { get: () => markup, set(value) { markup = value; node.children = []; if (tag === 'select') node.value = ''; } });
    return node;
  }
  for (const match of html.split('<script>')[0].matchAll(/<(\w+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) nodes.set(match[3], element(match[3], match[1], match[2]));
  const $ = id => nodes.get(id);
  const context = vm.createContext({ $, Event, console,
    document: { createElement: tag => element('', tag) },
    esc: value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    updateStart() {},
    apiJson: async url => { requests.push(url); return url === '/api/datasets' ? { datasets: [dataset()] } : dataset(); },
  });
  vm.runInContext(html.slice(html.indexOf('const state ='), html.indexOf('/* ---------- 基础工具 ---------- */')), context);
  const state = vm.runInContext('state', context);
  vm.runInContext(html.slice(html.indexOf('/* ---------- 数据集 ---------- */'), html.indexOf('function updateStart()')), context);
  // 保留历史上传区段边界，验证新按钮绑定及旧上传入口移除。
  vm.runInContext(html.slice(html.indexOf('/* 上传 */'), html.indexOf('/* ---------- 训练任务 ---------- */')), context);
  return { $, context, state, requests };
}

// 构造符合数据集列表和详情 API 的固定样本。
function dataset(extra = {}) {
  return { id: 'a', name: '客户.csv', rows: 7, columns: ['time', 'value', 'target'],
    preview: Array.from({ length: 7 }, (_, index) => ({ time: index, value: index + 10, target: index % 2 })), ...extra };
}

// 控制响应顺序，复现收起、重复点击和往返切换的并发场景。
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }

// 通过真实下拉框事件选择数据集。
function select(f, id) { f.$('datasetSelect').value = id; return f.$('datasetSelect').onchange({ target: f.$('datasetSelect') }); }

test('首屏预览默认隐藏，按钮名称与无障碍状态正确，上传交由管理页', () => {
  assert.match(html, /id="btnPreview"[^>]*aria-expanded="false"[^>]*>预览数据<\/button>/);
  assert.doesNotMatch(html, /<button[^>]*id="btnUpload"|id="fileInput"/);
  const f = fixture();
  assert.equal(f.$('previewWrap').hidden, true);
  assert.equal(f.$('previewMeta').hidden, true);
  assert.equal(f.$('btnPreview').disabled, true);
  assert.equal(f.$('btnUpload').attributes.href, '/dataset-manager.html');
});

test('加载列表与切换只使用列元信息，未预览也可配置训练', async () => {
  const f = fixture(); await f.context.loadDatasets();
  assert.deepEqual(f.requests, ['/api/datasets']);
  assert.equal(f.$('previewWrap').hidden, true);
  assert.equal(f.$('targetSelect').value, 'target');
  assert.equal(f.$('timeColumnSelect').value, 'time');
  assert.equal(f.$('targetSelect').children.length, 3);
  assert.equal(f.$('btnPreview').disabled, false);
  f.state.datasets.push(dataset({ id: 'b', columns: ['only'] })); select(f, 'b');
  assert.equal(f.$('targetSelect').value, 'only');
  assert.equal(f.$('timeColumnSelect').value, '');
  assert.equal(f.$('targetSelect').children.length, 1);
  assert.deepEqual(f.requests, ['/api/datasets']);
});

test('点击加载前五行与规模，再次点击收起并清空预览', async () => {
  const f = fixture(); await f.context.loadDatasets();
  await f.$('btnPreview').onclick();
  assert.deepEqual(f.requests, ['/api/datasets', '/api/datasets/a']);
  assert.equal(f.$('previewWrap').hidden, false);
  assert.equal((f.$('previewWrap').innerHTML.match(/<td>/g) || []).length, 15);
  assert.equal(f.$('previewMeta').textContent, '7 行 · 3 列 · 仅展示前 5 行');
  assert.equal(f.$('previewMeta').hidden, false);
  assert.equal(f.$('btnPreview').textContent, '收起预览');
  assert.equal(f.$('btnPreview').attributes['aria-expanded'], 'true');
  await f.$('btnPreview').onclick();
  assert.equal(f.$('previewWrap').hidden, true);
  assert.equal(f.$('previewWrap').innerHTML, '');
  assert.equal(f.$('previewMeta').hidden, true);
  assert.equal(f.$('btnPreview').textContent, '预览数据');
  assert.equal(f.$('btnPreview').attributes['aria-expanded'], 'false');
});

test('预览加载与完成不重置用户已选择的目标列及时间列', async () => {
  const f = fixture(); await f.context.loadDatasets();
  f.$('targetSelect').value = 'value'; f.$('timeColumnSelect').value = 'target';
  const pending = deferred(); f.context.apiJson = () => pending.promise;
  const action = f.$('btnPreview').onclick();
  assert.equal(f.$('targetSelect').value, 'value');
  f.$('timeColumnSelect').value = 'time'; pending.resolve(dataset()); await action;
  assert.equal(f.$('targetSelect').value, 'value');
  assert.equal(f.$('timeColumnSelect').value, 'time');
});

for (const failure of [false, true]) test(`收起后的旧请求${failure ? '失败' : '成功'}不会重新展开`, async () => {
  const f = fixture(); await f.context.loadDatasets();
  const pending = deferred(); f.context.apiJson = () => pending.promise;
  const action = f.$('btnPreview').onclick();
  assert.equal(f.$('previewWrap').attributes['aria-busy'], 'true');
  await f.$('btnPreview').onclick();
  failure ? pending.reject(new Error('旧错误')) : pending.resolve(dataset()); await action;
  assert.equal(f.$('previewWrap').hidden, true);
  assert.equal(f.$('previewWrap').innerHTML, '');
  assert.equal(f.$('previewWrap').attributes['aria-busy'], 'false');
  assert.equal(f.$('previewMeta').hidden, true);
});

for (const failure of [false, true]) test(`往返切换后旧请求${failure ? '失败' : '成功'}不覆盖最新预览`, async () => {
  const f = fixture(); await f.context.loadDatasets();
  f.state.datasets.push(dataset({ id: 'b' }));
  const old = deferred(), current = deferred(); let calls = 0;
  f.context.apiJson = () => ++calls === 1 ? old.promise : current.promise;
  const a = f.$('btnPreview').onclick(); select(f, 'b'); select(f, 'a');
  assert.equal(f.$('previewWrap').hidden, true);
  const b = f.$('btnPreview').onclick(); current.resolve(dataset({ rows: 99 })); await b;
  failure ? old.reject(new Error('旧错误')) : old.resolve(dataset({ rows: 1 })); await a;
  assert.match(f.$('previewMeta').textContent, /^99 行/);
  assert.equal(f.$('previewWrap').hidden, false);
  assert.doesNotMatch(f.$('previewWrap').innerHTML, /旧错误/);
});

test('空列表禁用预览并清除旧列，直接触发也不请求详情', async () => {
  const f = fixture(); await f.context.loadDatasets(); await f.$('btnPreview').onclick();
  f.state.datasets = []; f.context.renderDatasetSelect();
  const count = f.requests.length; await f.$('btnPreview').onclick();
  assert.equal(f.requests.length, count);
  assert.equal(f.$('btnPreview').disabled, true);
  assert.equal(f.$('datasetSelect').disabled, true);
  assert.equal(f.$('targetSelect').children.length, 0);
  assert.equal(f.$('previewWrap').hidden, true);
  assert.equal(f.$('previewMeta').textContent, '');
});

for (const status of ['remote', 'error', 'ready']) test(`Hub ${status} 仅点击后请求详情，列信息在下载后补齐`, async () => {
  const f = fixture(); f.state.datasets = [dataset({ source: 'huggingface', cache_status: status, columns: [] })];
  f.context.renderDatasetSelect();
  assert.equal(f.requests.length, 0);
  assert.equal(f.$('targetSelect').value, '');
  await f.$('btnPreview').onclick();
  assert.deepEqual(f.requests, ['/api/datasets/a']);
  assert.equal(f.$('targetSelect').value, 'target');
  assert.equal(f.$('previewWrap').hidden, false);
});

test('请求失败保留列选择并显示可重试提示，重试成功清除错误', async () => {
  const f = fixture(); await f.context.loadDatasets(); f.$('targetSelect').value = 'value';
  f.context.apiJson = async () => { throw new Error('<img src=x>离线'); };
  await f.$('btnPreview').onclick();
  assert.match(f.$('previewWrap').innerHTML, /&lt;img src=x&gt;离线/);
  assert.equal(f.$('btnPreview').textContent, '重试预览');
  assert.equal(f.$('targetSelect').value, 'value');
  assert.equal(f.$('previewMeta').hidden, true);
  f.context.apiJson = async () => dataset(); await f.$('btnPreview').onclick();
  assert.match(f.$('previewWrap').innerHTML, /<table/);
  assert.equal(f.$('btnPreview').textContent, '收起预览');
});

test('特殊字符与缺失单元格安全展示，零行零列不崩溃', async () => {
  const f = fixture(); await f.context.loadDatasets();
  f.context.apiJson = async () => dataset({ columns: ['<b>'], preview: [{ '<b>': '<script>x</script>' }, {}] });
  await f.$('btnPreview').onclick();
  assert.match(f.$('previewWrap').innerHTML, /&lt;b&gt;/);
  assert.match(f.$('previewWrap').innerHTML, /&lt;script&gt;x&lt;\/script&gt;/);
  assert.match(f.$('previewWrap').innerHTML, /<td><\/td>/);
  assert.doesNotMatch(f.$('previewWrap').innerHTML, /<script>/);
  await f.$('btnPreview').onclick();
  f.context.apiJson = async () => dataset({ rows: 0, columns: [], preview: [] });
  await f.$('btnPreview').onclick();
  assert.equal(f.$('previewMeta').textContent, '0 行 · 0 列 · 仅展示前 5 行');
  assert.equal(f.$('targetSelect').value, '');
});
