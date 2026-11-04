<template>
  <PageShell title="导入数据集" module="数据集管理" description="上传本地文件或导入内置示例，导入完成后可在列表中查看与预处理">
    <template #actions>
      <el-button @click="router.push('/datasets/list')">
        <el-icon class="gap-8"><Back /></el-icon>
        返回数据集列表
      </el-button>
    </template>

    <div class="import-grid">
      <!-- 本地文件上传 -->
      <SectionCard title="本地文件上传" description="支持单个 CSV 或含多个 CSV 的压缩包（最大 500MB）">
        <el-upload
          drag
          action="#"
          :auto-upload="false"
          :limit="1"
          :on-change="handleFileChange"
          :on-remove="() => (uploadForm.file = null)"
          :show-file-list="true"
          accept=".csv,.zip,.tar,.gz"
          class="upload-zone"
        >
          <el-icon class="upload-icon"><UploadFilled /></el-icon>
          <div class="el-upload__text">拖拽文件到此处，或 <em>点击选择</em></div>
          <template #tip>
            <div class="text-xs text-secondary mt-8">支持 .csv / .zip / .tar / .gz</div>
          </template>
        </el-upload>

        <el-form label-position="top" class="mt-16">
          <el-form-item label="描述">
            <el-input v-model="uploadForm.description" type="textarea" :rows="2" placeholder="数据集描述（可选）" maxlength="300" show-word-limit />
          </el-form-item>
          <el-form-item label="标签">
            <el-select
              v-model="uploadForm.tags"
              multiple
              filterable
              allow-create
              default-first-option
              placeholder="输入标签后回车创建"
              style="width: 100%"
            />
          </el-form-item>
          <el-button
            type="primary"
            size="large"
            :loading="uploading"
            :disabled="!uploadForm.file"
            @click="submitUpload"
          >
            <el-icon class="gap-8"><Upload /></el-icon>
            {{ uploading ? '正在导入…' : '上传并导入' }}
          </el-button>
          <div v-if="uploadError" class="submit-error">
            <el-icon><WarningFilled /></el-icon>
            {{ uploadError }}
          </div>
        </el-form>
      </SectionCard>

      <!-- 示例数据 -->
      <SectionCard title="示例数据" description="一键导入平台内置数据集，快速体验完整流程" flush>
        <template #extra>
          <el-button link :loading="samplesLoading" @click="fetchSamples">刷新</el-button>
        </template>
        <div v-loading="samplesLoading" class="sample-list">
          <template v-if="samples.length > 0">
            <div v-for="sample in samples" :key="sample.name" class="sample-row">
              <div class="sample-info">
                <span class="sample-name">{{ sample.name }}</span>
                <span v-if="sample.description" class="sample-desc">{{ sample.description }}</span>
              </div>
              <el-button
                size="small"
                type="primary"
                plain
                :loading="importingSample === sample.name"
                @click="importSample(sample.name)"
              >
                导入
              </el-button>
            </div>
          </template>
          <EmptyState
            v-else-if="samplesError"
            type="error"
            title="示例数据加载失败"
            :description="samplesError"
            retryable
            @retry="fetchSamples"
          />
          <EmptyState v-else-if="!samplesLoading" title="暂无示例数据" />
        </div>
      </SectionCard>
    </div>

    <!-- 导入历史 -->
    <SectionCard title="导入历史" description="最近的导入记录与结果" flush>
      <template #extra>
        <el-button link :loading="historyLoading" @click="fetchHistory">刷新</el-button>
      </template>
      <el-table
        v-if="history.length > 0"
        v-loading="historyLoading"
        :data="history"
        class="table-card"
        :header-cell-style="{ background: 'var(--mtp-bg-subtle)' }"
      >
        <el-table-column prop="dataset_id" label="数据集 ID" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="mono text-sm">{{ row.dataset_id }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="filename" label="文件名" min-width="160" show-overflow-tooltip />
        <el-table-column label="格式" width="90">
          <template #default="{ row }">{{ row.format || '-' }}</template>
        </el-table-column>
        <el-table-column label="行数" width="110">
          <template #default="{ row }">{{ formatInteger(row.rows) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'success' ? 'success' : 'danger'" disable-transitions>
              {{ row.status === 'success' ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ formatFullDateTime(row.imported_at || row.timestamp) }}</template>
        </el-table-column>
      </el-table>
      <EmptyState
        v-else-if="historyError"
        type="error"
        title="导入历史加载失败"
        :description="historyError"
        retryable
        @retry="fetchHistory"
      />
      <EmptyState v-else-if="!historyLoading" title="暂无导入记录" description="完成第一次导入后，记录将显示在这里" />
    </SectionCard>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * 导入数据集：本地文件上传与示例导入两个来源 + 导入历史。
 * - 提交按钮在无文件时禁用、上传中 loading 防重复提交；
 * - 失败时保留表单内容并展示可读错误，便于修正后重试；
 * - 示例与历史区块各自独立加载与重试，互不影响。
 */
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { formatInteger, formatFullDateTime } from '@/utils/format'
import { UploadFilled, Upload, Back, WarningFilled } from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()

// ---- 本地上传 ----
const uploadForm = reactive<{ file: File | null; description: string; tags: string[] }>({
  file: null,
  description: '',
  tags: [],
})
const uploading = ref(false)
const uploadError = ref('')

// ---- 示例数据 ----
const samples = ref<any[]>([])
const samplesLoading = ref(false)
const samplesError = ref('')
const importingSample = ref('')

// ---- 导入历史 ----
const history = ref<any[]>([])
const historyLoading = ref(false)
const historyError = ref('')

/** el-upload 选中文件回调（file.raw 为原生 File 对象） */
const handleFileChange = (file: any) => {
  uploadForm.file = file.raw
  uploadError.value = ''
}

/** 提交上传：描述与标签随文件一起提交 */
const submitUpload = async () => {
  if (!uploadForm.file || uploading.value) return
  uploading.value = true
  uploadError.value = ''
  try {
    const res: any = await api.upload('/datasets/import', uploadForm.file, undefined, {
      description: uploadForm.description,
      tags: uploadForm.tags,
    })
    const items = res.datasets || (res.id ? [res] : [])
    appStore.success('导入成功', `共导入 ${items.length} 个数据集`)
    uploadForm.file = null
    uploadForm.description = ''
    uploadForm.tags = []
    fetchHistory()
  } catch (err: any) {
    // 失败保留表单内容，展示错误供修正后重试
    uploadError.value = err?.message || '网络错误，请稍后重试'
    appStore.error('导入失败', err?.message)
  } finally {
    uploading.value = false
  }
}

/** 拉取内置示例列表 */
const fetchSamples = async () => {
  samplesLoading.value = true
  samplesError.value = ''
  try {
    const res: any = await api.get('/samples')
    samples.value = res || []
  } catch (err: any) {
    samplesError.value = err?.message || '网络错误'
    appStore.warning('示例数据加载失败', err?.message)
  } finally {
    samplesLoading.value = false
  }
}

/** 导入指定示例 */
const importSample = async (filename: string) => {
  importingSample.value = filename
  try {
    const res: any = await api.post(`/datasets/sample/${filename}`)
    appStore.success('示例导入成功', `数据集 ID：${res.id || filename}`)
    fetchHistory()
  } catch (err: any) {
    appStore.error('示例导入失败', err?.message)
  } finally {
    importingSample.value = ''
  }
}

/** 拉取导入历史（最近 50 条） */
const fetchHistory = async () => {
  historyLoading.value = true
  historyError.value = ''
  try {
    const res: any = await api.get('/datasets/import-history', { limit: 50 })
    history.value = res.history || []
  } catch (err: any) {
    historyError.value = err?.message || '网络错误'
    appStore.warning('导入历史加载失败', err?.message)
  } finally {
    historyLoading.value = false
  }
}

onMounted(() => {
  fetchSamples()
  fetchHistory()
})
</script>

<style scoped>
.import-grid {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  gap: 16px;
  align-items: start;
  margin-bottom: 16px;
}

@media (max-width: 992px) {
  .import-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.upload-zone {
  width: 100%;
}

.upload-zone :deep(.el-upload-dragger) {
  border-color: var(--mtp-ink-200);
  border-radius: var(--mtp-radius-md);
  transition: border-color 0.2s;
}

.upload-zone :deep(.el-upload-dragger:hover) {
  border-color: var(--mtp-brand-500);
}

.upload-icon {
  font-size: 40px;
  color: var(--mtp-ink-300);
  margin-bottom: 8px;
}

.submit-error {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
  padding: 8px 12px;
  border-radius: var(--mtp-radius-sm);
  background: var(--el-color-danger-light-9);
  color: var(--mtp-danger);
  font-size: 13px;
}

.sample-list {
  min-height: 120px;
}

.sample-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 18px;
  border-bottom: 1px solid var(--mtp-ink-100);
}

.sample-row:last-of-type {
  border-bottom: none;
}

.sample-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.sample-name {
  font-size: 13.5px;
  color: var(--mtp-ink-900);
  font-weight: 500;
}

.sample-desc {
  font-size: 12px;
  color: var(--mtp-ink-400);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
