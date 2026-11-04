<template>
  <PageShell title="远程服务器" module="计算集群" description="管理远程训练服务器、SSH 凭证与远程操作">
    <template #actions>
      <el-button @click="openCredentials">
        <el-icon class="gap-8"><Key /></el-icon>
        SSH 凭证
      </el-button>
      <el-button type="primary" @click="openCreate">
        <el-icon class="gap-8"><Plus /></el-icon>
        登记服务器
      </el-button>
      <el-button @click="refreshData" :loading="loading">
        <el-icon class="gap-8"><Refresh /></el-icon>
        刷新
      </el-button>
    </template>

    <!-- 服务器表格 -->
    <SectionCard flush>
      <el-table
        v-if="servers.length > 0"
        v-loading="loading"
        :data="servers"
        class="table-card"
        :header-cell-style="{ background: 'var(--mtp-bg-subtle)' }"
      >
        <el-table-column prop="machine_id" label="机器 ID" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="mono text-sm">{{ row.machine_id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="主机" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="mono text-sm">{{ row.host }}:{{ row.port }}</span>
          </template>
        </el-table-column>
        <el-table-column label="登录用户" width="110">
          <template #default="{ row }">{{ row.username || '-' }}</template>
        </el-table-column>
        <el-table-column label="模式" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.mode === 'gpu' ? 'success' : 'info'" disable-transitions>
              {{ row.mode === 'gpu' ? 'GPU' : 'CPU' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="cpu_slots" label="CPU 槽" width="80" />
        <el-table-column label="部署" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.deployed ? 'success' : 'info'" effect="plain" disable-transitions>
              {{ row.deployed ? '已部署' : '未部署' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <StatusTag :status="row.status" :map="SERVER_STATUS" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button size="small" link type="primary" @click="runAction(row, row.deployed ? 'deploy' : 'install')">
                {{ row.deployed ? '同步代码' : '部署' }}
              </el-button>
              <el-dropdown trigger="click" @command="(cmd: string) => runAction(row, cmd)">
                <el-button size="small" link :aria-label="`服务器 ${row.machine_id} 的更多操作`">
                  生命周期<el-icon><ArrowDown /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="start">启动</el-dropdown-item>
                    <el-dropdown-item command="stop">停止</el-dropdown-item>
                    <el-dropdown-item command="restart">重启</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <EmptyState
        v-else-if="error"
        type="error"
        title="服务器加载失败"
        :description="error"
        retryable
        @retry="refreshData"
      />
      <EmptyState
        v-else-if="!loading"
        title="暂无远程服务器"
        description="登记远程机器并部署训练环境，扩展平台算力"
      >
        <template #actions>
          <el-button type="primary" size="small" @click="openCreate">登记第一台服务器</el-button>
        </template>
      </EmptyState>
      <div v-else v-loading="true" class="loading-placeholder" />
    </SectionCard>

    <!-- 登记服务器对话框 -->
    <el-dialog v-model="showCreateDialog" title="登记远程服务器" width="560px" destroy-on-close>
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
    <el-dialog v-model="showCredentialsDialog" title="SSH 凭证管理" width="640px" destroy-on-close>
      <div class="cred-toolbar">
        <el-button size="small" type="primary" @click="resetCredentialForm(); showCredentialForm = true">
          <el-icon><Plus /></el-icon>
          新建凭证
        </el-button>
      </div>
      <el-table v-if="credentials.length > 0" :data="credentials" class="table-card" v-loading="credentialsLoading" :header-cell-style="{ background: 'var(--mtp-bg-subtle)' }">
        <el-table-column prop="name" label="名称" min-width="120" />
        <el-table-column prop="username" label="用户名" width="120" />
        <el-table-column label="认证方式" width="110">
          <template #default="{ row }">{{ authMethodText(row.auth_method) }}</template>
        </el-table-column>
        <el-table-column label="默认" width="70">
          <template #default="{ row }">
            <el-tag v-if="row.is_default" size="small" type="success">默认</el-tag>
            <span v-else class="text-ink-400">-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-popconfirm title="确认删除该凭证？仍被服务器引用的凭证无法删除。" @confirm="deleteCredential(row.id)">
              <template #reference>
                <el-button size="small" link type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <EmptyState
        v-else-if="!credentialsLoading"
        title="暂无凭证"
        description="创建 SSH 凭证后可登记远程服务器"
      />
      <div v-else v-loading="true" class="cred-loading" />

      <!-- 新建凭证表单 -->
      <el-dialog v-model="showCredentialForm" title="新建 SSH 凭证" width="480px" append-to-body destroy-on-close>
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
            <el-input v-model="credForm.password" type="password" show-password autocomplete="new-password" />
          </el-form-item>
          <el-form-item v-if="credForm.auth_method !== 'password'" label="私钥" prop="private_key">
            <el-input v-model="credForm.private_key" type="textarea" :rows="6" placeholder="粘贴完整 OpenSSH / PEM 私钥" />
          </el-form-item>
          <el-form-item v-if="credForm.auth_method === 'key_password'" label="私钥口令" prop="passphrase">
            <el-input v-model="credForm.passphrase" type="password" show-password autocomplete="new-password" />
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
        <StatusTag :status="operationStatus" :map="OPERATION_STATUS" size="large" />
        <span v-if="operationMachine" class="op-machine mono">{{ operationMachine }}</span>
      </div>
      <div ref="outputRef" class="op-output">
        <pre>{{ operationOutput || '等待输出...' }}</pre>
      </div>
      <template #footer>
        <el-button @click="showOperationDialog = false">关闭</el-button>
      </template>
    </el-dialog>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * 远程服务器：服务器登记、SSH 凭证管理与远程生命周期操作。
 * - 生命周期操作（部署/启动/停止/重启）下发后弹窗轮询进度（2 秒间隔），
 *   完成后刷新列表并按结果提示；轮询定时器在卸载时清理；
 * - 凭证输入使用 type=password + autocomplete=new-password，避免浏览器持久化；
 * - 单次轮询失败不终止弹窗，等待下一轮。
 */
import { ref, reactive, nextTick, onMounted, onUnmounted } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { SERVER_STATUS, type StatusMeta } from '@/utils/status'
import { Refresh, Plus, Key, ArrowDown } from '@element-plus/icons-vue'

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

/** 操作状态映射（StatusTag 消费） */
const OPERATION_STATUS: Record<string, StatusMeta> = {
  queued: { label: '排队中', type: 'info' },
  running: { label: '执行中', type: 'warning' },
  completed: { label: '已完成', type: 'success' },
  failed: { label: '失败', type: 'danger' },
  cancelled: { label: '已中断', type: 'info' },
}

const refreshData = async () => {
  loading.value = servers.value.length === 0
  error.value = ''
  try {
    const res: any = await api.get('/remote-servers')
    servers.value = res.servers || []
  } catch (err: any) {
    error.value = err?.message || '加载失败'
    appStore.error('服务器加载失败', err?.message)
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
  const valid = await serverFormRef.value?.validate().catch(() => false)
  if (!valid) return
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
    // 用户名仅在填写时提交，未填写由后端回落到凭证中的用户名
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
  const valid = await credFormRef.value?.validate().catch(() => false)
  if (!valid) return
  savingCredential.value = true
  try {
    const payload: Record<string, any> = {
      name: credForm.name,
      username: credForm.username,
      auth_method: credForm.auth_method,
    }
    // 按认证方式组装敏感字段（密码 / 私钥 / 私钥+口令）
    if (credForm.auth_method === 'password') payload.password = credForm.password
    else {
      payload.private_key = credForm.private_key
      if (credForm.auth_method === 'key_password') payload.passphrase = credForm.passphrase
    }
    const res: any = await api.post('/remote-ssh-credentials', payload)
    credentials.value = res.credentials || []
    showCredentialForm.value = false
    appStore.success('凭证已保存', credForm.name)
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
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// 组件卸载时停止操作轮询，避免泄漏定时器
onUnmounted(stopPolling)

// 页面加载时拉取服务器列表
onMounted(refreshData)
</script>

<style scoped>
.tip-text {
  color: var(--mtp-ink-400);
  font-size: 12px;
  line-height: 1.4;
  margin-top: 4px;
}

.cred-toolbar {
  margin-bottom: 12px;
  display: flex;
  justify-content: flex-end;
}

.cred-loading {
  min-height: 120px;
}

.loading-placeholder {
  min-height: 200px;
}

.op-status {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.op-machine {
  color: var(--mtp-ink-500);
  font-size: 13px;
}

.op-output {
  background: #1e1e2a;
  border-radius: var(--mtp-radius-md);
  max-height: 320px;
  overflow: auto;
}

.op-output pre {
  margin: 0;
  padding: 12px;
  color: #d4d4d4;
  font-family: var(--mtp-font-mono);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
