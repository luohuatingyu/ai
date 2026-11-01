<template>
  <div class="page-view">
    <div class="page-header">
      <h1 class="page-title">创建 Pipeline</h1>
    </div>
    <el-card>
      <el-form :model="form" label-width="120px">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="请输入名称" />
        </el-form-item>
        <el-form-item label="配置">
          <el-input v-model="form.config_json" type="textarea" :rows="10" placeholder="JSON 配置" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="submitting" @click="submit">创建</el-button>
          <el-button @click="$router.back()">取消</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'

const router = useRouter()
const appStore = useAppStore()
const submitting = ref(false)
const form = reactive({ name: '', config_json: '{"steps": []}' })

const submit = async () => {
  submitting.value = true
  try {
    const config = JSON.parse(form.config_json || '{}')
    await api.post('/api/pipelines', { name: form.name, ...config })
    appStore.success('创建成功')
    router.push('/pipelines/list')
  } catch (err) {
    appStore.error('创建失败', (err as any)?.message)
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.page-view { padding: 0; }
</style>
