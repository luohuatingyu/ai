<template>
  <PageShell :title="isEdit ? '编辑模板' : '创建模板'" module="脚本模板库" :loading="loadingDetail">
    <template #actions>
      <el-button @click="router.push('/templates/library')">
        <el-icon class="gap-8"><Back /></el-icon>
        返回模板库
      </el-button>
    </template>

    <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
      <!-- 基本信息 -->
      <SectionCard title="基本信息">
        <el-row :gutter="16">
          <el-col :xs="24" :sm="12">
            <el-form-item label="模板名称" prop="name">
              <el-input v-model="form.name" placeholder="如：XGBoost 分类模板" maxlength="80" show-word-limit />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="分类" prop="category">
              <el-select v-model="form.category" placeholder="选择或输入分类" style="width: 100%" filterable allow-create default-first-option>
                <el-option v-for="c in categoryOptions" :key="c" :label="c" :value="c" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="任务类型" prop="task_type">
              <el-select v-model="form.task_type" style="width: 100%">
                <el-option label="分类" value="classification" />
                <el-option label="回归" value="regression" />
                <el-option label="聚类" value="clustering" />
                <el-option label="时间序列" value="time_series" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="描述">
              <el-input v-model="form.description" placeholder="模板用途说明" maxlength="200" show-word-limit />
            </el-form-item>
          </el-col>
        </el-row>
      </SectionCard>

      <!-- 脚本内容 -->
      <SectionCard title="脚本内容" description="使用 {{参数}} 占位符引用参数，例如 {{epochs}}">
        <el-form-item prop="content">
          <el-input
            v-model="form.content"
            type="textarea"
            :rows="14"
            placeholder="训练脚本内容..."
            class="code-textarea mono"
          />
        </el-form-item>
      </SectionCard>

      <!-- 参数定义 -->
      <SectionCard title="参数定义" description="JSON 数组，每项含 name/type/label，可选 required/default/options/description">
        <el-form-item>
          <el-input
            v-model="parametersJson"
            type="textarea"
            :rows="6"
            class="mono"
            placeholder='[{"name": "epochs", "type": "number", "label": "训练轮数", "required": true, "default": 20}]'
          />
        </el-form-item>
        <div v-if="paramsError" class="params-error">
          <el-icon><WarningFilled /></el-icon>
          {{ paramsError }}
        </div>
      </SectionCard>

      <div class="submit-bar">
        <el-button type="primary" size="large" :loading="submitting" @click="submit">
          {{ isEdit ? '保存修改' : '创建模板' }}
        </el-button>
        <el-button size="large" @click="router.back()">取消</el-button>
      </div>
    </el-form>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * 创建 / 编辑模板：基本信息、脚本内容与参数定义分区组织。
 * - 编辑模式通过 ?id= 加载模板详情预填；
 * - 参数定义 JSON 前端校验（数组 + 必含 name/type/label），非法时阻止提交并内联提示。
 */
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { Back, WarningFilled } from '@element-plus/icons-vue'

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
const paramsError = ref('')

const isEdit = computed(() => Boolean(route.query.id))

const rules: FormRules = {
  name: [{ required: true, message: '请输入模板名称', trigger: 'blur' }],
  category: [{ required: true, message: '请选择分类', trigger: 'change' }],
  task_type: [{ required: true, message: '请选择任务类型', trigger: 'change' }],
  content: [{ required: true, message: '请输入脚本内容', trigger: 'blur' }],
}

/** 解析参数定义 JSON：非法或缺少必填字段返回 null */
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
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  const parameters = parseParameters()
  if (parameters === null) {
    paramsError.value = '参数定义必须是 JSON 数组，且每项包含 name、type、label 字段'
    return
  }
  paramsError.value = ''
  submitting.value = true
  try {
    const payload = { ...form, parameters, dataset_requirements: {} }
    if (isEdit.value) {
      await api.put(`/templates/${route.query.id}`, payload)
      appStore.success('模板已更新', form.name)
    } else {
      await api.post('/templates', payload)
      appStore.success('模板创建成功', form.name)
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
.code-textarea :deep(textarea) {
  font-family: var(--mtp-font-mono);
  font-size: 12px;
  line-height: 1.6;
}

.params-error {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  padding: 8px 12px;
  border-radius: var(--mtp-radius-sm);
  background: var(--el-color-danger-light-9);
  color: var(--mtp-danger);
  font-size: 13px;
}

.submit-bar {
  display: flex;
  gap: 10px;
  padding: 4px 0 20px;
}
</style>
