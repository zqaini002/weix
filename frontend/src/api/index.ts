import axios from 'axios'
import { ElMessage } from 'element-plus'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (res) => res,
  (err) => {
    const msg = err.response?.data?.detail || '请求失败'
    ElMessage.error(msg)
    if (err.response?.status === 401) {
      localStorage.removeItem('token')
      window.location.hash = '#/login'
    }
    return Promise.reject(err)
  }
)

// Auth
export const login = (username: string, password: string) =>
  api.post('/auth/login', { username, password })

// Dashboard
export const getDashboard = () => api.get('/dashboard/overview')

// Messages
export const getMessages = (params: any) => api.get('/messages', { params })
export const sendMessage = (data: any) => api.post('/messages/send', data)

// Statistics
export const getRanking = (params: any) => api.get('/statistics/ranking', { params })
export const getTimeline = (params: any) => api.get('/statistics/timeline', { params })
export const getKeywords = (params: any) => api.get('/statistics/keywords', { params })
export const getOverview = () => api.get('/statistics/overview')
export const generateSummary = (params: any) => api.post('/statistics/summary/generate', null, { params })

// Config
export const getChatConfig = () => api.get('/config/chat')
export const updateChatConfig = (data: any) => api.put('/config/chat', data)
export const getAIConfig = () => api.get('/config/ai')
export const updateAIConfig = (data: any) => api.put('/config/ai', data)
export const getPersona = () => api.get('/persona')
export const analyzePersona = (force: boolean = false) =>
  api.post('/persona/analyze', null, { params: { force } })
export const updatePersona = (data: any) => api.put('/persona', data)
export const clearPersona = () => api.delete('/persona')

// Rules
export const getRules = () => api.get('/rules')
export const createRule = (data: any) => api.post('/rules', data)
export const updateRule = (id: number, data: any) => api.put(`/rules/${id}`, data)
export const deleteRule = (id: number) => api.delete(`/rules/${id}`)

// Templates
export const getTemplates = () => api.get('/templates')
export const createTemplate = (data: any) => api.post('/templates', data)
export const updateTemplate = (id: number, data: any) => api.put(`/templates/${id}`, data)
export const deleteTemplate = (id: number) => api.delete(`/templates/${id}`)

// Workflows
export const getWorkflows = () => api.get('/workflows')
export const createWorkflow = (data: any) => api.post('/workflows', data)
export const updateWorkflow = (id: number, data: any) => api.put(`/workflows/${id}`, data)
export const deleteWorkflow = (id: number) => api.delete(`/workflows/${id}`)

// Forward Rules
export const getForwardRules = () => api.get('/forward-rules')
export const createForwardRule = (data: any) => api.post('/forward-rules', data)
export const updateForwardRule = (id: number, data: any) => api.put(`/forward-rules/${id}`, data)
export const deleteForwardRule = (id: number) => api.delete(`/forward-rules/${id}`)

// Scheduler
export const getJobs = () => api.get('/scheduler/jobs')
export const updateJob = (id: string, data: any) => api.put(`/scheduler/jobs/${id}`, data)
export const triggerJob = (id: string) => api.post(`/scheduler/jobs/${id}/trigger`)

// System Config
export const getSystemConfig = () => api.get('/system-config')
export const updateSystemConfig = (data: any) => api.put('/system-config', data)

// Platform
export const getContacts = (type: string = 'all') => api.get('/platform/contacts', { params: { type } })
export const searchChatrooms = (keyword: string) => api.get('/platform/contacts', { params: { type: 'chatrooms', search: keyword } })
export const searchContactsApi = (keyword: string) => api.get('/platform/contacts', { params: { type: 'contacts', search: keyword } })
export const getPlatformStatus = () => api.get('/platform/status')

// Health
export const getHealth = () => api.get('/health')

export default api
