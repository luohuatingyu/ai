import axios, { AxiosInstance, AxiosRequestConfig, AxiosResponse } from 'axios'

// 创建 axios 实例
const request: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
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

// 响应拦截器
request.interceptors.response.use(
  (response: AxiosResponse) => {
    return response.data
  },
  (error) => {
    const { response } = error
    if (response) {
      switch (response.status) {
        case 401:
          // 未授权，跳转登录
          localStorage.removeItem('token')
          window.location.href = '/login'
          break
        case 403:
          console.error('权限不足')
          break
        case 404:
          console.error('资源不存在')
          break
        case 500:
          console.error('服务器错误')
          break
        default:
          console.error(`请求失败: ${response.status}`)
      }
    } else {
      console.error('网络错误或服务不可用')
    }
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

  // 文件上传
  upload: (url: string, file: File, onProgress?: (progress: number) => void): Promise<any> => {
    const formData = new FormData()
    formData.append('file', file)
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