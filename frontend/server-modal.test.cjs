'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const staticDir = path.join(__dirname, '../backend/static');
const source = fs.readFileSync(path.join(staticDir, 'remote-management.js'), 'utf8').replace(/initRemoteManagement\(\);\s*$/, '');
const credential = { id: 'c1', name: '测试凭证', username: 'worker', auth_method: 'password' };
const catalog = { credentials: [credential], default_credential_id: 'c1', can_configure: true };

// 仅替代 DOM 与网络边界；校验、状态、载荷和异步控制均执行页面原函数。
function fixture(page = 'servers') {
  const html = fs.readFileSync(path.join(staticDir, page === 'servers' ? 'server-manager.html' : 'credential-manager.html'), 'utf8');
  const nodes = new Map();
  const requests = [];
  const document = { activeElement: null, getElementById: id => nodes.get(id), addEventListener() {} };
  // 创建最小字段接口，使用页面中实际声明的约束验证输入。
  function element(id, tagName = 'DIV', attrs = {}) {
    const node = {
      id, tagName, type: attrs.type || 'text', name: attrs.name || '', value: attrs.value || '',
      checked: 'checked' in attrs, disabled: 'disabled' in attrs, hidden: 'hidden' in attrs,
      required: 'required' in attrs, open: false, inert: false, isConnected: true,
      handlers: {}, addEventListener(name, handler) { node.handlers[name] = handler; }, attributes: {}, labels: [{ textContent: id }], parentElement: null, textContent: '',
      classList: { add() {}, remove() {} },
      // 模拟可编辑控件与焦点，而不模拟任何业务处理。
      matches(selector) { return /input|select|textarea/.test(selector) && ['INPUT', 'SELECT', 'TEXTAREA'].includes(tagName); },
      focus() { document.activeElement = node; },
      setAttribute(key, value) { node.attributes[key] = value; },
      removeAttribute(key) { delete node.attributes[key]; },
      closest(selector) { return selector === '[inert]' && node.parentElement?.inert ? node.parentElement : null; },
      getClientRects() { return node.hidden ? [] : [{}]; },
      reportValidity() { return node.checkValidity(); },
      checkValidity() {
        if (node.disabled || node.readOnly) return true;
        if (node.required && !node.value) return false;
        if (!node.value) return true;
        if (attrs.pattern && !new RegExp(`^(?:${attrs.pattern})$`).test(node.value)) return false;
        return node.type !== 'number' || (Number.isInteger(Number(node.value)) && Number(node.value) >= Number(attrs.min) && Number(node.value) <= Number(attrs.max));
      },
      validationMessage: '输入不符合约束',
      querySelectorAll() { return []; },
    };
    Object.defineProperty(node, 'willValidate', { get: () => !node.disabled && node.type !== 'hidden' });
    Object.defineProperty(node, 'innerHTML', {
      set(value) { node.markup = value; if (tagName === 'SELECT') node.value = ''; }, get() { return node.markup || ''; },
    });
    nodes.set(id, node);
    return node;
  }
  for (const match of html.matchAll(/<(input|select|textarea|button|details|div|label|h2|h3|h4|p|span|a|section)[^>]*\bid="([^"]+)"[^>]*>/g)) {
    const attrs = Object.fromEntries([...match[0].matchAll(/([\w-]+)(?:="([^"]*)")?/g)].map((part) => [part[1], part[2] || '']));
    element(match[2], match[1].toUpperCase(), attrs);
  }
  const gpu = element('gpu', 'INPUT', { name: 'mode', type: 'radio', value: 'gpu', checked: '' });
  const cpu = element('cpu', 'INPUT', { name: 'mode', type: 'radio', value: 'cpu' });
  // 表单字段从真实 HTML 读取；只忽略浏览器原生的布局与提示气泡。
  for (const id of ['remoteServerForm', 'remoteSshCredentialForm']) {
    if (!html.includes(`id="${id}"`)) continue;
    const fragment = html.split(`id="${id}"`, 2)[1].split('</form>', 1)[0];
    const form = element(id, 'FORM');
    form.elements = [...fragment.matchAll(/<(?:input|select|textarea)[^>]*\bid="([^"]+)"/g)].map((match) => nodes.get(match[1]));
    if (id === 'remoteServerForm') form.elements.push(gpu, cpu);
    const defaults = new Map(form.elements.map((field) => [field, [field.value, field.checked]]));
    form.reset = () => { for (const [field, [value, checked]] of defaults) { field.value = value; field.checked = checked; } };
    form.reportValidity = () => form.elements.every((field) => !field.willValidate || field.checkValidity());
    form.querySelector = () => cpu.checked ? cpu : gpu;
    form.querySelectorAll = () => form.elements;
    for (const field of form.elements) field.parentElement = form;
  }
  const advanced = nodes.get('remoteServerAdvanced');
  if (advanced) advanced.tagName = 'DETAILS';
  for (const id of ['remoteRedisUrl', 'remoteS3Endpoint', 'remoteDockerRegistry', 'remotePipIndexUrl', 'remoteAptMirror']) if (nodes.has(id)) nodes.get(id).parentElement = advanced;
  const modal = nodes.get('addServerModal');
  const card = element('dialogCard');
  const body = element('dialogBody');
  let focusables = [];
  if (modal) modal.querySelector = (selector) => selector === '.server-modal-body' ? body : card;
  if (modal) modal.querySelectorAll = () => focusables;
  if (nodes.has('remoteSshAuthMethod')) nodes.get('remoteSshAuthMethod').value = 'password';
  document.body = element('body');
  document.activeElement = nodes.get('btnAddServer');
  document.querySelector = (selector) => selector.includes('"cpu"') ? cpu : gpu;
  document.querySelectorAll = () => [];
  const windowHandlers = {};
  const navigation = [];
  // FormData 仅序列化成功控件，涵盖禁用和未选中的字段。
  class FormData {
    constructor(form) { this.fields = form.elements; }
    entries() { return this.fields.filter((field) => field.name && !field.disabled && (!['checkbox', 'radio'].includes(field.type) || field.checked)).map((field) => [field.name, field.value]); }
  }
  const context = vm.createContext({
    document, FormData, window: { confirm: () => true, addEventListener(name, handler) { windowHandlers[name] = handler; }, location: { assign: url => navigation.push(url) } },
    setInterval() {}, setTimeout() {}, clearTimeout() {}, navigator: {},
  });
  vm.runInContext(source, context);
  context.apiJson = async (url, options) => { requests.push({ url, ...options }); return catalog; };
  context.toast = () => {};
  const refreshWorkers = context.refreshWorkers;
  context.refreshWorkers = async () => {};
  const state = vm.runInContext('state', context);
  const dialog = vm.runInContext('remoteServerDialog', context);
  const editor = vm.runInContext('credentialEditor', context);
  if (page === 'credentials') context.resetRemoteSshCredentialForm(true);
  // 填写合法的最小服务器配置并建立无修改基线。
  function validServer() {
    state.remoteCredentials = [credential];
    nodes.get('remoteMachineId').value = 'node-01';
    nodes.get('remoteHost').value = 'node.example.net';
    nodes.get('remoteCredentialSelect').value = 'c1';
    nodes.get('remoteCredentialSelect').disabled = false;
    context.showRemoteServerDialog('remoteMachineId');
  }
  return { context, nodes, dialog, editor, state, requests, refreshWorkers, document, body, windowHandlers, navigation, validServer, setFocusables: (fields) => { focusables = fields; } };
}

// 可控的延迟响应验证重复提交和过期响应，不引入真实网络或凭证。
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

for (const [id, invalid, valid] of [
  ['remoteMachineId', '', 'n'], ['remoteMachineId', '-bad', 'a'.repeat(64)],
  ['remoteHost', 'host with spaces', '10.0.0.1'], ['remotePort', '0', '1'],
  ['remotePort', '65536', '65535'], ['remotePort', '2.5', '22'],
]) {
  test(`字段校验并聚焦：${id}=${invalid}`, () => {
    const f = fixture(); f.validServer();
    f.nodes.get(id).value = invalid;
    assert.equal(f.context.validateRemoteServerForm(), false);
    assert.equal(f.document.activeElement.id, id);
    assert.equal(f.nodes.get(id).attributes['aria-invalid'], 'true');
    f.nodes.get(id).value = valid;
    assert.equal(f.context.validateRemoteServerForm(), true);
  });
}

test('高级字段校验失败时自动展开；GPU 不受隐藏 CPU 输入影响', () => {
  const f = fixture(); f.validServer();
  // 模拟浏览器报告折叠区域内字段无效，验证展开与焦点定位。
  const endpoint = f.nodes.get('remoteS3Endpoint');
  const checkValidity = endpoint.checkValidity;
  endpoint.checkValidity = () => false;
  assert.equal(f.context.validateRemoteServerForm(), false);
  assert.equal(f.nodes.get('remoteServerAdvanced').open, true);
  assert.equal(f.document.activeElement, endpoint);
  endpoint.checkValidity = checkValidity;
  f.nodes.get('remoteCpuSlots').value = '-1';
  assert.equal(f.context.validateRemoteServerForm(), true);
  f.nodes.get('cpu').checked = true; f.context.updateRemoteServerMode();
  assert.equal(f.context.validateRemoteServerForm(), false);
  for (const value of ['0', '256']) {
    f.nodes.get('remoteCpuSlots').value = value;
    assert.equal(f.context.validateRemoteServerForm(), true);
  }
  f.nodes.get('remoteCpuSlots').value = '257';
  assert.equal(f.context.validateRemoteServerForm(), false);
});

test('空凭证与加载状态阻止提交，并把焦点留在凭证选择框', async () => {
  const f = fixture(); f.validServer();
  f.dialog.loading = true;
  assert.equal(f.context.validateRemoteServerForm(), false);
  f.dialog.loading = false;
  f.context.apiJson = async () => ({ credentials: [], can_configure: true });
  await f.context.loadRemoteSshSettings();
  assert.match(f.nodes.get('remoteCredentialStatus').textContent, /凭证管理/);
  assert.equal(f.context.validateRemoteServerForm(), false);
  assert.equal(f.document.activeElement.id, 'remoteCredentialSelect');
});

test('工作台和服务器弹窗不再提供跨页管理入口', () => {
  const workbench = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
  const server = fs.readFileSync(path.join(staticDir, 'server-manager.html'), 'utf8');
  assert.doesNotMatch(workbench, /<a href="\/server-manager\.html" class="management-link">管理服务器 ↗<\/a>/);
  assert.doesNotMatch(server, /id="btnManageRemoteCredentials"/);
  assert.match(server, /id="btnRefreshRemoteCredentials"/);
});

test('默认凭证加载不触发未保存提醒；编辑凭证正确恢复', async () => {
  const f = fixture();
  f.context.showRemoteServerDialog('remoteMachineId');
  await f.context.loadRemoteSshSettings();
  assert.equal(f.context.remoteServerDialogIsDirty(), false);
  f.state.remoteServers = [{ machine_id: 'existing', host: 'existing.net', mode: 'cpu', cpu_slots: 0, ssh_credential_id: 'c2', remote_s3_endpoint_override: 'https://s3.example.net' }];
  f.context.apiJson = async () => ({ ...catalog, credentials: [credential, { ...credential, id: 'c2' }] });
  await f.context.openEditRemoteServer('existing');
  await Promise.resolve();
  assert.equal(f.nodes.get('remoteCredentialSelect').value, 'c2');
  assert.equal(Number(f.nodes.get('remoteCpuSlots').value), 0);
  assert.equal(f.nodes.get('remoteCpuSlots').disabled, false);
  assert.equal(f.nodes.get('remoteMachineId').readOnly, true);
  assert.equal(f.nodes.get('remoteS3Endpoint').value, 'https://s3.example.net');
  assert.equal(f.context.remoteServerDialogIsDirty(), false);
});

test('加载失败可重试；失败时保留服务器输入', async () => {
  const f = fixture(); f.validServer();
  f.context.apiJson = async () => { throw new Error('offline'); };
  await f.context.loadRemoteSshSettings();
  assert.equal(f.dialog.loadFailed, true);
  assert.equal(f.nodes.get('remoteServerError').hidden, false);
  assert.equal(f.nodes.get('remoteHost').value, 'node.example.net');
  f.context.apiJson = async () => catalog;
  await f.context.loadRemoteSshSettings();
  assert.equal(f.dialog.loadFailed, false);
  assert.equal(f.nodes.get('remoteServerError').hidden, true);
});

test('关闭旧会话后返回的凭证响应不能覆盖新会话', async () => {
  const f = fixture(); f.validServer();
  const pending = deferred();
  f.context.apiJson = () => pending.promise;
  const loading = f.context.loadRemoteSshSettings();
  f.context.closeAddServerModal(true);
  f.context.showRemoteServerDialog('remoteHost');
  f.nodes.get('remoteCredentialSelect').value = 'new-session';
  pending.resolve(catalog);
  await loading;
  assert.equal(f.nodes.get('remoteCredentialSelect').value, 'new-session');
});

test('未保存内容可取消关闭，确认关闭会清空秘密并恢复触发焦点', () => {
  const f = fixture(); f.validServer();
  f.nodes.get('remoteRedisUrl').value = 'redis://synthetic-secret@redis.net';
  f.context.window.confirm = () => false;
  f.context.closeAddServerModal();
  assert.equal(f.nodes.get('addServerModal').hidden, false);
  f.context.window.confirm = () => true;
  f.context.closeAddServerModal();
  assert.equal(f.nodes.get('addServerModal').hidden, true);
  assert.equal(f.nodes.get('remoteRedisUrl').value, '');
  assert.equal(f.document.activeElement.id, 'btnAddServer');
});

test('键盘焦点循环且 Escape 经过未保存保护', () => {
  const f = fixture(); f.validServer();
  const first = f.nodes.get('btnCloseAddServer');
  const last = f.nodes.get('btnSubmitRemoteServer');
  f.setFocusables([first, last]);
  let prevented = 0;
  const event = { key: 'Tab', preventDefault() { prevented++; } };
  last.focus(); f.context.handleRemoteServerDialogKeydown(event);
  assert.equal(f.document.activeElement, first);
  f.context.handleRemoteServerDialogKeydown({ ...event, shiftKey: true });
  assert.equal(f.document.activeElement, last);
  assert.equal(prevented, 2);
  f.nodes.get('remoteHost').value = 'changed.net';
  f.context.window.confirm = () => false;
  f.context.handleRemoteServerDialogKeydown({ ...event, key: 'Escape' });
  assert.equal(f.nodes.get('addServerModal').hidden, false);
});

test('失败保留输入并解锁；重复提交只发送一次请求', async () => {
  const f = fixture(); f.validServer();
  const pending = deferred();
  f.context.apiJson = (url, options) => { f.requests.push({ url, ...options }); return pending.promise; };
  const event = { preventDefault() {} };
  const saving = f.context.submitRemoteServer(event);
  await f.context.submitRemoteServer(event);
  f.context.closeAddServerModal(true);
  assert.equal(f.requests.length, 1);
  assert.equal(f.nodes.get('addServerModal').hidden, false);
  assert.equal(f.nodes.get('remoteServerForm').inert, true);
  assert.equal(f.body.inert, true);
  pending.reject(new Error('conflict'));
  await saving;
  assert.equal(f.nodes.get('remoteHost').value, 'node.example.net');
  assert.equal(f.nodes.get('btnSubmitRemoteServer').disabled, false);
  assert.equal(f.nodes.get('remoteServerForm').inert, false);
  assert.equal(f.body.inert, false);
  assert.equal(f.nodes.get('remoteServerError').hidden, false);
});

for (const editing of [false, true]) {
  test(`${editing ? '编辑 PATCH' : '添加 POST'}保留载荷含义，成功后直接关闭`, async () => {
    const f = fixture(); f.validServer();
    if (editing) f.state.editingRemoteMachineId = 'existing';
    f.nodes.get('remoteRedisUrl').disabled = true;
    f.nodes.get('clearRemoteRedisOverride').checked = true;
    f.nodes.get('remoteS3Endpoint').value = 'https://s3.example.net';
    f.context.window.confirm = () => { throw new Error('成功保存不应提醒丢弃'); };
    await f.context.submitRemoteServer({ preventDefault() {} });
    const request = f.requests[0];
    const payload = JSON.parse(request.body);
    assert.equal(request.method, editing ? 'PATCH' : 'POST');
    assert.equal(request.url, editing ? '/api/remote-servers/existing' : '/api/remote-servers');
    assert.equal('machine_id' in payload, !editing);
    assert.equal(payload.port, 22);
    assert.equal(payload.cpu_slots, 2);
    assert.equal(payload.username, null);
    assert.equal(payload.remote_s3_endpoint, 'https://s3.example.net');
    assert.equal(payload.clear_remote_redis_url_override, true);
    assert.equal(payload.remote_redis_url, '');
    assert.equal(f.nodes.get('addServerModal').hidden, true);
  });
}


for (const method of ['password', 'key', 'key_password']) {
  for (const editing of [false, true]) {
    test(`凭证页独立保存 ${method} / ${editing ? '编辑' : '新增'}，成功清秘密并留在本页`, async () => {
      const f = fixture('credentials');
      f.nodes.get('remoteSshCredentialId').value = editing ? 'c1' : '';
      f.nodes.get('remoteSshCredentialName').value = '测试凭证';
      f.nodes.get('remoteSshCredentialUsername').value = 'worker';
      f.nodes.get('remoteSshAuthMethod').value = method;
      f.nodes.get('remoteSshPrivateKey').value = 'synthetic-key';
      f.nodes.get('remoteSshPassphrase').value = 'synthetic-phrase';
      f.nodes.get('remoteSshPassword').value = 'synthetic-password';
      f.nodes.get('remoteSshMakeDefault').checked = true;
      await f.context.saveRemoteSshSettings();
      const request = f.requests[0], payload = JSON.parse(request.body);
      assert.equal(request.method, editing ? 'PUT' : 'POST');
      assert.equal(payload.auth_method, method);
      assert.equal(payload.make_default, true);
      assert.equal('private_key' in payload, method !== 'password');
      assert.equal('password' in payload, method !== 'key');
      for (const id of ['remoteSshPrivateKey', 'remoteSshPassphrase', 'remoteSshPassword']) assert.equal(f.nodes.get(id).value, '');
      assert.equal(f.nodes.get('btnSaveRemoteSshSettings').disabled, false);
      assert.equal(f.context.credentialFormIsDirty(), false);
      assert.equal(f.navigation.length, 0);
      assert.equal(f.state.remoteCredentials.length, 1);
      assert.equal(f.nodes.has('remoteServerForm'), false);
    });
  }
}

test('凭证失败保留输入并解锁，重复提交只发送一次，认证切换清空隐藏秘密', async () => {
  const f = fixture('credentials');
  f.nodes.get('remoteSshCredentialName').value = '新凭证';
  f.nodes.get('remoteSshCredentialUsername').value = 'worker';
  f.nodes.get('remoteSshPassword').value = 'synthetic-password';
  const pending = deferred();
  f.context.apiJson = (url, options) => { f.requests.push({ url, ...options }); return pending.promise; };
  const saving = f.context.saveRemoteSshSettings();
  await f.context.saveRemoteSshSettings();
  assert.equal(f.requests.length, 1);
  assert.equal(f.editor.busy, true);
  assert.equal(f.context.confirmManagementNavigation(), false);
  pending.reject(new Error('offline'));
  await saving;
  assert.equal(f.nodes.get('remoteSshPassword').value, 'synthetic-password');
  assert.equal(f.nodes.get('btnSaveRemoteSshSettings').disabled, false);
  assert.equal(f.nodes.get('credentialFormError').textContent, 'offline');
  f.nodes.get('remoteSshAuthMethod').value = 'key';
  f.context.updateRemoteSshCredentialFields();
  assert.equal(f.nodes.get('remoteSshPassword').value, '');
  assert.equal(f.nodes.get('remoteSshPrivateKey').required, true);
});

test('凭证删除引用冲突保留目录和草稿，成功删除编辑对象后重置表单', async () => {
  const f = fixture('credentials');
  f.context.applyCredentialCatalog(catalog);
  f.context.editRemoteSshCredential('c1');
  f.nodes.get('remoteSshPassword').value = 'synthetic-password';
  f.context.apiJson = async () => { throw new Error('凭证正在被服务器使用'); };
  await f.context.deleteRemoteSshCredential('c1');
  assert.equal(f.state.remoteCredentials.length, 1);
  assert.equal(f.nodes.get('remoteSshPassword').value, 'synthetic-password');
  assert.match(f.nodes.get('credentialFormError').textContent, /正在被服务器使用/);
  assert.equal(f.editor.busy, false);
  f.context.apiJson = async () => ({ credentials: [], can_configure: true });
  await f.context.deleteRemoteSshCredential('c1');
  assert.equal(f.state.remoteCredentials.length, 0);
  assert.equal(f.nodes.get('remoteSshCredentialId').value, '');
  assert.equal(f.nodes.get('remoteSshPassword').value, '');
});

test('凭证页草稿保护、编辑切换和离开清理分别生效', () => {
  const f = fixture('credentials'); f.context.applyCredentialCatalog(catalog);
  f.nodes.get('remoteSshPassword').value = 'synthetic-password';
  f.context.window.confirm = () => false;
  f.context.editRemoteSshCredential('c1');
  assert.equal(f.nodes.get('remoteSshCredentialId').value, '');
  assert.equal(f.context.confirmManagementNavigation(), false);
  f.context.window.confirm = () => true;
  f.context.editRemoteSshCredential('c1');
  assert.equal(f.nodes.get('remoteSshCredentialId').value, 'c1');
  assert.equal(f.nodes.get('remoteSshPassword').value, '');
  assert.equal(f.context.credentialFormIsDirty(), false);
  f.nodes.get('remoteSshPassword').value = 'new-secret';
  assert.equal(f.context.confirmManagementNavigation(), true);
  assert.equal(f.nodes.get('remoteSshPassword').value, '');
  assert.equal(f.context.credentialFormIsDirty(), false);
});

test('凭证页保存期间旧目录响应失效，刷新失败不清空编辑内容', async () => {
  const f = fixture('credentials'); const pending = deferred();
  f.context.apiJson = () => pending.promise;
  const loading = f.context.loadCredentialCatalog();
  f.nodes.get('remoteSshCredentialName').value = '新凭证';
  f.nodes.get('remoteSshCredentialUsername').value = 'worker';
  f.nodes.get('remoteSshPassword').value = 'secret';
  f.context.apiJson = async () => catalog;
  await f.context.saveRemoteSshSettings();
  pending.resolve({ credentials: [], can_configure: true });
  await loading;
  assert.equal(f.state.remoteCredentials.length, 1);
  f.nodes.get('remoteSshCredentialName').value = 'editing';
  f.context.apiJson = async () => { throw new Error('offline'); };
  await f.context.loadCredentialCatalog();
  assert.equal(f.nodes.get('remoteSshCredentialName').value, 'editing');
  assert.match(f.nodes.get('remoteSshSettingsStatus').textContent, /读取失败/);
});

for (const page of ['servers', 'credentials']) {
  test(`${page} 页面可独立初始化且刷新/离开保护已绑定`, async () => {
    const f = fixture(page);
    f.context.initRemoteManagement();
    await Promise.resolve();
    assert.equal(typeof f.windowHandlers.beforeunload, 'function');
    assert.equal(typeof f.windowHandlers.pageshow, 'function');
    if (page === 'credentials') {
      assert.equal(f.requests[0].url, '/api/remote-ssh-credentials');
      assert.equal(typeof f.nodes.get('btnRefreshCredentials').onclick, 'function');
    } else {
      assert.equal(typeof f.nodes.get('btnAddServer').onclick, 'function');
      assert.equal(typeof f.nodes.get('btnRefreshRemoteCredentials').onclick, 'function');
      assert.equal(f.nodes.has('remoteSshCredentialForm'), false);
    }
  });
}

// 直接执行列表加载与渲染，验证从工作台迁移后不依赖其 DOM 或函数。
test('服务器页合并登记与心跳并保留远程操作，危险文本转义', async () => {
  const f = fixture();
  f.context.apiJson = async url => url === '/api/workers' ? { queue_depth: 2, workers: [{ worker_id: 'w1', machine_id: 'remote', active: true, enabled: true, managed: true, gpu: { available: true, name: '<img onerror=evil>' } }] } : { servers: [{ machine_id: 'remote', username: 'root', host: '<script>evil</script>', port: 22, mode: 'gpu', deployed: true }] };
  await f.refreshWorkers();
  const markup = f.nodes.get('workerGrid').innerHTML;
  assert.match(markup, /data-remote-action="stop"/);
  assert.match(markup, /data-worker-id="w1"/);
  assert.match(markup, /&lt;script&gt;/);
  assert.doesNotMatch(markup, /<script>|<img /);
  assert.match(f.nodes.get('resourceStatus').textContent, /1 个已启用 GPU/);
  assert.equal(f.nodes.get('queueDepth').textContent, '队列 2');
});

for (const scenario of ['empty', 'workers-failed', 'remote-failed']) {
  test(`服务器目录空状态和失败可以重试：${scenario}`, async () => {
    const f = fixture();
    f.context.apiJson = async url => {
      if (scenario === 'workers-failed' || (scenario === 'remote-failed' && url.includes('remote-servers'))) throw new Error('服务不可用');
      return {};
    };
    await f.refreshWorkers();
    assert.match(f.nodes.get('workerGrid').innerHTML, scenario === 'empty' ? /尚未添加/ : /读取.*失败/);
    assert.equal(f.state.refreshingWorkers, false);
    f.context.apiJson = async () => ({});
    await f.refreshWorkers();
    assert.match(f.nodes.get('workerGrid').innerHTML, /尚未添加/);
  });
}
