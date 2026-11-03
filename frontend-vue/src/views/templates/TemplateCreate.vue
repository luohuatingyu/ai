<template>
  <div class="page-view">
    <div class="page-header">
      <h1 class="page-title">{{ isEdit ? '编辑模板' : '创建模板' }}</h1>
      <el-button @click="router.push('/templates/library')">返回模板库</el-button>
    </div>
    <el-card v-loading="loadingDetail">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="模板名称" prop="name">
              <el-input v-model="form.name" placeholder="如：XGBoost 分类模板" maxlength="80" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="分类" prop="category">
              <el-select v-model="form.category" placeholder="选择或输入分类" style="width: 100%" filterable allow-create default-first-option>
                <el-option v-for="c in categoryOptions" :key="c" :label="c" :value="c" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="任务类型" prop="task_type">
              <el-select v-model="form.task_type" style="width: 100%">
                <el-option label="分类" value="classification" />
                <el-option label="回归" value="regression" />
                <el-option label="聚类" value="clustering" />
                <el-option label="时间序列" value="time_series" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="描述">
              <el-input v-model="form.description" placeholder="模板用途说明" maxlength="200" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="脚本内容" prop="content">
          <el-input
            v-model="form.content" type="textarea" :rows="14"
            placeholder="训练脚本，使用 {{param}} 占位符引用参数，例如 {{epochs}}" class="code-textarea"
          />
        </el-form-item>
        <el-form-item label="参数定义">
          <el-input
            v-model="parametersJson" type="textarea" :rows="6"
            placeholder='[{"name": "epochs", "type": "number", "label": "训练轮数", "required": true, "default": 20}]'
          />
          <div class="tip-text">JSON 数组，每项含 name/type/label，可选 required/default/options/description。</div>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="submitting" @click="submit">{{ isEdit ? '保存修改' : '创建模板' }}</el-button>
          <el-button @click="router.back()">取消</el-button>
        </el-form-item>
      </el-form>
    </el-card>
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
const loadingDetail = ref(false)
const categoryOptions = ref<string[]>(['数据预处理', '模型训练', '模型评估', '自定义脚本'])

const form = reactive({
  name: '', category: '', task_type: 'classification', description: '', content: '',
})
const parametersJson = ref('[]')

const isEdit = computed(() => Boolean(route.query.id))

const rules: FormRules = {
  name: [{ required: true, message: '请输入模板名称', trigger: 'blur' }],
  category: [{ required: true, message: '请选择分类', trigger: 'change' }],
  task_type: [{ required: true, message: '请选择任务类型', trigger: 'change' }],
  content: [{ required: true, message: '请输入脚本内容', trigger: 'blur' }],
}

const parseParameters = (): any[] | null => {
  try {
    const parsed = JSON.parse(parametersJson.value || '[]')
    if (!Array.isArray(parsed)) return null
    for (const item of parsed) {
      if (!item.name || !item.type || !item.label) return null
    }
    return parsed
  } catch {
    return null
  }
}

// 编辑模式：拉取模板详情预填
const loadTemplate = async () => {
  const templateId = String(route.query.id || '')
  if (!templateId) return
  loadingDetail.value = true
  try {
    const tpl: any = await api.get(`/templates/${templateId}`)
    form.name = tpl.name
    form.category = tpl.category
    form.task_type = tpl.task_type
    form.description = tpl.description || ''
    form.content = tpl.content || ''
    parametersJson.value = JSON.stringify(
      (tpl.parameters || []).map((p: any) => ({
        name: p.name, type: p.type, label: p.label,
        required: p.required, default: p.default,
        options: p.options, description: p.description,
      })),
      null, 2
    )
  } catch (err: any) {
    appStore.error('模板加载失败', err?.message)
  } finally {
    loadingDetail.value = false
  }
}

const submit = async () => {
  await formRef.value?.validate().catch(() => Promise.reject())
  const parameters = parseParameters()
  if (parameters === null) {
    appStore.error('参数定义不是合法 JSON 或缺少 name/type/label 字段')
    return
  }
  submitting.value = true
  try {
    const payload = { ...form, parameters, dataset_requirements: {} }
    if (isEdit.value) {
      await api.put(`/templates/${route.query.id}`, payload)
      appStore.success('模板已更新')
    } else {
      await api.post('/templates', payload)
      appStore.success('模板创建成功')
    }
    router.push('/templates/library')
  } catch (err: any) {
    appStore.error(isEdit.value ? '保存失败' : '创建失败', err?.message)
  } finally {
    submitting.value = false
  }
}

onMounted(loadTemplate)
</script>

<style scoped>
.page-view { padding: 0; }
.tip-text { color: #909399; font-size: 12px; line-height: 1.6; margin-top: 4px; }
.code-textarea :deep(textarea) { font-family: 'Monaco', 'Menlo', monospace; font-size: 12px; }
</style>
