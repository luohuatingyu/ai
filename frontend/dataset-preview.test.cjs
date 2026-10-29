'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const html = fs.readFileSync(path.join(__dirname, '../backend/static/index.html'), 'utf8');

// 仅隔离 DOM 与 HTTP 边界，执行页面真实的弹窗、分页、列配置和异步逻辑。
function fixture() {
  const nodes = new Map(), requests = [];
  const document = { activeElement: null, handlers: {}, body: { classList: { add() {}, remove() {} } },
    addEventListener(type, fn) { this.handlers[type] = fn; }, createElement: tag => element('', tag) };
  function element(id = '', tag = 'div', attrs = '') {
    let markup = '';
    const node = { id, tag, value: '', textContent: '', children: [], attributes: {}, hidden: /\bhidden\b/.test(attrs),
      disabled: /\bdisabled\b/.test(attrs), handlers: {}, isConnected: true,
      classList: { add() {}, remove() {}, contains() { return false; } },
      appendChild(child) { this.children.push(child); if (tag === 'select' && this.children.length === 1) this.value = child.value; },
      addEventListener(type, fn) { this.handlers[type] = fn; },
      setAttribute(key, value) { this.attributes[key] = String(value); },
      focus() { document.activeElement = this; },
      dispatchEvent(event) { return this['on' + event.type]?.({ target: this }); },
    };
    for (const match of attrs.matchAll(/([\w-]+)="([^"]*)"/g)) node.attributes[match[1]] = match[2];
    Object.defineProperty(node, 'innerHTML', { get: () => markup, set(value) { markup = value; node.children = []; if (tag === 'select') node.value = ''; } });
    return node;
  }
  for (const match of html.split('<script>')[0].matchAll(/<(\w+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) nodes.set(match[3], element(match[3], match[1], match[2]));
  const $ = id => { if (!nodes.has(id)) nodes.set(id, element(id)); return nodes.get(id); };
  const context = vm.createContext({ $, Event, console, document,
    esc: value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    updateStart() {},
    apiJson: async url => { requests.push(url); return url === '/api/datasets' ? { datasets: [dataset()] } : page(0); },
  });
  vm.runInContext(html.slice(html.indexOf('const state ='), html.indexOf('/* ---------- 基础工具 ---------- */')), context);
  const state = vm.runInContext('state', context);
  vm.runInContext(html.slice(html.indexOf('/* ---------- 数据集 ---------- */'), html.indexOf('// 汇总配置状态与提交锁')), context);
  vm.runInContext(html.slice(html.indexOf('/* 上传 */'), html.indexOf('/* ---------- 训练任务 ---------- */')), context);
  return { $, context, state, requests, document };
}

// 构造 25 行数据，覆盖三页、最后不足一页和特殊字符。
function dataset(extra = {}) {
  const rows = Array.from({ length: 25 }, (_, index) => ({ time: index, value: index + 10, target: index % 2 }));
  return { id: 'a', name: '客户.csv', rows: 25, columns: ['time', 'value', 'target'], preview: rows, ...extra };
}
function page(offset, extra = {}) {
  const source = dataset(extra);
  const rows = source.preview.slice(offset, offset + 10);
  return { ...source, preview: rows, preview_offset: offset, preview_limit: 10,
    preview_page: Math.floor(offset / 10) + 1, preview_page_count: 3,
    preview_has_previous: offset > 0, preview_has_next: offset + rows.length < source.rows };
}
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function select(f, id) { f.$('datasetSelect').value = id; return f.$('datasetSelect').onchange({ target: f.$('datasetSelect') }); }

test('预览入口打开弹窗，主页面不再内联表格', () => {
  assert.match(html, /id="btnPreview"[^>]*aria-controls="previewModal"/);
  assert.match(html, /id="previewModal"[^>]*role="dialog"/);
  assert.match(html, /id="previewPrev"/); assert.match(html, /id="previewNext"/);
  assert.doesNotMatch(html.slice(html.indexOf('id="datasetLocalPanel"'), html.indexOf('<h2>② 训练配置')), /<table|preview-table/);
});

test('列表加载只使用列元信息，未打开弹窗也可配置训练', async () => {
  const f = fixture(); await f.context.loadDatasets();
  assert.deepEqual(f.requests, ['/api/datasets']);
  assert.equal(f.$('previewModal').hidden, true);
  assert.equal(f.$('targetSelect').value, 'target');
  assert.equal(f.$('timeColumnSelect').value, 'time');
  assert.equal(f.$('btnPreview').disabled, false);
});

test('点击打开弹窗并加载第一页，显示每页 10 行和分页状态', async () => {
  const f = fixture(); await f.context.loadDatasets();
  await f.$('btnPreview').onclick();
  assert.deepEqual(f.requests, ['/api/datasets', '/api/datasets/a?rows=10&offset=0']);
  assert.equal(f.$('previewModal').hidden, false);
  assert.equal((f.$('previewWrap').innerHTML.match(/<tr>/g) || []).length, 11);
  assert.equal(f.$('previewModalMeta').textContent, '25 行 · 3 列 · 每页 10 行');
  assert.equal(f.$('previewPageStatus').textContent, '第 1 / 3 页');
  assert.equal(f.$('previewPrev').disabled, true);
  assert.equal(f.$('previewNext').disabled, false);
  assert.equal(f.document.activeElement, f.$('btnClosePreview'));
});

test('下一页和上一页按偏移请求，最后一页正确禁用下一页', async () => {
  const f = fixture(); await f.context.loadDatasets();
  f.context.apiJson = async url => {
    f.requests.push(url);
    const offset = Number(new URL('http://preview' + url).searchParams.get('offset') || 0);
    return url === '/api/datasets' ? { datasets: [dataset()] } : page(offset);
  };
  await f.$('btnPreview').onclick();
  await f.$('previewNext').onclick();
  assert.equal(f.requests.at(-1), '/api/datasets/a?rows=10&offset=10');
  assert.equal(f.$('previewPageStatus').textContent, '第 2 / 3 页');
  assert.equal(f.$('previewPrev').disabled, false);
  await f.$('previewNext').onclick();
  assert.equal(f.requests.at(-1), '/api/datasets/a?rows=10&offset=20');
  assert.equal(f.$('previewPageStatus').textContent, '第 3 / 3 页');
  assert.equal((f.$('previewWrap').innerHTML.match(/<tr>/g) || []).length, 6);
  assert.equal(f.$('previewNext').disabled, true);
  await f.$('previewPrev').onclick();
  assert.equal(f.$('previewPageStatus').textContent, '第 2 / 3 页');
});

test('关闭按钮、遮罩和 Escape 关闭弹窗并恢复触发焦点', async () => {
  const f = fixture(); await f.context.loadDatasets(); await f.$('btnPreview').onclick();
  await f.$('btnClosePreview').onclick();
  assert.equal(f.$('previewModal').hidden, true);
  assert.equal(f.$('btnPreview').textContent, '预览数据');
  assert.equal(f.document.activeElement, f.$('btnPreview'));
  await f.$('btnPreview').onclick();
  f.context.document.handlers.keydown({ key: 'Escape' });
  assert.equal(f.$('previewModal').hidden, true);
  assert.equal(f.document.activeElement, f.$('btnPreview'));
});

test('关闭或切换后的旧响应不会污染弹窗', async () => {
  const f = fixture(); await f.context.loadDatasets();
  const pending = deferred(); f.context.apiJson = url => { f.requests.push(url); return pending.promise; };
  const action = f.$('btnPreview').onclick();
  await f.$('btnClosePreview').onclick();
  pending.resolve(page(0)); await action;
  assert.equal(f.$('previewModal').hidden, true);
  assert.equal(f.$('previewWrap').innerHTML, '');

  const old = deferred(), current = deferred(); let calls = 0;
  f.context.apiJson = url => { f.requests.push(url); return ++calls === 1 ? old.promise : current.promise; };
  f.state.datasets.push(dataset({ id: 'b', name: '新数据.csv' }));
  select(f, 'a'); const oldAction = f.$('btnPreview').onclick(); select(f, 'b');
  const currentAction = f.$('btnPreview').onclick(); current.resolve(page(0, { id: 'b', name: '新数据.csv' })); await currentAction;
  old.resolve(page(0)); await oldAction;
  assert.equal(f.$('previewModalTitle').textContent, '新数据.csv · 数据预览');
  assert.doesNotMatch(f.$('previewWrap').innerHTML, /旧错误/);
});

test('请求失败可重试，特殊字符和空页安全展示', async () => {
  const f = fixture(); await f.context.loadDatasets();
  f.context.apiJson = async url => { f.requests.push(url); throw new Error('<img src=x>离线'); };
  await f.$('btnPreview').onclick();
  assert.match(f.$('previewWrap').innerHTML, /&lt;img src=x&gt;离线/);
  assert.equal(f.$('previewPageStatus').textContent, '当前页加载失败');
  f.context.apiJson = async url => { f.requests.push(url); return page(0, { columns: ['<b>'], preview: [{ '<b>': '<script>x</script>' }] }); };
  await f.$('previewRetry').onclick();
  assert.match(f.$('previewWrap').innerHTML, /&lt;b&gt;/);
  assert.match(f.$('previewWrap').innerHTML, /&lt;script&gt;x&lt;\/script&gt;/);
  assert.doesNotMatch(f.$('previewWrap').innerHTML, /<script>/);
  assert.equal(f.$('previewNext').disabled, false);

  f.context.apiJson = async url => { f.requests.push(url); return page(20, { rows: 0, preview: [] }); };
  await f.context.loadPreviewPage(3);
  assert.match(f.$('previewWrap').innerHTML, /当前页暂无数据/);
});

test('预览分页不重置训练列选择，切换数据集后重新从第一页开始', async () => {
  const f = fixture(); await f.context.loadDatasets();
  f.$('targetSelect').value = 'value'; f.$('timeColumnSelect').value = 'time';
  await f.$('btnPreview').onclick(); await f.$('previewNext').onclick();
  assert.equal(f.$('targetSelect').value, 'value');
  assert.equal(f.$('timeColumnSelect').value, 'time');
  f.state.datasets.push(dataset({ id: 'b', columns: ['only'], name: '另一个.csv' }));
  select(f, 'b');
  assert.equal(f.$('previewModal').hidden, true);
  assert.equal(f.$('targetSelect').value, 'only');
  assert.equal(f.state.previewPage, 1);
});
