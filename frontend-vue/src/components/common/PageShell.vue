<template>
  <div
    class="page-view"
    :style="moduleStyle"
    v-loading="loading"
  >
    <!-- 面包屑：模块 / 当前页 -->
    <el-breadcrumb v-if="showBreadcrumb" class="page-breadcrumb" separator="/">
      <el-breadcrumb-item :to="{ path: '/' }">首页</el-breadcrumb-item>
      <el-breadcrumb-item v-if="module">{{ module }}</el-breadcrumb-item>
      <el-breadcrumb-item>{{ title }}</el-breadcrumb-item>
    </el-breadcrumb>

    <div class="page-header">
      <div class="page-header__main">
        <h1 class="page-title">
          <!-- 模块章：业务域图标 + 强调色底，建立页面级视觉识别 -->
          <span v-if="resolvedModule" class="page-title__chip">
            <el-icon :size="17"><component :is="resolvedModule.icon" /></el-icon>
          </span>
          {{ title }}
        </h1>
        <p v-if="description || $slots.description" class="page-desc">
          <slot name="description">{{ description }}</slot>
        </p>
      </div>
      <div v-if="$slots.actions" class="page-actions">
        <slot name="actions" />
      </div>
    </div>

    <slot />
  </div>
</template>

<script setup lang="ts">
/**
 * 页面壳：统一"面包屑 + 标题 + 描述 + 主操作区 + 内容"结构。
 * 所有业务页面用本组件组织头部，保证 24 个页面的信息层级一致。
 * - 模块识别：按 moduleKey（或路由一级前缀）解析业务域，在标题左侧渲染
 *   模块章，并把模块强调色注入 `--mtp-module-accent` CSS 变量，
 *   区块卡片标题色条等子元素经 CSS 继承自动跟随业务域配色；
 * - 无路由上下文（单元测试）或未登记路径时回退品牌蓝，不渲染模块章。
 */
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { getModule, moduleKeyFromPath } from '@/utils/modules'

const props = withDefaults(
  defineProps<{
    /** 页面标题 */
    title: string
    /** 页面职责描述（副标题） */
    description?: string
    /** 所属模块（面包屑第二级，通常取 route.meta.module） */
    module?: string
    /** 业务域 key（datasets/training/models/pipelines/clusters/workbench/templates），缺省按路由前缀推导 */
    moduleKey?: string
    /** 是否展示面包屑（个别全屏页面可关闭） */
    showBreadcrumb?: boolean
    /** 内容加载遮罩 */
    loading?: boolean
  }>(),
  {
    description: '',
    module: '',
    moduleKey: '',
    showBreadcrumb: true,
    loading: false,
  }
)

// 无路由上下文（单测）时 useRoute 返回 undefined，需容错
let routePath = ''
try {
  routePath = useRoute()?.path || ''
} catch {
  routePath = ''
}

/** 解析当前页所属业务域：显式 moduleKey 优先，其次按路由前缀推导 */
const resolvedModule = computed(() => getModule(props.moduleKey) || getModule(moduleKeyFromPath(routePath)))

/** 模块强调色注入：供区块卡片、筛选栏等后代元素经 CSS 变量继承 */
const moduleStyle = computed(() => {
  if (!resolvedModule.value) return undefined
  return {
    '--mtp-module-accent': resolvedModule.value.accent,
    '--mtp-module-accent-soft': resolvedModule.value.accentSoft,
  }
})
</script>
