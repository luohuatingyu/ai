<template>
  <div class="page-view">
    <div class="page-header">
      <div class="header-left">
        <el-button @click="goBack">
          <el-icon><ArrowLeft /></el-icon>
          返回
        </el-button>
        <h1 class="page-title">模板详情</h1>
      </div>
      <div class="header-actions">
        <el-button v-if="detail && !detail.is_builtin" @click="router.push(`/templates/create?id=${detail.id}`)">编辑</el-button>
        <el-button type="primary" @click="router.push(`/training/create?template=${templateId}`)">使用模板</el-button>
      </div>
    </div>

    <div v-if="error" class="empty-state">
      <el-icon class="empty-state-icon" :size="48"><WarningFilled /></el-icon>
      <p>{{ error }}</p>
      <el-button type="primary" @click="fetchDetail">重试</el-button>
    </div>

    <template v-else>
      <el-card class="mb-20" v-loading="loading">
        <template #header>
          <div class="card-header">
            <span class="template-name">{{ detail?.name || '加载中' }}</span>
            <div>
              <el-tag v-if="detail?.is_builtin" effect="dark" size="small">内置</el-tag>
              <el-tag size="small" class="ml-8">{{ detail?.category }}</el-tag>
              <el-tag size="small" type="info" class="ml-8">{{ taskText(detail?.task_type) }}</el-tag>
            </div>
          </div>
        </template>
        <el-descriptions :column="2" border v-if="detail">
          <el-descriptions-item label="描述" :span="2">{{ detail.description || '-' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDate(detail.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ formatDate(detail.updated_at) }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-card class="mb-20" v-if="detail && detail.parameters?.length > 0">
        <template #header><span>模板参数</span></template>
        <el-table :data="detail.parameters" border size="small">
          <el-table-column prop="label" label="参数名" min-width="120" />
          <el-table-column prop="name" label="标识" min-width="120" />
          <el-table-column prop="type" label="类型" width="90" />
          <el-table-column label="必填" width="70">
            <template #default="scope">{{ scope.row.required ? '是' : '否' }}</template>
          </el-table-column>
          <el-table-column label="默认值" width="120">
            <template #default="scope">{{ formatDefault(scope.row.default) }}</template>
          </el-table-column>
          <el-table-column prop="description" label="说明" min-width="160" show-overflow-tooltip>
            <template #default="scope">{{ scope.row.description || '-' }}</template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card v-if="detail">
        <template #header><span>脚本内容</span></template>
        <pre class="code-content">{{ detail.content }}</pre>
      </el-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api'
import { ArrowLeft, WarningFilled } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const detail = ref<any>(null)
const loading = ref(false)
const error = ref('')

const templateId = String(route.params.id || '')

// 后端实际路径为 /api/templates/{id}（旧版模板字符串未替换导致 404）
const fetchDetail = async () => {
  loading.value = true
  error.value = ''
  try {
    detail.value = await api.get(`/templates/${templateId}`)
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

const TASK_TEXT: Record<string, string> = {
  classification: '分类', regression: '回归', clustering: '聚类', time_series: '时间序列',
}
const taskText = (task?: string) => TASK_TEXT[task || ''] || task || '-'
const formatDefault = (value: any) => (value === null || value === undefined || value === '' ? '-' : String(value))
const formatDate = (ts?: number) => {
  if (!ts) return '-'
  return new Date(ts * 1000).toLocaleString('zh-CN')
}

const goBack = () => window.history.length > 1 ? router.back() : router.replace('/templates/library')

onMounted(fetchDetail)
</script>

<style scoped>
.page-view { padding: 0; }
.header-left { display: flex; align-items: center; gap: 12px; }
.header-actions { display: flex; gap: 12px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.template-name { font-size: 16px; font-weight: 500; }
.ml-8 { margin-left: 8px; }
.mb-20 { margin-bottom: 20px; }
.code-content {
  margin: 0; padding: 16px; background: #1e1e1e; color: #d4d4d4; border-radius: 6px;
  font-family: 'Monaco', 'Menlo', monospace; font-size: 12px; line-height: 1.6;
  white-space: pre-wrap; word-break: break-word; max-height: 480px; overflow: auto;
}
</style>
