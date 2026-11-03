<template>
  <div class="template-library">
    <div class="page-header">
      <h1 class="page-title">脚本模板库</h1>
      <el-button type="primary" @click="router.push('/templates/create')">
        <el-icon><Plus /></el-icon>
        创建模板
      </el-button>
    </div>
    
    <!-- 分类筛选 -->
    <el-card class="mb-20">
      <el-row :gutter="16" align="middle">
        <el-col :xs="24" :md="8">
          <el-select v-model="selectedCategory" placeholder="全部分类" style="width: 100%" clearable>
            <el-option v-for="cat in categories" :key="cat.name" :label="cat.name" :value="cat.name" />
          </el-select>
        </el-col>
        <el-col :xs="24" :md="8">
          <el-select v-model="showBuiltin" placeholder="全部来源" style="width: 100%" clearable>
            <el-option label="内置模板" value="true" />
            <el-option label="用户模板" value="false" />
          </el-select>
        </el-col>
        <el-col :xs="24" :md="8">
          <el-input v-model="searchKeyword" placeholder="搜索模板名称/描述" clearable style="width: 100%" @keyup.enter="fetchTemplates" />
        </el-col>
      </el-row>
    </el-card>
    
    <!-- 模板网格 -->
    <div v-if="templates.length > 0" class="template-grid">
      <el-card class="template-card" v-for="template in templates" :key="template.id" @click="viewTemplate(template.id)">
        <div class="template-card-header">
          <span class="template-category">{{ template.category }}</span>
          <el-tag v-if="template.is_builtin" size="small" effect="dark">内置</el-tag>
        </div>
        <h3 class="template-name">{{ template.name }}</h3>
        <p class="template-description">{{ template.description }}</p>
        <div class="template-meta">
          <el-tag size="small">{{ template.task_type }}</el-tag>
        </div>
        <div class="template-params">
          <el-tag v-for="p in template.parameters.slice(0, 5)" :key="p.name" size="small" effect="light">{{ p.label }}</el-tag>
          <el-tag v-if="template.parameters.length > 5" size="small" effect="dark">+{{ template.parameters.length - 5 }} 更多</el-tag>
        </div>
        <div class="template-footer">
          <el-button size="small" type="primary" @click.stop="useTemplate(template)">
            <el-icon><VideoPlay /></el-icon>
            使用模板
          </el-button>
          <el-button size="small" @click.stop="viewTemplate(template.id)">
            <el-icon><View /></el-icon>
            查看详情
          </el-button>
          <el-popconfirm
            v-if="!template.is_builtin"
            title="确认删除该模板？删除后不可恢复。"
            confirm-button-text="删除"
            cancel-button-text="取消"
            @confirm="deleteTemplate(template.id)"
          >
            <template #reference>
              <el-button size="small" type="danger" plain @click.stop>删除</el-button>
            </template>
          </el-popconfirm>
        </div>
      </el-card>
    </div>
    
    <div v-else class="empty-state">
      <el-icon class="empty-state-icon"><Document /></el-icon>
      <p>暂无模板</p>
      <el-button type="primary" @click="router.push('/templates/create')">创建第一个模板</el-button>
    </div>
    
    <!-- 分页 -->
    <div class="pagination" v-if="total > pageSize">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50]"
        layout="total, sizes, prev, pager, next, jumper"
        @size-change="onPageSizeChange"
        @current-change="onPageChange"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import { Plus, View, VideoPlay, Document } from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()

const templates = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(12)
const selectedCategory = ref('')
const showBuiltin = ref('')
const searchKeyword = ref('')
const categories = ref<any[]>([])

const fetchTemplates = async () => {
  try {
    const res = await api.get('/templates', {
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
      category: selectedCategory.value || undefined,
      is_builtin: showBuiltin.value === '' ? undefined : showBuiltin.value === 'true',
      search: searchKeyword.value || undefined,
    })
    templates.value = res.templates || []
    total.value = res.total || 0
  } catch (err) {
    appStore.error('加载失败', err.message)
  }
}

const fetchCategories = async () => {
  try {
    const res = await api.get('/templates/categories')
    categories.value = res.categories || []
  } catch (err) {
    console.error('获取分类失败', err)
  }
}

const onPageChange = (p: number) => { page.value = p; fetchTemplates() }
const onPageSizeChange = (size: number) => { pageSize.value = size; page.value = 1; fetchTemplates() }

const viewTemplate = (id: string) => { router.push(`/templates/detail/${id}`) }

// 使用模板：携带模板 ID 跳转创建任务页，由创建页拉取模板并预填参数
const useTemplate = (template: any) => {
  router.push(`/training/create?template=${template.id}`)
}

const deleteTemplate = async (id: string) => {
  try {
    await api.delete(`/templates/${id}`)
    appStore.success('模板已删除')
    fetchTemplates()
  } catch (err: any) {
    appStore.error('删除失败', err?.message)
  }
}

onMounted(() => {
  fetchTemplates()
  fetchCategories()
})
</script>

<style scoped>
.template-library { padding: 0; }
.template-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 20px; }
.template-card { cursor: pointer; transition: all 0.3s; height: 100%; display: flex; flex-direction: column; }
.template-card:hover { border-color: #409eff; box-shadow: 0 4px 16px rgba(64, 158, 255, 0.15); transform: translateY(-2px); }
.template-card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.template-category { font-size: 12px; color: #409eff; font-weight: 500; background: #ecf5ff; padding: 2px 8px; border-radius: 4px; }
.template-name { font-size: 16px; font-weight: 500; margin: 8px 0; color: #303133; }
.template-description { color: #909399; font-size: 13px; margin-bottom: 12px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; flex: 1; }
.template-meta { margin: 8px 0; }
.template-params { display: flex; flex-wrap: wrap; gap: 6px; margin-top: auto; padding-top: 12px; }
.template-footer { display: flex; gap: 8px; margin-top: 16px; padding-top: 12px; border-top: 1px solid #ebeef5; }
.pagination { display: flex; justify-content: center; padding: 20px 0; }
</style>