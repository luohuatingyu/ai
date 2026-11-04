<template>
  <PageShell title="会话管理" module="交互式工作台" description="管理 Notebook 会话：状态、资源与最近活动">
    <template #actions>
      <el-button @click="fetchSessions" :loading="loading">
        <el-icon class="gap-8"><Refresh /></el-icon>
        刷新
      </el-button>
      <el-button type="primary" @click="router.push('/workbench/new')">
        <el-icon class="gap-8"><Plus /></el-icon>
        新建会话
      </el-button>
    </template>

    <!-- 状态汇总 -->
    <div class="stat-grid">
      <StatCard label="全部会话" :value="String(sessions.length)" :icon="Notebook" tone="blue" hint="所有 Notebook 会话" />
      <StatCard label="运行中" :value="String(statusCounts.running)" :icon="VideoPlay" tone="green" hint="内核运行中的会话" />
      <StatCard label="空闲" :value="String(statusCounts.idle)" :icon="Clock" tone="orange" hint="内核已连接但无活动" />
      <StatCard label="异常" :value="String(statusCounts.error)" :icon="WarningFilled" tone="red" hint="存在错误的会话" />
    </div>

    <!-- 会话卡片网格 -->
    <div v-loading="loading" class="session-grid-wrap">
      <div v-if="sessions.length > 0" class="session-grid">
        <div
          v-for="session in sessions"
          :key="session.id"
          class="session-card"
          :class="[`is-${session.status}`]"
        >
          <div class="session-card__head">
            <el-link type="primary" underline="never" class="session-card__name" @click="openSession(session.id)">
              {{ session.name }}
            </el-link>
            <StatusTag :status="session.status" :map="SESSION_STATUS" />
          </div>

          <div class="session-card__meta">
            <div class="meta-row">
              <span class="meta-label">内核</span>
              <el-tag size="small" effect="plain">{{ session.kernel_type }}</el-tag>
            </div>
            <div class="meta-row">
              <span class="meta-label">资源</span>
              <span>{{ session.bound_workers?.length || 0 }} 个 Worker</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">最近活动</span>
              <span>{{ formatFullDateTime(session.last_activity) }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">会话 ID</span>
              <span class="mono text-xs text-ink-400">{{ session.id }}</span>
            </div>
          </div>

          <div class="session-card__actions">
            <el-button
              size="small"
              :type="session.status === 'running' ? 'warning' : 'success'"
              :loading="kernelLoading[session.id]"
              @click="toggleKernel(session)"
            >
              <el-icon v-if="session.status === 'running'"><VideoPause /></el-icon>
              <el-icon v-else><VideoPlay /></el-icon>
              {{ session.status === 'running' ? '停止内核' : '启动内核' }}
            </el-button>
            <el-button size="small" type="primary" @click="openSession(session.id)">
              <el-icon><Edit /></el-icon>
              打开工作台
            </el-button>
            <el-dropdown trigger="click" @command="(cmd: string) => onSessionCommand(cmd, session)">
              <el-button size="small" :aria-label="`会话 ${session.name} 的更多操作`">
                <el-icon><More /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="duplicate">
                    <el-icon><DocumentCopy /></el-icon>复制会话
                  </el-dropdown-item>
                  <el-dropdown-item command="export">
                    <el-icon><Download /></el-icon>导出 Notebook
                  </el-dropdown-item>
                  <el-dropdown-item command="delete" divided>
                    <el-icon style="color: var(--mtp-danger)"><Delete /></el-icon>
                    <span style="color: var(--mtp-danger)">删除</span>
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>
      </div>

      <SectionCard v-else-if="!loading" flush>
        <EmptyState
          v-if="loadError"
          type="error"
          title="会话加载失败"
          :description="loadError"
          retryable
          @retry="fetchSessions"
        />
        <EmptyState v-else title="暂无会话" description="创建第一个 Notebook 会话，开始交互式实验">
          <template #actions>
            <el-button type="primary" @click="router.push('/workbench/new')">创建第一个会话</el-button>
          </template>
        </EmptyState>
      </SectionCard>
      <div v-else class="list-loading" />
    </div>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * 会话管理：状态汇总 + 会话卡片列表。
 * - 生命周期操作统一：启动/停止内核、复制、导出、删除（带名称确认）；
 * - 创建入口统一到独立页面 /workbench/new（本页不再重复放置创建表单）；
 * - 列表失败展示可重试错误态；导出直接生成 .ipynb 下载。
 */
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import StatCard from '@/components/common/StatCard.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { SESSION_STATUS } from '@/utils/status'
import { formatFullDateTime } from '@/utils/format'
import {
  Plus, Refresh, VideoPlay, VideoPause, Edit, More, Download, Delete,
  DocumentCopy, Notebook, Clock, WarningFilled,
} from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()

const sessions = ref<any[]>([])
const loading = ref(false)
const loadError = ref('')
const kernelLoading = ref<Record<string, boolean>>({})

/** 按状态聚合的数量（汇总卡片） */
const statusCounts = computed(() => {
  const counts: Record<string, number> = { running: 0, idle: 0, error: 0 }
  for (const session of sessions.value) {
    if (session.status in counts) counts[session.status]++
  }
  return counts
})

/** 拉取会话列表（最多 100 个） */
const fetchSessions = async () => {
  loading.value = true
  loadError.value = ''
  try {
    const res: any = await api.get('/workbench/sessions', { limit: 100 })
    sessions.value = res.sessions || []
  } catch (err: any) {
    loadError.value = err?.message || '网络错误'
    appStore.error('加载失败', err?.message)
  } finally {
    loading.value = false
  }
}

/** 行内命令分发 */
const onSessionCommand = (command: string, session: any) => {
  if (command === 'duplicate') duplicateSession(session)
  else if (command === 'export') exportSession(session)
  else if (command === 'delete') deleteSession(session)
}

/** 启动 / 停止内核 */
const toggleKernel = async (session: any) => {
  kernelLoading.value[session.id] = true
  try {
    if (session.status === 'running') {
      await api.post(`/workbench/sessions/${session.id}/kernel/stop`)
      appStore.success('内核已停止', session.name)
    } else {
      await api.post(`/workbench/sessions/${session.id}/kernel/start`)
      appStore.success('内核已启动', session.name)
    }
    fetchSessions()
  } catch (err: any) {
    appStore.error('操作失败', err?.message)
  } finally {
    kernelLoading.value[session.id] = false
  }
}

const openSession = (id: string) => {
  router.push(`/workbench/session/${id}`)
}

/** 复制会话：沿用原配置创建副本 */
const duplicateSession = async (session: any) => {
  try {
    await api.post('/workbench/sessions', {
      name: `${session.name} (副本)`,
      dataset_id: session.dataset_id,
      kernel_type: session.kernel_type,
      bound_workers: session.bound_workers,
      metadata: session.metadata,
    })
    appStore.success('会话已复制', session.name)
    fetchSessions()
  } catch (err: any) {
    appStore.error('复制失败', err?.message)
  }
}

/** 导出会话为标准 .ipynb 文件 */
const exportSession = async (session: any) => {
  try {
    const cells: any[] = await api.get(`/workbench/sessions/${session.id}/cells`)
    const content = {
      nbformat: 4,
      nbformat_minor: 4,
      metadata: { kernelspec: { name: 'python3', display_name: 'Python 3' } },
      cells: cells.map((c: any) => ({
        cell_type: c.cell_type,
        source: c.source.split('\n'),
        outputs: c.outputs || [],
        execution_count: c.execution_count,
        metadata: {},
      })),
    }
    downloadBlob(JSON.stringify(content, null, 2), `${session.name}.ipynb`)
    appStore.success('导出成功', session.name)
  } catch (err: any) {
    appStore.error('导出失败', err?.message)
  }
}

/** 生成并触发浏览器下载 */
const downloadBlob = (text: string, filename: string) => {
  const blob = new Blob([text], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/** 删除会话（确认文案包含会话名，防误删） */
const deleteSession = async (session: any) => {
  try {
    await ElMessageBox.confirm(
      `确认删除会话「${session.name}」？会话内的单元格与变量将一并清理。`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await api.delete(`/workbench/sessions/${session.id}`, { params: { force: 'true' } })
    appStore.success('删除成功', session.name)
    fetchSessions()
  } catch (err: any) {
    appStore.error('删除失败', err?.message)
  }
}

onMounted(() => {
  fetchSessions()
})
</script>

<style scoped>
.session-grid-wrap {
  min-height: 200px;
}

.list-loading {
  min-height: 200px;
}

.session-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
}

.session-card {
  background: var(--mtp-bg-card);
  border: 1px solid var(--mtp-ink-100);
  border-radius: var(--mtp-radius-lg);
  box-shadow: var(--mtp-shadow-card);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: box-shadow 0.2s, transform 0.2s;
  position: relative;
  overflow: hidden;
}

.session-card:hover {
  box-shadow: var(--mtp-shadow-card-hover);
  transform: translateY(-2px);
}

/* 状态左侧色条 */
.session-card::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--mtp-ink-200);
}

.session-card.is-running::before { background: var(--mtp-success); }
.session-card.is-busy::before { background: var(--mtp-brand-600); }
.session-card.is-idle::before { background: var(--mtp-warning); }
.session-card.is-error::before { background: var(--mtp-danger); }
.session-card.is-starting::before { background: var(--mtp-info); }

.session-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.session-card__name {
  font-size: 15px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-card__meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  background: var(--mtp-bg-subtle);
  border-radius: var(--mtp-radius-md);
  flex: 1;
}

.meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12.5px;
  color: var(--mtp-ink-700);
}

.meta-label {
  color: var(--mtp-ink-400);
  flex-shrink: 0;
}

.session-card__actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.session-card__actions .el-button + .el-button {
  margin-left: 0;
}
</style>
