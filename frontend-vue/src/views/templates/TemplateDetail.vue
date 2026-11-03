<template>
  <PageShell :title="detail?.name || '模板详情'" module="脚本模板库" :loading="loading && !detail">
    <template #actions>
      <el-button v-if="detail && !detail.is_builtin" @click="router.push(`/templates/create?id=${detail.id}`)">
        <el-icon class="gap-8"><EditPen /></el-icon>
        编辑
      </el-button>
      <el-button type="primary" @click="router.push(`/training/create?template=${templateId}`)">
        <el-icon class="gap-8"><VideoPlay /></el-icon>
        使用模板
      </el-button>
    </template>

    <!-- 加载失败 -->
    <SectionCard v-if="error" flush>
      <EmptyState type="error" title="模板加载失败" :description="error" retryable @retry="fetchDetail" />
    </SectionCard>

    <template v-else>
      <!-- 概览 -->
      <SectionCard v-loading="loading && !detail" title="概览">
        <div v-if="detail" class="tpl-head">
          <div class="tpl-tags">
            <el-tag v-if="detail.is_builtin" effect="dark" size="small">内置</el-tag>
            <el-tag size="small" effect="plain">{{ detail.category }}</el-tag>
            <StatusTag :status="detail.task_type" :map="TASK_TYPE" />
          </div>
          <el-descriptions :column="2" border>
            <el-descriptions-item label="描述" :span="2">{{ detail.description || '-' }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatFullDateTime(detail.created_at) }}</el-descriptions-item>
            <el-descriptions-item label="更新时间">{{ formatFullDateTime(detail.updated_at) }}</el-descriptions-item>
          </el-descriptions>
        </div>
        <div v-else class="loading-placeholder" />
      </SectionCard>

      <!-- 参数定义 -->
      <SectionCard v-if="detail && detail.parameters?.length > 0" title="模板参数" description="使用模板时将按此定义生成配置表单" flush>
        <el-table :data="detail.parameters" class="table-card" :header-cell-style="{ background: 'var(--mtp-bg-subtle)' }">
          <el-table-column prop="label" label="参数名" min-width="120" />
          <el-table-column prop="name" label="标识" min-width="120">
            <template #default="{ row }"><span class="mono text-sm">{{ row.name }}</span></template>
          </el-table-column>
          <el-table-column prop="type" label="类型" width="90" />
          <el-table-column label="必填" width="70">
            <template #default="{ row }">{{ row.required ? '是' : '否' }}</template>
          </el-table-column>
          <el-table-column label="默认值" width="120">
            <template #default="{ row }">{{ formatDefault(row.default) }}</template>
          </el-table-column>
          <el-table-column prop="description" label="说明" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">{{ row.description || '-' }}</template>
          </el-table-column>
        </el-table>
      </SectionCard>

      <!-- 脚本内容 -->
      <SectionCard v-if="detail" title="脚本内容" description="使用 {{参数}} 占位符引用参数">
        <pre class="code-content">{{ detail.content }}</pre>
      </SectionCard>
    </template>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * 模板详情：概览（来源/分类/任务类型/时间）、参数定义表与脚本内容。
 * - 内置模板不可编辑（隐藏编辑按钮），使用模板按钮始终可用；
 * - 加载失败展示可重试错误态。
 */
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api'
import PageShell from '@/components/common/PageShell.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { TASK_TYPE } from '@/utils/status'
import { formatFullDateTime } from '@/utils/format'
import { EditPen, VideoPlay } from '@element-plus/icons-vue'

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

const formatDefault = (value: any) => (value === null || value === undefined || value === '' ? '-' : String(value))

onMounted(fetchDetail)
</script>

<style scoped>
.loading-placeholder {
  min-height: 160px;
}

.tpl-tags {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.code-content {
  margin: 0;
  padding: 16px;
  background: #1e1e2a;
  color: #d4d4d4;
  border-radius: var(--mtp-radius-md);
  font-family: var(--mtp-font-mono);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 480px;
  overflow: auto;
}
</style>
