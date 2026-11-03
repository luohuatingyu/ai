<template>
  <PageShell title="创建训练任务" module="训练任务" description="选择数据集、任务类型、算法与资源后提交训练">
    <template #actions>
      <el-tag v-if="templateName" closable type="primary" effect="plain" size="large" @close="clearTemplate">
        模板：{{ templateName }}
      </el-tag>
      <el-button @click="router.push('/training/jobs')">
        <el-icon class="gap-8"><Back /></el-icon>
        返回任务列表
      </el-button>
    </template>

    <div class="create-grid">
      <div class="create-main">
        <!-- 数据与任务 -->
        <SectionCard title="数据与任务">
          <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
            <el-row :gutter="16">
              <el-col :xs="24" :sm="12">
                <el-form-item label="数据集" prop="dataset_id">
                  <el-select
                    v-model="form.dataset_id"
                    placeholder="选择数据集"
                    style="width: 100%"
                    filterable
                    :loading="datasetsLoading"
                    @change="onDatasetChange"
                  >
                    <el-option v-for="ds in datasets" :key="ds.id" :label="`${ds.name} (${formatInteger(ds.rows)} 行)`" :value="ds.id" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col v-if="form.task !== 'clustering'" :xs="24" :sm="12">
                <el-form-item label="目标列" prop="target">
                  <el-select v-model="form.target" placeholder="选择目标列" style="width: 100%" :loading="columnsLoading">
                    <el-option v-for="col in columns" :key="col" :label="col" :value="col" />
                  </el-select>
                </el-form-item>
              </el-col>
            </el-row>

            <el-form-item label="任务类型" prop="task">
              <div class="task-options">
                <button
                  v-for="option in TASK_OPTIONS"
                  :key="option.value"
                  type="button"
                  class="task-option"
                  :class="{ 'is-active': form.task === option.value }"
                  @click="selectTask(option.value)"
                >
                  <el-icon :size="18"><component :is="option.icon" /></el-icon>
                  <span class="task-option__name">{{ option.label }}</span>
                  <span class="task-option__desc">{{ option.desc }}</span>
                </button>
              </div>
            </el-form-item>

            <el-row v-if="form.task === 'clustering'" :gutter="16">
              <el-col :xs="12" :sm="8">
                <el-form-item label="聚类数">
                  <el-input-number v-model="form.n_clusters" :min="2" :max="64" style="width: 100%" />
                </el-form-item>
              </el-col>
            </el-row>
            <el-row v-if="form.task === 'time_series_forecasting'" :gutter="16">
              <el-col :xs="24" :sm="12">
                <el-form-item label="时间列" prop="time_column">
                  <el-select v-model="form.time_column" placeholder="选择时间列" style="width: 100%" :loading="columnsLoading">
                    <el-option v-for="col in columns" :key="col" :label="col" :value="col" />
                  </el-select>
                </el-form-item>
              </el-col>
            </el-row>
          </el-form>
        </SectionCard>

        <!-- 算法与参数 -->
        <SectionCard title="算法与参数">
          <el-form :model="form" label-position="top">
            <el-form-item label="模型" prop="model_type">
              <el-select v-model="form.model_type" style="width: 100%">
                <el-option v-for="m in modelOptions" :key="m.value" :label="m.label" :value="m.value" />
              </el-select>
            </el-form-item>
            <el-row :gutter="16">
              <el-col :xs="8">
                <el-form-item label="Epochs">
                  <el-input-number v-model="form.epochs" :min="1" :max="500" style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :xs="8">
                <el-form-item label="批大小">
                  <el-input-number v-model="form.batch_size" :min="1" :max="4096" style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :xs="8">
                <el-form-item label="学习率">
                  <el-input-number v-model="form.lr" :min="0.000001" :max="1" :step="0.001" style="width: 100%" />
                </el-form-item>
              </el-col>
            </el-row>
          </el-form>
        </SectionCard>

        <!-- 计算资源 -->
        <SectionCard title="计算资源">
          <div class="resource-row">
            <el-checkbox v-model="form.require_gpu">仅 GPU</el-checkbox>
            <el-select v-model="form.device_backend" style="width: 180px">
              <el-option label="自动后端" value="auto" />
              <el-option label="CUDA" value="cuda" />
              <el-option label="ROCm" value="rocm" />
              <el-option label="CPU" value="cpu" />
            </el-select>
          </div>
        </SectionCard>

        <!-- 高级参数 -->
        <SectionCard title="高级参数" description="以 JSON 追加请求体，用于模板或少量试验参数（可选）">
          <el-input
            v-model="advancedJson"
            type="textarea"
            :rows="8"
            class="mono"
            placeholder='如 {"seed": 42, "test_split": 0.2}'
          />
          <div v-if="advancedError" class="json-error">
            <el-icon><WarningFilled /></el-icon>
            {{ advancedError }}
          </div>
        </SectionCard>
      </div>

      <!-- 配置摘要 -->
      <div class="create-side">
        <SectionCard title="配置摘要" description="确认无误后提交">
          <div class="summary-list">
            <div class="summary-row">
              <span class="summary-label">数据集</span>
              <span class="summary-value" :class="{ 'is-empty': !selectedDataset }">{{ selectedDataset?.name || '未选择' }}</span>
            </div>
            <div class="summary-row">
              <span class="summary-label">任务类型</span>
              <span class="summary-value">{{ TASK_OPTIONS.find((t) => t.value === form.task)?.label }}</span>
            </div>
            <div v-if="form.task !== 'clustering'" class="summary-row">
              <span class="summary-label">目标列</span>
              <span class="summary-value" :class="{ 'is-empty': !form.target }">{{ form.target || '未选择' }}</span>
            </div>
            <div v-if="form.task === 'clustering'" class="summary-row">
              <span class="summary-label">聚类数</span>
              <span class="summary-value">{{ form.n_clusters }}</span>
            </div>
            <div v-if="form.task === 'time_series_forecasting'" class="summary-row">
              <span class="summary-label">时间列</span>
              <span class="summary-value" :class="{ 'is-empty': !form.time_column }">{{ form.time_column || '未选择' }}</span>
            </div>
            <div class="summary-row">
              <span class="summary-label">模型</span>
              <span class="summary-value">{{ modelOptions.find((m) => m.value === form.model_type)?.label || form.model_type }}</span>
            </div>
            <div class="summary-row">
              <span class="summary-label">训练轮数</span>
              <span class="summary-value">{{ form.epochs }}</span>
            </div>
            <div class="summary-row">
              <span class="summary-label">资源</span>
              <span class="summary-value">{{ form.require_gpu ? 'GPU' : '不限' }} · {{ backendLabel }}</span>
            </div>
            <div class="summary-row">
              <span class="summary-label">模板</span>
              <span class="summary-value" :class="{ 'is-empty': !templateName }">{{ templateName || '未使用' }}</span>
            </div>
          </div>
          <el-button type="primary" size="large" class="w-full mt-16" :loading="submitting" @click="submit">
            创建任务
          </el-button>
          <el-button class="w-full mt-8" @click="router.back()">取消</el-button>
        </SectionCard>

        <SectionCard title="说明">
          <ul class="help-list">
            <li>数据集切换后需重新选择目标列（列信息来自数据集详情）。</li>
            <li>模板参数默认值已合并到高级参数，可继续修改。</li>
            <li>任务类型切换后，模型列表仅保留该类型支持的算法。</li>
          </ul>
        </SectionCard>
      </div>
    </div>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * 创建训练任务：分组配置（数据与任务 / 算法与参数 / 资源 / 高级）+ 实时摘要。
 * - 四类任务（分类/回归/聚类/时间序列）参数语义与后端 TrainRequest 校验保持一致；
 * - 数据集切换拉取列信息并清空目标列/时间列，防止旧请求覆盖；
 * - 任务类型切换后模型选项回落到该类型默认模型；
 * - 支持从模板库跳转预填（?template=id），模板参数默认值合并进高级参数。
 */
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { formatInteger } from '@/utils/format'
import { Back, WarningFilled, Collection, TrendCharts, PieChart, Timer } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()

/** 任务类型选项（选择卡片） */
const TASK_OPTIONS = [
  { value: 'classification', label: '分类', desc: '预测离散类别标签', icon: Collection },
  { value: 'regression', label: '回归', desc: '预测连续数值', icon: TrendCharts },
  { value: 'clustering', label: '聚类', desc: '无监督样本分组', icon: PieChart },
  { value: 'time_series_forecasting', label: '时间序列', desc: '按时间趋势预测', icon: Timer },
] as const

const BACKEND_LABELS: Record<string, string> = {
  auto: '自动后端', cuda: 'CUDA', rocm: 'ROCm', cpu: 'CPU',
}

const formRef = ref<FormInstance>()
const submitting = ref(false)
const datasets = ref<any[]>([])
const datasetsLoading = ref(false)
const columns = ref<string[]>([])
const columnsLoading = ref(false)
const templateName = ref('')

const form = reactive({
  dataset_id: '',
  task: 'classification',
  target: '',
  n_clusters: 4,
  time_column: '',
  model_type: 'mlp',
  epochs: 20,
  batch_size: 32,
  lr: 0.001,
  require_gpu: false,
  device_backend: 'auto',
})
const advancedJson = ref('')
const advancedError = ref('')

const rules: FormRules = {
  dataset_id: [{ required: true, message: '请选择数据集', trigger: 'change' }],
  task: [{ required: true, message: '请选择任务类型', trigger: 'change' }],
  target: [{ required: true, message: '请选择目标列', trigger: 'change' }],
  model_type: [{ required: true, message: '请选择模型', trigger: 'change' }],
}

/** 选中的数据集对象（摘要展示名称） */
const selectedDataset = computed(() => datasets.value.find((ds) => ds.id === form.dataset_id))

/** 当前资源后端展示名 */
const backendLabel = computed(() => BACKEND_LABELS[form.device_backend] || form.device_backend)

// 各任务类型支持的模型（与后端 TrainRequest 校验保持一致）
const modelOptions = computed(() => {
  switch (form.task) {
    case 'classification':
      return [
        { value: 'mlp', label: 'MLP 神经网络' },
        { value: 'xgboost', label: 'XGBoost' },
        { value: 'logistic_regression', label: '逻辑回归' },
        { value: 'decision_tree', label: '决策树' },
        { value: 'random_forest', label: '随机森林' },
        { value: 'gradient_boosting', label: '梯度提升' },
        { value: 'svm', label: 'SVM' },
        { value: 'knn', label: 'KNN' },
      ]
    case 'regression':
      return [
        { value: 'mlp', label: 'MLP 神经网络' },
        { value: 'xgboost', label: 'XGBoost' },
        { value: 'linear_regression', label: '线性回归' },
        { value: 'decision_tree', label: '决策树' },
        { value: 'random_forest', label: '随机森林' },
        { value: 'gradient_boosting', label: '梯度提升' },
        { value: 'svm', label: 'SVR' },
        { value: 'knn', label: 'KNN' },
      ]
    default:
      return [{ value: 'mlp', label: 'MLP 神经网络' }]
  }
})

/** 切换任务类型：重置目标列/时间列，模型回落到该类型默认值 */
const selectTask = (task: string) => {
  if (form.task === task) return
  form.task = task
  if (!modelOptions.value.some((m) => m.value === form.model_type)) {
    form.model_type = 'mlp'
  }
}

const fetchDatasets = async () => {
  datasetsLoading.value = true
  try {
    const res: any = await api.get('/datasets', { limit: 200 })
    datasets.value = res.datasets || []
  } catch (err: any) {
    appStore.error('数据集加载失败', err?.message)
  } finally {
    datasetsLoading.value = false
  }
}

// 选择数据集后拉取列信息，供目标列/时间列下拉
const onDatasetChange = async (datasetId: string) => {
  columns.value = []
  form.target = ''
  form.time_column = ''
  if (!datasetId) return
  columnsLoading.value = true
  try {
    const res: any = await api.get(`/datasets/${datasetId}`)
    columns.value = res.columns || []
  } catch (err: any) {
    appStore.error('列信息加载失败', err?.message)
  } finally {
    columnsLoading.value = false
  }
}

// 支持从模板库跳转预填：/training/create?template={id}
const loadTemplate = async () => {
  const templateId = String(route.query.template || '')
  if (!templateId) return
  try {
    const tpl: any = await api.get(`/templates/${templateId}`)
    templateName.value = tpl.name
    const taskTypeMap: Record<string, string> = {
      classification: 'classification', regression: 'regression',
      clustering: 'clustering', time_series: 'time_series_forecasting',
      time_series_forecasting: 'time_series_forecasting',
    }
    if (taskTypeMap[tpl.task_type]) form.task = taskTypeMap[tpl.task_type]
    // 用模板参数的默认值预填高级参数
    const defaults: Record<string, any> = {}
    for (const p of tpl.parameters || []) {
      if (p.default !== null && p.default !== undefined) defaults[p.name] = p.default
    }
    if (Object.keys(defaults).length > 0) {
      advancedJson.value = JSON.stringify(defaults, null, 2)
    }
  } catch (err: any) {
    appStore.warning('模板加载失败', err?.message)
  }
}

const clearTemplate = () => {
  templateName.value = ''
  router.replace('/training/create')
}

// 统一提交到 /api/training/jobs（此前误拼为 /api/api/training/jobs 导致 404）
const submit = async () => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  let advanced: Record<string, any> = {}
  if (advancedJson.value.trim()) {
    try {
      advanced = JSON.parse(advancedJson.value)
    } catch {
      advancedError.value = '高级参数不是合法 JSON 对象'
      return
    }
  }
  advancedError.value = ''
  const payload: Record<string, any> = {
    dataset_id: form.dataset_id,
    task: form.task,
    model_type: form.model_type,
    epochs: form.epochs,
    batch_size: form.batch_size,
    lr: form.lr,
    require_gpu: form.require_gpu,
    device_backend: form.device_backend,
    ...advanced,
  }
  if (form.task === 'clustering') {
    payload.n_clusters = form.n_clusters
  } else {
    payload.target = form.target
  }
  if (form.task === 'time_series_forecasting') {
    payload.time_column = form.time_column
  }

  submitting.value = true
  try {
    const res: any = await api.post('/training/jobs', payload)
    appStore.success('任务创建成功', `任务 ID：${res.job_id}`)
    router.push('/training/jobs')
  } catch (err: any) {
    appStore.error('创建失败', err?.message)
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  fetchDatasets()
  loadTemplate()
})
</script>

<style scoped>
.create-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  align-items: start;
}

@media (max-width: 1200px) {
  .create-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.create-main,
.create-side {
  min-width: 0;
}

/* 任务类型选择卡片 */
.task-options {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  width: 100%;
}

@media (max-width: 992px) {
  .task-options {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.task-option {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 12px;
  border: 1px solid var(--mtp-ink-200);
  border-radius: var(--mtp-radius-md);
  background: var(--mtp-bg-card);
  cursor: pointer;
  text-align: left;
  transition: all 0.15s;
}

.task-option:hover {
  border-color: var(--mtp-brand-500);
}

.task-option.is-active {
  border-color: var(--mtp-brand-600);
  background: var(--mtp-brand-50);
  box-shadow: 0 0 0 2px rgba(48, 86, 211, 0.15);
}

.task-option__name {
  font-weight: 600;
  color: var(--mtp-ink-900);
  font-size: 14px;
}

.task-option__desc {
  font-size: 12px;
  color: var(--mtp-ink-400);
}

.resource-row {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
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

/* 配置摘要 */
.summary-list {
  display: flex;
  flex-direction: column;
}

.summary-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 0;
  border-bottom: 1px dashed var(--mtp-ink-100);
  font-size: 13px;
}

.summary-row:last-child {
  border-bottom: none;
}

.summary-label {
  color: var(--mtp-ink-400);
  flex-shrink: 0;
}

.summary-value {
  color: var(--mtp-ink-900);
  font-weight: 500;
  text-align: right;
  word-break: break-all;
}

.summary-value.is-empty {
  color: var(--mtp-ink-300);
  font-weight: 400;
}

.help-list {
  padding-left: 18px;
  color: var(--mtp-ink-500);
  font-size: 13px;
  line-height: 2;
}
</style>
