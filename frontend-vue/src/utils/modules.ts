/**
 * 业务域模块视觉标识注册表。
 * 为 7 个业务域（+总览）各自定义一致的强调色 / 浅底色 / 图标：
 * - accent：模块强调色，用于页面标题章、区块标题色条、关键数字强调（浅色背景）；
 * - accentLight：暗色背景（深色侧栏）下使用的提亮变体，保证图标可辨识；
 * - accentSoft：强调色的浅底色，用于图标章背景与徽标底色。
 * 页面通过 PageShell 注入 `--mtp-module-accent` / `--mtp-module-accent-soft`
 * 两个 CSS 变量，区块卡片、筛选栏等子组件经 CSS 变量继承自动着色。
 */
import type { Component } from 'vue'
import {
  DataBoard, Folder, Notebook, Document, Cpu, Box, Connection, OfficeBuilding,
} from '@element-plus/icons-vue'

/** 单个业务域的视觉标识定义 */
export interface ModuleMeta {
  /** 模块 key（与路由前缀一致） */
  key: string
  /** 模块显示名 */
  label: string
  /** 强调色（浅色背景场景） */
  accent: string
  /** 强调色浅底（图标章 / 徽标背景） */
  accentSoft: string
  /** 提亮变体（深色侧栏等暗色背景场景） */
  accentLight: string
  /** 模块图标 */
  icon: Component
  /** 路由前缀 */
  prefix: string
}

/** 模块注册表：key 与路由一级前缀保持一致，便于按路径推导 */
export const MODULES: Record<string, ModuleMeta> = {
  dashboard: {
    key: 'dashboard', label: '总览', prefix: '/dashboard',
    accent: '#3056d3', accentSoft: '#e8eefc', accentLight: '#6c8ae4', icon: DataBoard,
  },
  datasets: {
    key: 'datasets', label: '数据集', prefix: '/datasets',
    accent: '#0e9db5', accentSoft: '#e5f5f8', accentLight: '#22b8d4', icon: Folder,
  },
  workbench: {
    key: 'workbench', label: '工作台', prefix: '/workbench',
    accent: '#7c5cd6', accentSoft: '#f1ecfb', accentLight: '#9d85e8', icon: Notebook,
  },
  templates: {
    key: 'templates', label: '模板库', prefix: '/templates',
    accent: '#c2508f', accentSoft: '#faecf5', accentLight: '#d97bb4', icon: Document,
  },
  training: {
    key: 'training', label: '训练任务', prefix: '/training',
    accent: '#3056d3', accentSoft: '#e8eefc', accentLight: '#6c8ae4', icon: Cpu,
  },
  models: {
    key: 'models', label: '模型', prefix: '/models',
    accent: '#1f9d63', accentSoft: '#e7f6ef', accentLight: '#3dbd85', icon: Box,
  },
  pipelines: {
    key: 'pipelines', label: 'Pipeline', prefix: '/pipelines',
    accent: '#d98600', accentSoft: '#fbf1e0', accentLight: '#f0a63a', icon: Connection,
  },
  clusters: {
    key: 'clusters', label: '计算集群', prefix: '/clusters',
    accent: '#4e7ca8', accentSoft: '#e9f1f8', accentLight: '#7fa6cc', icon: OfficeBuilding,
  },
}

/**
 * 按路由路径推导模块 key：取一级前缀（如 /training/detail/1 → training）。
 * 未匹配返回 undefined（调用方回退到品牌色）。
 */
export function moduleKeyFromPath(path: string): string | undefined {
  const prefix = '/' + (path.split('/')[1] || '')
  return Object.keys(MODULES).find((key) => MODULES[key].prefix === prefix)
}

/** 按模块 key 取视觉标识；未登记返回 undefined */
export function getModule(key?: string): ModuleMeta | undefined {
  return key ? MODULES[key] : undefined
}
