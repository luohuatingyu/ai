<template>
  <div class="page-view">
    <div class="page-header">
      <h1 class="page-title">远程服务器</h1>
      <div class="header-actions">
        <el-button @click="openCredentials">
          <el-icon><Key /></el-icon>
          SSH 凭证
        </el-button>
        <el-button type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>
          登记服务器
        </el-button>
        <el-button @click="refreshData" :loading="loading">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>
    <el-card>
      <div v-if="loading" class="loading-placeholder">
        <el-icon class="is-loading" :size="32"><Loading /></el-icon>
        <p>加载中...</p>
      </div>
      <div v-else-if="error" class="empty-state">
        <el-icon class="empty-state-icon" :size="48"><WarningFilled /></el-icon>
        <p>{{ error }}</p>
        <el-button type="primary" @click="refreshData">重试</el-button>
      </div>
      <template v-else>
        <el-alert v-if="servers.length === 0" title="暂无远程服务器，点击右上角登记" type="info" :closable="false" center />
        <el-table v-else :data="servers" border size="small" style="width: 100%">
          <el-table-column prop="machine_id" label="机器 ID" min-width="150" show-overflow-tooltip />
          <el-table-column label="主机" min-width="160" show-overflow-tooltip>
            <template #default="scope">{{ scope.row.host }}:{{ scope.row.port }}</template>
          </el-table-column>
          <el-table-column label="登录用户" width="110">
            <template #default="scope">{{ scope.row.username || '-' }}</template>
          </el-table-column>
          <el-table-column label="模式" width="90">
            <template #default="scope">
              <el-tag size="small" :type="(scope.row.mode === 'gpu' ? 'success' : 'info') as any">
                {{ scope.row.mode === 'gpu' ? 'GPU' : 'CPU' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="cpu_slots" label="CPU 槽" width="80" />
          <el-table-column label="部署状态" width="100">
            <template #default="scope">
              <el-tag size="small" :type="scope.row.deployed ? 'success' : 'info'">
                {{ scope.row.deployed ? '已部署' : '未部署' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="scope">
              <el-tag size="small" :type="statusTag(scope.row.status) as any" effect="dark">
                {{ statusText(scope.row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="230" fixed="right">
            <template #default="scope">
              <el-button size="small" link type="primary" @click="runAction(scope.row, scope.row.deployed ? 'deploy' : 'install')">
                {{ scope.row.deployed ? '同步代码' : '部署' }}
              </el-button>
              <el-button size="small" link type="success" @click="runAction(scope.row, 'start')">启动</el-button>
              <el-button size="small" link type="warning" @click="runAction(scope.row, 'stop')">停止</el-button>
              <el-button size="small" link type="info" @click="runAction(scope.row, 'restart')">重启</el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-card>

    <!-- 登记服务器对话框 -->
    <el-dialog v-model="showCreateDialog" title="登记远程服务器" width="560px">
      <el-form ref="serverFormRef" :model="serverForm" :rules="serverRules" label-width="110px">
        <el-form-item label="机器 ID" prop="machine_id">
          <el-input v-model="serverForm.machine_id" placeholder="唯一标识，如 gpu-node-01" />
        </el-form-item>
        <el-form-item label="SSH 主机" prop="host">
          <el-input v-model="serverForm.host" placeholder="IP 或主机名" />
        </el-form-item>
        <el-form-item label="SSH 端口" prop="port">
          <el-input-number v-model="serverForm.port" :min="1" :max="65535" style="width: 100%" />
        </el-form-item>
        <el-form-item label="SSH 用户名">
          <el-input v-model="serverForm.username" placeholder="留空则使用凭证中的用户名" />
        </el-form-item>
        <el-form-item label="SSH 凭证" prop="ssh_credential_id">
          <el-select v-model="serverForm.ssh_credential_id" placeholder="选择凭证" style="width: 100%" :loading="credentialsLoading">
            <el-option v-for="c in credentials" :key="c.id" :label="`${c.name} (${c.username})`" :value="c.id" />
          </el-select>
          <div class="tip-text">没有凭证？点击右上角「SSH 凭证」先创建。</div>
        </el-form-item>
        <el-form-item label="计算类型" prop="mode">
          <el-radio-group v-model="serverForm.mode">
            <el-radio value="gpu">GPU</el-radio>
            <el-radio value="cpu">CPU</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="CPU 槽数" prop="cpu_slots">
          <el-input-number v-model="serverForm.cpu_slots" :min="0" :max="256" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">登记</el-button>
      </template>
    </el-dialog>

    <!-- SSH 凭证管理对话框 -->
    <el-dialog v-model="showCredentialsDialog" title="SSH 凭证管理" width="640px">
      <div class="cred-toolbar">
        <el-button size="small" type="primary" @click="resetCredentialForm(); showCredentialForm = true">
          <el-icon><Plus /></el-icon>
          新建凭证
        </el-button>
      </div>
      <el-table :data="credentials" border size="small" v-loading="credentialsLoading">
        <el-table-column prop="name" label="名称" min-width="120" />
        <el-table-column prop="username" label="用户名" width="120" />
        <el-table-column label="认证方式" width="110">
          <template #default="scope">{{ authMethodText(scope.row.auth_method) }}</template>
        </el-table-column>
        <el-table-column label="默认" width="70">
          <template #default="scope">
            <el-tag v-if="scope.row.is_default" size="small" type="success">默认</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="scope">
            <el-popconfirm title="确认删除该凭证？仍被服务器引用的凭证无法删除。" @confirm="deleteCredential(scope.row.id)">
              <template #reference>
                <el-button size="small" link type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!credentialsLoading && credentials.length === 0" description="暂无凭证" :image-size="60" />

      <!-- 新建凭证表单 -->
      <el-dialog v-model="showCredentialForm" title="新建 SSH 凭证" width="480px" append-to-body>
        <el-form ref="credFormRef" :model="credForm" :rules="credRules" label-width="100px">
          <el-form-item label="名称" prop="name">
            <el-input v-model="credForm.name" placeholder="如 prod-cluster" />
          </el-form-item>
          <el-form-item label="用户名" prop="username">
            <el-input v-model="credForm.username" placeholder="SSH 登录用户" />
          </el-form-item>
          <el-form-item label="认证方式" prop="auth_method">
            <el-radio-group v-model="credForm.auth_method">
              <el-radio value="password">密码</el-radio>
              <el-radio value="key">私钥</el-radio>
              <el-radio value="key_password">私钥+口令</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item v-if="credForm.auth_method === 'password'" label="密码" prop="password">
            <el-input v-model="credForm.password" type="password" show-password />
          </el-form-item>
          <el-form-item v-if="credForm.auth_method !== 'password'" label="私钥" prop="private_key">
            <el-input v-model="credForm.private_key" type="textarea" :rows="6" placeholder="粘贴完整 OpenSSH / PEM 私钥" />
          </el-form-item>
          <el-form-item v-if="credForm.auth_method === 'key_password'" label="私钥口令" prop="passphrase">
            <el-input v-model="credForm.passphrase" type="password" show-password />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="showCredentialForm = false">取消</el-button>
          <el-button type="primary" :loading="savingCredential" @click="submitCredential">保存</el-button>
        </template>
      </el-dialog>
    </el-dialog>

    <!-- 远程操作进度对话框 -->
    <el-dialog v-model="showOperationDialog" :title="`远程操作：${operationAction}`" width="720px" :close-on-click-modal="false">
      <div class="op-status">
        <el-tag :type="operationStatusTag as any" effect="dark">{{ operationStatusText }}</el-tag>
        <span v-if="operationMachine" class="op-machine">{{ operationMachine }}</span>
      </div>
      <div class="op-output" ref="outputRef">
        <pre>{{ operationOutput || '等待输出...' }}</pre>
      </div>
      <template #footer>
        <el-button @click="showOperationDialog = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, nextTick, onUnmounted } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import { Refresh, Loading, WarningFilled, Plus, Key } from '@element-plus/icons-vue'

const appStore = useAppStore()
const servers = ref<any[]>([])
const credentials = ref<any[]>([])
const loading = ref(false)
const error = ref('')
const credentialsLoading = ref(false)

// 服务器登记
const showCreateDialog = ref(false)
const creating = ref(false)
const serverFormRef = ref<FormInstance>()
const serverForm = reactive({
  machine_id: '', host: '', port: 22, username: '',
  ssh_credential_id: '', mode: 'gpu', cpu_slots: 2,
})
const serverRules: FormRules = {
  machine_id: [
    { required: true, message: '请输入机器 ID', trigger: 'blur' },
    { pattern: /^[A-Za-z0-9][A-Za-z0-9_.-]*$/, message: '仅允许字母数字与 . _ -', trigger: 'blur' },
  ],
  host: [{ required: true, message: '请输入 SSH 主机', trigger: 'blur' }],
  port: [{ required: true, message: '请输入端口', trigger: 'change' }],
  ssh_credential_id: [{ required: true, message: '请选择 SSH 凭证', trigger: 'change' }],
  mode: [{ required: true, message: '请选择计算类型', trigger: 'change' }],
}

// 凭证管理
const showCredentialsDialog = ref(false)
const showCredentialForm = ref(false)
const savingCredential = ref(false)
const credFormRef = ref<FormInstance>()
const credForm = reactive({
  name: '', username: '', auth_method: 'password',
  password: '', private_key: '', passphrase: '',
})
const credRules: FormRules = {
  name: [{ required: true, message: '请输入凭证名称', trigger: 'blur' }],
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { pattern: /^[A-Za-z_][A-Za-z0-9_-]*$/, message: '用户名格式无效', trigger: 'blur' },
  ],
  auth_method: [{ required: true, message: '请选择认证方式', trigger: 'change' }],
}

// 远程操作轮询
const showOperationDialog = ref(false)
const operationOutput = ref('')
const operationStatus = ref('')
const operationAction = ref('')
const operationMachine = ref('')
const outputRef = ref<HTMLElement>()
let pollTimer: ReturnType<typeof setInterval> | null = null

const refreshData = async () => {
  loading.value = servers.value.length === 0
  error.value = ''
  try {
    const res: any = await api.get('/remote-servers')
    servers.value = res.servers || []
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

const fetchCredentials = async () => {
  credentialsLoading.value = true
  try {
    const res: any = await api.get('/remote-ssh-credentials')
    credentials.value = res.credentials || []
  } catch (err: any) {
    appStore.error('凭证加载失败', err?.message)
  } finally {
    credentialsLoading.value = false
  }
}

const openCreate = async () => {
  resetServerForm()
  await fetchCredentials()
  showCreateDialog.value = true
}

const openCredentials = async () => {
  showCredentialsDialog.value = true
  await fetchCredentials()
}

const resetServerForm = () => {
  Object.assign(serverForm, {
    machine_id: '', host: '', port: 22, username: '',
    ssh_credential_id: '', mode: 'gpu', cpu_slots: 2,
  })
}

const resetCredentialForm = () => {
  Object.assign(credForm, {
    name: '', username: '', auth_method: 'password',
    password: '', private_key: '', passphrase: '',
  })
}

const submitCreate = async () => {
  await serverFormRef.value?.validate().catch(() => Promise.reject())
  creating.value = true
  try {
    const payload: Record<string, any> = {
      machine_id: serverForm.machine_id,
      host: serverForm.host,
      port: serverForm.port,
      ssh_credential_id: serverForm.ssh_credential_id,
      mode: serverForm.mode,
      cpu_slots: serverForm.cpu_slots,
    }
    if (serverForm.username) payload.username = serverForm.username
    await api.post('/remote-servers', payload)
    appStore.success('服务器已登记', '可执行「部署」接入该机器')
    showCreateDialog.value = false
    refreshData()
  } catch (err: any) {
    appStore.error('登记失败', err?.message)
  } finally {
    creating.value = false
  }
}

const submitCredential = async () => {
  await credFormRef.value?.validate().catch(() => Promise.reject())
  savingCredential.value = true
  try {
    const payload: Record<string, any> = {
      name: credForm.name,
      username: credForm.username,
      auth_method: credForm.auth_method,
    }
    if (credForm.auth_method === 'password') payload.password = credForm.password
    else {
      payload.private_key = credForm.private_key
      if (credForm.auth_method === 'key_password') payload.passphrase = credForm.passphrase
    }
    const res: any = await api.post('/remote-ssh-credentials', payload)
    credentials.value = res.credentials || []
    showCredentialForm.value = false
    appStore.success('凭证已保存')
  } catch (err: any) {
    appStore.error('凭证保存失败', err?.message)
  } finally {
    savingCredential.value = false
  }
}

const deleteCredential = async (credentialId: string) => {
  try {
    const res: any = await api.delete(`/remote-ssh-credentials/${credentialId}`)
    credentials.value = res.credentials || []
    appStore.success('凭证已删除')
  } catch (err: any) {
    appStore.error('删除失败', err?.message)
  }
}

const authMethodText = (method?: string) => {
  const map: Record<string, string> = { password: '密码', key: '私钥', key_password: '私钥+口令' }
  return map[method || ''] || method || '-'
}

// 触发生命周期操作并轮询进度
const runAction = async (server: any, action: string) => {
  const labels: Record<string, string> = {
    install: '首次部署', deploy: '同步代码', start: '启动', stop: '停止', restart: '重启',
  }
  try {
    const res: any = await api.post(`/remote-servers/${server.machine_id}/${action}`)
    operationAction.value = labels[action] || action
    operationMachine.value = server.machine_id
    operationStatus.value = res.status || 'queued'
    operationOutput.value = ''
    showOperationDialog.value = true
    startPolling(res.operation_id)
  } catch (err: any) {
    appStore.error('操作下发失败', err?.message)
  }
}

const startPolling = (operationId: string) => {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      const op: any = await api.get(`/remote-operations/${operationId}`)
      operationStatus.value = op.status
      operationOutput.value = op.output || ''
      await nextTick()
      if (outputRef.value) outputRef.value.scrollTop = outputRef.value.scrollHeight
      if (['completed', 'failed', 'cancelled'].includes(op.status)) {
        stopPolling()
        refreshData()
        if (op.status === 'completed') appStore.success('远程操作完成')
        else if (op.status === 'failed') appStore.error('远程操作失败', op.error || '详见输出日志')
        else appStore.warning('远程操作已中断', op.error || '')
      }
    } catch {
      // 单次轮询失败不终止弹窗，等待下一轮
    }
  }, 2000)
}

const stopPolling = () => {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

// 组件卸载时停止操作轮询，避免泄漏定时器
onUnmounted(stopPolling)

const STATUS_TEXT: Record<string, string> = {
  not_deployed: '未部署', provisioning: '装机中', configuring: '下发配置', deploying: '部署中',
  starting: '启动中', stopping: '停止中', restarting: '重启中', online: '在线',
  stopped: '已停止', error: '异常',
}
const statusText = (status?: string) => STATUS_TEXT[status || ''] || status || '-'
const statusTag = (status?: string) => {
  const map: Record<string, string> = {
    not_deployed: 'info', online: 'success', stopped: 'info', error: 'danger',
  }
  if (map[status || '']) return map[status || '']
  return ['provisioning', 'configuring', 'deploying', 'starting', 'stopping', 'restarting'].includes(status || '')
    ? 'warning' : 'info'
}

const operationStatusText = computed(() => {
  const map: Record<string, string> = {
    queued: '排队中', running: '执行中', completed: '已完成', failed: '失败', cancelled: '已中断',
  }
  return map[operationStatus.value] || operationStatus.value || '-'
})
const operationStatusTag = computed(() => {
  const map: Record<string, string> = {
    queued: 'info', running: 'warning', completed: 'success', failed: 'danger', cancelled: 'info',
  }
  return map[operationStatus.value] || 'info'
})
</script>

<style scoped>
.page-view { padding: 0; }
.header-actions { display: flex; align-items: center; gap: 12px; }
.tip-text { color: #909399; font-size: 12px; line-height: 1.4; margin-top: 4px; }
.cred-toolbar { margin-bottom: 12px; display: flex; justify-content: flex-end; }
.loading-placeholder { text-align: center; padding: 60px; color: #909399; }
.op-status { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.op-machine { color: #909399; font-size: 13px; }
.op-output {
  background: #1e1e1e; border-radius: 6px; max-height: 320px; overflow: auto;
}
.op-output pre {
  margin: 0; padding: 12px; color: #d4d4d4;
  font-family: 'Monaco', 'Menlo', monospace; font-size: 12px; line-height: 1.6;
  white-space: pre-wrap; word-break: break-word;
}
</style>
