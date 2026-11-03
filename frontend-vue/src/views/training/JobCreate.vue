<template>
  <div class="page-view">
    <div class="page-header">
      <h1 class="page-title">创建训练任务</h1>
      <el-tag v-if="templateName" closable @close="clearTemplate">模板：{{ templateName }}</el-tag>
    </div>
    <el-row :gutter="20">
      <el-col :xs="24" :lg="16">
        <el-card>
          <el-form ref="formRef" :model="form" :rules="rules" label-width="120px">
            <el-form-item label="数据集" prop="dataset_id">
              <el-select v-model="form.dataset_id" placeholder="选择数据集" style="width: 100%" :loading="datasetsLoading" @change="onDatasetChange">
                <el-option v-for="ds in datasets" :key="ds.id" :label="`${ds.name} (${ds.rows} 行)`" :value="ds.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="任务类型" prop="task">
              <el-select v-model="form.task" style="width: 100%" @change="onTaskChange">
                <el-option label="分类" value="classification" />
                <el-option label="回归" value="regression" />
                <el-option label="聚类" value="clustering" />
                <el-option label="时间序列预测" value="time_series_forecasting" />
              </el-select>
            </el-form-item>
            <el-form-item v-if="form.task !== 'clustering'" label="目标列" prop="target">
              <el-select v-model="form.target" placeholder="选择目标列" style="width: 100%" :loading="columnsLoading">
                <el-option v-for="col in columns" :key="col" :label="col" :value="col" />
              </el-select>
            </el-form-item>
            <el-form-item v-if="form.task === 'clustering'" label="聚类数">
              <el-input-number v-model="form.n_clusters" :min="2" :max="64" />
            </el-form-item>
            <el-form-item v-if="form.task === 'time_series_forecasting'" label="时间列" prop="time_column">
              <el-select v-model="form.time_column" placeholder="选择时间列" style="width: 100%" :loading="columnsLoading">
                <el-option v-for="col in columns" :key="col" :label="col" :value="col" />
              </el-select>
            </el-form-item>
            <el-form-item label="模型" prop="model_type">
              <el-select v-model="form.model_type" style="width: 100%">
                <el-option v-for="m in modelOptions" :key="m.value" :label="m.label" :value="m.value" />
              </el-select>
            </el-form-item>
            <el-row :gutter="12">
              <el-col :span="8">
                <el-form-item label="Epochs">
                  <el-input-number v-model="form.epochs" :min="1" :max="500" style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="批大小">
                  <el-input-number v-model="form.batch_size" :min="1" :max="4096" style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="学习率">
                  <el-input-number v-model="form.lr" :min="0.000001" :max="1" :step="0.001" style="width: 100%" />
                </el-form-item>
              </el-col>
            </el-row>
            <el-form-item label="计算资源">
              <el-checkbox v-model="form.require_gpu">仅 GPU</el-checkbox>
              <el-select v-model="form.device_backend" style="width: 160px; margin-left: 16px">
                <el-option label="自动后端" value="auto" />
                <el-option label="CUDA" value="cuda" />
                <el-option label="ROCm" value="rocm" />
                <el-option label="CPU" value="cpu" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="submitting" @click="submit">创建任务</el-button>
              <el-button @click="router.back()">取消</el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="8">
        <el-card>
          <template #header><span>高级参数（可选）</span></template>
          <el-input v-model="advancedJson" type="textarea" :rows="14" placeholder='以 JSON 追加请求体，如 {"seed": 42, "test_split": 0.2}' />
          <p class="tip-text">此处字段会合并进提交请求，用于模板或少量试验参数；格式非法时无法提交。</p>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()

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

const rules: FormRules = {
  dataset_id: [{ required: true, message: '请选择数据集', trigger: 'change' }],
  task: [{ required: true, message: '请选择任务类型', trigger: 'change' }],
  target: [{ required: true, message: '请选择目标列', trigger: 'change' }],
  model_type: [{ required: true, message: '请选择模型', trigger: 'change' }],
}

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

const fetchDatasets = async () => {
  datasetsLoading.value = true
  try {
    const res = await api.get('/datasets', { limit: 200 })
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
    const res = await api.get(`/datasets/${datasetId}`)
    columns.value = res.columns || []
  } catch (err: any) {
    appStore.error('列信息加载失败', err?.message)
  } finally {
    columnsLoading.value = false
  }
}

const onTaskChange = () => {
  // 任务类型切换后模型选项变化，回落到默认模型
  if (!modelOptions.value.some((m) => m.value === form.model_type)) {
    form.model_type = 'mlp'
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
  await formRef.value?.validate().catch(() => Promise.reject())
  let advanced: Record<string, any> = {}
  if (advancedJson.value.trim()) {
    try {
      advanced = JSON.parse(advancedJson.value)
    } catch {
      appStore.error('高级参数不是合法 JSON')
      return
    }
  }
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
    const res = await api.post('/training/jobs', payload)
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
.page-view { padding: 0; }
.tip-text { color: #909399; font-size: 12px; margin-top: 8px; }
</style>
