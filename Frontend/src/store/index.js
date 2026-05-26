import { defineStore } from 'pinia'

// 从 URL query 读取 portal_project_id（由 UniPortal iframe 跳转时附带）。
// 用 sessionStorage 持久化：SPA 内部 router.push 后 URL query 可能丢失，
// 但同一 iframe 会话内仍需复用此值；跨 tab/窗口各自从自己的 URL 读取。
const _readPortalProjectId = () => {
  const fromUrl = new URLSearchParams(window.location.search).get('portal_project_id')
  if (fromUrl) {
    sessionStorage.setItem('portalProjectId', fromUrl)
    return fromUrl
  }
  return sessionStorage.getItem('portalProjectId') || null
}

export const useAppStore = defineStore('app', {
  state: () => ({
    projectId: localStorage.getItem('projectId') || null,
    projectName: localStorage.getItem('projectName') || null,
    functionId: localStorage.getItem('functionId') || null,
    functionName: localStorage.getItem('functionName') || null,
    taskId: localStorage.getItem('taskId') || null,
    failureContext: null,
    failedTaskId: null,
    portalProjectId: _readPortalProjectId(),
  }),
  actions: {
    setFailureContext(context, taskId = null) {
      this.failureContext = context
      this.failedTaskId = taskId
    },
    setProject(id, name) {
      this.projectId = id
      this.projectName = name
      localStorage.setItem('projectId', id)
      localStorage.setItem('projectName', name)
      // Reset dependent states
      this.setFunction(null, null)
      this.setTask(null)
    },
    setFunction(id, name) {
      this.functionId = id
      this.functionName = name
      if (id) {
        localStorage.setItem('functionId', id)
        localStorage.setItem('functionName', name)
      } else {
        localStorage.removeItem('functionId')
        localStorage.removeItem('functionName')
      }
      // Reset task when function changes
      this.setTask(null)
    },
    setTask(id) {
      this.taskId = id
      if (id) {
        localStorage.setItem('taskId', id)
      } else {
        localStorage.removeItem('taskId')
      }
    }
  }
})
