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
    </div>
    <el-card>
      <el-descriptions :column="2" border v-if="detail">
        <el-descriptions-item v-for="(value, key) in displayDetail" :key="key" :label="key">
          {{ value }}
        </el-descriptions-item>
      </el-descriptions>
      <div v-else class="loading-placeholder">
        <el-icon class="is-loading" :size="32"><Loading /></el-icon>
        <p>加载中...</p>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import { ArrowLeft, Loading } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()
const detail = ref<any>(null)

const skipKeys = ['artifacts', 'params', 'resource_hint', 'spec', 'segments', 'steps', 'metrics']
const displayDetail = computed(() => {
  if (!detail.value) return {}
  const result: Record<string, any> = {}
  for (const [k, v] of Object.entries(detail.value)) {
    if (skipKeys.includes(k)) continue
    result[k] = typeof v === 'object' ? JSON.stringify(v) : v
  }
  return result
})

const fetchDetail = async () => {
  try {
    detail.value = await api.get('/templates/{id}'.replace('{id}', String(route.params.id)))
  } catch (err: any) {
    appStore.error('加载失败', err?.message)
  }
}

const goBack = () => window.history.length > 1 ? router.back() : router.replace('/')

onMounted(fetchDetail)
</script>

<style scoped>
.page-view { padding: 0; }
.header-left { display: flex; align-items: center; gap: 12px; }
.loading-placeholder { text-align: center; padding: 60px; color: #909399; }
</style>
