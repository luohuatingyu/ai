<template>
  <el-config-provider :locale="zhCn">
    <el-container class="app-container">
      <el-header>
        <div class="header-left">
          <el-icon class="logo-icon"><Monitor /></el-icon>
          <span class="logo-text">MTP - 模型训练平台</span>
        </div>
        <div class="header-right">
          <el-dropdown>
            <span class="el-dropdown-link">
              <el-icon><User /></el-icon>
              <span style="margin-left: 8px;">Admin</span>
              <el-icon style="margin-left: 4px;"><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="logout">
                  <el-icon><SwitchButton /></el-icon>
                  <span>退出登录</span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-container>
        <el-aside :width="isCollapsed ? '64px' : '220px'">
          <el-menu
            :default-active="activeMenu"
            class="nav-menu"
            router
            :collapse="isCollapsed"
            :unique-opened="true"
            :collapse-transition="false"
          >
            <el-menu-item index="/dashboard">
              <el-icon><DataBoard /></el-icon>
              <template #title>仪表盘</template>
            </el-menu-item>

            <el-sub-menu index="/datasets">
              <template #title>
                <el-icon><Folder /></el-icon>
                <span>数据集管理</span>
              </template>
              <el-menu-item index="/datasets/list">数据集列表</el-menu-item>
              <el-menu-item index="/datasets/import">导入数据集</el-menu-item>
              <el-menu-item index="/datasets/huggingface">HuggingFace</el-menu-item>
            </el-sub-menu>

            <el-sub-menu index="/workbench">
              <template #title>
                <el-icon><Notebook /></el-icon>
                <span>交互式工作台</span>
              </template>
              <el-menu-item index="/workbench/sessions">会话管理</el-menu-item>
              <el-menu-item index="/workbench/new">新建会话</el-menu-item>
            </el-sub-menu>

            <el-sub-menu index="/templates">
              <template #title>
                <el-icon><Document /></el-icon>
                <span>脚本模板库</span>
              </template>
              <el-menu-item index="/templates/library">模板库</el-menu-item>
              <el-menu-item index="/templates/my">我的模板</el-menu-item>
            </el-sub-menu>

            <el-sub-menu index="/training">
              <template #title>
                <el-icon><Cpu /></el-icon>
                <span>训练任务</span>
              </template>
              <el-menu-item index="/training/jobs">任务列表</el-menu-item>
              <el-menu-item index="/training/create">创建任务</el-menu-item>
            </el-sub-menu>

            <el-sub-menu index="/models">
              <template #title>
                <el-icon><Box /></el-icon>
                <span>模型注册表</span>
              </template>
              <el-menu-item index="/models/registry">模型列表</el-menu-item>
              <el-menu-item index="/models/deployments">部署管理</el-menu-item>
            </el-sub-menu>

            <el-sub-menu index="/pipelines">
              <template #title>
                <el-icon><Connection /></el-icon>
                <span>Pipeline 编排</span>
              </template>
              <el-menu-item index="/pipelines/list">Pipeline 列表</el-menu-item>
              <el-menu-item index="/pipelines/create">创建 Pipeline</el-menu-item>
              <el-menu-item index="/pipelines/runs">执行历史</el-menu-item>
            </el-sub-menu>

            <el-sub-menu index="/clusters">
              <template #title>
                <el-icon><OfficeBuilding /></el-icon>
                <span>计算集群</span>
              </template>
              <el-menu-item index="/clusters/workers">Worker 管理</el-menu-item>
              <el-menu-item index="/clusters/remote">远程服务器</el-menu-item>
            </el-sub-menu>
          </el-menu>
          <div class="collapse-toggle" @click="isCollapsed = !isCollapsed">
            <el-icon><component :is="isCollapsed ? Expand : Fold" /></el-icon>
            <span v-if="!isCollapsed">收起菜单</span>
          </div>
        </el-aside>

        <el-main>
          <router-view v-slot="{ Component }">
            <transition name="fade" mode="out-in">
              <component :is="Component" />
            </transition>
          </router-view>
        </el-main>
      </el-container>

      <el-footer>
        MTP Model Training Platform v2.0 | Powered by Vue 3 + FastAPI
      </el-footer>
    </el-container>
  </el-config-provider>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { zhCn } from 'element-plus/es/locales.mjs'
import { ElNotification } from 'element-plus'
import { useAppStore } from '@/stores/app'
import {
  Monitor, User, ArrowDown, SwitchButton,
  DataBoard, Folder, Notebook, Document, Cpu, Box, Connection,
  OfficeBuilding, Fold, Expand
} from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()
const isCollapsed = ref(false)

const activeMenu = computed(() => route.path)

// 全局通知：把 pinia 中的通知队列渲染为右上角通知（此前队列从未展示）
watch(
  () => appStore.notifications,
  (list) => {
    const latest = list[list.length - 1]
    if (!latest) return
    ElNotification({
      title: latest.title,
      message: latest.message || '',
      type: latest.type,
      duration: latest.duration === 0 ? 6000 : (latest.duration || 4500),
      showClose: true,
    })
  },
  { deep: true }
)

const logout = () => {
  // 内网部署暂无认证体系：清理本地凭证并回到入口页
  localStorage.removeItem('token')
  ElNotification({ title: '已退出', message: '本地凭证已清理', type: 'info', duration: 2000 })
  router.push('/dashboard')
}
</script>

<style scoped>
.app-container {
  height: 100vh;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.logo-icon {
  font-size: 24px;
  color: #409eff;
}

.logo-text {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.collapse-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 20px;
  color: #909399;
  font-size: 12px;
  cursor: pointer;
  border-top: 1px solid #ebeef5;
  user-select: none;
}

.collapse-toggle:hover {
  color: #409eff;
  background: #f5f7fa;
}

.el-menu--collapse {
  width: 64px !important;
}
</style>
