<template>
  <PageShell :title="detail?.name || 'Pipeline 详情'" module="Pipeline 编排" :loading="loading && !detail">
    <template #actions>
      <el-button @click="fetchDetail" :loading="loading">
        <el-icon class="gap-8"><Refresh /></el-icon>
        刷新
      </el-button>
      <el-button v-if="detail && detail.status !== 'active'" type="success" plain @click="setStatus('active')">激活</el-button>
      <el-button v-if="detail && detail.status === 'active'" type="warning" plain @click="setStatus('paused')">暂停调度</el-button>
      <el-button v-if="detail && detail.status === 'active'" type="primary" @click="runPipeline">立即运行</el-button>
      <el-button @click="router.push(`/pipelines/runs?pipeline=${pipelineId}`)">执行历史</el-button>
      <el-button @click="router.push(`/pipelines/create?id=${pipelineId}`)">
        <el-icon class="gap-8"><EditPen /></el-icon>
        编辑
      </el-button>
    </template>

    <!-- 加载失败 -->
    <SectionCard v-if="error" flush>
      <EmptyState type="error" title="Pipeline 加载失败" :description="error" retryable @retry="fetchDetail" />
    </SectionCard>

    <template v-else>
      <!-- 基本信息 -->
      <SectionCard v-loading="loading && !detail" title="基本信息">
        <template #extra>
          <StatusTag v-if="detail" :status="detail.status" :map="PIPELINE_STATUS" size="large" />
        </template>
        <el-descriptions v-if="detail" :column="2" border>
          <el-descriptions-item label="描述" :span="2">{{ detail.description || '-' }}</el-descriptions-item>
          <el-descriptions-item label="调度">{{ detail.schedule || '手动触发' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatFullDateTime(detail.created_at) }}</el-descriptions-item>
        </el-descriptions>
        <div v-else class="loading-placeholder" />
      </SectionCard>

      <!-- 步骤编排图 -->
      <SectionCard v-if="detail && steps.length > 0" title="步骤编排" :description="`共 ${steps.length} 步，点击节点查看配置`" flush>
        <div class="graph-layout">
          <div class="graph-canvas">
            <VueFlow
              class="pipeline-graph"
              :nodes="flowNodes"
              :edges="flowEdges"
              :fit-view-on-init="true"
              :nodes-connectable="false"
              :elements-selectable="false"
              :min-zoom="0.3"
              :max-zoom="1.6"
              @node-click="onNodeClick"
            >
              <Background />
              <Controls />
              <MiniMap pannable zoomable />
            </VueFlow>
          </div>
          <div class="graph-side">
            <div class="step-list">
              <button
                v-for="(step, index) in steps"
                :key="step.id"
                type="button"
                class="step-item"
                :class="{ 'is-active': selectedStepId === step.id }"
                @click="selectStep(step.id)"
              >
                <span class="step-item__index">{{ index + 1 }}</span>
                <span class="step-item__name">{{ step.name || step.id }}</span>
                <span class="step-item__type">{{ stepTypeText(step.type) }}</span>
              </button>
            </div>
          </div>
        </div>
      </SectionCard>

      <!-- 步骤明细表 -->
      <SectionCard v-if="detail" :title="`步骤明细（${steps.length} 步）`" flush>
        <el-table v-if="steps.length > 0" :data="steps" class="table-card" :header-cell-style="{ background: 'var(--mtp-bg-subtle)' }">
          <el-table-column type="index" label="#" width="50" />
          <el-table-column prop="name" label="步骤名称" min-width="140">
            <template #default="{ row }">{{ row.name || row.id }}</template>
          </el-table-column>
          <el-table-column label="类型" width="120">
            <template #default="{ row }">{{ stepTypeText(row.type) }}</template>
          </el-table-column>
          <el-table-column label="依赖" min-width="140">
            <template #default="{ row }">
              <template v-if="row.depends_on?.length">
                <el-tag v-for="dep in row.depends_on" :key="dep" size="small" type="info" effect="plain" class="dep-tag">
                  {{ depName(dep) }}
                </el-tag>
              </template>
              <span v-else class="text-ink-400">-</span>
            </template>
          </el-table-column>
          <el-table-column label="超时" width="90">
            <template #default="{ row }">{{ row.timeout ? formatDuration(row.timeout) : '-' }}</template>
          </el-table-column>
          <el-table-column label="配置" min-width="220">
            <template #default="{ row }">
              <span class="config-text mono">{{ truncate(JSON.stringify(row.config || {}), 80) }}</span>
            </template>
          </el-table-column>
        </el-table>
        <EmptyState v-else title="该 Pipeline 暂无步骤" description="点击右上角「编辑」添加步骤" />
      </SectionCard>

      <!-- 步骤配置抽屉 -->
      <el-drawer v-model="showStepDrawer" :title="`步骤配置 · ${drawerStep?.name || drawerStep?.id || ''}`" size="46%" destroy-on-close>
        <template v-if="drawerStep">
          <el-descriptions :column="2" border class="mb-16">
            <el-descriptions-item label="类型">{{ stepTypeText(drawerStep.type) }}</el-descriptions-item>
            <el-descriptions-item label="超时">{{ drawerStep.timeout ? formatDuration(drawerStep.timeout) : '-' }}</el-descriptions-item>
            <el-descriptions-item label="失败继续">{{ drawerStep.continue_on_failure ? '是' : '否' }}</el-descriptions-item>
            <el-descriptions-item label="依赖">{{ drawerStep.depends_on?.length ? drawerStep.depends_on.map(depName).join('、') : '无' }}</el-descriptions-item>
          </el-descriptions>
          <div class="drawer-section-title">运行配置 config</div>
          <pre class="code-content">{{ JSON.stringify(drawerStep.config || {}, null, 2) }}</pre>
          <template v-if="drawerStep.resource_hint && Object.keys(drawerStep.resource_hint).length">
            <div class="drawer-section-title">资源要求 resource_hint</div>
            <pre class="code-content">{{ JSON.stringify(drawerStep.resource_hint, null, 2) }}</pre>
          </template>
          <template v-if="drawerStep.retry_policy && Object.keys(drawerStep.retry_policy).length">
            <div class="drawer-section-title">重试策略 retry_policy</div>
            <pre class="code-content">{{ JSON.stringify(drawerStep.retry_policy, null, 2) }}</pre>
          </template>
        </template>
      </el-drawer>
    </template>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * Pipeline 详情：步骤依赖图（Vue Flow 只读）+ 步骤清单 + 配置抽屉。
 * - 点击画布节点或列表项查看该步骤的完整配置（含高级字段）；
 * - 激活/暂停/立即运行按当前状态显隐。
 */
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { VueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { PIPELINE_STATUS, PIPELINE_STEP_TYPE } from '@/utils/status'
import { computeLayout, type DagNode, type DagEdge } from '@/utils/dag'
import { formatFullDateTime, formatDuration, truncate } from '@/utils/format'
import { Refresh, EditPen } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()
const detail = ref<any>(null)
const loading = ref(false)
const error = ref('')
const selectedStepId = ref('')
const showStepDrawer = ref(false)

const pipelineId = String(route.params.id || '')
const steps = computed<any[]>(() => detail.value?.steps || [])

/** 画布节点与边（只读图，无拖拽状态） */
const flowNodes = computed<DagNode[]>(() => {
  const layout = computeLayout(steps.value)
  return steps.value.map((step) => ({
    id: step.id,
    label: step.name || step.id,
    type: step.type,
    position: layout[step.id] || { x: 0, y: 0 },
  }))
})

const flowEdges = computed<DagEdge[]>(() => {
  const edges: DagEdge[] = []
  for (const step of steps.value) {
    for (const dep of step.depends_on || []) {
      if (steps.value.some((s) => s.id === dep) && dep !== step.id) {
        edges.push({ id: `e-${dep}-${step.id}`, source: dep, target: step.id })
      }
    }
  }
  return edges
})

/** 当前抽屉展示的步骤 */
const drawerStep = computed(() => steps.value.find((s) => s.id === selectedStepId.value) || null)

const selectStep = (stepId: string) => {
  selectedStepId.value = stepId
  showStepDrawer.value = true
}

const onNodeClick = (mouseEvent: { node: { id: string } }) => selectStep(mouseEvent.node.id)

// 后端实际路径为 /api/pipelines/{id}（旧版模板字符串未替换导致 404）
const fetchDetail = async () => {
  loading.value = true
  error.value = ''
  try {
    detail.value = await api.get(`/pipelines/${pipelineId}`)
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

const setStatus = async (status: string) => {
  try {
    await api.put(`/pipelines/${pipelineId}`, { status })
    appStore.success(status === 'active' ? 'Pipeline 已激活' : 'Pipeline 已暂停调度')
    fetchDetail()
  } catch (err: any) {
    appStore.error('状态更新失败', err?.message)
  }
}

const runPipeline = async () => {
  try {
    const res: any = await api.post(`/pipelines/${pipelineId}/run`, { trigger: 'manual', parameters: {} })
    appStore.success('Pipeline 执行已启动', `执行 ID：${res.run_id}`)
    router.push(`/pipelines/run/${pipelineId}/${res.run_id}`)
  } catch (err: any) {
    appStore.error('运行失败', err?.message)
  }
}

const depName = (depId: string) => {
  const step = steps.value.find((s) => s.id === depId)
  return step?.name || depId
}

const stepTypeText = (type?: string) => PIPELINE_STEP_TYPE[type || '']?.label || type || '-'

onMounted(fetchDetail)
</script>

<style scoped>
.loading-placeholder {
  min-height: 160px;
}

.graph-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 0;
}

@media (max-width: 992px) {
  .graph-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

.graph-canvas {
  height: 420px;
  border-right: 1px solid var(--mtp-ink-100);
  background: var(--mtp-bg-subtle);
}

@media (max-width: 992px) {
  .graph-canvas {
    border-right: none;
    border-bottom: 1px solid var(--mtp-ink-100);
  }
}

.pipeline-graph {
  width: 100%;
  height: 100%;
}

.graph-side {
  max-height: 420px;
  overflow-y: auto;
  padding: 10px;
}

.step-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.step-item {
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

.step-item:hover {
  border-color: var(--mtp-brand-500);
}

.step-item.is-active {
  border-color: var(--mtp-brand-600);
  background: var(--mtp-brand-50);
}

.step-item__index {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--mtp-brand-600);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  flex-shrink: 0;
}

.step-item__name {
  flex: 1;
  font-size: 13px;
  color: var(--mtp-ink-900);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.step-item__type {
  font-size: 11px;
  color: var(--mtp-ink-400);
  flex-shrink: 0;
}

.dep-tag {
  margin-right: 4px;
}

.config-text {
  font-size: 11px;
  color: var(--mtp-ink-500);
  word-break: break-all;
}

.drawer-section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--mtp-ink-900);
  margin: 14px 0 8px;
}

.code-content {
  margin: 0;
  padding: 12px;
  background: #1e1e2a;
  color: #d4d4d4;
  border-radius: var(--mtp-radius-md);
  font-family: var(--mtp-font-mono);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  overflow: auto;
}
</style>
