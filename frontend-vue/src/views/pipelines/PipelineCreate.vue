<template>
  <div class="page-view">
    <div class="page-header">
      <h1 class="page-title">{{ isEdit ? '编辑 Pipeline' : '创建 Pipeline' }}</h1>
      <el-button @click="router.push('/pipelines/list')">返回列表</el-button>
    </div>
    <el-row :gutter="20">
      <el-col :xs="24" :lg="14">
        <el-card v-loading="loadingDetail">
          <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
            <el-form-item label="名称" prop="name">
              <el-input v-model="form.name" placeholder="如：销量预测全流程" maxlength="120" />
            </el-form-item>
            <el-form-item label="描述">
              <el-input v-model="form.description" placeholder="Pipeline 用途说明" maxlength="300" />
            </el-form-item>
            <el-form-item label="定时调度">
              <el-input v-model="form.schedule" placeholder="cron 表达式，留空表示手动触发（如 0 2 * * *）" />
            </el-form-item>
          </el-form>

          <div class="steps-header">
            <span class="steps-title">步骤编排</span>
            <el-button size="small" type="primary" @click="addStep">
              <el-icon><Plus /></el-icon>
              添加步骤
            </el-button>
          </div>

          <el-empty v-if="steps.length === 0" description="暂无步骤，点击「添加步骤」开始编排" :image-size="70" />
          <div v-for="(step, index) in steps" :key="index" class="step-card">
            <div class="step-head">
              <span class="step-index">{{ index + 1 }}</span>
              <el-input v-model="step.name" placeholder="步骤名称" size="small" style="width: 180px" />
              <el-select v-model="step.type" placeholder="步骤类型" size="small" style="width: 160px">
                <el-option v-for="t in STEP_TYPES" :key="t.value" :label="t.label" :value="t.value" />
              </el-select>
              <el-button size="small" type="danger" plain @click="removeStep(index)">移除</el-button>
            </div>
            <div class="step-body">
              <div class="step-row">
                <span class="step-label">依赖步骤</span>
                <el-select v-model="step.depends_on" multiple collapse-tags size="small" placeholder="无" style="flex: 1">
                  <el-option
                    v-for="(s, i) in steps" :key="i"
                    :label="`${i + 1}. ${s.name || '未命名'}`"
                    :value="s.id" :disabled="i === index"
                  />
                </el-select>
              </div>
              <div class="step-row">
                <span class="step-label">配置 JSON</span>
                <el-input v-model="step.config_json" type="textarea" :rows="3" size="small" placeholder='{"dataset_id": "..."}' class="config-input" />
              </div>
            </div>
          </div>

          <div class="submit-bar">
            <el-button type="primary" :loading="submitting" :disabled="steps.length === 0" @click="submit">
              {{ isEdit ? '保存修改' : '创建 Pipeline' }}
            </el-button>
            <el-button @click="router.back()">取消</el-button>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="10">
        <el-card>
          <template #header><span>说明</span></template>
          <ul class="help-list">
            <li>步骤按依赖关系组成 DAG，无依赖关系的步骤会并行执行。</li>
            <li>「训练」步骤的配置通常包含 dataset_id、task、target、epochs 等字段，与创建训练任务的请求体一致。</li>
            <li>「数据集拉取」「模型部署」等步骤的配置字段见各步骤类型说明。</li>
            <li>创建后 Pipeline 默认为草稿状态，需在详情页激活后才能运行。</li>
          </ul>
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

const router = useRouter()
const route = useRoute()
const appStore = useAppStore()

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

interface StepDraft {
  id: string
  name: string
  type: string
  config_json: string
  depends_on: string[]
}

const formRef = ref<FormInstance>()
const submitting = ref(false)
const loadingDetail = ref(false)
const form = reactive({ name: '', description: '', schedule: '' })
const steps = ref<StepDraft[]>([])

const isEdit = computed(() => Boolean(route.query.id))

const rules: FormRules = {
  name: [{ required: true, message: '请输入 Pipeline 名称', trigger: 'blur' }],
}

const addStep = () => {
  steps.value.push({
    id: `step-${Date.now().toString(36)}-${steps.value.length}`,
    name: '',
    type: 'training',
    config_json: '{}',
    depends_on: [],
  })
}

const removeStep = (index: number) => {
  const removedId = steps.value[index].id
  steps.value.splice(index, 1)
  // 清理其他步骤对被移除步骤的依赖
  for (const step of steps.value) {
    step.depends_on = step.depends_on.filter((d) => d !== removedId)
  }
}

// 编辑模式：拉取 Pipeline 详情预填
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
    }))
  } catch (err: any) {
    appStore.error('Pipeline 加载失败', err?.message)
  } finally {
    loadingDetail.value = false
  }
}

const buildStepsPayload = (): any[] | null => {
  const result: any[] = []
  for (const step of steps.value) {
    let config: Record<string, any> = {}
    if (step.config_json.trim()) {
      try {
        config = JSON.parse(step.config_json)
      } catch {
        return null
      }
    }
    result.push({
      id: step.id,
      name: step.name || step.id,
      type: step.type,
      config,
      depends_on: [...step.depends_on],
      resource_hint: {},
      retry_policy: {},
      timeout: 3600,
      continue_on_failure: false,
    })
  }
  return result
}

const submit = async () => {
  await formRef.value?.validate().catch(() => Promise.reject())
  const stepsPayload = buildStepsPayload()
  if (stepsPayload === null) {
    appStore.error('步骤配置不是合法 JSON')
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
      appStore.success('Pipeline 已更新')
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
.page-view { padding: 0; }
.steps-header {
  display: flex; align-items: center; justify-content: space-between;
  margin: 8px 0 12px; padding-top: 16px; border-top: 1px solid #ebeef5;
}
.steps-title { font-weight: 500; color: #303133; }
.step-card {
  border: 1px solid #ebeef5; border-radius: 8px; padding: 12px;
  margin-bottom: 12px; background: #fafbfc;
}
.step-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.step-index {
  width: 22px; height: 22px; border-radius: 50%; background: #409eff; color: #fff;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 12px; flex-shrink: 0;
}
.step-body { display: flex; flex-direction: column; gap: 8px; }
.step-row { display: flex; align-items: flex-start; gap: 10px; }
.step-label { width: 70px; font-size: 12px; color: #909399; padding-top: 6px; flex-shrink: 0; }
.config-input { flex: 1; }
.config-input :deep(textarea) { font-family: 'Monaco', 'Menlo', monospace; font-size: 12px; }
.submit-bar { display: flex; gap: 12px; margin-top: 16px; }
.help-list { padding-left: 18px; color: #909399; font-size: 13px; line-height: 2; }
</style>
