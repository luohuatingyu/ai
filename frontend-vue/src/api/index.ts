import axios, { AxiosInstance, AxiosRequestConfig, AxiosResponse } from 'axios'

// 创建 axios 实例（训练任务列表等接口可能读取大量任务指标，适当放宽超时）
const request: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
  // 数组参数按重复键序列化（tags=a&tags=b），与 FastAPI 的 list 查询参数约定一致
  paramsSerializer: {
    serialize: (params: Record<string, any>) => {
      const search = new URLSearchParams()
      for (const [key, value] of Object.entries(params || {})) {
        if (value === undefined || value === null || value === '') continue
        if (Array.isArray(value)) {
          for (const item of value) search.append(key, String(item))
        } else {
          search.append(key, String(value))
        }
      }
      return search.toString()
    },
  },
})

// 请求拦截器
request.interceptors.request.use(
  (config) => {
    // 可以添加 token 等认证信息
    const token = localStorage.getItem('token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

/** 从后端错误响应中提取可读的中文错误信息（FastAPI detail 字符串或列表）。 */
const extractErrorMessage = (response?: { status?: number; data?: any }) => {
  if (!response) return '网络错误或服务不可用，请检查后端服务是否启动'
  const detail = response.data?.detail ?? response.data?.message
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && detail.length > 0) {
    // pydantic 校验错误列表：拼接字段位置与错误描述
    const first = detail[0]
    const path = Array.isArray(first.loc) ? first.loc.join('.') : ''
    return `${path ? path + ': ' : ''}${first.msg || '请求参数校验失败'}`
  }
  const statusText: Record<number, string> = {
    400: '请求参数无效',
    401: '登录状态已失效',
    403: '权限不足',
    404: '资源不存在',
    409: '资源状态冲突，请刷新后重试',
    422: '请求参数校验失败',
    500: '服务器内部错误',
    502: '后端服务暂时不可达，请稍后重试',
    503: '依赖服务暂不可用，请稍后重试',
  }
  return statusText[response.status || 0] || `请求失败 (${response.status})`
}

// 响应拦截器：统一解包 data 并把后端 detail 提取到 error.message
request.interceptors.response.use(
  (response: AxiosResponse) => response.data,
  (error) => {
    const response = error?.response
    const message = extractErrorMessage(response)
    if (response?.status === 401) {
      // 未授权：清理凭证后回到入口页（当前部署为内网单用户，无独立登录页）
      localStorage.removeItem('token')
    } else {
      console.error(`[API] ${error?.config?.method?.toUpperCase() || ''} ${error?.config?.url || ''}: ${message}`)
    }
    // 保持 axios 原始结构，同时让 err.message 直接可用于页面提示
    error.message = message
    return Promise.reject(error)
  }
)

export default request

// 封装常用方法（拦截器已解包 response.data，这里统一返回 Promise<any>）
export const api = {
  get: (url: string, params?: any, config?: AxiosRequestConfig): Promise<any> =>
    request.get(url, { params, ...config }) as any,

  post: (url: string, data?: any, config?: AxiosRequestConfig): Promise<any> =>
    request.post(url, data, config) as any,

  put: (url: string, data?: any, config?: AxiosRequestConfig): Promise<any> =>
    request.put(url, data, config) as any,

  patch: (url: string, data?: any, config?: AxiosRequestConfig): Promise<any> =>
    request.patch(url, data, config) as any,

  delete: (url: string, config?: AxiosRequestConfig): Promise<any> =>
    request.delete(url, config) as any,

  // 文件上传（支持同时附带描述/标签等普通表单字段；数组按重复键追加）
  upload: (
    url: string,
    file: File,
    onProgress?: (progress: number) => void,
    fields?: Record<string, any>
  ): Promise<any> => {
    const formData = new FormData()
    formData.append('file', file)
    for (const [key, value] of Object.entries(fields || {})) {
      if (value === undefined || value === null) continue
      if (Array.isArray(value)) {
        for (const item of value) formData.append(key, String(item))
      } else {
        formData.append(key, String(value))
      }
    }
    return request.post(url, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (progressEvent) => {
        if (onProgress && progressEvent.total) {
          onProgress(Math.round((progressEvent.loaded * 100) / progressEvent.total))
        }
      },
    }) as any
  },
}

// 导出原始实例用于特殊需求
export { request }
