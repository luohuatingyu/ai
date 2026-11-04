<template>
  <PageShell title="执行历史" module="Pipeline 编排" description="回溯 Pipeline 执行记录、耗时与结果">
    <template #actions>
      <el-button @click="fetchPipelines" :loading="pipelinesLoading">
        <el-icon class="gap-8"><Refresh /></el-icon>
        刷新
      </el-button>
    </template>

    <!-- Pipeline 选择 -->
    <FilterBar>
      <el-select
        v-model="selectedPipeline"
        placeholder="选择 Pipeline"
        style="width: 280px"
        :loading="pipelinesLoading"
        @change="onPipelineChange"
      >
        <el-option v-for="p in pipelines" :key="p.id" :label="p.name" :value="p.id" />
      </el-select>
      <template #actions>
        <span v-if="selectedPipeline" class="text-xs text-secondary">共 {{ formatInteger(total) }} 次执行</span>
      </template>
    </FilterBar>

    <!-- 执行记录 -->
    <SectionCard flush>
      <el-table
        v-if="runs.length > 0"
        v-loading="runsLoading"
        :data="runs"
        class="table-card"
        :header-cell-style="{ background: 'var(--mtp-bg-subtle)' }"
      >
        <el-table-column prop="id" label="执行 ID" min-width="190" show-overflow-tooltip>
          <template #default="{ row }">
            <el-link type="primary" underline="never" class="mono text-sm" @click="viewRun(row.id)">{{ row.id }}</el-link>
          </template>
        </el-table-column>
        <el-table-column label="触发方式" width="100">
          <template #default="{ row }">{{ triggerText(row.trigger) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <StatusTag :status="row.status" :map="PIPELINE_RUN_STATUS" />
          </template>
        </el-table-column>
        <el-table-column label="开始时间" width="120">
          <template #default="{ row }">{{ formatDateTime(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="结束时间" width="120">
          <template #default="{ row }">{{ formatDateTime(row.finished_at) }}</template>
        </el-table-column>
        <el-table-column label="耗时" width="110">
          <template #default="{ row }">{{ formatDuration(durationOf(row)) }}</template>
        </el-table-column>
        <el-table-column label="错误" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.error" class="error-text">{{ row.error }}</span>
            <span v-else class="text-ink-400">-</span>
          </template>
        </el-table-column>
      </el-table>

      <EmptyState
        v-else-if="runsError"
        type="error"
        title="执行历史加载失败"
        :description="runsError"
        retryable
        @retry="fetchRuns"
      />
      <EmptyState
        v-else-if="pipelines.length === 0 && !pipelinesLoading"
        title="暂无 Pipeline"
        description="创建 Pipeline 并运行后，执行记录将显示在这里"
      >
        <template #actions>
          <el-button type="primary" size="small" @click="router.push('/pipelines/create')">创建 Pipeline</el-button>
        </template>
      </EmptyState>
      <EmptyState
        v-else-if="selectedPipeline && !runsLoading"
        title="该 Pipeline 暂无执行记录"
        description="在详情页激活并运行后可在此回溯"
      />
      <div v-else v-loading="true" class="loading-placeholder" />

      <div class="table-pagination" v-if="total > pageSize">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next, jumper"
          @current-change="onPageChange"
        />
      </div>
    </SectionCard>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * Pipeline 执行历史：按 Pipeline 查看执行记录，支持耗时与错误摘要。
 * - 支持 ?pipeline={id} 直达指定 Pipeline，否则默认选中第一个；
 * - 耗时由结束时间 - 开始时间计算（前端展示，不改接口）。
 */
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import FilterBar from '@/components/common/FilterBar.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { PIPELINE_RUN_STATUS } from '@/utils/status'
import { formatDateTime, formatDuration, formatInteger } from '@/utils/format'
import { Refresh } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()

const pipelines = ref<any[]>([])
const pipelinesLoading = ref(false)
const selectedPipeline = ref('')
const runs = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const runsLoading = ref(false)
const runsError = ref('')

const fetchPipelines = async () => {
  pipelinesLoading.value = true
  try {
    const res: any = await api.get('/pipelines', { limit: 100 })
    pipelines.value = res.pipelines || []
    // 支持 ?pipeline={id} 直达；默认选中第一个
    const fromQuery = String(route.query.pipeline || '')
    if (fromQuery && pipelines.value.some((p) => p.id === fromQuery)) {
      selectedPipeline.value = fromQuery
    } else if (pipelines.value.length > 0) {
      selectedPipeline.value = pipelines.value[0].id
    }
    if (selectedPipeline.value) await fetchRuns()
  } catch (err: any) {
    appStore.error('Pipeline 列表加载失败', err?.message)
  } finally {
    pipelinesLoading.value = false
  }
}

// 执行历史按 Pipeline 查询（后端接口为 /pipelines/{id}/runs）
const fetchRuns = async () => {
  if (!selectedPipeline.value) return
  runsLoading.value = true
  runsError.value = ''
  try {
    const res: any = await api.get(`/pipelines/${selectedPipeline.value}/runs`, {
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    runs.value = res.runs || []
    total.value = res.total || 0
  } catch (err: any) {
    runsError.value = err?.message || '加载失败'
    appStore.error('执行历史加载失败', err?.message)
  } finally {
    runsLoading.value = false
  }
}

/** 切换 Pipeline：回到第一页再查询 */
const onPipelineChange = () => {
  page.value = 1
  fetchRuns()
}

const onPageChange = (p: number) => {
  page.value = p
  fetchRuns()
}

const viewRun = (runId: string) => router.push(`/pipelines/run/${selectedPipeline.value}/${runId}`)

/** 执行耗时（秒）：结束时间 - 开始时间（字段缺失返回 null 显示 '-'） */
const durationOf = (row: any): number | null => {
  if (!row.started_at || !row.finished_at) return null
  return Math.max(0, row.finished_at - row.started_at)
}

const triggerText = (trigger?: string) => {
  const map: Record<string, string> = { manual: '手动', scheduled: '定时', event: '事件', api: 'API' }
  return map[trigger || ''] || trigger || '-'
}

onMounted(fetchPipelines)
</script>

<style scoped>
.loading-placeholder {
  min-height: 200px;
}

.error-text {
  color: var(--mtp-danger);
}
</style>
