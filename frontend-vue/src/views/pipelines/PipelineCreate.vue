<template>
  <PageShell :title="isEdit ? '编辑 Pipeline' : '创建 Pipeline'" module="Pipeline 编排" :loading="loadingDetail">
    <template #actions>
      <el-button @click="router.push('/pipelines/list')">
        <el-icon class="gap-8"><Back /></el-icon>
        返回列表
      </el-button>
    </template>

    <!-- 编辑模式加载失败 -->
    <SectionCard v-if="loadError" flush>
      <EmptyState type="error" title="Pipeline 加载失败" :description="loadError" retryable @retry="retryLoad" />
    </SectionCard>

    <el-form v-else ref="formRef" :model="form" :rules="rules" label-position="top">
      <!-- 基本信息 -->
      <SectionCard title="基本信息">
        <el-row :gutter="16">
          <el-col :xs="24" :sm="12">
            <el-form-item label="名称" prop="name">
              <el-input v-model="form.name" placeholder="如：销量预测全流程" maxlength="120" show-word-limit />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="定时调度">
              <el-input v-model="form.schedule" placeholder="cron 表达式，留空表示手动触发（如 0 2 * * *）" class="mono" />
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="描述">
              <el-input v-model="form.description" placeholder="Pipeline 用途说明" maxlength="300" show-word-limit />
            </el-form-item>
          </el-col>
        </el-row>
      </SectionCard>

      <!-- 可视化编排 -->
      <SectionCard title="步骤编排" description="点击节点编辑属性；连线表示依赖（被依赖步骤先执行）" flush>
        <template #extra>
          <div class="canvas-toolbar">
            <el-button size="small" @click="relayout">
              <el-icon><Sort /></el-icon>
              重新布局
            </el-button>
            <el-button size="small" type="primary" @click="addStep">
              <el-icon><Plus /></el-icon>
              添加步骤
            </el-button>
          </div>
        </template>

        <div class="canvas-layout">
          <!-- 画布 -->
          <div class="canvas-wrap">
            <VueFlow
              v-if="steps.length > 0"
              class="pipeline-flow"
              :nodes="flowNodes"
              :edges="flowEdges"
              :fit-view-on-init="true"
              :min-zoom="0.3"
              :max-zoom="1.6"
              @node-click="onNodeClick"
            >
              <Background />
              <Controls />
              <MiniMap pannable zoomable />
            </VueFlow>
            <EmptyState
              v-else
              title="暂无步骤"
              description="点击右上角「添加步骤」开始编排，节点将出现在画布中"
            />
          </div>

          <!-- 属性面板 -->
          <div class="property-panel">
            <template v-if="selectedStep">
              <div class="panel-head">
                <span class="panel-title">步骤属性</span>
                <el-button size="small" type="danger" plain @click="removeStep(selectedStep.id)">移除步骤</el-button>
              </div>

              <el-form label-position="top" size="small" :key="selectedStep.id">
                <el-form-item label="步骤 ID">
                <el-input :model-value="selectedStep.id" class="mono" disabled />
                <div class="text-xs text-ink-400" style="margin-top: 4px">ID 创建后不可修改（被依赖关系引用），重命名请使用步骤名称</div>
              </el-form-item>
                <el-form-item label="步骤名称">
                  <el-input v-model="selectedStep.name" placeholder="如：拉取销量数据" />
                </el-form-item>
                <el-form-item label="步骤类型">
                  <el-select v-model="selectedStep.type" style="width: 100%">
                    <el-option v-for="t in STEP_TYPES" :key="t.value" :label="t.label" :value="t.value" />
                  </el-select>
                </el-form-item>
                <el-form-item label="依赖步骤（被依赖步骤先执行）">
                  <el-select
                    v-model="selectedStep.depends_on"
                    multiple
                    collapse-tags
                    collapse-tags-tooltip
                    placeholder="无（并行执行）"
                    style="width: 100%"
                  >
                    <el-option
                      v-for="s in steps"
                      :key="s.id"
                      :label="stepLabel(s)"
                      :value="s.id"
                      :disabled="s.id === selectedStep.id"
                    />
                  </el-select>
                </el-form-item>

                <el-collapse class="advanced-collapse">
                  <el-collapse-item name="config">
                    <template #title>运行配置（JSON）</template>
                    <el-input
                      v-model="selectedStep.config_json"
                      type="textarea"
                      :rows="6"
                      class="mono"
                      placeholder='{"dataset_id": "..."}'
                    />
                  </el-collapse-item>
                  <el-collapse-item name="advanced">
                    <template #title>高级选项（资源 / 重试 / 超时）</template>
                    <el-form-item label="资源要求 resource_hint（JSON）">
                      <el-input v-model="selectedStep.resource_hint_json" type="textarea" :rows="3" class="mono" placeholder='{"require_gpu": true}' />
                    </el-form-item>
                    <el-form-item label="重试策略 retry_policy（JSON）">
                      <el-input v-model="selectedStep.retry_policy_json" type="textarea" :rows="3" class="mono" placeholder='{"max_retries": 2}' />
                    </el-form-item>
                    <el-form-item label="超时（秒）">
                      <el-input-number v-model="selectedStep.timeout" :min="1" :max="86400" style="width: 100%" />
                    </el-form-item>
                    <el-form-item label="失败继续">
                      <el-switch v-model="selectedStep.continue_on_failure" />
                      <span class="text-xs text-secondary" style="margin-left: 8px">该步骤失败时不中断后续步骤</span>
                    </el-form-item>
                  </el-collapse-item>
                </el-collapse>
                <div v-if="selectedStepJsonError" class="json-error">
                  <el-icon><WarningFilled /></el-icon>
                  {{ selectedStepJsonError }}
                </div>
              </el-form>
            </template>
            <EmptyState
              v-else
              title="未选中步骤"
              description="点击画布中的节点查看与编辑步骤属性"
            />
          </div>
        </div>

        <!-- 依赖问题提示 -->
        <el-alert
          v-for="issue in issues"
          :key="issue.message"
          :title="issue.message"
          type="error"
          show-icon
          :closable="false"
          class="issue-alert"
        />
      </SectionCard>

      <div class="submit-bar">
        <el-button type="primary" size="large" :loading="submitting" :disabled="steps.length === 0" @click="submit">
          {{ isEdit ? '保存修改' : '创建 Pipeline' }}
        </el-button>
        <el-button size="large" @click="router.back()">取消</el-button>
      </div>
    </el-form>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * 创建 / 编辑 Pipeline：Vue Flow 可视化画布 + 步骤属性面板。
 * - 画布节点与步骤草稿双向同步：添加/移除/改名/改依赖实时反映到图上；
 * - 依赖校验（重复 ID / 自依赖 / 缺失依赖 / 循环）在保存前阻断并逐条提示；
 * - 编辑模式完整保留后端高级字段（resource_hint / retry_policy /
 *   timeout / continue_on_failure），不再固定覆盖为默认值；
 * - 画布坐标仅用于前端展示，不进入提交载荷。
 */
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
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
import EmptyState from '@/components/common/EmptyState.vue'
import { detectIssues, computeLayout, stepLabel, type DagNode, type DagEdge } from '@/utils/dag'
import { Back, Plus, Sort, WarningFilled } from '@element-plus/icons-vue'

const router = useRouter()
const route = useRoute()
const appStore = useAppStore()

/** 步骤类型（与后端 PipelineStep.type 枚举一致） */
const STEP_TYPES = [
  { value: 'dataset_pull', label: '数据集拉取' },
  { value: 'dataset_process', label: '数据预处理' },
  { value: 'training', label: '模型训练' },
  { value: 'model_upload', label: '模型注册' },
  { value: 'model_deploy', label: '模型部署' },
  { value: 'custom_script', label: '自定义脚本' },
  { value: 'notebook_run', label: 'Notebook 执行' },
  { value: 'inference', label: '批量推理' },
]

/** 步骤草稿：高级字段以 JSON 文本承载，保存时解析回传 */
interface StepDraft {
  id: string
  name: string
  type: string
  config_json: string
  depends_on: string[]
  resource_hint_json: string
  retry_policy_json: string
  timeout: number
  continue_on_failure: boolean
}

const formRef = ref<FormInstance>()
const submitting = ref(false)
const loadingDetail = ref(false)
const loadError = ref('')
const form = reactive({ name: '', description: '', schedule: '' })
const steps = ref<StepDraft[]>([])
const selectedStepId = ref('')
const nodePositions = ref<Record<string, { x: number; y: number }>>({})

const isEdit = computed(() => Boolean(route.query.id))

const rules: FormRules = {
  name: [{ required: true, message: '请输入 Pipeline 名称', trigger: 'blur' }],
}

/** 当前选中的步骤（属性面板绑定对象） */
const selectedStep = computed(() => steps.value.find((s) => s.id === selectedStepId.value) || null)

/** 选中步骤的 JSON 实时校验错误（内联提示，保存前阻断） */
const selectedStepJsonError = computed(() => {
  if (!selectedStep.value) return ''
  if (parseJsonField(selectedStep.value.config_json) === null) return '运行配置必须是合法 JSON 对象'
  if (parseJsonField(selectedStep.value.resource_hint_json) === null) return '资源要求必须是合法 JSON 对象'
  if (parseJsonField(selectedStep.value.retry_policy_json) === null) return '重试策略必须是合法 JSON 对象'
  return ''
})

/** 依赖问题（保存前阻断） */
const issues = computed(() => detectIssues(steps.value))

// ---- 画布节点与边（由步骤草稿推导，坐标保留用户拖拽结果） ----
const flowNodes = computed<DagNode[]>(() => {
  const layout = Object.keys(nodePositions.value).length > 0 ? nodePositions.value : computeLayout(steps.value)
  return steps.value.map((step) => ({
    id: step.id,
    label: stepLabel(step),
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

/** 重新自动布局（丢弃拖拽位置） */
const relayout = () => {
  nodePositions.value = {}
}

/** 点击画布节点：选中步骤 */
const onNodeClick = (mouseEvent: { node: { id: string } }) => {
  selectedStepId.value = mouseEvent.node.id
}

// ---- 步骤草稿管理 ----
/** 新增步骤：默认无依赖，位置放在画布空白列 */
const addStep = () => {
  const id = `step-${Date.now().toString(36)}-${steps.value.length}`
  steps.value.push({
    id,
    name: '',
    type: 'training',
    config_json: '{}',
    depends_on: [],
    resource_hint_json: '{}',
    retry_policy_json: '{}',
    timeout: 3600,
    continue_on_failure: false,
  })
  selectedStepId.value = id
}

/** 移除步骤：同步清理其他步骤对它的依赖，并自动选中下一个剩余步骤 */
const removeStep = (stepId: string) => {
  steps.value = steps.value.filter((s) => s.id !== stepId)
  for (const step of steps.value) {
    step.depends_on = step.depends_on.filter((d) => d !== stepId)
  }
  if (selectedStepId.value === stepId) {
    selectedStepId.value = steps.value[0]?.id || ''
  }
  delete nodePositions.value[stepId]
}

// ---- 编辑模式：加载详情并完整保留高级字段 ----
const loadPipeline = async () => {
  const pipelineId = String(route.query.id || '')
  if (!pipelineId) return
  loadingDetail.value = true
  try {
    const detail: any = await api.get(`/pipelines/${pipelineId}`)
    form.name = detail.name
    form.description = detail.description || ''
    form.schedule = detail.schedule || ''
    steps.value = (detail.steps || []).map((s: any, i: number) => ({
      id: s.id || `step-${i}`,
      name: s.name || '',
      type: s.type || 'training',
      config_json: JSON.stringify(s.config || {}, null, 2),
      depends_on: [...(s.depends_on || [])],
      // 高级字段原样保留（旧版本保存时被固定覆盖为默认值，此处修复）
      resource_hint_json: JSON.stringify(s.resource_hint || {}, null, 2),
      retry_policy_json: JSON.stringify(s.retry_policy || {}, null, 2),
      timeout: s.timeout ?? 3600,
      continue_on_failure: Boolean(s.continue_on_failure),
    }))
  } catch (err: any) {
    loadError.value = err?.message || '加载失败'
    appStore.error('Pipeline 加载失败', err?.message)
  } finally {
    loadingDetail.value = false
  }
}

/** 加载失败重试 */
const retryLoad = () => loadPipeline()

/** 解析 JSON 文本为对象；非法返回 null */
const parseJsonField = (text: string): Record<string, any> | null => {
  if (!text.trim()) return {}
  try {
    const parsed = JSON.parse(text)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null
  } catch {
    return null
  }
}

/** 组装步骤载荷：解析全部 JSON 字段；非法返回 null */
const buildStepsPayload = (): any[] | null => {
  const result: any[] = []
  for (const step of steps.value) {
    const config = parseJsonField(step.config_json)
    if (config === null) return null
    const resourceHint = parseJsonField(step.resource_hint_json)
    if (resourceHint === null) return null
    const retryPolicy = parseJsonField(step.retry_policy_json)
    if (retryPolicy === null) return null
    result.push({
      id: step.id,
      name: step.name || step.id,
      type: step.type,
      config,
      depends_on: [...step.depends_on],
      resource_hint: resourceHint,
      retry_policy: retryPolicy,
      timeout: step.timeout,
      continue_on_failure: step.continue_on_failure,
    })
  }
  return result
}

const submit = async () => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  // 依赖关系校验：逐条提示，不合法不提交
  if (issues.value.length > 0) {
    appStore.error('步骤依赖关系存在问题', issues.value[0].message)
    return
  }

  const stepsPayload = buildStepsPayload()
  if (stepsPayload === null) {
    appStore.error('步骤配置 / 资源要求 / 重试策略必须是合法 JSON 对象')
    return
  }
  submitting.value = true
  try {
    const payload = {
      name: form.name,
      description: form.description,
      steps: stepsPayload,
      schedule: form.schedule || null,
    }
    if (isEdit.value) {
      await api.put(`/pipelines/${route.query.id}`, payload)
      appStore.success('Pipeline 已更新', form.name)
    } else {
      await api.post('/pipelines', payload)
      appStore.success('Pipeline 创建成功', '可在详情页激活后运行')
    }
    router.push('/pipelines/list')
  } catch (err: any) {
    appStore.error(isEdit.value ? '保存失败' : '创建失败', err?.message)
  } finally {
    submitting.value = false
  }
}

onMounted(loadPipeline)
</script>

<style scoped>
.canvas-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 0;
}

@media (max-width: 992px) {
  .canvas-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

.canvas-wrap {
  height: 480px;
  border-right: 1px solid var(--mtp-ink-100);
  background: var(--mtp-bg-subtle);
  position: relative;
}

@media (max-width: 992px) {
  .canvas-wrap {
    border-right: none;
    border-bottom: 1px solid var(--mtp-ink-100);
  }
}

.pipeline-flow {
  width: 100%;
  height: 100%;
}

.canvas-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.property-panel {
  padding: 14px 16px;
  max-height: 480px;
  overflow-y: auto;
  background: var(--mtp-bg-card);
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.panel-title {
  font-weight: 600;
  color: var(--mtp-ink-900);
  font-size: 14px;
}

.advanced-collapse {
  border: none;
}

.advanced-collapse :deep(.el-collapse-item__header) {
  font-size: 13px;
  color: var(--mtp-ink-700);
}

.issue-alert {
  margin: 0 16px 12px;
}

.json-error {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  padding: 8px 12px;
  border-radius: var(--mtp-radius-sm);
  background: var(--el-color-danger-light-9);
  color: var(--mtp-danger);
  font-size: 13px;
}

.submit-bar {
  display: flex;
  gap: 12px;
  padding: 4px 0 20px;
}
</style>
