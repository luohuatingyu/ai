<template>
  <div class="dataset-list">
    <div class="page-header">
      <h1 class="page-title">数据集列表</h1>
      <div class="header-actions">
        <el-button type="primary" @click="showImportDialog = true">
          <el-icon><Upload /></el-icon>
          导入数据集
        </el-button>
        <el-button @click="showHuggingFaceDialog = true">
          <el-icon><Link /></el-icon>
          HuggingFace
        </el-button>
      </div>
    </div>
    
    <!-- 搜索栏 -->
    <el-card class="mb-20">
      <el-form :model="searchForm" inline size="small">
        <el-form-item label="搜索">
          <el-input v-model="searchForm.search" placeholder="名称/描述/列名" clearable style="width: 250px" />
        </el-form-item>
        <el-form-item label="标签">
          <el-select v-model="searchForm.tags" multiple placeholder="选择标签" style="width: 200px">
            <el-option v-for="tag in allTags" :key="tag" :label="tag" :value="tag" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="fetchDatasets">搜索</el-button>
          <el-button @click="resetSearch">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>
    
    <!-- 数据集表格 -->
    <el-card>
      <el-table :data="datasets" border size="small" style="width: 100%" v-loading="loading">
        <el-table-column type="selection" width="55" />
        <el-table-column prop="id" label="ID" width="180" />
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column prop="source" label="来源" width="100">
          <template #default="scope">
            <el-tag :type="getSourceTag(scope.row.source)" size="small">
              {{ getSourceText(scope.row.source) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="rows" label="行数" width="80">
          <template #default="scope">
            {{ formatNumber(scope.row.rows) }}
          </template>
        </el-table-column>
        <el-table-column prop="columns" label="列数" width="80">
          <template #default="scope">
            {{ scope.row.columns?.length || 0 }}
          </template>
        </el-table-column>
        <el-table-column label="标签" min-width="150">
          <template #default="scope">
            <el-tag v-for="tag in scope.row.tags" :key="tag" size="small" effect="light">{{ tag }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="160">
          <template #default="scope">
            {{ formatDate(scope.row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="scope">
            <el-dropdown>
              <el-button size="small" link>
                操作
                <el-icon><ArrowDown /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item @click="viewDataset(scope.row.id)">
                    <el-icon><View /></el-icon>
                    <span>预览</span>
                  </el-dropdown-item>
                  <el-dropdown-item @click="viewStats(scope.row.id)">
                    <el-icon><DataAnalysis /></el-icon>
                    <span>统计信息</span>
                  </el-dropdown-item>
                  <el-dropdown-item @click="processDataset(scope.row.id)">
                    <el-icon><Setting /></el-icon>
                    <span>预处理</span>
                  </el-dropdown-item>
                  <el-dropdown-item @click="materializeDataset(scope.row.id)" v-if="scope.row.source === 'huggingface'">
                    <el-icon><Download /></el-icon>
                    <span>下载到本地</span>
                  </el-dropdown-item>
                  <el-dropdown-item divided @click="deleteDataset(scope.row.id)">
                    <el-icon><Delete /></el-icon>
                    <span style="color: #f56c6c">删除</span>
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
      </el-table>
      
      <!-- 分页 -->
      <div class="pagination" v-if="total > 0">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="onPageSizeChange"
          @current-change="onPageChange"
        />
      </div>
    </el-card>
    
    <!-- 导入对话框 -->
    <el-dialog v-model="showImportDialog" title="导入数据集" width="600px">
      <el-form :model="importForm" ref="importFormRef" label-width="100px">
        <el-form-item label="文件" prop="file">
          <el-upload
            action="#"
            :auto-upload="false"
            :on-change="handleFileChange"
            :show-file-list="true"
            accept=".csv,.zip,.tar,.gz"
          >
            <el-button type="primary">选择文件</el-button>
            <template #tip>
              <div>支持 CSV、ZIP 压缩包，最大 500MB</div>
            </template>
          </el-upload>
        </el-form-item>
        <el-form-item label="描述" prop="description">
          <el-input v-model="importForm.description" type="textarea" placeholder="数据集描述" />
        </el-form-item>
        <el-form-item label="标签" prop="tags">
          <el-tag-input v-model="importForm.tags" placeholder="按回车添加标签" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showImportDialog = false">取消</el-button>
        <el-button type="primary" :loading="importing" @click="submitImport">导入</el-button>
      </template>
    </el-dialog>
    
    <!-- HuggingFace 对话框 -->
    <el-dialog v-model="showHuggingFaceDialog" title="从 HuggingFace 导入" width="600px">
      <el-form :model="hfForm" ref="hfFormRef" label-width="100px">
        <el-form-item label="仓库 ID" prop="repo_id">
          <el-input v-model="hfForm.repo_id" placeholder="如: imdb, squad, glue" />
        </el-form-item>
        <el-form-item label="版本" prop="revision">
          <el-input v-model="hfForm.revision" placeholder="main (默认)" />
        </el-form-item>
        <el-form-item label="子集" prop="subset">
          <el-input v-model="hfForm.subset" placeholder="可选，如: en, zh" />
        </el-form-item>
        <el-form-item label="Split" prop="split">
          <el-select v-model="hfForm.split" placeholder="选择 split">
            <el-option label="train" value="train" />
            <el-option label="test" value="test" />
            <el-option label="validation" value="validation" />
          </el-select>
        </el-form-item>
        <el-form-item label="存储模式" prop="storage_mode">
          <el-radio-group v-model="hfForm.storage_mode">
            <el-radio value="local">本地存储</el-radio>
            <el-radio value="oss">对象存储 (OSS)</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="描述" prop="description">
          <el-input v-model="hfForm.description" type="textarea" placeholder="数据集描述" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showHuggingFaceDialog = false">取消</el-button>
        <el-button type="primary" :loading="hfImporting" @click="submitHuggingFace">导入</el-button>
      </template>
    </el-dialog>
    
    <!-- 预览对话框 -->
    <el-dialog v-model="showPreviewDialog" title="数据集预览" width="90%" fullscreen>
      <div v-if="previewData">
        <div class="preview-header">
          <h3>{{ previewData.name }} ({{ previewData.rows }} 行 × {{ previewData.columns?.length || 0 }} 列)</h3>
          <div class="preview-pagination">
            <el-pagination
              v-model:current-page="previewPage"
              :page-size="previewPageSize"
              :total="previewData.rows"
              layout="prev, pager, next, jumper"
              @current-change="fetchPreview"
              size="small"
            />
          </div>
        </div>
        <el-table :data="previewData.preview" border size="small" style="width: 100%">
          <el-table-column v-for="col in previewData.columns" :key="col" :prop="col" :label="col" />
        </el-table>
      </div>
    </el-dialog>
    
    <!-- 统计信息对话框 -->
    <el-dialog v-model="showStatsDialog" title="统计信息" width="800px">
      <div v-if="statsData">
        <el-tabs v-model="statsTab">
          <el-tab-pane label="概览" name="overview">
            <el-descriptions :column="2" border>
              <el-descriptions-item label="总行数">{{ statsData.total_rows }}</el-descriptions-item>
              <el-descriptions-item label="总列数">{{ statsData.total_columns }}</el-descriptions-item>
              <el-descriptions-item label="内存占用">{{ (statsData.memory_usage_mb || 0).toFixed(2) }} MB</el-descriptions-item>
            </el-descriptions>
          </el-tab-pane>
          <el-tab-pane label="列详情" name="columns">
            <el-table :data="columnStats" border size="small" style="width: 100%">
              <el-table-column prop="column" label="列名" />
              <el-table-column prop="type" label="类型" width="100" />
              <el-table-column prop="null_count" label="空值数" width="100" />
              <el-table-column prop="unique_count" label="唯一值数" width="100" />
              <el-table-column prop="min" label="最小值" width="100" />
              <el-table-column prop="max" label="最大值" width="100" />
              <el-table-column prop="mean" label="平均值" width="100" />
              <el-table-column prop="std" label="标准差" width="100" />
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </div>
    </el-dialog>
    
    <!-- 预处理对话框 -->
    <el-dialog v-model="showProcessDialog" title="数据预处理" width="800px">
      <el-form :model="processForm" label-width="120px">
        <el-form-item label="操作类型">
          <el-select v-model="processForm.operation_type" placeholder="选择预处理操作" style="width: 300px">
            <el-option label="去除重复行" value="remove_duplicates" />
            <el-option label="填充缺失值" value="fill_missing" />
            <el-option label="数值归一化" value="normalize" />
            <el-option label="分类变量编码" value="encode_categorical" />
            <el-option label="去除异常值" value="remove_outliers" />
            <el-option label="选择列" value="select_columns" />
            <el-option label="过滤行" value="filter_rows" />
            <el-option label="列转换" value="transform_column" />
          </el-select>
        </el-form-item>
        <el-form-item label="参数配置">
          <el-input v-model="processForm.params_json" type="textarea" rows="8" placeholder="JSON 格式参数" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showProcessDialog = false">取消</el-button>
        <el-button type="primary" :loading="processing" @click="submitProcess">执行</el-button>
      </template>
    </el-dialog>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import {
  Upload, Link, View, DataAnalysis, Setting, Download, Delete,
  ArrowDown, Folder, DataBoard
} from '@element-plus/icons-vue'

const appStore = useAppStore()

// 状态
const loading = ref(false)
const datasets = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const searchForm = reactive({ search: '', tags: [] as string[] })
const allTags = ref([])

// 导入
const showImportDialog = ref(false)
const importing = ref(false)
const importForm = reactive({ file: null as File | null, description: '', tags: [] as string[] })
const importFormRef = ref()

// HuggingFace
const showHuggingFaceDialog = ref(false)
const hfImporting = ref(false)
const hfForm = reactive({
  repo_id: '',
  revision: 'main',
  subset: '',
  split: 'train',
  storage_mode: 'local',
  description: '',
})
const hfFormRef = ref()

// 预览
const showPreviewDialog = ref(false)
const previewData = ref(null)
const previewPage = ref(1)
const previewPageSize = ref(50)

// 统计
const showStatsDialog = ref(false)
const statsData = ref(null)
const statsTab = ref('overview')
const columnStats = ref([])

// 预处理
const showProcessDialog = ref(false)
const processing = ref(false)
const processForm = reactive({ dataset_id: '', operation_type: '', params_json: '{}' })

// 方法
const fetchDatasets = async () => {
  loading.value = true
  try {
    const res = await api.get('/datasets', {
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
      search: searchForm.search || undefined,
      tags: searchForm.tags.length > 0 ? searchForm.tags.join(',') : undefined,
    })
    datasets.value = res.datasets || []
    total.value = res.total || 0
  } catch (err) {
    appStore.error('加载失败', err.message)
  } finally {
    loading.value = false
  }
}

const fetchTags = async () => {
  try {
    const res = await api.get('/datasets/tags')
    allTags.value = res.tags || []
  } catch (err) {
    console.error('获取标签失败', err)
  }
}

const onPageChange = (p: number) => {
  page.value = p
  fetchDatasets()
}

const onPageSizeChange = (size: number) => {
  pageSize.value = size
  page.value = 1
  fetchDatasets()
}

const resetSearch = () => {
  searchForm.search = ''
  searchForm.tags = []
  page.value = 1
  fetchDatasets()
}

const getSourceTag = (source: string) => {
  const map: Record<string, string> = { local: 'success', huggingface: 'info', oss: 'warning' }
  return map[source] || 'info'
}

const getSourceText = (source: string) => {
  const map: Record<string, string> = { local: '本地上传', huggingface: 'HuggingFace', oss: '对象存储' }
  return map[source] || source
}

const formatDate = (ts: number) => {
  if (!ts) return '-'
  return new Date(ts * 1000).toLocaleString('zh-CN')
}

const formatNumber = (num: number) => {
  if (!num) return '0'
  if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M'
  if (num >= 1000) return (num / 1000).toFixed(1) + 'K'
  return num.toString()
}

// 导入处理
const handleFileChange = (file: any) => {
  importForm.file = file.raw
}

const submitImport = async () => {
  if (!importForm.file) {
    appStore.warning('请选择文件')
    return
  }
  importing.value = true
  try {
    const formData = new FormData()
    formData.append('file', importForm.file)
    formData.append('description', importForm.description)
    formData.append('tags', JSON.stringify(importForm.tags))
    await api.upload('/datasets/import', importForm.file)
    appStore.success('导入成功')
    showImportDialog.value = false
    importForm.file = null
    importForm.description = ''
    importForm.tags = []
    fetchDatasets()
  } catch (err) {
    appStore.error('导入失败', err.message)
  } finally {
    importing.value = false
  }
}

const submitHuggingFace = async () => {
  hfImporting.value = true
  try {
    await api.post('/datasets/hub', hfForm)
    appStore.success('HuggingFace 数据集导入成功')
    showHuggingFaceDialog.value = false
    Object.assign(hfForm, { repo_id: '', revision: 'main', subset: '', split: 'train', storage_mode: 'local', description: '' })
    fetchDatasets()
  } catch (err) {
    appStore.error('导入失败', err.message)
  } finally {
    hfImporting.value = false
  }
}

// 预览
const viewDataset = async (id: string) => {
  previewData.value = null
  previewPage.value = 1
  showPreviewDialog.value = true
  await fetchPreview()
}

const fetchPreview = async () => {
  try {
    const res = await api.get(`/datasets/${processForm.dataset_id || ''}`, {
      rows: previewPageSize.value,
      offset: (previewPage.value - 1) * previewPageSize.value,
    })
    previewData.value = res
  } catch (err) {
    appStore.error('预览失败', err.message)
  }
}

// 统计
const viewStats = async (id: string) => {
  try {
    const res = await api.get(`/datasets/${id}/stats`)
    statsData.value = res
    columnStats.value = Object.entries(res.column_stats || {}).map(([col, stat]: [string, any]) => ({
      column: col,
      ...stat,
    }))
    showStatsDialog.value = true
  } catch (err) {
    appStore.error('获取统计失败', err.message)
  }
}

// 预处理
const processDataset = (id: string) => {
  processForm.dataset_id = id
  processForm.operation_type = ''
  processForm.params_json = '{}'
  showProcessDialog.value = true
}

const submitProcess = async () => {
  processing.value = true
  try {
    const params = JSON.parse(processForm.params_json)
    await api.post(`/datasets/${processForm.dataset_id}/process`, {
      operations: [{ type: processForm.operation_type, ...params }],
    })
    appStore.success('预处理完成')
    showProcessDialog.value = false
    fetchDatasets()
  } catch (err) {
    appStore.error('预处理失败', err.message)
  } finally {
    processing.value = false
  }
}

// 删除
const deleteDataset = async (id: string) => {
  try {
    await api.delete(`/datasets/${id}`)
    appStore.success('删除成功')
    fetchDatasets()
  } catch (err) {
    appStore.error('删除失败', err.message)
  }
}

// 下载到本地
const materializeDataset = async (id: string) => {
  try {
    await api.post(`/datasets/${id}/materialize`)
    appStore.success('下载完成')
    fetchDatasets()
  } catch (err) {
    appStore.error('下载失败', err.message)
  }
}

onMounted(() => {
  fetchDatasets()
  fetchTags()
})
</script>

<style scoped>
.dataset-list { padding: 0; }
.preview-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.preview-pagination { display: flex; justify-content: flex-end; }
.mb-20 { margin-bottom: 20px; }
.pagination { padding: 16px 0; display: flex; justify-content: flex-end; }
</style>