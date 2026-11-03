<template>
  <el-config-provider :locale="zhCn">
    <div class="app-shell">
      <!-- 顶栏：品牌 / 环境标识 / 通知 / 用户 -->
      <header class="app-header">
        <button
          class="app-header__toggle"
          type="button"
          :aria-label="isMobile ? '打开导航菜单' : (isCollapsed ? '展开菜单' : '收起菜单')"
          @click="onToggleNav"
        >
          <el-icon :size="18"><component :is="toggleIcon" /></el-icon>
        </button>

        <div class="app-header__brand">
          <div class="app-header__logo">
            <el-icon><Monitor /></el-icon>
          </div>
          <div>
            <span class="app-header__name">MTP 模型训练平台</span>
            <span class="app-header__sub">Model Training Platform</span>
          </div>
        </div>

        <div class="app-header__spacer" />

        <div class="app-header__actions">
          <!-- 通知中心：展示 pinia 通知队列，支持一键清空 -->
          <el-popover placement="bottom-end" :width="320" trigger="click">
            <template #reference>
              <el-badge :value="unreadCount" :hidden="unreadCount === 0" :max="99">
                <el-button text :aria-label="`通知（${unreadCount} 条未读）`">
                  <el-icon :size="18"><Bell /></el-icon>
                </el-button>
              </el-badge>
            </template>
            <div class="notify-panel">
              <div class="notify-panel__head">
                <span>通知</span>
                <el-button v-if="appStore.notifications.length" link size="small" @click="appStore.clearNotifications">
                  全部清空
                </el-button>
              </div>
              <div v-if="appStore.notifications.length === 0" class="notify-panel__empty">暂无通知</div>
              <div
                v-for="item in appStore.notifications"
                :key="item.id"
                class="notify-panel__item"
                :class="`is-${item.type}`"
              >
                <div class="notify-panel__title">{{ item.title }}</div>
                <div v-if="item.message" class="notify-panel__msg">{{ item.message }}</div>
              </div>
            </div>
          </el-popover>

          <!-- 环境标识：当前为内网单用户部署，暂无独立认证体系 -->
          <el-tag effect="plain" size="small" class="env-tag">内网环境</el-tag>

          <el-dropdown trigger="click">
            <span class="user-entry" role="button" tabindex="0">
              <span class="user-entry__avatar"><el-icon><User /></el-icon></span>
              <span class="user-entry__name">Admin</span>
              <el-icon class="user-entry__caret"><ArrowDown /></el-icon>
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
      </header>

      <div class="app-main-wrap">
        <!-- 桌面侧栏 -->
        <aside class="app-aside" :class="{ 'is-collapsed': isCollapsed }">
          <AppNav :collapse="isCollapsed" />
          <div class="app-aside__footer">
            <el-icon :size="12"><InfoFilled /></el-icon>
            <span v-if="!isCollapsed">MTP v2.0 · Vue 3 + FastAPI</span>
          </div>
        </aside>

        <!-- 主内容区 -->
        <main class="app-main">
          <router-view v-slot="{ Component }">
            <transition name="page-fade" mode="out-in">
              <component :is="Component" />
            </transition>
          </router-view>
        </main>
      </div>

      <!-- 移动端抽屉导航 -->
      <el-drawer
        v-model="mobileNavOpen"
        direction="ltr"
        size="248px"
        :with-header="false"
        class="mobile-nav-drawer"
      >
        <div class="mobile-nav-drawer__brand">
          <div class="app-header__logo">
            <el-icon><Monitor /></el-icon>
          </div>
          <span class="app-header__name">MTP 模型训练平台</span>
        </div>
        <AppNav />
      </el-drawer>
    </div>
  </el-config-provider>
</template>

<script setup lang="ts">
/**
 * 应用框架：顶栏（品牌、通知、环境标识、用户）、侧栏（分组导航、可折叠）、
 * 主内容区与移动端抽屉导航。
 * - 通知：pinia 通知队列同时以右上角通知与铃铛面板两种形式呈现；
 * - 折叠：桌面端切换侧栏宽度并持久化；移动端（≤992px）改为抽屉；
 * - 身份：内网单用户部署，暂无认证体系，仅保留本地凭证清理入口。
 */
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { zhCn } from 'element-plus/es/locales.mjs'
import { ElNotification } from 'element-plus'
import { useAppStore } from '@/stores/app'
import AppNav from '@/components/AppNav.vue'
import {
  Monitor, User, ArrowDown, SwitchButton, Bell, Fold, Expand, Menu as MenuIcon, InfoFilled,
} from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()

/** 侧栏折叠状态（持久化到 localStorage） */
const COLLAPSE_KEY = 'mtp.sidebar-collapsed'
const isCollapsed = ref(localStorage.getItem(COLLAPSE_KEY) === '1')
watch(isCollapsed, (value) => {
  localStorage.setItem(COLLAPSE_KEY, value ? '1' : '0')
})

/** 移动端判定（≤992px 使用抽屉导航） */
const mobileQuery = window.matchMedia('(max-width: 992px)')
const isMobile = ref(mobileQuery.matches)
const mobileNavOpen = ref(false)
const onMediaChange = (event: MediaQueryListEvent) => {
  isMobile.value = event.matches
  if (!event.matches) mobileNavOpen.value = false
}

onMounted(() => {
  mobileQuery.addEventListener('change', onMediaChange)
})

onBeforeUnmount(() => {
  mobileQuery.removeEventListener('change', onMediaChange)
})

/** 顶栏汉堡按钮：移动端打开抽屉，桌面端切换折叠 */
const onToggleNav = () => {
  if (isMobile.value) {
    mobileNavOpen.value = true
  } else {
    isCollapsed.value = !isCollapsed.value
  }
}

const toggleIcon = computed(() => {
  if (isMobile.value) return MenuIcon
  return isCollapsed.value ? Expand : Fold
})

/** 未读通知数（错误类通知始终保留，视为未读） */
const unreadCount = computed(() => appStore.notifications.length)

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
/* 顶栏 */
.app-header__toggle {
  width: 34px;
  height: 34px;
  border: none;
  background: transparent;
  border-radius: 8px;
  color: var(--mtp-ink-500);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.app-header__toggle:hover {
  background: var(--mtp-bg-subtle);
  color: var(--mtp-brand-600);
}

.app-header__sub {
  display: none;
}

@media (min-width: 993px) {
  .app-header__sub {
    display: block;
  }
}

.env-tag {
  color: var(--mtp-ink-500);
  border-color: var(--mtp-ink-200);
}

.user-entry {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  border-radius: 8px;
  cursor: pointer;
  color: var(--mtp-ink-700);
  outline: none;
}

.user-entry:hover,
.user-entry:focus-visible {
  background: var(--mtp-bg-subtle);
}

.user-entry__avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--mtp-brand-100);
  color: var(--mtp-brand-600);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
}

.user-entry__name {
  font-size: 13px;
  font-weight: 500;
}

.user-entry__caret {
  font-size: 12px;
  color: var(--mtp-ink-400);
}

/* 通知面板 */
.notify-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
  color: var(--mtp-ink-900);
  padding-bottom: 8px;
  border-bottom: 1px solid var(--mtp-ink-100);
}

.notify-panel__empty {
  padding: 24px 0;
  text-align: center;
  color: var(--mtp-ink-400);
  font-size: 13px;
}

.notify-panel__item {
  padding: 10px 4px;
  border-bottom: 1px dashed var(--mtp-ink-100);
}

.notify-panel__item:last-child {
  border-bottom: none;
}

.notify-panel__title {
  font-size: 13px;
  font-weight: 500;
  color: var(--mtp-ink-900);
}

.notify-panel__item.is-error .notify-panel__title {
  color: var(--mtp-danger);
}

.notify-panel__item.is-warning .notify-panel__title {
  color: var(--mtp-warning);
}

.notify-panel__item.is-success .notify-panel__title {
  color: var(--mtp-success);
}

.notify-panel__msg {
  font-size: 12px;
  color: var(--mtp-ink-500);
  margin-top: 2px;
  word-break: break-all;
}

/* 移动端抽屉导航 */
.mobile-nav-drawer__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 8px 16px;
  color: var(--mtp-ink-900);
}

.mobile-nav-drawer :deep(.el-menu) {
  border-right: none;
}

/* 桌面端隐藏抽屉入口语义：小屏时侧栏由 CSS 收起 */
@media (max-width: 992px) {
  .app-aside {
    display: none;
  }
}

/* 页面切换过渡（prefers-reduced-motion 已在全局降级） */
.page-fade-enter-active,
.page-fade-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.page-fade-enter-from {
  opacity: 0;
  transform: translateY(4px);
}

.page-fade-leave-to {
  opacity: 0;
}
</style>
