<template>
  <!-- 主导航：侧栏与移动端抽屉共用 -->
  <el-menu
    class="app-nav"
    :default-active="activeMenu"
    router
    :collapse="collapse"
    :collapse-transition="false"
    :unique-opened="false"
  >
    <el-menu-item index="/dashboard">
      <el-icon><DataBoard /></el-icon>
      <template #title>仪表盘</template>
    </el-menu-item>

    <el-sub-menu v-for="group in navGroups" :key="group.key" :index="group.key">
      <template #title>
        <el-icon :style="groupIconStyle(group.key)"><component :is="group.icon" /></el-icon>
        <span>{{ group.title }}</span>
      </template>
      <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path">
        {{ item.title }}
      </el-menu-item>
    </el-sub-menu>
  </el-menu>
</template>

<script setup lang="ts">
/**
 * 应用主导航。
 * 菜单结构集中在本组件内维护，新增页面时同步更新对应分组即可；
 * 详情页通过路由 meta.nav 归属到列表页菜单项保持高亮。
 */
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import {
  DataBoard, Folder, Notebook, Document, Cpu, Box, Connection, OfficeBuilding,
} from '@element-plus/icons-vue'
import { MODULES } from '@/utils/modules'

/** 折叠态由父级（侧栏 / 抽屉）决定，仅影响菜单渲染形态 */
defineProps<{ collapse?: boolean }>()

const route = useRoute()

/** 当前激活菜单：详情页回退到所属列表项 */
const activeMenu = computed(() => (route.meta.nav as string) || route.path)

/**
 * 分组图标着色：使用业务域强调色的暗背景变体（accentLight），
 * 让 7 个业务域在深色侧栏中即具备一致的视觉识别。
 */
const groupIconStyle = (groupKey: string) => {
  const meta = MODULES[groupKey]
  return meta ? { color: meta.accentLight } : undefined
}

/** 导航分组（key 与路由前缀一致，用于子菜单展开标识） */
const navGroups = [
  {
    key: '/datasets',
    title: '数据集管理',
    icon: Folder,
    items: [
      { path: '/datasets/list', title: '数据集列表' },
      { path: '/datasets/import', title: '导入数据集' },
      { path: '/datasets/huggingface', title: 'HuggingFace' },
    ],
  },
  {
    key: '/workbench',
    title: '交互式工作台',
    icon: Notebook,
    items: [
      { path: '/workbench/sessions', title: '会话管理' },
      { path: '/workbench/new', title: '新建会话' },
    ],
  },
  {
    key: '/templates',
    title: '脚本模板库',
    icon: Document,
    items: [
      { path: '/templates/library', title: '模板库' },
      { path: '/templates/my', title: '我的模板' },
    ],
  },
  {
    key: '/training',
    title: '训练任务',
    icon: Cpu,
    items: [
      { path: '/training/jobs', title: '任务列表' },
      { path: '/training/create', title: '创建任务' },
    ],
  },
  {
    key: '/models',
    title: '模型注册表',
    icon: Box,
    items: [
      { path: '/models/registry', title: '模型列表' },
      { path: '/models/deployments', title: '部署管理' },
    ],
  },
  {
    key: '/pipelines',
    title: 'Pipeline 编排',
    icon: Connection,
    items: [
      { path: '/pipelines/list', title: 'Pipeline 列表' },
      { path: '/pipelines/create', title: '创建 Pipeline' },
      { path: '/pipelines/runs', title: '执行历史' },
    ],
  },
  {
    key: '/clusters',
    title: '计算集群',
    icon: OfficeBuilding,
    items: [
      { path: '/clusters/workers', title: 'Worker 管理' },
      { path: '/clusters/remote', title: '远程服务器' },
    ],
  },
]
</script>
