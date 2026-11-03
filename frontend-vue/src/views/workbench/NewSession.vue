<template>
  <div class="page-view">
    <div class="page-header">
      <h1 class="page-title">新建会话</h1>
      <el-button @click="router.push('/workbench/sessions')">返回会话列表</el-button>
    </div>
    <el-row :gutter="20">
      <el-col :xs="24" :lg="16">
        <el-card>
          <el-form ref="formRef" :model="form" :rules="rules" label-width="120px">
            <el-form-item label="会话名称" prop="name">
              <el-input v-model="form.name" placeholder="如：销量预测实验" maxlength="80" show-word-limit />
            </el-form-item>
            <el-form-item label="关联数据集">
              <el-select v-model="form.dataset_id" placeholder="选择数据集（可选）" clearable style="width: 100%" :loading="datasetsLoading">
                <el-option v-for="ds in datasets" :key="ds.id" :label="`${ds.name} (${ds.rows} 行)`" :value="ds.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="内核类型" prop="kernel_type">
              <el-radio-group v-model="form.kernel_type">
                <el-radio value="python">Python</el-radio>
                <el-radio value="bash">Bash</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="绑定计算槽">
              <div v-if="workersLoading" v-loading="true" class="workers-loading" />
              <template v-else-if="workerOptions.length > 0">
                <el-checkbox-group v-model="form.bound_workers">
                  <el-checkbox
                    v-for="w in workerOptions" :key="w.worker_id"
                    :label="w.worker_id" :value="w.worker_id" border class="worker-checkbox">
                    <span class="worker-name">{{ w.worker_id }}</span>
                    <el-tag size="small" :type="w.device === 'cpu' ? 'info' : 'success'">
                      {{ w.device === 'cpu' ? 'CPU' : (w.gpu_name || 'GPU') }}
                    </el-tag>
                    <el-tag v-if="w.state === 'busy' || w.current_job" size="small" type="warning">占用中</el-tag>
                  </el-checkbox>
                </el-checkbox-group>
              </template>
              <el-empty v-else description="暂无可用计算槽，可不绑定稍后在会话中选择" :image-size="60" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="creating" @click="submit">创建并打开</el-button>
              <el-button @click="router.push('/workbench/sessions')">取消</el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="8">
        <el-card>
          <template #header><span>说明</span></template>
          <ul class="help-list">
            <li>会话创建后默认包含一个代码单元格，可在工作台中继续添加。</li>
            <li>绑定计算槽后，单元格可把训练分发到所选资源执行。</li>
            <li>内核可在会话详情中随时启动或停止。</li>
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
const creating = ref(false)
const datasets = ref<any[]>([])
const datasetsLoading = ref(false)
const workerOptions = ref<any[]>([])
const workersLoading = ref(false)

const form = reactive({
  name: '',
  dataset_id: '',
  kernel_type: 'python',
  bound_workers: [] as string[],
})
const rules: FormRules = {
  name: [{ required: true, message: '请输入会话名称', trigger: 'blur' }],
  kernel_type: [{ required: true, message: '请选择内核类型', trigger: 'change' }],
}

const fetchDatasets = async () => {
  datasetsLoading.value = true
  try {
    const res = await api.get('/datasets', { limit: 200 })
    datasets.value = res.datasets || []
  } catch (err: any) {
    appStore.warning('数据集加载失败', err?.message)
  } finally {
    datasetsLoading.value = false
  }
}

const fetchWorkers = async () => {
  workersLoading.value = true
  try {
    const res: any = await api.get('/clusters/workers/summary')
    workerOptions.value = res.details || []
  } catch (err: any) {
    appStore.warning('计算槽加载失败', err?.message)
  } finally {
    workersLoading.value = false
  }
}

const submit = async () => {
  await formRef.value?.validate().catch(() => Promise.reject())
  creating.value = true
  try {
    const res: any = await api.post('/workbench/sessions', {
      name: form.name,
      dataset_id: form.dataset_id || null,
      kernel_type: form.kernel_type,
      bound_workers: form.bound_workers,
      metadata: {},
    })
    appStore.success('会话创建成功')
    router.push(`/workbench/session/${res.session_id}`)
  } catch (err: any) {
    appStore.error('创建失败', err?.message)
  } finally {
    creating.value = false
  }
}

onMounted(() => {
  fetchDatasets()
  fetchWorkers()
})
</script>

<style scoped>
.page-view { padding: 0; }
.workers-loading { height: 48px; width: 100%; }
.worker-checkbox { margin: 0 12px 12px 0; padding: 10px 16px; }
.worker-name { margin-right: 8px; font-size: 12px; }
.help-list { padding-left: 18px; color: #909399; font-size: 13px; line-height: 2; }
</style>
