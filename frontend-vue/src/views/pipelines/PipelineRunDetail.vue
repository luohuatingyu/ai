<template>
  <PageShell title="执行详情" module="Pipeline 编排" :loading="loading && !detail">
    <template #actions>
      <el-button @click="refreshAll" :loading="loading">
        <el-icon class="gap-8"><Refresh /></el-icon>
        刷新
      </el-button>
      <el-button
        v-if="detail && ['pending', 'running'].includes(detail.status)"
        type="warning"
        @click="cancelRun"
      >
        取消执行
      </el-button>
    </template>

    <!-- 加载失败 -->
    <SectionCard v-if="error" flush>
      <EmptyState type="error" title="执行详情加载失败" :description="error" retryable @retry="refreshAll" />
    </SectionCard>

    <template v-else>
      <!-- 失败告警 -->
      <el-alert
        v-if="detail && detail.status === 'failed' && detail.error"
        :title="`执行失败：${detail.error}`"
        type="error"
        show-icon
        :closable="false"
        class="mb-16"
      />

      <!-- 执行概要 -->
      <SectionCard v-loading="loading && !detail" title="执行概要">
        <template #extra>
          <StatusTag v-if="detail" :status="detail.status" :map="PIPELINE_RUN_STATUS" size="large" />
        </template>
        <el-descriptions v-if="detail" :column="2" border>
          <el-descriptions-item label="执行 ID"><span class="mono text-sm">{{ detail.id }}</span></el-descriptions-item>
          <el-descriptions-item label="触发方式">{{ triggerText(detail.trigger) }}</el-descriptions-item>
          <el-descriptions-item label="开始时间">{{ formatFullDateTime(detail.started_at) }}</el-descriptions-item>
          <el-descriptions-item label="结束时间">{{ formatFullDateTime(detail.finished_at) }}</el-descriptions-item>
        </el-descriptions>
        <div v-else class="loading-placeholder" />
      </SectionCard>

      <!-- 步骤状态图（依赖结构来自 Pipeline 定义，状态来自本次执行） -->
      <SectionCard
        v-if="detail && graphSteps.length > 0"
        title="步骤执行图"
        description="节点状态即本次执行的步骤状态，点击节点查看该步骤日志"
        flush
      >
        <div class="run-graph-layout">
          <div class="run-graph-canvas">
            <VueFlow
              class="run-flow"
              :nodes="runFlowNodes"
              :edges="runFlowEdges"
              :fit-view-on-init="true"
              :nodes-connectable="false"
              :elements-selectable="false"
              :min-zoom="0.3"
              :max-zoom="1.6"
              @node-click="onNodeClick"
            >
              <Background />
              <Controls />
            </VueFlow>
          </div>
          <div class="run-graph-side">
            <button
              v-for="step in runSteps"
              :key="step.step_id || step.id"
              type="button"
              class="run-step-item"
              :class="{ 'is-active': selectedRunStepId === (step.step_id || step.id) }"
              @click="selectRunStep(step)"
            >
              <StatusTag :status="step.status" :map="PIPELINE_RUN_STATUS" />
              <span class="run-step-item__name">{{ step.step_name || step.step_id }}</span>
              <span class="run-step-item__type">{{ stepTypeText(step.step_type) }}</span>
            </button>
          </div>
        </div>
      </SectionCard>

      <!-- 步骤明细 -->
      <SectionCard v-if="detail" title="步骤执行情况" flush>
        <el-table v-if="runSteps.length > 0" :data="runSteps" class="table-card" :header-cell-style="{ background: 'var(--mtp-bg-subtle)' }">
          <el-table-column prop="step_name" label="步骤" min-width="140">
            <template #default="{ row }">{{ row.step_name || row.step_id }}</template>
          </el-table-column>
          <el-table-column label="类型" width="120">
            <template #default="{ row }">{{ stepTypeText(row.step_type) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="96">
            <template #default="{ row }">
              <StatusTag :status="row.status" :map="PIPELINE_RUN_STATUS" />
            </template>
          </el-table-column>
          <el-table-column label="重试" width="70">
            <template #default="{ row }">{{ row.retry_count || 0 }}</template>
          </el-table-column>
          <el-table-column label="执行槽" min-width="120">
            <template #default="{ row }">
              <span v-if="row.worker_id" class="mono text-sm">{{ row.worker_id }}</span>
              <span v-else class="text-ink-400">-</span>
            </template>
          </el-table-column>
          <el-table-column label="开始时间" width="120">
            <template #default="{ row }">{{ formatDateTime(row.started_at) }}</template>
          </el-table-column>
          <el-table-column label="结束时间" width="120">
            <template #default="{ row }">{{ formatDateTime(row.finished_at) }}</template>
          </el-table-column>
          <el-table-column label="错误" min-width="150" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.error" class="error-text">{{ row.error }}</span>
              <span v-else class="text-ink-400">-</span>
            </template>
          </el-table-column>
        </el-table>
        <EmptyState v-else title="暂无步骤执行记录" />
      </SectionCard>

      <!-- 步骤日志抽屉 -->
      <el-drawer v-model="showLogDrawer" :title="`步骤日志 · ${selectedRunStep?.step_name || selectedRunStep?.step_id || ''}`" size="52%" destroy-on-close>
        <template v-if="selectedRunStep">
          <el-descriptions :column="2" border class="mb-16">
            <el-descriptions-item label="状态">
              <StatusTag :status="selectedRunStep.status" :map="PIPELINE_RUN_STATUS" />
            </el-descriptions-item>
            <el-descriptions-item label="重试次数">{{ selectedRunStep.retry_count || 0 }}</el-descriptions-item>
            <el-descriptions-item label="开始时间">{{ formatFullDateTime(selectedRunStep.started_at) }}</el-descriptions-item>
            <el-descriptions-item label="结束时间">{{ formatFullDateTime(selectedRunStep.finished_at) }}</el-descriptions-item>
          </el-descriptions>
          <el-alert
            v-if="selectedRunStep.error"
            :title="selectedRunStep.error"
            type="error"
            show-icon
            :closable="false"
            class="mb-16"
          />
          <div class="drawer-section-title">步骤日志</div>
          <div v-if="stepLogs(selectedRunStep).length > 0" class="log-panel">
            <pre>{{ stepLogs(selectedRunStep).join('\n') }}</pre>
          </div>
          <EmptyState v-else title="该步骤暂无日志" />
          <template v-if="selectedRunStep.output_artifacts && Object.keys(selectedRunStep.output_artifacts).length > 0">
            <div class="drawer-section-title">输出产物</div>
            <el-descriptions :column="1" border>
              <el-descriptions-item v-for="(value, key) in selectedRunStep.output_artifacts" :key="key" :label="String(key)">
                <span class="mono text-sm">{{ value }}</span>
              </el-descriptions-item>
            </el-descriptions>
          </template>
        </template>
      </el-drawer>
    </template>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * Pipeline 执行详情：步骤状态映射到依赖图 + 点击节点查看步骤日志。
 * - 依赖结构来自 Pipeline 定义（GET /pipelines/{id}），节点状态来自本次执行；
 * - Pipeline 定义加载失败时降级为纯列表展示（不阻塞执行数据）；
 * - 运行中/等待中执行每 5 秒自动轮询，页面卸载时清理。
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { VueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { PIPELINE_RUN_STATUS, PIPELINE_STEP_TYPE } from '@/utils/status'
import { computeLayout, type DagNode, type DagEdge } from '@/utils/dag'
import { formatDateTime, formatFullDateTime } from '@/utils/format'
import { Refresh } from '@element-plus/icons-vue'

const route = useRoute()
const appStore = useAppStore()
const detail = ref<any>(null)
const pipelineDef = ref<any>(null)
const loading = ref(false)
const error = ref('')
const selectedRunStepId = ref('')
const showLogDrawer = ref(false)

const pipelineId = String(route.params.pipelineId || '')
const runId = String(route.params.runId || '')

const runSteps = computed<any[]>(() => detail.value?.steps || [])

/** 按 step_id 查询执行状态（step_states 兜底） */
const statusOfStep = (stepId: string): string | undefined => {
  const runStep = runSteps.value.find((s) => s.step_id === stepId || s.id === stepId)
  if (runStep) return runStep.status
  return detail.value?.step_states?.[stepId]
}

/** 图结构步骤（来自 Pipeline 定义；加载失败时用执行步骤回退，无依赖边） */
const graphSteps = computed<any[]>(() => {
  if (pipelineDef.value?.steps?.length) return pipelineDef.value.steps
  return runSteps.value.map((s: any) => ({ id: s.step_id || s.id, name: s.step_name, depends_on: [] }))
})

const runFlowNodes = computed<DagNode[]>(() => {
  const layout = computeLayout(graphSteps.value)
  return graphSteps.value.map((step: any) => ({
    id: step.id,
    label: step.name || step.id,
    position: layout[step.id] || { x: 0, y: 0 },
    // 节点样式随执行状态变化（运行中高亮、失败标红等）
    class: `run-node run-node--${statusOfStep(step.id) || 'pending'}`,
  }))
})

const runFlowEdges = computed<DagEdge[]>(() => {
  const edges: DagEdge[] = []
  for (const step of graphSteps.value) {
    for (const dep of step.depends_on || []) {
      if (graphSteps.value.some((s: any) => s.id === dep) && dep !== step.id) {
        edges.push({ id: `e-${dep}-${step.id}`, source: dep, target: step.id })
      }
    }
  }
  return edges
})

/** 当前抽屉展示的执行步骤 */
const selectedRunStep = computed(() =>
  runSteps.value.find((s) => (s.step_id || s.id) === selectedRunStepId.value) || null
)

const selectRunStep = (step: any) => {
  selectedRunStepId.value = step.step_id || step.id
  showLogDrawer.value = true
}

const onNodeClick = (mouseEvent: { node: { id: string } }) => {
  const runStep = runSteps.value.find((s) => (s.step_id || s.id) === mouseEvent.node.id)
  if (runStep) selectRunStep(runStep)
}

/** 单步骤日志行 */
const stepLogs = (step: any): string[] => (step.logs || []).filter(Boolean)

const stepTypeText = (type?: string) => PIPELINE_STEP_TYPE[type || '']?.label || type || '-'

// 路由携带 pipelineId + runId：/pipelines/run/{pipelineId}/{runId}
const fetchDetail = async () => {
  loading.value = true
  error.value = ''
  try {
    detail.value = await api.get(`/pipelines/${pipelineId}/runs/${runId}`)
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

/** 拉取 Pipeline 定义（图结构），失败不阻塞执行数据展示 */
const fetchPipelineDef = async () => {
  try {
    pipelineDef.value = await api.get(`/pipelines/${pipelineId}`)
  } catch {
    pipelineDef.value = null
  }
}

const refreshAll = async () => {
  await Promise.allSettled([fetchDetail(), fetchPipelineDef()])
}

const cancelRun = async () => {
  try {
    await api.post(`/pipelines/${pipelineId}/runs/${runId}/cancel`)
    appStore.success('已发送取消请求')
    fetchDetail()
  } catch (err: any) {
    appStore.error('取消失败', err?.message)
  }
}

const triggerText = (trigger?: string) => {
  const map: Record<string, string> = { manual: '手动', scheduled: '定时', event: '事件', api: 'API' }
  return map[trigger || ''] || trigger || '-'
}

// 运行中/等待中执行自动轮询
let pollTimer: ReturnType<typeof setInterval> | null = null
const startPolling = () => {
  const status = detail.value?.status
  if ((status === 'running' || status === 'pending') && !pollTimer) {
    pollTimer = setInterval(fetchDetail, 5000)
  }
}
const stopPolling = () => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(async () => {
  await refreshAll()
  startPolling()
})

onUnmounted(stopPolling)
</script>

<style scoped>
.loading-placeholder {
  min-height: 160px;
}

.error-text {
  color: var(--mtp-danger);
}

.run-graph-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 0;
}

@media (max-width: 992px) {
  .run-graph-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

.run-graph-canvas {
  height: 380px;
  border-right: 1px solid var(--mtp-ink-100);
  background: var(--mtp-bg-subtle);
}

@media (max-width: 992px) {
  .run-graph-canvas {
    border-right: none;
    border-bottom: 1px solid var(--mtp-ink-100);
  }
}

.run-flow {
  width: 100%;
  height: 100%;
}

.run-graph-side {
  max-height: 380px;
  overflow-y: auto;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.run-step-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--mtp-ink-100);
  border-radius: var(--mtp-radius-md);
  background: var(--mtp-bg-card);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.15s, background 0.15s;
}

.run-step-item:hover {
  border-color: var(--mtp-brand-500);
}

.run-step-item.is-active {
  border-color: var(--mtp-brand-600);
  background: var(--mtp-brand-50);
}

.run-step-item__name {
  flex: 1;
  font-size: 13px;
  color: var(--mtp-ink-900);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-step-item__type {
  font-size: 11px;
  color: var(--mtp-ink-400);
  flex-shrink: 0;
}

.drawer-section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--mtp-ink-900);
  margin: 14px 0 8px;
}

.log-panel {
  background: #1e1e2a;
  border-radius: var(--mtp-radius-md);
  max-height: 320px;
  overflow: auto;
}

.log-panel pre {
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
