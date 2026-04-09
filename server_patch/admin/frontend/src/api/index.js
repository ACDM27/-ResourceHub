import axios from 'axios'

// 管理端独立后端，与学生/教师端分离。
// 线上统一指向 /admin-api，由 Nginx 代理到 8001 端口
const BASE_URL = import.meta.env.VITE_ADMIN_API_URL || 
                 (import.meta.env.MODE === 'development' ? 'http://127.0.0.1:8001' : '/admin-api')

const api = axios.create({ baseURL: BASE_URL })

api.interceptors.request.use(config => {
  const token = localStorage.getItem('admin_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  res => res.data,
  err => {
    if (err.response?.status === 401 || err.response?.status === 403) {
      localStorage.removeItem('admin_token')
      localStorage.removeItem('admin_info')
      window.location.href = '/#/login'
    }
    return Promise.reject(err.response?.data || err)
  }
)

export const login = (data) => api.post('/auth/login', data)
export const refreshToken = () => api.post('/auth/refresh')
export const getDashboardStats = () => api.get('/admin/dashboard/stats')
export const getClasses = () => api.get('/admin/classes')
export const createClass = (data) => api.post('/admin/classes', data)
export const deleteClass = (id) => api.delete(`/admin/classes/${id}`)
export const getUsers = (params) => api.get('/admin/users', { params })
export const getUserMajors = () => api.get('/admin/users/majors')
export const createUser = (data, params) => api.post('/admin/users', data, { params })
export const deleteUser = (id) => api.delete(`/admin/users/${id}`)
export const resetPassword = (id) => api.post(`/admin/users/${id}/reset-password`)
export const importStudents = (file) => {
  const form = new FormData(); form.append('file', file)
  return api.post('/admin/import/students', form, { headers: { 'Content-Type': 'multipart/form-data' } })
}
export const importTeachers = (file) => {
  const form = new FormData(); form.append('file', file)
  return api.post('/admin/import/teachers', form, { headers: { 'Content-Type': 'multipart/form-data' } })
}
export const downloadStudentTemplate = () => `${BASE_URL}/admin/import/template/students`
export const downloadTeacherTemplate = () => `${BASE_URL}/admin/import/template/teachers`
export const downloadProfilesTemplate = () => `${BASE_URL}/admin/import/template/profiles`
export const importProfiles = (file) => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/admin/import/profiles', form, { headers: { 'Content-Type': 'multipart/form-data' } })
}

// Teacher ↔ Class bindings
export const getTeacherClasses = (teacherId) => api.get(`/admin/teacher-classes/${teacherId}`)
export const updateTeacherClasses = (teacherId, data) => api.post(`/admin/teacher-classes/${teacherId}`, data)

// 阳光跑全校看板
export const getSunshineClassStats = () => api.get('/admin/sunshine/class-stats')
