<template>
  <div class="page-view">
    <div class="page-header">
      <h1 class="page-title">HuggingFace 数据集</h1>
      <el-button @click="router.push('/datasets/list')">返回数据集列表</el-button>
    </div>
    <el-row :gutter="20">
      <el-col :xs="24" :lg="14">
        <el-card>
          <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
            <el-form-item label="仓库 ID" prop="repo_id">
              <el-input v-model="form.repo_id" placeholder="如 imdb、squad、glue" />
              <div class="tip-text">仅支持公开仓库，格式为 owner/name 或单名数据集</div>
            </el-form-item>
            <el-form-item label="版本" prop="revision">
              <el-input v-model="form.revision" placeholder="main（默认）" />
            </el-form-item>
            <el-form-item label="子集">
              <el-input v-model="form.subset" placeholder="可选，如 en、zh" />
            </el-form-item>
            <el-form-item label="Split" prop="split">
              <el-select v-model="form.split" style="width: 100%">
                <el-option label="train" value="train" />
                <el-option label="test" value="test" />
                <el-option label="validation" value="validation" />
              </el-select>
            </el-form-item>
            <el-form-item label="存储模式">
              <el-radio-group v-model="form.storage_mode">
                <el-radio value="remote">引用（首次使用时拉取）</el-radio>
                <el-radio value="local">立即下载到本地</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="描述">
              <el-input v-model="form.description" type="textarea" :rows="2" placeholder="数据集描述（可选）" />
            </el-form-item>
            <el-form-item label="标签">
              <el-select
                v-model="form.tags" multiple filterable allow-create default-first-option
                placeholder="输入标签后回车创建" style="width: 100%"
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="submitting" @click="submit">导入数据集</el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="10">
        <el-card>
          <template #header><span>说明</span></template>
          <ul class="help-list">
            <li>「引用」模式仅保存仓库引用，训练或预览首次使用时自动拉取，不占用当前空间。</li>
            <li>「立即下载」会在导入时下载所选 split 到本地存储。</li>
            <li>导入完成后可在数据集列表中执行「下载到本地」补拉数据。</li>
            <li>平台仅访问 HuggingFace 公开仓库，无需配置访问令牌。</li>
          </ul>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'

const router = useRouter()
const appStore = useAppStore()

const formRef = ref<FormInstance>()
const submitting = ref(false)
const form = reactive({
  repo_id: '', revision: 'main', subset: '', split: 'train',
  storage_mode: 'remote', description: '', tags: [] as string[],
})

const rules: FormRules = {
  repo_id: [
    { required: true, message: '请输入 HuggingFace 仓库 ID', trigger: 'blur' },
    { pattern: /^[A-Za-z0-9][A-Za-z0-9._-]*(\/[A-Za-z0-9][A-Za-z0-9._-]*)?$/, message: '仓库 ID 格式无效', trigger: 'blur' },
  ],
  revision: [{ required: true, message: '请输入版本', trigger: 'blur' }],
  split: [{ required: true, message: '请选择 split', trigger: 'change' }],
}

const submit = async () => {
  await formRef.value?.validate().catch(() => Promise.reject())
  submitting.value = true
  try {
    const payload: Record<string, any> = {
      repo_id: form.repo_id,
      revision: form.revision,
      split: form.split,
      storage_mode: form.storage_mode,
      description: form.description,
      tags: form.tags,
    }
    if (form.subset) payload.subset = form.subset
    const res: any = await api.post('/datasets/hub', payload)
    appStore.success('HuggingFace 数据集导入成功', `数据集 ID：${res.id || ''}`)
    if (form.storage_mode === 'remote' && res.id) {
      // 引用模式导入后立即尝试物化，失败不影响导入结果
      try {
        await api.post(`/datasets/${res.id}/materialize`)
        appStore.success('数据已拉取到本地')
      } catch {
        appStore.info('数据将在首次使用时自动拉取')
      }
    }
    router.push('/datasets/list')
  } catch (err: any) {
    appStore.error('导入失败', err?.message)
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  // 保持默认值即可，无需额外初始化
})
</script>

<style scoped>
.page-view { padding: 0; }
.tip-text { color: #909399; font-size: 12px; line-height: 1.6; margin-top: 4px; }
.help-list { padding-left: 18px; color: #909399; font-size: 13px; line-height: 2; }
</style>
