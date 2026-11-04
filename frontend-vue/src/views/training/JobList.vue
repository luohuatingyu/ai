<template>
  <PageShell title="任务列表" module="训练任务" description="跟踪全部训练任务的状态、进度与结果">
    <template #actions>
      <el-button @click="refreshData" :loading="loading">
        <el-icon class="gap-8"><Refresh /></el-icon>
        刷新
      </el-button>
      <el-button type="primary" @click="router.push('/training/create')">
        <el-icon class="gap-8"><Plus /></el-icon>
        创建任务
      </el-button>
    </template>

    <!-- 状态汇总（基于全部任务聚合） -->
    <div class="stat-grid">
      <StatCard label="全部任务" :value="String(jobs.length)" :icon="Monitor" tone="blue" hint="所有记录" />
      <StatCard label="进行中" :value="String(statusCounts.running + statusCounts.queued)" :icon="Loading" tone="orange" hint="排队 + 运行" />
      <StatCard label="已完成" :value="String(statusCounts.completed)" :icon="CircleCheck" tone="green" hint="训练成功" />
      <StatCard label="失败/取消" :value="String(statusCounts.failed + statusCounts.cancelled)" :icon="CircleClose" tone="red" hint="需要关注" />
    </div>

    <!-- 筛选 -->
    <FilterBar>
      <el-select v-model="statusFilter" placeholder="全部状态" clearable style="width: 160px" @change="refreshData">
        <el-option label="排队中" value="queued" />
        <el-option label="运行中" value="running" />
        <el-option label="已暂停" value="paused" />
        <el-option label="已完成" value="completed" />
        <el-option label="已失败" value="failed" />
        <el-option label="已取消" value="cancelled" />
      </el-select>
      <template #actions>
        <span class="text-xs text-secondary">共 {{ formatInteger(jobs.length) }} 个任务{{ statusFilter ? `，当前筛选 ${filteredJobs.length} 个` : '' }}</span>
      </template>
    </FilterBar>

    <!-- 任务表格 -->
    <SectionCard flush>
      <el-table
        v-if="filteredJobs.length > 0"
        v-loading="loading"
        :data="filteredJobs"
        class="table-card"
        :header-cell-style="{ background: 'var(--mtp-bg-subtle)' }"
      >
        <el-table-column prop="id" label="任务 ID" min-width="190" show-overflow-tooltip>
          <template #default="{ row }">
            <el-link type="primary" underline="never" class="mono text-sm" @click="viewJob(row.id)">{{ row.id }}</el-link>
          </template>
        </el-table-column>
        <el-table-column label="任务类型" width="120">
          <template #default="{ row }">
            <StatusTag :status="specOf(row).task" :map="TASK_TYPE" />
          </template>
        </el-table-column>
        <el-table-column label="数据集" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ specOf(row).dataset_name || specOf(row).dataset_id || '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <StatusTag :status="row.status" :map="JOB_STATUS" />
          </template>
        </el-table-column>
        <el-table-column label="设备" width="130">
          <template #default="{ row }">{{ row.device || resourceText(row) }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="120">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button size="small" link type="primary" @click="viewJob(row.id)">详情</el-button>
              <el-button
                v-if="row.status === 'running' || row.status === 'queued'"
                size="small" link type="warning" @click="cancelJob(row)">取消</el-button>
              <el-button
                v-if="['failed', 'cancelled', 'completed', 'paused'].includes(row.status)"
                size="small" link type="danger" @click="deleteJob(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <EmptyState
        v-else-if="error"
        type="error"
        title="任务加载失败"
        :description="error"
        retryable
        @retry="refreshData"
      />
      <EmptyState v-else-if="statusFilter" title="当前筛选条件下没有任务" description="切换状态筛选或查看全部任务">
        <template #actions>
          <el-button size="small" @click="statusFilter = ''; refreshData()">查看全部</el-button>
        </template>
      </EmptyState>
      <EmptyState v-else title="暂无训练任务" description="从模板创建或手动配置第一个训练任务">
        <template #actions>
          <el-button type="primary" size="small" @click="router.push('/training/create')">创建训练任务</el-button>
          <el-button size="small" @click="router.push('/templates/library')">浏览模板库</el-button>
        </template>
      </EmptyState>
    </SectionCard>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * 训练任务列表：状态汇总、筛选与任务表格。
 * - 汇总卡片基于全部任务聚合，筛选仅影响表格展示；
 * - 取消/删除带确认弹窗；失败展示可重试错误态；
 * - 后端返回的任务视图把任务类型/数据集放在 spec 内，统一经 specOf 取出。
 */
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import StatCard from '@/components/common/StatCard.vue'
import FilterBar from '@/components/common/FilterBar.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { JOB_STATUS, TASK_TYPE } from '@/utils/status'
import { formatDateTime, formatInteger } from '@/utils/format'
import { Refresh, Plus, Monitor, Loading, CircleCheck, CircleClose } from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()
const jobs = ref<any[]>([])
const loading = ref(false)
const error = ref('')
const statusFilter = ref('')

// 后端返回的任务视图把任务类型/数据集放在 spec 内，这里统一取出
const specOf = (row: any) => row?.spec || {}

/** 按状态聚合数量（汇总卡片） */
const statusCounts = computed(() => {
  const counts: Record<string, number> = { running: 0, queued: 0, completed: 0, failed: 0, cancelled: 0 }
  for (const job of jobs.value) {
    if (job.status in counts) counts[job.status]++
  }
  return counts
})

const filteredJobs = computed(() => {
  if (!statusFilter.value) return jobs.value
  return jobs.value.filter((job) => job.status === statusFilter.value)
})

// 后端实际路径为 /api/training/jobs（旧版误请求 /api/jobs 导致 502/404）
const refreshData = async () => {
  loading.value = true
  error.value = ''
  try {
    const res: any = await api.get('/training/jobs')
    jobs.value = Array.isArray(res) ? res : (res.jobs || [])
  } catch (err: any) {
    error.value = err?.message || '加载失败'
    appStore.error('任务加载失败', err?.message)
  } finally {
    loading.value = false
  }
}

const viewJob = (id: string) => router.push(`/training/detail/${id}`)

const cancelJob = async (row: any) => {
  try {
    await ElMessageBox.confirm(
      `确认取消任务 ${row.id}？正在运行的任务会在当前批次结束后停止。`,
      '操作确认',
      { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await api.post(`/training/jobs/${row.id}/cancel`)
    appStore.success('任务已取消', row.id)
    refreshData()
  } catch (err: any) {
    appStore.error('取消失败', err?.message)
  }
}

const deleteJob = async (row: any) => {
  try {
    await ElMessageBox.confirm(
      `确认删除任务 ${row.id}？删除后任务记录与日志不可恢复。`,
      '操作确认',
      { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await api.delete(`/training/jobs/${row.id}`)
    appStore.success('任务已删除', row.id)
    refreshData()
  } catch (err: any) {
    appStore.error('删除失败', err?.message)
  }
}

const resourceText = (row: any) => {
  const hint = row?.resource_hint || {}
  if (hint.parallel_workers) return `联合训练 ×${hint.parallel_workers}`
  return hint.require_gpu ? 'GPU' : '自动'
}

onMounted(refreshData)
</script>

<style scoped>
/* 汇总卡片在窄屏 2 列 */
@media (max-width: 768px) {
  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
