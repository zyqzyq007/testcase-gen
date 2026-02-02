<template>
  <div class="h-full overflow-y-auto p-8 bg-white">
    <div class="max-w-4xl mx-auto space-y-8">
      <!-- Header -->
      <div class="text-center space-y-2">
        <h1 class="text-3xl font-bold text-slate-900">开始测试您的 C 项目</h1>
        <p class="text-slate-500 font-medium">上传 .c 单文件或 .zip 压缩包以开始自动化单元测试生成</p>
      </div>

      <!-- Upload Card -->
      <div class="bg-slate-50 border border-slate-200 rounded-xl p-8 shadow-sm">
        <div 
          v-if="!selectedFile"
          class="border-2 border-dashed border-slate-300 rounded-lg p-12 text-center hover:border-primary-600 transition-colors cursor-pointer group bg-white"
          @click="triggerFileInput"
          @dragover.prevent
          @drop.prevent="handleDrop"
        >
          <input type="file" ref="fileInput" class="hidden" @change="handleFileChange" accept=".c,.h,.zip" />
          <div class="flex flex-col items-center space-y-4">
            <div class="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center group-hover:bg-primary-50 transition-colors">
              <Upload class="w-8 h-8 text-slate-400 group-hover:text-primary-600" />
            </div>
            <div class="space-y-1">
              <p class="text-lg font-bold text-slate-700">点击或拖拽文件至此处上传</p>
              <p class="text-sm text-slate-500">支持 .c, .h 或包含项目的 .zip 文件</p>
            </div>
          </div>
        </div>

        <!-- File Selected State -->
        <div v-else class="space-y-6">
          <div class="flex items-center justify-between p-4 bg-white border border-slate-200 rounded-lg">
            <div class="flex items-center space-x-4">
              <div class="w-12 h-12 bg-primary-50 rounded flex items-center justify-center">
                <FileCode class="w-7 h-7 text-primary-600" v-if="selectedFile.name.endsWith('.c')" />
                <Package class="w-7 h-7 text-primary-600" v-else />
              </div>
              <div>
                <p class="text-sm font-bold text-slate-700">{{ selectedFile.name }}</p>
                <p class="text-xs text-slate-500">{{ (selectedFile.size / 1024).toFixed(2) }} KB</p>
              </div>
            </div>
            <button @click="cancelSelection" class="text-slate-400 hover:text-red-500 transition-colors" v-if="!uploading">
              <X class="w-5 h-5" />
            </button>
          </div>

          <div class="space-y-2">
            <label class="text-sm font-bold text-slate-700">项目名称</label>
            <input 
              v-model="projectName" 
              type="text" 
              class="w-full px-4 py-2 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-all"
              placeholder="请输入项目名称"
              :disabled="uploading"
            />
          </div>

          <button 
            @click="uploadFile" 
            :disabled="uploading || !projectName.trim()"
            class="w-full py-3 bg-primary-900 hover:bg-primary-800 disabled:bg-slate-300 text-white font-bold rounded-lg transition-all shadow-md flex items-center justify-center space-x-2"
          >
            <Upload class="w-5 h-5" v-if="!uploading" />
            <RotateCw class="w-5 h-5 animate-spin" v-else />
            <span>{{ uploading ? '正在上传...' : '确认上传并开始分析' }}</span>
          </button>
        </div>

        <div v-if="uploading" class="mt-6 space-y-2">
          <div class="flex justify-between text-sm mb-1 font-medium text-slate-600">
            <span>正在上传并分析项目...</span>
            <span>{{ uploadProgress }}%</span>
          </div>
          <div class="w-full bg-slate-200 rounded-full h-2">
            <div class="bg-primary-600 h-2 rounded-full transition-all duration-300" :style="{ width: uploadProgress + '%' }"></div>
          </div>
        </div>
      </div>

      <!-- Project List Section -->
      <div class="space-y-4">
        <div class="flex items-center justify-between border-b border-slate-100 pb-2">
          <h2 class="text-xl font-bold flex items-center gap-2 text-slate-800">
            <History class="w-5 h-5 text-primary-600" />
            已有项目
          </h2>
          <button @click="fetchProjects" class="text-sm font-semibold text-primary-600 hover:text-primary-700">刷新列表</button>
        </div>

        <div v-if="loading" class="text-center py-12">
          <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600 mx-auto"></div>
        </div>

        <div v-else-if="projects.length === 0" class="bg-slate-50 border border-slate-200 rounded-lg p-12 text-center">
          <p class="text-slate-400 font-medium">暂无项目，请先上传一个</p>
        </div>

        <div v-else class="grid grid-cols-1 gap-4">
          <div 
            v-for="project in projects" 
            :key="project.project_id"
            class="bg-white border border-slate-200 rounded-lg p-4 flex items-center justify-between hover:border-primary-600 hover:shadow-md transition-all cursor-pointer group"
            @click="selectProject(project)"
          >
            <div class="flex items-center space-x-4">
              <div class="w-10 h-10 bg-slate-50 rounded flex items-center justify-center">
                <FileCode class="w-6 h-6 text-primary-600" v-if="project.project_name.endsWith('.c')" />
                <Package class="w-6 h-6 text-primary-600" v-else />
              </div>
              <div>
                <h3 class="font-bold text-slate-800 group-hover:text-primary-600 transition-colors">{{ project.project_name }}</h3>
                <p class="text-xs font-medium text-slate-500">{{ project.project_id }} • {{ project.file_count }} 个文件</p>
              </div>
            </div>
            <div class="flex items-center space-x-4">
              <span class="px-2 py-1 bg-green-100 text-green-700 text-[10px] uppercase font-bold rounded border border-green-200">
                {{ project.status }}
              </span>
              <button 
                @click="(e) => deleteProject(e, project)" 
                class="p-1 text-slate-400 hover:text-red-600 hover:bg-red-50 rounded transition-all"
                title="删除项目"
              >
                <Trash2 class="w-4 h-4" />
              </button>
              <ChevronRight class="w-5 h-5 text-slate-300 group-hover:text-primary-600 transition-colors" />
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '../store'
import axios from 'axios'
import { Upload, History, FileCode, Package, ChevronRight, X, RotateCw, Trash2 } from 'lucide-vue-next'

const router = useRouter()
const store = useAppStore()

const fileInput = ref(null)
const selectedFile = ref(null)
const projectName = ref('')
const uploading = ref(false)
const uploadProgress = ref(0)
const loading = ref(false)
const projects = ref([])

const triggerFileInput = () => fileInput.value.click()

const handleFileChange = (e) => {
  const file = e.target.files[0]
  if (file) prepareUpload(file)
}

const handleDrop = (e) => {
  const file = e.dataTransfer.files[0]
  if (file) prepareUpload(file)
}

const prepareUpload = (file) => {
  selectedFile.value = file
  projectName.value = file.name.split('.')[0]
}

const cancelSelection = () => {
  selectedFile.value = null
  projectName.value = ''
  if (fileInput.value) fileInput.value.value = ''
}

const uploadFile = async () => {
  if (!selectedFile.value) return

  const formData = new FormData()
  formData.append('file', selectedFile.value)
  formData.append('project_name', projectName.value)

  uploading.value = true
  uploadProgress.value = 0

  try {
    const response = await axios.post('/api/project/upload', formData, {
      onUploadProgress: (progressEvent) => {
        uploadProgress.value = Math.round((progressEvent.loaded * 100) / progressEvent.total)
      }
    })
    
    const { project_id, project_name } = response.data
    store.setProject(project_id, project_name)
    router.push('/browse')
  } catch (error) {
    console.error('Upload failed:', error)
    alert('上传失败，请重试')
  } finally {
    uploading.value = false
  }
}

const fetchProjects = async () => {
  loading.value = true
  try {
    const response = await axios.get('/api/project/list')
    projects.value = response.data
  } catch (error) {
    console.error('Fetch projects failed:', error)
  } finally {
    loading.value = false
  }
}

const selectProject = (project) => {
  store.setProject(project.project_id, project.project_name)
  router.push('/browse')
}

const deleteProject = async (e, project) => {
  e.stopPropagation() // Prevent triggering selectProject
  if (!confirm(`确定要删除项目 "${project.project_name}" 吗？此操作不可恢复。`)) {
    return
  }
  
  try {
    await axios.delete(`/api/project/${project.project_id}`)
    await fetchProjects() // Refresh list
  } catch (error) {
    console.error('Delete project failed:', error)
    alert('删除失败，请重试')
  }
}

onMounted(() => {
  fetchProjects()
})
</script>
