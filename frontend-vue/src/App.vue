<template>
  <el-config-provider :locale="zhCn">
    <el-container class="app-container">
      <el-header>
        <div class="logo">
          <el-icon><Monitor /></el-icon>
          <span>MTP - 模型训练平台</span>
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
        <el-aside width="220px">
          <el-menu
            :default-active="activeMenu"
            class="nav-menu"
            router
            :collapse="isCollapsed"
            :unique-opened="true"
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
                <el-icon><Cpu /></el-icon>
                <span>计算集群</span>
              </template>
              <el-menu-item index="/clusters/workers">Worker 管理</el-menu-item>
              <el-menu-item index="/clusters/remote">远程服务器</el-menu-item>
            </el-sub-menu>
          </el-menu>
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
    
    <el-loading v-model="loading" :fullscreen="true" lock-body>
      <div class="loading-spinner">
        <el-icon class="is-loading"><Loading /></el-icon>
        <p style="margin-top: 16px; color: #909399;">{{ loadingText }}</p>
      </div>
    </el-loading>
  </el-config-provider>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { zhCn } from 'element-plus/es/locales.mjs'
import {
  Monitor, User, ArrowDown, SwitchButton,
  DataBoard, Folder, Notebook, Document, Cpu, Box, Connection,
  Loading
} from '@element-plus/icons-vue'

const route = useRoute()
const isCollapsed = ref(false)
const loading = ref(false)
const loadingText = ref('加载中...')

const activeMenu = computed(() => route.path)

const logout = () => {
  // TODO: 实现登出逻辑
  console.log('Logout')
}
</script>

<style scoped>
.app-container {
  height: 100vh;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.loading-spinner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.loading-spinner .is-loading {
  font-size: 32px;
  color: #409eff;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.el-menu--collapse {
  width: 64px !important;
}
</style>