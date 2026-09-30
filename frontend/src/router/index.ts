import { createRouter, createWebHashHistory } from 'vue-router'
import Layout from '../components/Layout.vue'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/Login.vue'),
  },
  {
    path: '/',
    component: Layout,
    redirect: '/dashboard',
    meta: { requiresAuth: true },
    children: [
      { path: 'dashboard', name: 'Dashboard', component: () => import('../views/Dashboard.vue') },
      { path: 'statistics', name: 'Statistics', component: () => import('../views/Statistics.vue') },
      { path: 'chat-config', name: 'ChatConfig', component: () => import('../views/ChatConfig.vue') },
      { path: 'auto-reply', name: 'AutoReply', component: () => import('../views/AutoReply.vue') },
      { path: 'templates', name: 'Templates', component: () => import('../views/Templates.vue') },
      { path: 'workflows', name: 'Workflows', component: () => import('../views/Workflows.vue') },
      { path: 'forward-rules', name: 'ForwardRules', component: () => import('../views/ForwardRules.vue') },
      { path: 'ai-config', name: 'AIConfig', component: () => import('../views/AIConfig.vue') },
      { path: 'persona-skill', name: 'PersonaSkill', component: () => import('../views/PersonaSkill.vue') },
      { path: 'schedule', name: 'ScheduleConfig', component: () => import('../views/ScheduleConfig.vue') },
      { path: 'messages', name: 'MessageLog', component: () => import('../views/MessageLog.vue') },
      { path: 'system-config', name: 'SystemConfig', component: () => import('../views/SystemConfig.vue') },
    ],
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

// 路由守卫：未登录跳转登录页
router.beforeEach((to, _from, next) => {
  const token = localStorage.getItem('token')
  if (to.path !== '/login' && !token) {
    next('/login')
  } else if (to.path === '/login' && token) {
    next('/dashboard')
  } else {
    next()
  }
})

export default router
