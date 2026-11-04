<template>
  <PageShell title="Worker 管理" module="计算集群" description="监控训练节点心跳、资源占用与任务分布">
    <template #actions>
      <el-switch v-model="autoRefresh" active-text="30 秒自动刷新" />
      <el-button @click="refreshData" :loading="loading">
        <el-icon class="gap-8"><Refresh /></el-icon>
        刷新
      </el-button>
    </template>

    <!-- 集群汇总瓦片 -->
    <div class="stat-grid summary-grid">
      <div class="summary-tile">
        <div class="summary-tile__value">{{ queueDepth }}</div>
        <div class="summary-tile__label">队列深度</div>
      </div>
      <div class="summary-tile">
        <div class="summary-tile__value text-success">{{ onlineCount }}</div>
        <div class="summary-tile__label">在线</div>
      </div>
      <div class="summary-tile">
        <div class="summary-tile__value text-warning">{{ idleCount }}</div>
        <div class="summary-tile__label">空闲</div>
      </div>
      <div class="summary-tile">
        <div class="summary-tile__value text-running">{{ occupiedCount }}</div>
        <div class="summary-tile__label">占用</div>
      </div>
      <div class="summary-tile">
        <div class="summary-tile__value text-secondary">{{ offlineCount }}</div>
        <div class="summary-tile__label">离线</div>
      </div>
    </div>

    <!-- Worker 表格 -->
    <SectionCard flush>
      <el-table
        v-if="workers.length > 0"
        v-loading="loading"
        :data="workers"
        class="table-card"
        :header-cell-style="{ background: 'var(--mtp-bg-subtle)' }"
      >
        <el-table-column prop="worker_id" label="Worker ID" min-width="190" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="mono text-sm">{{ row.worker_id }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="machine_id" label="机器" width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.machine_id || '-' }}</template>
        </el-table-column>
        <el-table-column prop="slot" label="槽位" width="70" />
        <el-table-column label="设备" width="190" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.gpu?.available" class="device-cell">
              <el-icon><Cpu /></el-icon>
              {{ row.gpu.name }}（{{ formatVram(row.gpu.vram_mb) }}）
            </span>
            <span v-else class="text-secondary">CPU</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <StatusTag :status="row.active ? row.state : 'offline'" :map="workerStatusMap" />
          </template>
        </el-table-column>
        <el-table-column label="当前任务" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.current_job" class="mono text-sm">{{ row.current_job }}</span>
            <span v-else class="text-ink-400">-</span>
          </template>
        </el-table-column>
        <el-table-column label="心跳" width="100">
          <template #default="{ row }">
            <span :class="{ 'heartbeat-stale': isHeartbeatStale(row) }">{{ formatHeartbeat(row.heartbeat_age_sec) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="接收任务" width="100">
          <template #default="{ row }">
            <el-tooltip :disabled="row.managed" content="本机内置槽由平台自动管理，不可手动停用" placement="top">
              <el-switch
                :model-value="row.enabled"
                :loading="toggling[row.worker_id]"
                :disabled="!row.managed"
                @change="toggleEnabled(row)"
              />
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>

      <EmptyState
        v-else-if="error"
        type="error"
        title="Worker 加载失败"
        :description="error"
        retryable
        @retry="refreshData"
      />
      <EmptyState
        v-else-if="!loading"
        title="暂无 Worker 注册"
        description="启动后端内置计算槽或接入远程服务器后，节点将出现在这里"
      >
        <template #actions>
          <el-button type="primary" size="small" @click="refreshData">重新加载</el-button>
        </template>
      </EmptyState>
      <div v-else v-loading="true" class="loading-placeholder" />
    </SectionCard>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * Worker 管理：集群汇总（队列/在线/空闲/占用/离线）+ 节点表格。
 * - 状态合并 active 与 state：离线节点独立展示，不误标为空闲；
 * - 心跳超过 2 分钟标黄提示（在线但心跳滞后的节点需关注）；
 * - 「接收任务」开关仅对托管型远程 Worker 开放（本机内置槽由平台自动管理）；
 * - 可选 30 秒自动刷新，切换与卸载时清理定时器。
 */
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { WORKER_STATE, type StatusMeta } from '@/utils/status'
import { Refresh, Cpu } from '@element-plus/icons-vue'

const appStore = useAppStore()
const workers = ref<any[]>([])
const queueDepth = ref(0)
const loading = ref(false)
const error = ref('')
const autoRefresh = ref(false)
const toggling = ref<Record<string, boolean>>({})
let timer: ReturnType<typeof setInterval> | null = null

/** 状态映射 = Worker 状态 + 离线（active=false 时由页面注入） */
const workerStatusMap: Record<string, StatusMeta> = {
  ...WORKER_STATE,
  offline: { label: '离线', type: 'info' },
}

const onlineCount = computed(() => workers.value.filter((w) => w.active).length)
const idleCount = computed(() => workers.value.filter((w) => w.active && w.state === 'idle').length)
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
    appStore.error('Worker 加载失败', err?.message)
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
    appStore.success(res.message || '操作成功', worker.worker_id)
    refreshData()
  } catch (err: any) {
    appStore.error('操作失败', err?.message)
  } finally {
    toggling.value[worker.worker_id] = false
  }
}

/** 在线但心跳超过 2 分钟视为滞后 */
const isHeartbeatStale = (worker: any) =>
  worker.active && typeof worker.heartbeat_age_sec === 'number' && worker.heartbeat_age_sec > 120

const formatVram = (mb?: number) => (mb ? `${(mb / 1024).toFixed(1)} GB` : '未知显存')
const formatHeartbeat = (age?: number | null) => {
  if (age === null || age === undefined) return '-'
  if (age < 60) return `${Math.round(age)} 秒前`
  return `${Math.round(age / 60)} 分钟前`
}

watch(autoRefresh, (enabled) => {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
  if (enabled) timer = setInterval(refreshData, 30000)
})

onMounted(refreshData)
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.summary-grid {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}

@media (max-width: 768px) {
  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.summary-tile {
  background: var(--mtp-bg-card);
  border: 1px solid var(--mtp-ink-100);
  border-radius: var(--mtp-radius-lg);
  box-shadow: var(--mtp-shadow-card);
  padding: 14px 16px;
  text-align: center;
}

.summary-tile__value {
  font-size: 24px;
  font-weight: 600;
  color: var(--mtp-ink-900);
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}

.summary-tile__label {
  font-size: 12px;
  color: var(--mtp-ink-400);
  margin-top: 2px;
}

.text-success { color: var(--mtp-success); }
.text-warning { color: var(--mtp-warning); }
.text-running { color: var(--mtp-brand-600); }
.text-secondary { color: var(--mtp-ink-500); }

.device-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.heartbeat-stale {
  color: var(--mtp-warning);
  font-weight: 500;
}

.loading-placeholder {
  min-height: 200px;
}
</style>
