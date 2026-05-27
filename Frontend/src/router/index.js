import { createRouter, createWebHistory } from 'vue-router'
import UploadView from '../views/UploadView.vue'
import BrowseView from '../views/BrowseView.vue'
import GenerateView from '../views/GenerateView.vue'
import ResultView from '../views/ResultView.vue'
import ResultDashboardView from '../views/ResultDashboardView.vue'
import { useAppStore } from '../store'

const routes = [
  {
    path: '/',
    name: 'upload',
    component: UploadView,
  },
  {
    path: '/browse',
    name: 'browse',
    component: BrowseView,
    beforeEnter: (to, from, next) => {
      const store = useAppStore()
      if (!store.projectId) next('/')
      else next()
    }
  },
  {
    path: '/generate',
    name: 'generate',
    component: GenerateView,
    beforeEnter: (to, from, next) => {
      const store = useAppStore()
      if (!store.projectId) next('/')
      else if (!store.functionId) next('/browse')
      else next()
    }
  },
  {
    path: '/result',
    name: 'result',
    component: ResultView,
    beforeEnter: (to, from, next) => {
      const store = useAppStore()
      if (!store.taskId) next('/')
      else next()
    }
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: ResultDashboardView,
    beforeEnter: (to, from, next) => {
      const store = useAppStore()
      if (!store.projectId) next('/')
      else next()
    }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 全局守卫: SPA 路由切换时自动把 portal_project_id 同步到 URL query
// 这样无论刷新页面、复制 URL、跨 tab 打开, URL 自身就保留了当前工程上下文,
// 不再单纯依赖 sessionStorage 兜底.
router.beforeEach((to, from, next) => {
  const store = useAppStore()
  if (store.portalProjectId && !to.query.portal_project_id) {
    next({
      path: to.path,
      query: { ...to.query, portal_project_id: store.portalProjectId },
      replace: true,
    })
    return
  }
  next()
})

export default router
