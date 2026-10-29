'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../backend/static/index.html'), 'utf8');
const source = html.slice(html.indexOf('function updateRemoteSshCredentialFields('), html.indexOf('// 通过平台 SSH 启动、停止或重启'));
const credential = { id: 'c1', name: '测试凭证', username: 'worker', auth_method: 'password' };
const catalog = { credentials: [credential], default_credential_id: 'c1', can_configure: true };

// 仅替代 DOM 与网络边界；校验、状态、载荷和异步控制均执行页面原函数。
function fixture() {
  const nodes = new Map();
  const requests = [];
  const document = { activeElement: null };
  // 创建最小字段接口，使用页面中实际声明的约束验证输入。
  function element(id, tagName = 'DIV', attrs = {}) {
    const node = {
      id, tagName, type: attrs.type || 'text', name: attrs.name || '', value: attrs.value || '',
      checked: 'checked' in attrs, disabled: 'disabled' in attrs, hidden: 'hidden' in attrs,
      required: 'required' in attrs, open: false, inert: false, isConnected: true,
      attributes: {}, labels: [{ textContent: id }], parentElement: null, textContent: '',
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
        if (node.disabled) return true;
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
  for (const match of html.matchAll(/<(input|select|textarea|button|details|div|label|h3|h4|p|span)[^>]*\bid="([^"]+)"[^>]*>/g)) {
    const attrs = Object.fromEntries([...match[0].matchAll(/([\w-]+)(?:="([^"]*)")?/g)].map((part) => [part[1], part[2] || '']));
    element(match[2], match[1].toUpperCase(), attrs);
  }
  const gpu = element('gpu', 'INPUT', { name: 'mode', type: 'radio', value: 'gpu', checked: '' });
  const cpu = element('cpu', 'INPUT', { name: 'mode', type: 'radio', value: 'cpu' });
  // 表单字段从真实 HTML 读取；只忽略浏览器原生的布局与提示气泡。
  for (const id of ['remoteServerForm', 'remoteSshCredentialForm']) {
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
  advanced.tagName = 'DETAILS';
  for (const id of ['remoteRedisUrl', 'remoteS3Endpoint', 'remoteDockerRegistry', 'remotePipIndexUrl', 'remoteAptMirror']) nodes.get(id).parentElement = advanced;
  const modal = nodes.get('addServerModal');
  const card = element('dialogCard');
  const body = element('dialogBody');
  let focusables = [];
  modal.querySelector = (selector) => selector === '.server-modal-body' ? body : card;
  modal.querySelectorAll = () => focusables;
  nodes.get('remoteSshAuthMethod').value = 'password';
  document.body = element('body');
  document.activeElement = nodes.get('btnAddServer');
  document.querySelector = (selector) => selector.includes('"cpu"') ? cpu : gpu;
  // FormData 仅序列化成功控件，涵盖禁用和未选中的字段。
  class FormData {
    constructor(form) { this.fields = form.elements; }
    entries() { return this.fields.filter((field) => field.name && !field.disabled && (!['checkbox', 'radio'].includes(field.type) || field.checked)).map((field) => [field.name, field.value]); }
  }
  const state = { remoteCredentials: [], remoteServers: [], remoteCredentialDefaultId: null };
  const context = vm.createContext({
    document, state, FormData, $: (id) => nodes.get(id), esc: (value) => String(value).replaceAll('<', '&lt;'),
    window: { confirm: () => true }, toast() {}, refreshWorkers: async () => {},
    apiJson: async (url, options) => { requests.push({ url, ...options }); return catalog; },
  });
  vm.runInContext(source, context);
  const dialog = vm.runInContext('remoteServerDialog', context);
  // 填写合法的最小服务器配置并建立无修改基线。
  function validServer() {
    state.remoteCredentials = [credential];
    nodes.get('remoteMachineId').value = 'node-01';
    nodes.get('remoteHost').value = 'node.example.net';
    nodes.get('remoteCredentialSelect').value = 'c1';
    nodes.get('remoteCredentialSelect').disabled = false;
    context.showRemoteServerDialog('remoteMachineId');
  }
  return { context, nodes, dialog, state, requests, document, body, validServer, setFocusables: (fields) => { focusables = fields; } };
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

test('空凭证与加载状态阻止提交，空目录引导创建凭证', async () => {
  const f = fixture(); f.validServer();
  f.dialog.loading = true;
  assert.equal(f.context.validateRemoteServerForm(), false);
  f.dialog.loading = false;
  f.context.apiJson = async () => ({ credentials: [], can_configure: true });
  await f.context.loadRemoteSshSettings();
  assert.equal(f.nodes.get('remoteSshSettings').open, true);
  assert.equal(f.context.validateRemoteServerForm(), false);
  assert.equal(f.document.activeElement.id, 'remoteSshCredentialName');
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
  f.nodes.get('remoteSshPassword').value = 'synthetic-password';
  f.context.window.confirm = () => false;
  f.context.closeAddServerModal();
  assert.equal(f.nodes.get('addServerModal').hidden, false);
  assert.equal(f.nodes.get('remoteSshPassword').value, 'synthetic-password');
  f.context.window.confirm = () => true;
  f.context.closeAddServerModal();
  assert.equal(f.nodes.get('addServerModal').hidden, true);
  assert.equal(f.nodes.get('remoteSshPassword').value, '');
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

test('凭证保存后选中新建项、清空秘密并恢复按钮，服务器输入不丢失', async () => {
  const f = fixture(); f.validServer();
  f.nodes.get('remoteSshCredentialName').value = '新凭证';
  f.nodes.get('remoteSshCredentialUsername').value = 'worker';
  f.nodes.get('remoteSshPassword').value = 'synthetic-password';
  f.dialog.loadFailed = true;
  f.context.apiJson = async () => ({ ...catalog, credentials: [credential, { ...credential, id: 'new' }] });
  await f.context.saveRemoteSshSettings();
  assert.equal(f.nodes.get('remoteCredentialSelect').value, 'new');
  assert.equal(f.nodes.get('remoteSshPassword').value, '');
  assert.equal(f.nodes.get('btnSaveRemoteSshSettings').disabled, false);
  assert.equal(f.nodes.get('remoteHost').value, 'node.example.net');
  assert.equal(f.nodes.get('remoteSshSettings').open, false);
  assert.equal(f.dialog.loadFailed, false);
  assert.equal(f.context.remoteServerDialogIsDirty(), true);
});

test('凭证失败保留输入并解锁，认证切换清空已隐藏的秘密', async () => {
  const f = fixture(); f.validServer();
  f.nodes.get('remoteSshCredentialName').value = '新凭证';
  f.nodes.get('remoteSshCredentialUsername').value = 'worker';
  f.nodes.get('remoteSshPassword').value = 'synthetic-password';
  f.context.apiJson = async () => { throw new Error('offline'); };
  await f.context.saveRemoteSshSettings();
  assert.equal(f.nodes.get('remoteSshPassword').value, 'synthetic-password');
  assert.equal(f.nodes.get('btnSaveRemoteSshSettings').disabled, false);
  f.nodes.get('remoteSshAuthMethod').value = 'key';
  f.context.updateRemoteSshCredentialFields();
  assert.equal(f.nodes.get('remoteSshPassword').value, '');
  assert.equal(f.nodes.get('remoteSshPrivateKey').required, true);
});
