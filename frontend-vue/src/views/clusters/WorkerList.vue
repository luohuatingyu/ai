<template>
  <div class="page-view">
    <div class="page-header">
      <h1 class="page-title">Worker 管理</h1>
      <div class="header-actions">
        <el-checkbox v-model="autoRefresh" border size="small">30 秒自动刷新</el-checkbox>
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
        <div class="summary-bar">
          <el-tag>队列深度：{{ queueDepth }}</el-tag>
          <el-tag type="success">在线：{{ onlineCount }}</el-tag>
          <el-tag type="warning">空闲：{{ idleCount }}</el-tag>
          <el-tag type="primary">占用：{{ occupiedCount }}</el-tag>
          <el-tag type="info">离线：{{ offlineCount }}</el-tag>
        </div>
        <el-alert v-if="workers.length === 0" title="暂无 Worker 注册，请启动后端或接入远程服务器" type="info" :closable="false" center />
        <el-table v-else :data="workers" border size="small" style="width: 100%">
          <el-table-column prop="worker_id" label="Worker ID" min-width="200" show-overflow-tooltip />
          <el-table-column prop="machine_id" label="机器" width="150" show-overflow-tooltip>
            <template #default="scope">{{ scope.row.machine_id || '-' }}</template>
          </el-table-column>
          <el-table-column prop="slot" label="槽位" width="80" />
          <el-table-column label="设备" width="180" show-overflow-tooltip>
            <template #default="scope">
              <span v-if="scope.row.gpu?.available">
                <el-icon><Cpu /></el-icon>
                {{ scope.row.gpu.name }}（{{ formatVram(scope.row.gpu.vram_mb) }}）
              </span>
              <span v-else>CPU</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="scope">
              <el-tag :type="getStateTag(scope.row) as any" size="small">{{ stateText(scope.row) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="当前任务" min-width="140" show-overflow-tooltip>
            <template #default="scope">{{ scope.row.current_job || '-' }}</template>
          </el-table-column>
          <el-table-column label="心跳" width="110">
            <template #default="scope">{{ formatHeartbeat(scope.row.heartbeat_age_sec) }}</template>
          </el-table-column>
          <el-table-column label="接收任务" width="110">
            <template #default="scope">
              <el-switch
                :model-value="scope.row.enabled"
                :loading="toggling[scope.row.worker_id]"
                :disabled="!scope.row.managed"
                @change="toggleEnabled(scope.row)"
              />
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import { Refresh, Loading, WarningFilled, Cpu } from '@element-plus/icons-vue'

const appStore = useAppStore()
const workers = ref<any[]>([])
const queueDepth = ref(0)
const loading = ref(false)
const error = ref('')
const autoRefresh = ref(false)
const toggling = ref<Record<string, boolean>>({})
let timer: ReturnType<typeof setInterval> | null = null

const onlineCount = computed(() => workers.value.filter((w) => w.active).length)
const idleCount = computed(() => workers.value.filter((w) => w.state === 'idle').length)
const occupiedCount = computed(() => workers.value.filter((w) => w.occupied).length)
const offlineCount = computed(() => workers.value.filter((w) => !w.active).length)

const refreshData = async () => {
  loading.value = workers.value.length === 0
  error.value = ''
  try {
    const res: any = await api.get('/clusters/workers')
    workers.value = res.workers || []
    queueDepth.value = res.queue_depth ?? 0
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

// 启用/停用计算槽（仅托管型远程 Worker 可操作，本机内置槽由平台自动管理）
const toggleEnabled = async (worker: any) => {
  toggling.value[worker.worker_id] = true
  try {
    const res: any = await api.patch(`/clusters/workers/${worker.worker_id}/enable`, null, {
      params: { enabled: !worker.enabled },
    })
    appStore.success(res.message || '操作成功')
    refreshData()
  } catch (err: any) {
    appStore.error('操作失败', err?.message)
  } finally {
    toggling.value[worker.worker_id] = false
  }
}

const getStateTag = (worker: any) => {
  if (!worker.active) return 'info'
  const map: Record<string, string> = {
    idle: 'success', reserved: 'warning', busy: 'primary', disabled: 'info',
  }
  return map[worker.state] || 'warning'
}

const stateText = (worker: any) => {
  if (!worker.active) return '离线'
  const map: Record<string, string> = {
    idle: '空闲', reserved: '已预约', busy: '忙碌中', disabled: '已禁用',
  }
  return map[worker.state] || worker.state
}

const formatVram = (mb?: number) => (mb ? `${(mb / 1024).toFixed(1)} GB` : '未知显存')
const formatHeartbeat = (age?: number | null) => {
  if (age === null || age === undefined) return '-'
  if (age < 60) return `${Math.round(age)} 秒前`
  return `${Math.round(age / 60)} 分钟前`
}

watch(autoRefresh, (enabled) => {
  if (timer) { clearInterval(timer); timer = null }
  if (enabled) timer = setInterval(refreshData, 30000)
})

onMounted(refreshData)
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.page-view { padding: 0; }
.header-actions { display: flex; align-items: center; gap: 12px; }
.summary-bar { display: flex; gap: 10px; margin-bottom: 14px; }
.loading-placeholder { text-align: center; padding: 60px; color: #909399; }
</style>
