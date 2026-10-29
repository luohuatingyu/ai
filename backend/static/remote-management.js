'use strict';
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const state = {workerSlots: [], remoteServers: [], remoteOperations: new Map(), remoteOperationNotified: new Set(), remoteLogOperationId: null, remoteLogPollTimer: null, editingRemoteMachineId: null, remoteCredentials: [], remoteCredentialDefaultId: null, remoteCredentialsCanConfigure: false};
// 读取接口并只向用户展示服务端提供的公开错误。
async function apiJson(url, opt = {}) {
  const res = await fetch(url, opt);
  let body = null;
  try { body = await res.json(); } catch { /* ignore */ }
  if (!res.ok) throw new Error((body && body.detail) || `HTTP ${res.status}`);
  return body;
}
// 在页面顶部显示短暂反馈，不把输入内容当作 HTML。
function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = msg;
  $("toastWrap").appendChild(el);
  setTimeout(() => el.remove(), 3500);
}
/* ---------- 计算服务器与手动资源选择 ---------- */
const workerStateText = {
  idle: "空闲", running: "训练中", degraded: "卡降级", recovering: "恢复中",
  disabled: "未启用", offline: "离线",
};

// 把检测到的内存统一格式化为用户容易比较的容量。
function formatMemory(memoryMb) {
  if (!Number.isFinite(Number(memoryMb)) || Number(memoryMb) <= 0) return "内存未知";
  const gb = Number(memoryMb) / 1024;
  return `${gb >= 10 ? gb.toFixed(0) : gb.toFixed(1)} GB 内存`;
}

// 渲染单个 CPU/GPU 执行槽，并把加入资源池的操作绑定到槽位 ID。
function workerSlotCard(w) {
  const gpu = w.gpu || {};
  const cpu = w.cpu || {};
  const isGpu = Boolean(gpu.available);
  const device = isGpu
    ? `${gpu.name || "GPU"}${gpu.vram_mb ? ` · ${(Number(gpu.vram_mb) / 1024).toFixed(1)} GB 显存` : ""}`
    : `CPU${cpu.cores ? ` · ${cpu.cores} 线程` : ""}${cpu.memory_mb ? ` · ${formatMemory(cpu.memory_mb)}` : ""}`;
  const active = Boolean(w.active);
  const enabled = Boolean(w.enabled);
  const rawState = w.state || (active ? "idle" : "offline");
  const state = !active ? "offline" : (["running", "degraded", "recovering"].includes(rawState)
    ? rawState : (enabled ? rawState : "disabled"));
  const job = w.current_job ? `${w.current_job}${w.epoch ? ` · 第 ${w.epoch} 轮` : ""}` : "当前无任务";
  const heartbeat = w.heartbeat_age_sec != null ? `最近心跳 ${w.heartbeat_age_sec}s 前` : "尚无心跳";
  const action = w.managed
    ? `<button class="slot-action ${enabled ? "disable" : "enable"}" data-worker-id="${esc(w.worker_id)}" data-worker-enabled="${enabled ? "true" : "false"}" ${active ? "" : "disabled"}>${enabled ? "停止接单" : "加入资源池"}</button>`
    : `<span class="server-kind">平台内置</span>`;
  return `<div class="resource-slot ${enabled && active ? "enabled" : ""}">
    <div class="slot-main">
      <div class="slot-title"><span class="dot"></span>${isGpu ? "GPU" : "CPU"} · ${esc(w.slot || "计算槽")}
        <span class="wstate ${esc(state)}">${workerStateText[state] || esc(state)}</span></div>
      <div class="slot-details">${esc(device)} · ${esc(job)} · ${esc(heartbeat)}</div>
    </div>
    ${action}
  </div>`;
}

// 按服务器聚合计算槽、SSH 操作和远程生命周期按钮。
function workerServerCard(machineId, slots, remoteServer = null) {
  const online = slots.filter((slot) => slot.active).length;
  const managed = Boolean(remoteServer) || slots.some((slot) => slot.managed);
  const gpuCount = slots.filter((slot) => slot.gpu && slot.gpu.available).length;
  const cpuCount = slots.length - gpuCount;
  const cpu = slots.find((slot) => slot.cpu && slot.cpu.cores)?.cpu || {};
  const memory = formatMemory(cpu.memory_mb);
  const heartbeat = slots
    .filter((slot) => slot.heartbeat_age_sec != null)
    .map((slot) => Number(slot.heartbeat_age_sec));
  const lastSeen = heartbeat.length ? `${Math.min(...heartbeat).toFixed(0)} 秒前更新` : "等待首次心跳";
  const summary = [gpuCount ? `${gpuCount} 个 GPU 槽` : "", cpuCount ? `${cpuCount} 个 CPU 槽` : ""]
    .filter(Boolean).join(" · ") || "未发现计算槽";
  const deployed = Boolean(remoteServer && remoteServer.deployed);
  const operation = remoteServer && remoteServer.operation;
  const operationBusy = operation && ["queued", "running"].includes(operation.status);
  const cancelRequested = Boolean(operation && operation.cancel_requested);
  const remoteStatus = remoteServer ? {
    provisioning: "正在安装并检测资源", starting: "正在启动 worker", stopping: "正在停止 worker",
    restarting: "正在重启 worker", configuring: "正在写入 worker 配置", deploying: "正在部署 worker 代码",
    online: "worker 已启动", stopped: "worker 已停止",
    not_deployed: "尚未部署", error: "上次远程操作失败",
  }[remoteServer.status] || "SSH 服务器已登记" : "";
  const operationLabel = operation ? {
    queued: "操作排队中", running: cancelRequested ? "正在中断远程操作…" : operation.action === "config" ? "正在写入远端配置，重启后生效…" : ["deploy", "update"].includes(operation.action) ? "正在部署 worker 代码…" : operation.action === "install" ? "正在首次部署并构建镜像…" : `正在远程${operation.action}…`,
    completed: "最近操作已完成", failed: "最近操作失败", cancelled: "最近操作已中断",
  }[operation.status] || remoteStatus : remoteStatus;
  const operationPhaseLabels = {
    "检查部署配置": "检查部署配置", "检查远端 Docker 平台": "检查远端 Docker",
    "检查镜像架构": "比较镜像架构", "构建目标架构镜像": "构建目标架构镜像",
    "传输并加载 worker 镜像": "传输并加载镜像", "远端 docker load": "远端加载镜像",
    "同步 worker 代码": "同步 worker 代码", "覆盖 worker 配置": "覆盖 worker 配置",
    "执行远程部署脚本": "执行远端部署脚本", "等待资源心跳": "等待资源心跳",
  };
  const phaseLabel = operation && operationPhaseLabels[operation.phase];
  const phaseElapsed = operation && operation.phase_started_at
    ? Math.max(0, Math.floor(Date.now() / 1000 - Number(operation.phase_started_at))) : null;
  const lifecycleControls = !deployed ? "" : online
    ? `<button data-remote-machine-id="${esc(machineId)}" data-remote-action="stop" ${operationBusy ? "disabled" : ""}>停止</button>
      <button data-remote-machine-id="${esc(machineId)}" data-remote-action="restart" ${operationBusy ? "disabled" : ""}>重启</button>`
    : `<button data-remote-machine-id="${esc(machineId)}" data-remote-action="start" ${operationBusy ? "disabled" : ""}>启动</button>
      <button data-remote-machine-id="${esc(machineId)}" data-remote-action="restart" ${operationBusy ? "disabled" : ""}>重启</button>`;
  const serverControls = remoteServer ? `<div class="server-controls" aria-label="远程 worker 控制">
      <button class="server-log-button" data-remote-server-logs="${esc(machineId)}">日志</button>
      <button data-remote-effective-config="${esc(machineId)}">生效配置</button>
      <button data-edit-remote-machine-id="${esc(machineId)}" ${operationBusy ? "disabled" : ""}>更新配置</button>
      <button title="${deployed ? "同步最新 worker 代码并重建容器" : "首次部署 worker 镜像、配置和代码"}" data-remote-machine-id="${esc(machineId)}" data-remote-action="deploy" ${operationBusy ? "disabled" : ""}>${deployed ? "部署代码" : "部署"}</button>
      ${lifecycleControls}
      ${operationBusy ? `<button data-cancel-remote-operation="${esc(operation.operation_id)}" ${cancelRequested ? "disabled" : ""}>${cancelRequested ? "正在中断…" : "中断操作"}</button>` : ""}
    </div>` : "";
  const endpoint = remoteServer ? `${remoteServer.username}@${remoteServer.host}:${remoteServer.port} · ${remoteServer.mode.toUpperCase()}` : "";
  const operationDetails = operation && ["failed", "cancelled"].includes(operation.status) && operation.error
    ? `${operationLabel}：${operation.error}`
    : operationBusy && phaseLabel
      ? `${operationLabel} · ${phaseLabel}${phaseElapsed == null ? "" : ` · ${phaseElapsed} 秒`}`
      : operationLabel;
  const operationLog = operation && [operation.output, operation.stderr, operation.error]
    .filter((part) => typeof part === "string" && part.trim())
    .join("\n");
  return `<article class="server-card">
    <header class="server-head">
      <div class="server-head-main">
        <div class="server-title"><strong>${esc(machineId || "未命名服务器")}</strong>
          <span class="server-kind">${managed ? "远程服务器" : "平台内置"}</span>
          <span class="chip">${esc(summary)}</span></div>
        ${endpoint ? `<div class="server-meta">SSH ${esc(endpoint)}</div>` : ""}
      </div>
      <div class="server-head-actions">${serverControls}<span class="server-presence ${online ? "" : "offline"}">${online ? `在线 · ${online}/${slots.length} 槽` : remoteServer && operationBusy ? "远程操作中" : "离线"}</span></div>
    </header>
    ${remoteServer && operationDetails ? `<div class="server-operation" role="status">${esc(operationDetails)}</div>` : ""}
    ${slots.length
      ? `<div class="server-meta">${cpu.cores ? `CPU ${esc(cpu.cores)} 线程` : "CPU 信息未知"} · ${esc(memory)} · ${esc(lastSeen)}</div><div class="server-slots">${slots.map(workerSlotCard).join("")}</div>`
      : `<div class="server-meta">${remoteServer ? "尚未收到资源心跳；worker 启动后将自动检测 CPU / GPU。" : "等待计算资源心跳"}</div>`}
  </article>`;
}

// 读取仅在当前会话授权的远程服务器状态，缓存操作结果以减少重复请求。
async function loadRemoteServers() {
  try {
    const result = await apiJson("/api/remote-servers");
    const servers = result.servers || [];
    for (const server of servers) {
      if (!server.operation_id) continue;
      let operation = state.remoteOperations.get(server.operation_id);
      if (!operation || ["queued", "running"].includes(operation.status)) {
        try {
          operation = await apiJson(`/api/remote-operations/${encodeURIComponent(server.operation_id)}`);
          state.remoteOperations.set(server.operation_id, operation);
          if (["completed", "failed", "cancelled"].includes(operation.status) && !state.remoteOperationNotified.has(server.operation_id)) {
            state.remoteOperationNotified.add(server.operation_id);
            const message = operation.status === "completed"
              ? operation.action === "config" ? `${server.machine_id}：配置已写入，请重启后生效` : `${server.machine_id}：远程操作完成`
              : operation.status === "cancelled"
                ? `${server.machine_id}：远程操作已中断`
                : `${server.machine_id}：${operation.error || "远程操作失败"}`;
            toast(message, operation.status === "completed" ? "ok" : operation.status === "cancelled" ? "" : "err");
          }
        } catch (error) {
          operation = { status: "failed", error: error.message };
        }
      }
      server.operation = operation;
    }
    state.remoteServers = servers;
    return { servers, error: null, authorized: true };
  } catch (error) {
    state.remoteServers = [];
    return { servers: [], error: error.message, authorized: true };
  }
}

// 刷新服务器和槽位状态，同时合并 SSH 登记记录与 worker 心跳信息。
async function refreshWorkers() {
  if (state.refreshingWorkers) return;
  state.refreshingWorkers = true;
  try {
    const d = await apiJson("/api/workers");
    const grid = $("workerGrid");
    const workers = d.workers || [];
    state.workerSlots = workers;
    const remoteResult = await loadRemoteServers();
    const groups = new Map();
    for (const worker of workers) {
      const machineId = worker.machine_id || worker.hostname || "未命名服务器";
      if (!groups.has(machineId)) groups.set(machineId, []);
      groups.get(machineId).push(worker);
    }
    const remoteById = new Map(remoteResult.servers.map((server) => [server.machine_id, server]));
    for (const server of remoteResult.servers) if (!groups.has(server.machine_id)) groups.set(server.machine_id, []);
    const servers = [...groups.entries()].sort(([a], [b]) => a.localeCompare(b, "zh-CN"));
    const cards = servers.map(([machineId, slots]) => workerServerCard(machineId, slots, remoteById.get(machineId))).join("");
    const remoteError = remoteResult.error
      ? `<div class="empty">远程服务器列表读取失败：${esc(remoteResult.error)}。请检查连接后刷新。</div>` : "";
    grid.innerHTML = cards + remoteError || `<div class="empty">尚未添加计算服务器。点击「添加服务器」，由平台通过 SSH 安装 worker 并检测 CPU / GPU 资源。</div>`;
    const onlineSlots = workers.filter((worker) => worker.active).length;
    const selectedSlots = workers.filter((worker) => worker.active && worker.enabled).length;
    const pendingSlots = workers.filter((worker) => worker.managed && worker.active && !worker.enabled).length;
    const selectedGpuSlots = workers.filter((worker) => worker.active && worker.enabled && worker.gpu && worker.gpu.available).length;
    const onlineServers = new Set(workers.filter((worker) => worker.active).map((worker) => worker.machine_id || worker.hostname)).size;
    $("workerSummary").innerHTML = [
      `<span class="chip">在线服务器 ${onlineServers}</span>`,
      `<span class="chip">在线计算槽 ${onlineSlots}</span>`,
      `<span class="chip">已加入资源池 ${selectedSlots}</span>`,
      `<span class="chip">待选择 ${pendingSlots}</span>`,
      `<span class="chip">远程服务器 ${remoteResult.servers.length}</span>`,
    ].join("");
    $("resourceStatus").textContent = selectedGpuSlots
      ? `当前有 ${selectedGpuSlots} 个已启用 GPU 槽可接单。`
      : "当前没有已启用的 GPU 槽；要求 GPU 的任务会排队等待。可先添加服务器并启用 GPU 槽。";
    $("queueDepth").textContent = `队列 ${d.queue_depth ?? 0}`;
  } catch {
    $("resourceStatus").textContent = "计算资源状态暂不可用，请检查连接后刷新。";
    if (!state.workerSlots.length && !state.remoteServers.length) $("workerGrid").innerHTML = '<div class="empty">读取服务器失败，请点击「刷新状态」重试。</div>';
  } finally {
    state.refreshingWorkers = false;
  }
}

// 保存弹窗会话与初始表单状态，异步响应仅作用于发起请求的这一轮弹窗。
const remoteServerDialog = {
  session: 0, credentialLoad: 0, loading: false, loadFailed: false, saving: false,
  initialFields: new Map(), trigger: null,
};

// 读取可编辑字段状态；初始快照不复制已保存的密码或私钥。
function remoteFormSnapshot(form) {
  return new Map([...form.elements].filter((field) => field.matches("input,select,textarea"))
    .map((field) => [field, ["checkbox", "radio"].includes(field.type) ? field.checked : field.value]));
}

// 比较当前字段与打开时的状态，用于取消和 Escape 的未保存提醒。
function remoteServerDialogIsDirty() {
  return [remoteServerDialog.initialFields].some((snapshot) =>
    [...snapshot].some(([field, value]) => (["checkbox", "radio"].includes(field.type) ? field.checked : field.value) !== value));
}

// 在操作栏展示错误；仅使用文本，避免回显 HTML 或凭证明文。
function showRemoteServerError(message = "") {
  $("remoteServerError").textContent = message;
  $("remoteServerError").hidden = !message;
}

// 切换计算类型时同步隐藏和校验状态，GPU 不校验不可见的 CPU 槽输入。
function updateRemoteServerMode() {
  const cpu = $("remoteServerForm").querySelector('input[name="mode"]:checked').value === "cpu";
  $("remoteCpuSlotsField").hidden = !cpu;
  $("remoteCpuSlots").disabled = !cpu;
}

// 请求期间锁住服务器表单和关闭入口，防止重复提交与请求结果串到新会话。
function updateRemoteServerBusyState() {
  const busy = remoteServerDialog.saving;
  $("addServerModal").setAttribute("aria-busy", String(busy));
  $("addServerModal").querySelector(".server-modal-body").inert = busy;
  $("remoteServerForm").inert = busy;
  ["btnSubmitRemoteServer", "btnCloseAddServer", "btnCancelAddServer", "btnRefreshRemoteCredentials"]
    .forEach((id) => { $(id).disabled = busy; });
  if (busy && !$("addServerModal").hidden) $("addServerModal").querySelector(".server-dialog-card").focus();
}

// 打开弹窗时建立基线、锁定背景滚动，并把焦点移到首个连接字段。
function showRemoteServerDialog(focusId) {
  remoteServerDialog.session += 1;
  remoteServerDialog.trigger = document.activeElement;
  remoteServerDialog.initialFields = remoteFormSnapshot($("remoteServerForm"));
  $("remoteServerAdvanced").open = false;
  showRemoteServerError();
  $("remoteServerForm").querySelectorAll('[aria-invalid]').forEach((field) => field.removeAttribute("aria-invalid"));
  updateRemoteServerMode();
  updateRemoteServerBusyState();
  $("addServerModal").hidden = false;
  $("addServerModal").classList.add("open");
  document.body.classList.add("server-dialog-open");
  $("addServerModal").querySelector(".server-modal-body").scrollTop = 0;
  $(focusId).focus();
}

// 校验前展开错误字段所在分组，保证错误可见且可通过键盘修正。
function focusRemoteServerInvalidField(field, message) {
  for (let parent = field.parentElement; parent && parent !== $("addServerModal"); parent = parent.parentElement) {
    if (parent.tagName === "DETAILS") parent.open = true;
  }
  field.setAttribute("aria-invalid", "true");
  showRemoteServerError(message);
  field.focus();
  field.reportValidity();
}

// 提交前按浏览器约束校验所有有效字段，包括折叠的高级项和凭证空状态。
function validateRemoteServerForm() {
  const select = $("remoteCredentialSelect");
  if (remoteServerDialog.loading) {
    showRemoteServerError("正在读取 SSH 凭证，请稍候再提交。");
    return false;
  }
  for (const field of $("remoteServerForm").elements) {
    if (field.willValidate && !field.checkValidity()) {
      const label = field.labels?.[0]?.textContent.trim() || "字段";
      focusRemoteServerInvalidField(field, `请检查${label}：${field.validationMessage}`);
      return false;
    }
  }
  if (!select.value) {
    showRemoteServerError("请先添加并选择 SSH 凭证，再保存服务器。");
    $("btnManageRemoteCredentials").focus();
    return false;
  }
  showRemoteServerError();
  return true;
}

// Tab 在可见且可用的控件之间循环，Escape 统一经过关闭保护。
function handleRemoteServerDialogKeydown(event) {
  if ($("addServerModal").hidden) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeAddServerModal();
    return;
  }
  if (event.key !== "Tab") return;
  const fields = [...$("addServerModal").querySelectorAll('button,input,select,textarea,summary,a[href],[tabindex="0"]')]
    .filter((field) => !field.disabled && !field.closest("[inert]") && field.getClientRects().length);
  const first = fields[0];
  const last = fields[fields.length - 1];
  if (!first) {
    event.preventDefault();
    $("addServerModal").querySelector(".server-dialog-card").focus();
  } else if (event.shiftKey && (document.activeElement === first || !fields.includes(document.activeElement))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (document.activeElement === last || !fields.includes(document.activeElement))) {
    event.preventDefault();
    first.focus();
  }
}

// 更新服务器凭证下拉框，保留选择并展示凭证公开元数据。
function renderRemoteCredentialOptions() {
  const select = $("remoteCredentialSelect");
  const currentId = select.value;
  const selectedId = state.remoteCredentials.some((item) => item.id === currentId)
    ? currentId : state.remoteCredentialDefaultId;
  const methodName = { password: "用户名 + 密码", key: "用户名 + 密钥", key_password: "用户名 + 密钥 + 密码" };
  select.innerHTML = `<option value="">${state.remoteCredentials.length ? "选择 SSH 凭证" : "请先配置 SSH 凭证"}</option>`
    + state.remoteCredentials.map((item) => `<option value="${esc(item.id)}">${esc(item.name)} · ${esc(item.username)} · ${esc(methodName[item.auth_method] || item.auth_method)}${item.is_default ? " · 默认" : ""}</option>`).join("");
  select.value = selectedId || "";
  select.disabled = state.remoteCredentials.length === 0;
}

// 打开新服务器表单并恢复默认选项，凭证在弹窗展示后异步读取。
function openAddServerModal() {
  state.editingRemoteMachineId = null;
  $("remoteServerForm").reset();
  $("remoteMachineId").readOnly = false;
  $("addServerTitle").textContent = "添加服务器";
  $("btnCloseAddServer").setAttribute("aria-label", "关闭添加服务器弹窗");
  $("btnSubmitRemoteServer").textContent = "登记服务器配置";
  $("remoteConfigApplyHint").textContent = "登记配置后，可在服务器卡片上单独部署。";
  $("clearRemoteRedisOverrideField").hidden = true;
  $("clearRemoteRedisOverride").checked = false;
  $("remoteRedisUrl").disabled = false;
  $("remoteRedisUrl").placeholder = "留空时使用平台 .env 的 Redis";
  $("remoteRedisUrlHint").textContent = "可使用 redis:// 或 rediss://；URL 中的密码会加密保存。";
  renderRemoteCredentialOptions();
  showRemoteServerDialog("remoteMachineId");
  loadRemoteSshSettings();
}

// 载入已登记服务器配置，并打开编辑表单；Redis URL 不回显明文。
async function openEditRemoteServer(machineId) {
  const server = state.remoteServers.find((item) => item.machine_id === machineId);
  if (!server) return;
  $("remoteServerForm").reset();
  renderRemoteCredentialOptions();
  state.editingRemoteMachineId = machineId;
  $("remoteMachineId").value = server.machine_id;
  $("remoteMachineId").readOnly = true;
  $("remoteHost").value = server.host || "";
  $("remoteCredentialSelect").value = server.ssh_credential_id || "";
  $("remoteUsername").value = server.username_override !== undefined
    ? (server.username_override || "") : (server.username || "");
  $("remotePort").value = server.port || 22;
  document.querySelector(`input[name="mode"][value="${server.mode === "cpu" ? "cpu" : "gpu"}"]`).checked = true;
  $("remoteDeviceBackend").value = server.device_backend || "auto";
  $("remoteCpuSlots").value = server.cpu_slots ?? 2;
  $("remoteDistributedHost").value = server.distributed_host || "";
  $("remoteCpuSlotsField").hidden = server.mode !== "cpu";
  $("remoteRedisUrl").value = "";
  $("remoteRedisUrl").placeholder = server.has_remote_redis_url_override
    ? "已设置服务器专属值；留空使用平台 .env"
    : "留空时使用平台 .env 的 Redis";
  $("remoteRedisUrlHint").textContent = server.has_remote_redis_url_override
    ? "服务器 Redis URL 已加密保存，不会回显；输入新值可替换，留空会回退平台 .env。"
    : "可使用 redis:// 或 rediss://；URL 中的密码会加密保存，留空使用平台 .env。";
  $("clearRemoteRedisOverrideField").hidden = !server.has_remote_redis_url_override;
  $("clearRemoteRedisOverride").checked = false;
  $("remoteRedisUrl").disabled = false;
  $("remoteS3Endpoint").value = server.remote_s3_endpoint_override || "";
  $("remoteDockerRegistry").value = server.docker_registry || "";
  $("remotePipIndexUrl").value = server.pip_index_url || "";
  $("remoteAptMirror").value = server.apt_mirror || "";
  $("addServerTitle").textContent = `编辑服务器 ${machineId}`;
  $("btnCloseAddServer").setAttribute("aria-label", "关闭编辑服务器弹窗");
  $("btnSubmitRemoteServer").textContent = server.deployed ? "更新配置" : "保存配置";
  $("remoteConfigApplyHint").textContent = server.deployed
    ? "保存后覆盖远端配置文件，下次启动/重启时生效（不影响当前运行中的 worker）。"
    : "服务器尚未部署；保存后可在服务器卡片上点击“部署”。";
  showRemoteServerDialog("remoteHost");
  loadRemoteSshSettings(server.ssh_credential_id);
}

// 关闭接入引导并把焦点交还给触发按钮。
function closeAddServerModal(force = false) {
  const modal = $("addServerModal");
  if (remoteServerDialog.saving) return;
  if (force !== true && remoteServerDialogIsDirty() && !window.confirm("有尚未保存的修改，确定放弃并关闭？")) return;
  remoteServerDialog.session += 1;
  remoteServerDialog.credentialLoad += 1;
  remoteServerDialog.loading = false;
  modal.classList.remove("open");
  modal.hidden = true;
  state.editingRemoteMachineId = null;
  $("remoteServerForm").reset();
  $("remoteMachineId").readOnly = false;
  document.body.classList.remove("server-dialog-open");
  const trigger = remoteServerDialog.trigger;
  (trigger?.isConnected ? trigger : $("btnAddServer")).focus();
}

// 登记未部署服务器，或保存配置后覆盖已部署服务器的远程配置文件。
async function submitRemoteServer(event) {
  event.preventDefault();
  if (remoteServerDialog.saving || !validateRemoteServerForm()) return;
  const form = $("remoteServerForm");
  const submit = $("btnSubmitRemoteServer");
  const payload = Object.fromEntries(new FormData(form).entries());
  payload.username = payload.username.trim() || null;
  payload.port = Number(payload.port);
  payload.cpu_slots = Number($("remoteCpuSlots").value || 0);
  payload.device_backend = $("remoteDeviceBackend").value || "auto";
  payload.remote_redis_url = String(payload.remote_redis_url || "").trim();
  payload.remote_s3_endpoint = payload.remote_s3_endpoint.trim() || null;
  payload.docker_registry = String(payload.docker_registry || "").trim() || null;
  payload.pip_index_url = String(payload.pip_index_url || "").trim() || null;
  payload.apt_mirror = String(payload.apt_mirror || "").trim() || null;
  payload.clear_remote_redis_url_override = $("clearRemoteRedisOverride").checked;
  const editingMachineId = state.editingRemoteMachineId;
  if (editingMachineId) delete payload.machine_id;
  remoteServerDialog.saving = true;
  updateRemoteServerBusyState();
  submit.disabled = true;
  submit.textContent = "正在提交…";
  try {
    const result = await apiJson(editingMachineId
      ? `/api/remote-servers/${encodeURIComponent(editingMachineId)}`
      : "/api/remote-servers", {
      method: editingMachineId ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    toast(editingMachineId
      ? result.operation_id ? "配置已保存，正在写入远端；操作成功后请点击“重启”使其生效" : "配置已保存；服务器部署后才会写入远程配置"
      : "服务器配置已登记；请在服务器卡片上点击“部署”", "ok");
    remoteServerDialog.saving = false;
    closeAddServerModal(true);
    refreshWorkers();
  } catch (error) {
    showRemoteServerError("服务器配置保存失败，请检查输入或连接后重试。");
    toast(error.message, "err");
  } finally {
    remoteServerDialog.saving = false;
    updateRemoteServerBusyState();
    submit.disabled = false;
    const savedServer = state.remoteServers.find((server) => server.machine_id === editingMachineId);
    submit.textContent = editingMachineId
      ? savedServer && savedServer.deployed ? "更新配置" : "保存配置"
      : "登记服务器配置";
  }
}

// 通过平台 SSH 启动、停止或重启服务器上的 worker 容器。
async function manageRemoteServer(button) {
  const machineId = button.dataset.remoteMachineId;
  const action = button.dataset.remoteAction;
  const actionText = ["install", "deploy"].includes(action)
    ? button.textContent.trim()
    : { start: "启动", stop: "停止", restart: "重启" }[action];
  if (action !== "start" && !window.confirm(`确认${actionText} ${machineId} 上的 worker？`)) return;
  button.disabled = true;
  try {
    await apiJson(`/api/remote-servers/${encodeURIComponent(machineId)}/${action}`, { method: "POST" });
    toast(`${machineId}：已提交${actionText}操作`, "ok");
    await refreshWorkers();
  } catch (error) {
    toast(error.message, "err");
    button.disabled = false;
  }
}

// 打开远程日志窗口，并从操作状态接口读取该服务器最近一次操作的输出。
async function showRemoteServerLogs(button) {
  const machineId = button.dataset.remoteServerLogs;
  const server = state.remoteServers.find((item) => item.machine_id === machineId);
  const modal = $("remoteLogModal");
  const output = $("remoteLogOutput");
  const status = $("remoteLogStatus");
  stopRemoteLogPolling();
  output.dataset.rawLog = "";
  $("remoteLogTitle").textContent = `${machineId} · 执行日志`;
  modal.hidden = false;
  modal.classList.add("open");
  renderRemoteLogLines(output, ["正在读取日志…"]);
  status.textContent = "";
  if (!server || !server.operation_id) {
    state.remoteLogOperationId = null;
    status.textContent = "该服务器还没有远程操作记录。";
    renderRemoteLogLines(output, ["暂无执行日志"]);
    return;
  }
  state.remoteLogOperationId = server.operation_id;
  await refreshRemoteOperationLogs();
}

// 打开只读配置窗口，并读取平台当前解析出的远程配置摘要。
async function showRemoteEffectiveConfig(button) {
  const machineId = button.dataset.remoteEffectiveConfig;
  const modal = $("remoteEffectiveConfigModal");
  const status = $("remoteEffectiveConfigStatus");
  const output = $("remoteEffectiveConfigOutput");
  modal.hidden = false;
  modal.classList.add("open");
  status.textContent = "正在读取生效配置…";
  output.innerHTML = "";
  try {
    const result = await apiJson("/api/remote-servers/" + encodeURIComponent(machineId) + "/effective-config");
    renderRemoteEffectiveConfig(result.config || {});
    status.textContent = machineId + " · 平台当前解析结果";
  } catch (error) {
    status.textContent = "读取生效配置失败。";
    output.innerHTML = "<div class=\"server-modal-note\">" + esc(error.message) + "</div>";
  }
}

// 将配置摘要渲染为只读键值行，并为来源与敏感字段提供明确提示。
function renderRemoteEffectiveConfig(config) {
  const sourceLabels = {
    server_override: "服务器专属覆盖",
    WORKER_REMOTE_REDIS_URL: "平台 WORKER_REMOTE_REDIS_URL",
    REDIS_URL: "平台 .env REDIS_URL",
    WORKER_REMOTE_S3_ENDPOINT: "平台 WORKER_REMOTE_S3_ENDPOINT",
    S3_ENDPOINT: "平台 .env S3_ENDPOINT",
    WORKER_REMOTE_IMAGE: "平台 WORKER_REMOTE_IMAGE / WORKER_IMAGE",
  };
  const rows = [
    ["目标状态", config.deployed ? "已部署 · " + (config.status || "未知") : "尚未部署", ""],
    ["SSH 地址", (config.host || "未知") + ":" + (config.port ?? "未知"), ""],
    ["计算类型", String(config.mode || "未知").toUpperCase(), "CPU 槽位：" + (config.cpu_slots ?? "未知")],
    ["Worker 镜像", config.worker_image || "未配置", sourceLabels[config.worker_image_source] || "平台配置"],
    ["Redis 地址", config.redis_configured ? (config.redis_endpoint || "已配置但无法解析") : "未配置", (sourceLabels[config.redis_source] || "平台配置") + " · " + (config.redis_auth_configured ? "认证信息已配置且隐藏" : "未检测到认证信息")],
    ["S3 地址", config.s3_endpoint || "未配置", sourceLabels[config.s3_source] || "平台配置"],
    ["S3 Bucket", config.s3_bucket || "未配置", config.s3_secure ? "HTTPS" : "HTTP"],
  ];
  $("remoteEffectiveConfigOutput").innerHTML = rows.map(([label, value, source]) =>
    "<div class=\"server-effective-config-row\"><div class=\"server-effective-config-label\">" +
    esc(label) + "</div><div class=\"server-effective-config-value\">" + esc(value) +
    (source ? "<span class=\"server-effective-config-source\">" + esc(source) + "</span>" : "") +
    "</div></div>"
  ).join("");
}

// 轮询当前远程操作日志，使运行阶段和命令输出在操作过程中可见。
async function refreshRemoteOperationLogs() {
  const operationId = state.remoteLogOperationId;
  const modal = $("remoteLogModal");
  if (!operationId || modal.hidden) return;
  const output = $("remoteLogOutput");
  const status = $("remoteLogStatus");
  try {
    const operation = await apiJson(`/api/remote-operations/${encodeURIComponent(operationId)}`);
    state.remoteOperations.set(operation.operation_id, operation);
    const fallbackLog = [operation.output, operation.stderr, operation.error]
      .filter((part) => typeof part === "string" && part.trim()).join("\n");
    const log = typeof operation.log === "string" && operation.log.trim()
      ? operation.log : fallbackLog;
    const stateText = {
      queued: "操作排队中；开始执行后会显示阶段和日志。",
      running: "操作执行中；日志会自动刷新。",
      completed: "操作已完成。",
      failed: "操作失败。",
      cancelled: "操作已中断。",
    }[operation.status] || `操作状态：${operation.status || "未知"}`;
    const phaseAge = operation.phase_started_at
      ? `，已持续 ${Math.max(0, Math.floor(Date.now() / 1000 - Number(operation.phase_started_at)))} 秒` : "";
    const phaseText = operation.phase ? ` · 当前阶段：${operation.phase}${phaseAge}` : "";
    status.textContent = `${operation.action || "远程操作"} · ${stateText}${phaseText}`;
    const displayLog = log || (operation.status === "queued" || operation.status === "running"
      ? "等待远程命令输出；若停留在某阶段，可根据该阶段判断检查位置。"
      : "暂无执行日志");
    const wasAtBottom = output.scrollHeight - output.scrollTop - output.clientHeight < 48;
    output.dataset.rawLog = displayLog;
    renderRemoteLogLines(output, displayLog.split("\n"));
    if (wasAtBottom) output.scrollTop = output.scrollHeight;
    if (["queued", "running"].includes(operation.status)
        && !modal.hidden && state.remoteLogOperationId === operationId) {
      state.remoteLogPollTimer = setTimeout(refreshRemoteOperationLogs, 1500);
    } else {
      state.remoteLogPollTimer = null;
    }
  } catch (error) {
    status.textContent = "读取执行日志失败。";
    renderRemoteLogLines(output, [error.message]);
    if (!modal.hidden) state.remoteLogPollTimer = setTimeout(refreshRemoteOperationLogs, 2000);
  }
}

// 清除当前远程操作的日志输出，保留操作状态并继续轮询运行中的操作。
async function clearRemoteOperationLogs() {
  const operationId = state.remoteLogOperationId;
  if (!operationId) return;
  const button = $("btnClearRemoteLog");
  button.disabled = true;
  try {
    await apiJson(`/api/remote-operations/${encodeURIComponent(operationId)}/logs`, { method: "DELETE" });
    if (state.remoteLogOperationId !== operationId || $("remoteLogModal").hidden) return;
    $("remoteLogOutput").dataset.rawLog = "";
    renderRemoteLogLines($("remoteLogOutput"), ["暂无执行日志"]);
    toast("执行日志已清除", "ok");
    await refreshRemoteOperationLogs();
  } catch (error) {
    toast(error.message || "清除执行日志失败", "error");
  } finally {
    if (state.remoteLogOperationId === operationId) button.disabled = false;
  }
}

// 停止日志轮询，避免关闭窗口后继续请求远程操作状态。
function stopRemoteLogPolling() {
  if (state.remoteLogPollTimer !== null) clearTimeout(state.remoteLogPollTimer);
  state.remoteLogPollTimer = null;
}

// 逐行创建日志文本和复制按钮，文本通过 textContent 写入避免执行远程输出内容。
function renderRemoteLogLines(container, lines) {
  container.replaceChildren();
  lines.forEach((line, index) => {
    const row = document.createElement("div");
    row.className = "server-log-line";
    const text = document.createElement("span");
    text.className = "server-log-line-text";
    text.textContent = line || " ";
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "server-log-line-copy";
    copy.dataset.copyRemoteLogLine = String(index);
    copy.textContent = "复制";
    copy.setAttribute("aria-label", `复制第 ${index + 1} 行日志`);
    row.append(text, copy);
    container.appendChild(row);
  });
}

// 复制日志行或全部日志，并兼容未授予 Clipboard API 权限的页面环境。
async function copyRemoteLogText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (error) {
    const fallback = document.createElement("textarea");
    fallback.value = text;
    fallback.style.position = "fixed";
    fallback.style.opacity = "0";
    document.body.appendChild(fallback);
    fallback.select();
    const copied = document.execCommand("copy");
    fallback.remove();
    if (!copied) throw error;
  }
  toast("日志已复制", "ok");
}

// 关闭远程日志窗口。
function closeRemoteLogModal() {
  const modal = $("remoteLogModal");
  stopRemoteLogPolling();
  state.remoteLogOperationId = null;
  modal.classList.remove("open");
  modal.hidden = true;
}

// 关闭只读远程生效配置窗口。
function closeRemoteEffectiveConfigModal() {
  const modal = $("remoteEffectiveConfigModal");
  modal.classList.remove("open");
  modal.hidden = true;
}

// 中断当前 SSH 操作；远端已经产生的部分变更不会自动回滚。
async function cancelRemoteOperation(button) {
  const operationId = button.dataset.cancelRemoteOperation;
  if (!window.confirm("确认中断这项远程操作？Linux 上已经完成的部分变更不会自动回滚。")) return;
  button.disabled = true;
  button.textContent = "正在中断…";
  try {
    const result = await apiJson(`/api/remote-operations/${encodeURIComponent(operationId)}/cancel`, { method: "POST" });
    const operation = state.remoteOperations.get(operationId) || { operation_id: operationId };
    if (result.status === "cancelled") operation.status = "cancelled";
    operation.cancel_requested = result.status === "cancelling";
    state.remoteOperations.set(operationId, operation);
    toast(result.status === "cancelled" ? "远程操作已中断" : "已发送中断请求，正在停止 SSH 操作", "ok");
    await refreshWorkers();
  } catch (error) {
    toast(error.message, "err");
    button.disabled = false;
    button.textContent = "中断操作";
  }
}

// 修改一个外接槽位是否接收新任务，并立即刷新状态。
async function toggleWorkerSelection(button) {
  const workerId = button.dataset.workerId;
  const enabled = button.dataset.workerEnabled !== "true";
  button.disabled = true;
  try {
    const result = await apiJson(`/api/workers/${encodeURIComponent(workerId)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    toast(result.message, "ok");
    await refreshWorkers();
  } catch (error) {
    toast(error.message, "err");
    button.disabled = false;
  }
}

// 按凭证认证组合显示秘密输入项，并只校验当前需要的字段。
function updateRemoteSshCredentialFields(clearHidden = true) {
  const method = $("remoteSshAuthMethod").value;
  const useKey = method === "key" || method === "key_password";
  const usePassword = method === "password" || method === "key_password";
  $("remoteSshKeyFields").hidden = !useKey;
  $("remoteSshPassphraseField").hidden = !useKey;
  $("remoteSshPasswordField").hidden = !usePassword;
  $("remoteSshPrivateKey").required = useKey;
  $("remoteSshPassword").required = usePassword;
  if (clearHidden) {
    if (!usePassword) $("remoteSshPassword").value = "";
    if (!useKey) {
      $("remoteSshPrivateKey").value = "";
      $("remoteSshPassphrase").value = "";
    }
  }
}


// 凭证页的编辑与读取状态独立于服务器弹窗。
const credentialEditor = { busy: false, loading: false, request: 0, initialFields: new Map() };

// 按服务器弹窗的会话号加载凭证；旧响应不能覆盖重新打开的表单。
async function loadRemoteSshSettings(preferredId = null) {
  const request = ++remoteServerDialog.credentialLoad;
  const session = remoteServerDialog.session;
  const select = $("remoteCredentialSelect");
  const previous = select.value;
  remoteServerDialog.loading = true;
  remoteServerDialog.loadFailed = false;
  $("remoteCredentialStatus").textContent = "正在读取 SSH 凭证…";
  try {
    const catalog = await apiJson("/api/remote-ssh-credentials");
    if (request !== remoteServerDialog.credentialLoad || session !== remoteServerDialog.session) return;
    state.remoteCredentials = catalog.credentials || [];
    state.remoteCredentialDefaultId = catalog.default_credential_id || null;
    const unchanged = select.value === previous;
    renderRemoteCredentialOptions();
    if (unchanged && preferredId && state.remoteCredentials.some(item => item.id === preferredId)) select.value = preferredId;
    if (unchanged && remoteServerDialog.initialFields.get(select) === previous) remoteServerDialog.initialFields.set(select, select.value);
    $("remoteCredentialStatus").textContent = state.remoteCredentials.length
      ? "凭证在独立页面维护，修改后可点击刷新。" : "暂无凭证，请前往「凭证管理」新增后返回。";
    showRemoteServerError();
  } catch (error) {
    if (request !== remoteServerDialog.credentialLoad || session !== remoteServerDialog.session) return;
    state.remoteCredentials = [];
    state.remoteCredentialDefaultId = null;
    renderRemoteCredentialOptions();
    if (remoteServerDialog.initialFields.get(select) === previous) remoteServerDialog.initialFields.set(select, select.value);
    remoteServerDialog.loadFailed = true;
    $("remoteCredentialStatus").textContent = "凭证读取失败，请点击刷新重试。";
    showRemoteServerError(error.message);
  } finally {
    if (request === remoteServerDialog.credentialLoad && session === remoteServerDialog.session) remoteServerDialog.loading = false;
  }
}

// 绘制公开凭证元数据，不读取或渲染任何秘密。
function renderRemoteCredentialList() {
  const methods = { password: "用户名 + 密码", key: "用户名 + 密钥", key_password: "密钥优先，密码回退" };
  $("remoteSshCredentialList").innerHTML = state.remoteCredentials.map(item => `
    <article class="credential-row">
      <div class="credential-description"><strong>${esc(item.name)}</strong>${item.is_default ? '<span class="chip">默认</span>' : ''}
        <p>${esc(item.username)} · ${esc(methods[item.auth_method] || item.auth_method)}</p></div>
      <div class="compute-actions"><button type="button" data-ssh-credential-action="edit" data-ssh-credential-id="${esc(item.id)}" ${state.remoteCredentialsCanConfigure ? '' : 'disabled'}>编辑</button>
      <button type="button" data-ssh-credential-action="delete" data-ssh-credential-id="${esc(item.id)}" ${state.remoteCredentialsCanConfigure ? '' : 'disabled'}>删除</button></div>
    </article>`).join('') || '<div class="empty">暂无凭证。在右侧填写第一条 SSH 凭证，即可供服务器使用。</div>';
}

// 应用凭证目录而不重置正在编辑的表单。
function applyCredentialCatalog(catalog) {
  state.remoteCredentials = catalog.credentials || [];
  state.remoteCredentialDefaultId = catalog.default_credential_id || null;
  state.remoteCredentialsCanConfigure = Boolean(catalog.can_configure);
  renderRemoteCredentialList();
  $("remoteSshSettingsStatus").textContent = `${state.remoteCredentials.length} 条凭证已加密保存在 MySQL；页面不会显示已保存的秘密。`
    + (catalog.can_configure ? '' : ' 当前无法维护凭证，请检查存储与加密配置。');
}

// 目录刷新独立于编辑状态，保存时使旧目录请求失效。
async function loadCredentialCatalog() {
  if (credentialEditor.busy) return;
  const request = ++credentialEditor.request;
  credentialEditor.loading = true;
  $("remoteSshSettingsStatus").textContent = "正在读取凭证目录…";
  try {
    const catalog = await apiJson("/api/remote-ssh-credentials");
    if (request === credentialEditor.request) applyCredentialCatalog(catalog);
  } catch (error) {
    if (request === credentialEditor.request) $("remoteSshSettingsStatus").textContent = `读取失败：${error.message}；请刷新重试。`;
  } finally {
    if (request === credentialEditor.request) credentialEditor.loading = false;
  }
}

// 比较独立凭证表单的初始状态，用于重置、切换编辑对象与离开提醒。
function credentialFormIsDirty() {
  return [...credentialEditor.initialFields].some(([field, value]) =>
    (["checkbox", "radio"].includes(field.type) ? field.checked : field.value) !== value);
}

// 错误只作为纯文本呈现，避免凭证名称或异常内容注入 HTML。
function showCredentialError(message = "") {
  $("credentialFormError").textContent = message;
  $("credentialFormError").hidden = !message;
}

// 清空凭证编辑状态与秘密，主动重置时先保护未保存输入。
function resetRemoteSshCredentialForm(force = false) {
  if (force !== true && (credentialEditor.busy || (credentialFormIsDirty() && !window.confirm("有尚未保存的凭证修改，确定放弃？")))) return false;
  $("remoteSshCredentialForm").reset();
  $("remoteSshCredentialId").value = "";
  $("credentialEditorTitle").textContent = "新增凭证";
  $("btnSaveRemoteSshSettings").textContent = "加密保存凭证";
  updateRemoteSshCredentialFields();
  credentialEditor.initialFields = remoteFormSnapshot($("remoteSshCredentialForm"));
  showCredentialError();
  return true;
}

// 锁定凭证页的所有变更入口，保留页面导航的离开保护。
function setCredentialBusy(busy) {
  credentialEditor.busy = busy;
  $("remoteSshCredentialForm").inert = busy;
  $("remoteSshCredentialList").inert = busy;
  $("credentialEditor").setAttribute("aria-busy", String(busy));
  ["btnSaveRemoteSshSettings", "btnCancelEditRemoteSshCredential", "btnNewCredential", "btnRefreshCredentials"].forEach(id => { $(id).disabled = busy; });
  if (busy) $("credentialEditorTitle").focus();
}

// 完整提交当前认证组合；成功后留在凭证页并清空秘密，失败保留输入。
async function saveRemoteSshSettings() {
  if (credentialEditor.busy) return;
  const form = $("remoteSshCredentialForm");
  updateRemoteSshCredentialFields(false);
  if (!form.reportValidity()) { showCredentialError("请填写凭证名称、用户名及当前认证方式要求的内容。"); return; }
  const method = $("remoteSshAuthMethod").value;
  const payload = { name: $("remoteSshCredentialName").value.trim(), username: $("remoteSshCredentialUsername").value.trim(), auth_method: method, make_default: $("remoteSshMakeDefault").checked };
  if (method === "key" || method === "key_password") { payload.private_key = $("remoteSshPrivateKey").value; payload.passphrase = $("remoteSshPassphrase").value || null; }
  if (method === "password" || method === "key_password") payload.password = $("remoteSshPassword").value;
  const id = $("remoteSshCredentialId").value;
  const button = $("btnSaveRemoteSshSettings");
  button.disabled = true;
  credentialEditor.request += 1;
  credentialEditor.loading = false;
  setCredentialBusy(true);
  showCredentialError();
  try {
    const catalog = await apiJson(id ? `/api/remote-ssh-credentials/${encodeURIComponent(id)}` : "/api/remote-ssh-credentials", {
      method: id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    applyCredentialCatalog(catalog);
    resetRemoteSshCredentialForm(true);
    toast("SSH 凭证已加密保存，可在服务器管理中选择使用", "ok");
  } catch (error) { showCredentialError(error.message); }
  finally { setCredentialBusy(false); button.disabled = false; }
}

// 编辑公开元数据，重新填写秘密；切换对象前保护现有草稿。
function editRemoteSshCredential(id) {
  if (credentialEditor.busy) return;
  const credential = state.remoteCredentials.find(item => item.id === id);
  if (!credential || !resetRemoteSshCredentialForm()) return;
  $("remoteSshCredentialId").value = credential.id;
  $("remoteSshCredentialName").value = credential.name;
  $("remoteSshCredentialUsername").value = credential.username;
  $("remoteSshAuthMethod").value = credential.auth_method;
  $("remoteSshMakeDefault").checked = Boolean(credential.is_default);
  $("credentialEditorTitle").textContent = `编辑凭证：${credential.name}`;
  $("btnSaveRemoteSshSettings").textContent = "加密保存修改";
  updateRemoteSshCredentialFields();
  credentialEditor.initialFields = remoteFormSnapshot($("remoteSshCredentialForm"));
  $("remoteSshCredentialName").focus();
}

// 删除前确认，引用冲突由服务端判断；失败不清空目录或草稿。
async function deleteRemoteSshCredential(id) {
  if (credentialEditor.busy) return;
  const credential = state.remoteCredentials.find(item => item.id === id);
  if (!credential || !window.confirm(`确认删除 SSH 凭证“${credential.name}”？被服务器使用的凭证不能删除。`)) return;
  if (id === $("remoteSshCredentialId").value && credentialFormIsDirty() && !window.confirm("该凭证还有未保存修改，确定放弃并删除？")) return;
  credentialEditor.request += 1;
  credentialEditor.loading = false;
  setCredentialBusy(true);
  showCredentialError();
  try {
    const catalog = await apiJson(`/api/remote-ssh-credentials/${encodeURIComponent(id)}`, { method: "DELETE" });
    applyCredentialCatalog(catalog);
    if (id === $("remoteSshCredentialId").value) resetRemoteSshCredentialForm(true);
    toast("SSH 凭证已删除", "ok");
  } catch (error) { showCredentialError(error.message); }
  finally { setCredentialBusy(false); }
}

// 页面切换只检查本页状态，服务器和凭证之间不共享草稿或秘密。
function managementIsDirty() {
  return $("remoteSshCredentialForm") ? credentialFormIsDirty() : !$("addServerModal").hidden && remoteServerDialogIsDirty();
}

// 主动跳转经过一次确认，避免浏览器再次弹出相同的离开提醒。
function confirmManagementNavigation() {
  if (credentialEditor.busy || remoteServerDialog.saving) { toast("正在保存，请稍候再离开", "err"); return false; }
  if (managementIsDirty() && !window.confirm("有尚未保存的修改，确定放弃并离开？")) return false;
  clearManagementSecrets();
  return true;
}

// 页面离开时清空秘密与初始快照，避免浏览器往返缓存恢复密码。
function clearManagementSecrets() {
  ["remoteSshPrivateKey", "remoteSshPassword", "remoteSshPassphrase", "remoteRedisUrl"].forEach(id => { if ($(id)) $(id).value = ""; });
  credentialEditor.initialFields = new Map();
  remoteServerDialog.initialFields = new Map();
}

// 按页面绑定各自的表单和列表，直接访问任何 Tab 都可以独立初始化。
function initRemoteManagement() {
  document.querySelectorAll('a[href]').forEach(link => link.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    if (!confirmManagementNavigation()) event.preventDefault();
  }));
  window.addEventListener('beforeunload', event => {
    if (credentialEditor.busy || remoteServerDialog.saving || managementIsDirty()) { event.preventDefault(); event.returnValue = ''; }
  });
  window.addEventListener('pagehide', clearManagementSecrets);
  if ($("remoteSshCredentialForm")) {
    resetRemoteSshCredentialForm(true);
    $("remoteSshCredentialForm").addEventListener('submit', event => { event.preventDefault(); saveRemoteSshSettings(); });
    $("remoteSshAuthMethod").addEventListener('change', () => updateRemoteSshCredentialFields());
    $("btnCancelEditRemoteSshCredential").onclick = () => resetRemoteSshCredentialForm();
    $("btnNewCredential").onclick = () => { if (resetRemoteSshCredentialForm()) $("remoteSshCredentialName").focus(); };
    $("btnRefreshCredentials").onclick = loadCredentialCatalog;
    $("remoteSshCredentialList").addEventListener('click', event => {
      const button = event.target.closest('[data-ssh-credential-action]');
      if (!button || button.disabled) return;
      if (button.dataset.sshCredentialAction === 'edit') editRemoteSshCredential(button.dataset.sshCredentialId);
      else deleteRemoteSshCredential(button.dataset.sshCredentialId);
    });
    window.addEventListener('pageshow', event => { if (event.persisted) { resetRemoteSshCredentialForm(true); loadCredentialCatalog(); } });
    loadCredentialCatalog();
    return;
  }
  $("btnAddServer").onclick = openAddServerModal;
  $("btnCloseAddServer").onclick = closeAddServerModal;
  $("btnCancelAddServer").onclick = closeAddServerModal;
  $("btnRefreshWorkers").onclick = refreshWorkers;
  $("btnRefreshRemoteCredentials").onclick = () => loadRemoteSshSettings();
  $("remoteServerForm").addEventListener('submit', submitRemoteServer);
  $("remoteServerForm").addEventListener('input', event => { event.target.removeAttribute('aria-invalid'); showRemoteServerError(); });
  $("clearRemoteRedisOverride").addEventListener('change', () => { $("remoteRedisUrl").disabled = $("clearRemoteRedisOverride").checked; });
  document.querySelectorAll("input[name='mode']").forEach(input => input.addEventListener('change', updateRemoteServerMode));
  $("addServerModal").addEventListener('keydown', handleRemoteServerDialogKeydown);
  $("addServerModal").addEventListener("click", (event) => {
    if (event.target === $("addServerModal")) event.preventDefault();
  });
$("workerGrid").addEventListener("click", (event) => {
  const logsButton = event.target.closest("[data-remote-server-logs]");
  if (logsButton) {
    showRemoteServerLogs(logsButton);
    return;
  }
  const effectiveConfigButton = event.target.closest("[data-remote-effective-config]");
  if (effectiveConfigButton && !effectiveConfigButton.disabled) {
    showRemoteEffectiveConfig(effectiveConfigButton);
    return;
  }
  const editRemoteButton = event.target.closest("[data-edit-remote-machine-id]");
  if (editRemoteButton && !editRemoteButton.disabled) {
    openEditRemoteServer(editRemoteButton.dataset.editRemoteMachineId);
    return;
  }
  const cancelRemoteButton = event.target.closest("[data-cancel-remote-operation]");
  if (cancelRemoteButton && !cancelRemoteButton.disabled) {
    cancelRemoteOperation(cancelRemoteButton);
    return;
  }
  const button = event.target.closest("[data-worker-id]");
  if (button && !button.disabled) toggleWorkerSelection(button);
  const remoteButton = event.target.closest("[data-remote-machine-id]");
  if (remoteButton && !remoteButton.disabled) manageRemoteServer(remoteButton);
  if (event.target.closest("[data-open-remote-modal]")) openAddServerModal();
});
$("btnCloseRemoteLog").onclick = closeRemoteLogModal;
$("btnCloseRemoteEffectiveConfig").onclick = closeRemoteEffectiveConfigModal;
$("btnClearRemoteLog").onclick = clearRemoteOperationLogs;
$("btnCopyRemoteLog").onclick = () => copyRemoteLogText($("remoteLogOutput").dataset.rawLog || "");
$("remoteLogOutput").addEventListener("click", (event) => {
  const button = event.target.closest("[data-copy-remote-log-line]");
  if (!button) return;
  const line = $("remoteLogOutput").querySelectorAll(".server-log-line-text")[Number(button.dataset.copyRemoteLogLine)];
  if (line) copyRemoteLogText(line.textContent === " " ? "" : line.textContent);
});
$("remoteLogModal").addEventListener("click", (event) => {
  if (event.target === $("remoteLogModal")) closeRemoteLogModal();
});
$("remoteEffectiveConfigModal").addEventListener("click", (event) => {
  if (event.target === $("remoteEffectiveConfigModal")) closeRemoteEffectiveConfigModal();
});
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !$("remoteLogModal").hidden) closeRemoteLogModal();
    if (event.key === 'Escape' && !$("remoteEffectiveConfigModal").hidden) closeRemoteEffectiveConfigModal();
  });
  window.addEventListener('pageshow', event => { if (event.persisted) { closeAddServerModal(true); refreshWorkers(); } });
  refreshWorkers();
  setInterval(refreshWorkers, 5000);
}
initRemoteManagement();
