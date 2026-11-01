import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useAppStore = defineStore('app', () => {
  // 全局加载状态
  const globalLoading = ref(false)
  const loadingText = ref('加载中...')
  
  // 侧边栏折叠状态
  const sidebarCollapsed = ref(false)
  
  // 当前用户
  const user = ref<any>(null)
  
  // 通知消息
  const notifications = ref<Array<{
    id: string
    type: 'success' | 'warning' | 'info' | 'error'
    title: string
    message?: string
    duration?: number
  }>>([])
  
  // WebSocket 连接
  const wsConnections = ref<Map<string, WebSocket>>(new Map())
  
  // Actions
  const setGlobalLoading = (loading: boolean, text?: string) => {
    globalLoading.value = loading
    if (text) loadingText.value = text
  }
  
  const toggleSidebar = () => {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }
  
  const setUser = (userData: any) => {
    user.value = userData
  }
  
  const addNotification = (notification: Omit<typeof notifications.value[0], 'id'>) => {
    const id = Date.now().toString()
    notifications.value.push({ ...notification, id })
    if (notification.duration !== 0) {
      setTimeout(() => removeNotification(id), notification.duration || 4500)
    }
    return id
  }
  
  const removeNotification = (id: string) => {
    const index = notifications.value.findIndex(n => n.id === id)
    if (index > -1) notifications.value.splice(index, 1)
  }
  
  const clearNotifications = () => {
    notifications.value = []
  }
  
  // 成功/错误/警告/信息快捷方法
  const success = (title: string, message?: string, duration?: number) =>
    addNotification({ type: 'success', title, message, duration })
  
  const error = (title: string, message?: string, duration?: number) =>
    addNotification({ type: 'error', title, message, duration: duration || 0 })
  
  const warning = (title: string, message?: string, duration?: number) =>
    addNotification({ type: 'warning', title, message, duration })
  
  const info = (title: string, message?: string, duration?: number) =>
    addNotification({ type: 'info', title, message, duration })
  
  return {
    globalLoading,
    loadingText,
    sidebarCollapsed,
    user,
    notifications,
    wsConnections,
    setGlobalLoading,
    toggleSidebar,
    setUser,
    addNotification,
    removeNotification,
    clearNotifications,
    success,
    error,
    warning,
    info,
  }
})