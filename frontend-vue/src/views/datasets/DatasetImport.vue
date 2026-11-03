<template>
  <div class="page-view">
    <div class="page-header">
      <h1 class="page-title">导入数据集</h1>
      <el-button @click="router.push('/datasets/list')">返回数据集列表</el-button>
    </div>
    <el-row :gutter="20">
      <!-- 文件导入 -->
      <el-col :xs="24" :lg="14">
        <el-card class="mb-20">
          <template #header><span>上传文件（CSV / ZIP 压缩包）</span></template>
          <el-form label-width="100px">
            <el-form-item label="数据文件">
              <el-upload
                action="#"
                :auto-upload="false"
                :limit="1"
                :on-change="handleFileChange"
                :on-remove="() => (uploadForm.file = null)"
                :show-file-list="true"
                accept=".csv,.zip,.tar,.gz"
                drag
              >
                <el-icon class="upload-icon"><UploadFilled /></el-icon>
                <div class="el-upload__text">拖拽文件到此处，或 <em>点击选择</em></div>
                <template #tip>
                  <div class="tip-text">支持单个 CSV 或含多个 CSV 的压缩包，最大 500MB</div>
                </template>
              </el-upload>
            </el-form-item>
            <el-form-item label="描述">
              <el-input v-model="uploadForm.description" type="textarea" :rows="2" placeholder="数据集描述（可选）" />
            </el-form-item>
            <el-form-item label="标签">
              <el-select
                v-model="uploadForm.tags" multiple filterable allow-create default-first-option
                placeholder="输入标签后回车创建" style="width: 100%"
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="uploading" :disabled="!uploadForm.file" @click="submitUpload">
                上传并导入
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>

      <!-- 示例数据 -->
      <el-col :xs="24" :lg="10">
        <el-card class="mb-20">
          <template #header><span>示例数据</span></template>
          <div v-loading="samplesLoading">
            <div v-for="sample in samples" :key="sample.name" class="sample-row">
              <span class="sample-name">{{ sample.name }}</span>
              <el-button size="small" type="primary" plain :loading="importingSample === sample.name" @click="importSample(sample.name)">
                导入
              </el-button>
            </div>
            <el-empty v-if="!samplesLoading && samples.length === 0" description="暂无示例数据" :image-size="60" />
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 导入历史 -->
    <el-card>
      <template #header>
        <div class="card-header">
          <span>导入历史</span>
          <el-button size="small" @click="fetchHistory">刷新</el-button>
        </div>
      </template>
      <el-alert v-if="!historyLoading && history.length === 0" title="暂无导入记录" type="info" :closable="false" center />
      <el-table v-else :data="history" border size="small" v-loading="historyLoading">
        <el-table-column prop="dataset_id" label="数据集 ID" min-width="160" show-overflow-tooltip />
        <el-table-column prop="filename" label="文件名" min-width="140" show-overflow-tooltip />
        <el-table-column label="格式" width="90">
          <template #default="scope">{{ scope.row.format || '-' }}</template>
        </el-table-column>
        <el-table-column prop="rows" label="行数" width="100">
          <template #default="scope">{{ scope.row.rows ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="scope">
            <el-tag size="small" :type="scope.row.status === 'success' ? 'success' : 'danger'">
              {{ scope.row.status === 'success' ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="170">
          <template #default="scope">{{ formatHistoryTime(scope.row.imported_at || scope.row.timestamp) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import { UploadFilled } from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()

const uploadForm = reactive<{ file: File | null; description: string; tags: string[] }>({
  file: null, description: '', tags: [],
})
const uploading = ref(false)
const samples = ref<any[]>([])
const samplesLoading = ref(false)
const importingSample = ref('')
const history = ref<any[]>([])
const historyLoading = ref(false)

const handleFileChange = (file: any) => {
  uploadForm.file = file.raw
}

const submitUpload = async () => {
  if (!uploadForm.file) return
  uploading.value = true
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
    appStore.error('导入失败', err?.message)
  } finally {
    uploading.value = false
  }
}

const fetchSamples = async () => {
  samplesLoading.value = true
  try {
    const res: any = await api.get('/samples')
    samples.value = res || []
  } catch (err: any) {
    appStore.warning('示例数据加载失败', err?.message)
  } finally {
    samplesLoading.value = false
  }
}

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

const fetchHistory = async () => {
  historyLoading.value = true
  try {
    const res: any = await api.get('/datasets/import-history', { limit: 50 })
    history.value = res.history || []
  } catch (err: any) {
    appStore.warning('导入历史加载失败', err?.message)
  } finally {
    historyLoading.value = false
  }
}

const formatHistoryTime = (ts?: number) => {
  if (!ts) return '-'
  // 兼容秒级与毫秒级时间戳
  return new Date(ts > 1e12 ? ts : ts * 1000).toLocaleString('zh-CN')
}

onMounted(() => {
  fetchSamples()
  fetchHistory()
})
</script>

<style scoped>
.page-view { padding: 0; }
.mb-20 { margin-bottom: 20px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.tip-text { color: #909399; font-size: 12px; margin-top: 6px; }
.upload-icon { font-size: 40px; color: #c0c4cc; margin-bottom: 8px; }
.sample-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 4px; border-bottom: 1px solid #ebeef5;
}
.sample-row:last-of-type { border-bottom: none; }
.sample-name { font-size: 13px; color: #303133; }
</style>
