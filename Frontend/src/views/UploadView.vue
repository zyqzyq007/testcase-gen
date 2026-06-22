<template>
  <div class="h-full overflow-y-auto p-8 bg-white">
    <div class="max-w-4xl mx-auto space-y-8">
      <!-- Header -->
      <div class="text-center space-y-2">
        <h1 class="text-3xl font-bold text-slate-900">开始测试您的项目</h1>
        <p class="text-slate-500 font-medium">支持 C 或 Python 项目，上传源码文件或 .zip 压缩包以开始自动化测试生成</p>
        <p class="text-xs text-slate-400 max-w-2xl mx-auto leading-relaxed">
          上传 Python 项目时需同时上传打包好的运行环境（与源码一起放进 zip）。
          <button
            type="button"
            @click="showEnvGuide = true"
            class="ml-1 text-primary-600 hover:text-primary-700 underline underline-offset-2 font-medium"
          >如何打包 env.tar.gz？</button>
        </p>
      </div>

      <!-- 离线环境打包指引弹窗 -->
      <div
        v-if="showEnvGuide"
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
        @click.self="showEnvGuide = false"
      >
        <div class="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[85vh] overflow-hidden flex flex-col">
          <!-- 弹窗头 -->
          <div class="flex items-center justify-between px-6 py-4 border-b border-slate-200">
            <h2 class="text-lg font-bold text-slate-900">如何打包离线环境 env.tar.gz</h2>
            <button
              type="button"
              @click="showEnvGuide = false"
              class="text-slate-400 hover:text-slate-600 transition-colors"
            >
              <X class="w-5 h-5" />
            </button>
          </div>

          <!-- 弹窗正文 -->
          <div class="px-6 py-5 overflow-y-auto space-y-4 text-sm leading-relaxed text-slate-700">
            <div class="p-3 rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-xs">
              必须用 <code class="px-1 bg-amber-100 rounded">conda pack</code> 打包（不能用 venv 手搓）。环境里要装齐项目全部依赖 + <code class="px-1 bg-amber-100 rounded">pytest/coverage/pytest-cov</code>。
            </div>

            <div>
              <pre class="bg-slate-900 text-slate-100 text-xs rounded-lg p-3 overflow-x-auto"><code># 1. 建环境（Python 版本按项目填）
conda create -y -n myenv python=3.8
conda activate myenv
conda install -y -c conda-forge conda-pack

# 2. 装项目依赖（有 requirements.txt 用它；conda 能装的也可 conda install）
pip install -r requirements.txt

# 3. 必须补装测试框架所需包
pip install pytest coverage pytest-cov

# 4. 打包
conda pack -n myenv -o env.tar.gz --force --ignore-missing-files</code></pre>
            </div>

            <div>
              <p class="font-semibold text-slate-900 mb-1">要点</p>
              <ul class="list-disc list-inside space-y-1 text-xs">
                <li>Python 版本要自己在第 1 步指定（requirements.txt 里通常没有）。</li>
                <li>conda 与 pip 可混用，都装进同一个 conda 环境即可。</li>
                <li>环境装完、无 conda 进程在跑后再打包，避免包内不全。</li>
              </ul>
            </div>

            <div>
              <p class="font-semibold text-slate-900 mb-1">如何上传</p>
              <p class="text-xs text-slate-600">把生成的 <code class="px-1 py-0.5 bg-slate-100 rounded">env.tar.gz</code> 与源码放进同一个 zip 上传；也可直接选整个文件夹上传（自动打包）。</p>
            </div>
          </div>

          <!-- 弹窗底 -->
          <div class="px-6 py-4 border-t border-slate-200 flex justify-end">
            <button
              type="button"
              @click="showEnvGuide = false"
              class="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white text-sm font-medium rounded-lg transition-colors"
            >我知道了</button>
          </div>
        </div>
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
          <input type="file" ref="fileInput" class="hidden" @change="handleFileChange" accept=".c,.h,.py,.zip" />
          <input type="file" ref="folderInput" class="hidden" webkitdirectory directory multiple @change="handleFolderChange" />
          <div v-if="!packing" class="flex flex-col items-center space-y-4">
            <div class="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center group-hover:bg-primary-50 transition-colors">
              <Upload class="w-8 h-8 text-slate-400 group-hover:text-primary-600" />
            </div>
            <div class="space-y-1">
              <p class="text-lg font-bold text-slate-700">点击或拖拽文件至此处上传</p>
              <p class="text-sm text-slate-500">支持 .c, .h, .py、.zip，或上传整个文件夹</p>
            </div>
            <button
              type="button"
              @click.stop="triggerFolderInput"
              class="text-sm text-primary-600 hover:text-primary-700 underline underline-offset-2 font-medium"
            >或选择整个文件夹（自动打包为 zip）</button>
          </div>
          <div v-else class="flex flex-col items-center space-y-3 py-4">
            <RotateCw class="w-8 h-8 text-primary-600 animate-spin" />
            <p class="text-sm font-medium text-slate-700">正在打包文件夹... {{ packProgress }}%</p>
            <p class="text-xs text-slate-400">大文件夹（含离线环境 env.tar.gz）可能需要数十秒</p>
          </div>
        </div>

        <!-- File Selected State -->
        <div v-else class="space-y-6">
          <div class="flex items-center justify-between p-4 bg-white border border-slate-200 rounded-lg">
            <div class="flex items-center space-x-4">
              <div class="w-12 h-12 bg-primary-50 rounded flex items-center justify-center">
                <FileCode class="w-7 h-7 text-primary-600" v-if="selectedFile.name.endsWith('.c') || selectedFile.name.endsWith('.py')" />
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
            :disabled="uploading || batchTesting || !projectName.trim()"
            class="w-full py-3 bg-primary-900 hover:bg-primary-800 disabled:bg-slate-300 text-white font-bold rounded-lg transition-all shadow-md flex items-center justify-center space-x-2"
          >
            <Upload class="w-5 h-5" v-if="!uploading && !batchTesting" />
            <RotateCw class="w-5 h-5 animate-spin" v-else />
            <span>{{ uploading ? '正在上传...' : batchTesting ? '正在全量自动测试...' : '确认上传并开始分析' }}</span>
          </button>
        </div>

        <!-- Batch Testing Options -->
        <div v-if="!uploading && !batchTesting && selectedFile" class="mt-6 p-4 bg-slate-50 border border-slate-200 rounded-lg">
          <div class="flex items-center justify-between mb-2">
            <div class="flex items-center space-x-2">
              <input type="checkbox" v-model="enableBatchTest" id="batch-test" class="w-4 h-4 text-primary-600 rounded border-slate-300 focus:ring-primary-500">
              <label for="batch-test" class="text-sm font-bold text-slate-700 cursor-pointer">上传后自动对必测函数执行全量测试</label>
            </div>
            <div class="flex items-center space-x-2" v-if="enableBatchTest">
              <label class="text-xs text-slate-500 font-medium">并发度:</label>
              <select v-model="concurrency" class="text-xs border-slate-200 rounded py-1 px-2 focus:ring-primary-500 focus:border-primary-500">
                <option :value="2">2</option>
                <option :value="4">4</option>
                <option :value="8">8</option>
                <option :value="16">16</option>
              </select>
            </div>
          </div>
          <p class="text-xs text-slate-500 ml-6">仅对“外部接口（External Linkage）”执行测试，内部静态函数默认为间接测试。</p>
        </div>

        <!-- Batch Testing Progress -->
        <div v-if="batchTesting" class="mt-6 space-y-4">
          <div class="flex justify-between items-center">
            <h3 class="font-bold text-slate-800 flex items-center gap-2">
              <Zap class="w-4 h-4 text-yellow-500" />
              全量自动测试进度
            </h3>
            <span class="text-xs font-mono font-bold text-primary-600">{{ batchStatus.completed }}/{{ batchStatus.total }}</span>
          </div>
          
          <!-- Main Progress Bar -->
          <div class="space-y-1">
             <div class="flex justify-between text-xs text-slate-500">
                <span>总进度</span>
                <span>{{ totalProgress }}%</span>
             </div>
             <div class="w-full bg-slate-200 rounded-full h-2.5 overflow-hidden">
                <div class="bg-primary-600 h-full transition-all duration-500 ease-out" :style="{ width: totalProgress + '%' }"></div>
             </div>
          </div>
          
          <!-- Stages -->
          <div class="grid grid-cols-3 gap-2 text-xs">
            <div class="p-2 bg-slate-100 rounded border border-slate-200" :class="{'border-primary-500 bg-primary-50': batchStage >= 1}">
              <div class="font-bold text-slate-700 mb-1">1. 解析</div>
              <div class="text-slate-500" v-if="batchStage === 0">等待中...</div>
              <div class="text-primary-600 font-medium" v-if="batchStage === 1">解析中...</div>
              <div class="text-green-600 font-medium" v-if="batchStage > 1">完成 ({{ batchStatus.total }}个)</div>
            </div>
             <div class="p-2 bg-slate-100 rounded border border-slate-200" :class="{'border-primary-500 bg-primary-50': batchStage >= 2}">
              <div class="font-bold text-slate-700 mb-1">2. 生成</div>
               <div class="text-slate-500" v-if="batchStage < 2">等待中...</div>
               <div class="text-primary-600 font-medium" v-if="batchStage === 2">{{ batchStatus.generated }}/{{ batchStatus.total }}</div>
               <div class="text-green-600 font-medium" v-if="batchStage > 2">完成</div>
            </div>
             <div class="p-2 bg-slate-100 rounded border border-slate-200" :class="{'border-primary-500 bg-primary-50': batchStage >= 2}">
              <div class="font-bold text-slate-700 mb-1">3. 执行</div>
               <div class="text-slate-500" v-if="batchStage < 2">等待中...</div>
               <div class="text-primary-600 font-medium" v-if="batchStage === 2">{{ batchStatus.executed }}/{{ batchStatus.total }}</div>
               <div class="text-green-600 font-medium" v-if="batchStage > 2">完成</div>
            </div>
          </div>
          
          <!-- Stats -->
          <div class="flex items-center justify-between text-xs bg-slate-50 p-2 rounded border border-slate-200">
             <div class="flex gap-3">
               <span class="text-green-600 font-medium">成功: {{ batchStatus.success }}</span>
               <span class="text-red-500 font-medium">失败: {{ batchStatus.failed }}</span>
               <span class="text-orange-500 font-medium">编译错: {{ batchStatus.compileError }}</span>
             </div>
             <div class="text-slate-400">并发度: {{ concurrency }}</div>
          </div>

           <!-- Warning for high failure rate -->
           <div v-if="batchStatus.compileError > (batchStatus.total * 0.3) && batchStatus.total > 5" class="p-2 bg-yellow-50 border border-yellow-200 rounded text-xs text-yellow-700 flex items-start gap-2">
              <AlertTriangle class="w-4 h-4 shrink-0" />
              <span>检测到较多编译/链接失败，可能缺少外部依赖（如 zlib）。请检查项目完整性。</span>
           </div>
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
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '../store'
import axios from 'axios'
import JSZip from 'jszip'
import { Upload, History, FileCode, Package, ChevronRight, X, RotateCw, Trash2, Zap, AlertTriangle } from 'lucide-vue-next'

const router = useRouter()
const store = useAppStore()

const fileInput = ref(null)
const selectedFile = ref(null)
const showEnvGuide = ref(false)
const projectName = ref('')
const uploading = ref(false)
const uploadProgress = ref(0)
const loading = ref(false)
const projects = ref([])

// Batch Testing State
const enableBatchTest = ref(false)
const concurrency = ref(16)
const batchTesting = ref(false)
const batchStage = ref(0) // 0: Idle, 1: Parsing, 2: Running
const batchStatus = ref({
  total: 0,
  generated: 0,
  executed: 0,
  completed: 0,
  success: 0,
  failed: 0,
  compileError: 0
})

const totalProgress = computed(() => {
  if (batchStage.value === 0) return 0
  if (batchStage.value === 1) return 10
  if (batchStatus.value.total === 0) return 10
  
  // Parse: 10%
  // Generate + Execute: 90%
  const processed = batchStatus.value.completed
  const progress = 10 + Math.floor((processed / batchStatus.value.total) * 90)
  return Math.min(progress, 100)
})

const triggerFileInput = () => fileInput.value.click()
const folderInput = ref(null)
const packing = ref(false)
const packProgress = ref(0)

const triggerFolderInput = () => folderInput.value.click()

const handleFolderChange = async (e) => {
  const files = Array.from(e.target.files || [])
  if (!files.length) return

  packing.value = true
  packProgress.value = 0
  try {
    const zip = new JSZip()
    // webkitRelativePath 形如 "myproject/src/main.py"，保留相对结构（含顶层文件夹名）
    for (const f of files) {
      const rel = f.webkitRelativePath || f.name
      zip.file(rel, f)
    }
    // STORE 模式不压缩：源码体积小可忽略，而 env.tar.gz 等已压缩内容避免重复压缩拖慢打包
    const blob = await zip.generateAsync(
      { type: 'blob', compression: 'STORE' },
      (meta) => { packProgress.value = Math.round(meta.percent) }
    )
    const folderName = (files[0].webkitRelativePath || files[0].name).split('/')[0] || 'project'
    const zipped = new File([blob], `${folderName}.zip`, { type: 'application/zip' })
    prepareUpload(zipped)
  } catch (err) {
    console.error('zip folder failed', err)
    alert('文件夹打包失败，请重试，或改用将文件夹压缩为 zip 后上传')
  } finally {
    packing.value = false
    if (folderInput.value) folderInput.value.value = ''
  }
}

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
    
    const { project_id, project_name, language, test_framework } = response.data
    store.setProject(project_id, project_name, language, test_framework)

    if (enableBatchTest.value) {
       await startBatchTesting(project_id)
    } else {
       router.push('/browse')
    }
  } catch (error) {
    console.error('Upload failed:', error)
    alert('上传失败，请重试')
    uploading.value = false
  }
}

// Queue helper for concurrency control
class TaskQueue {
  constructor(concurrency) {
    this.concurrency = concurrency
    this.running = 0
    this.queue = []
  }

  add(task) {
    return new Promise((resolve, reject) => {
      this.queue.push({ task, resolve, reject })
      this.process()
    })
  }

  async process() {
    if (this.running >= this.concurrency || this.queue.length === 0) return
    
    this.running++
    const { task, resolve, reject } = this.queue.shift()
    
    try {
      const result = await task()
      resolve(result)
    } catch (e) {
      reject(e)
    } finally {
      this.running--
      this.process()
    }
  }
}

const startBatchTesting = async (projectId) => {
  uploading.value = false // Stop upload spinner
  batchTesting.value = true
  batchStage.value = 1 // Parsing
  
  try {
    // 1. Get Test Targets
    const targetsRes = await axios.get(`/api/project/${projectId}/test-targets`)
    const mustTest = targetsRes.data.must_test || []
    
    batchStatus.value.total = mustTest.length
    if (mustTest.length === 0) {
      alert('未找到必测函数（外部接口），即将跳转浏览页。')
      router.push('/browse')
      return
    }

    batchStage.value = 2 // Running
    const queue = new TaskQueue(concurrency.value)
    const promises = []

    // 2. Queue Generate & Execute Tasks
    for (const func of mustTest) {
      const p = queue.add(async () => {
         try {
            // Generate
            const genRes = await axios.post('/api/testcase/generate', {
              project_id: projectId,
              function_id: func.function_id
            })
            batchStatus.value.generated++
            
            const taskId = genRes.data.task_id
            
            // Execute
            const execRes = await axios.post('/api/testcase/execute', { task_id: taskId })
            batchStatus.value.executed++
            
            const result = execRes.data
            if (result.compile_success) {
               if (result.test_result && result.test_result.failed === 0) {
                 batchStatus.value.success++
               } else {
                 batchStatus.value.failed++
               }
            } else {
               batchStatus.value.compileError++
            }
         } catch (e) {
            console.error(`Task failed for ${func.name}:`, e)
            batchStatus.value.failed++
         } finally {
            batchStatus.value.completed++
         }
      })
      promises.push(p)
    }

    await Promise.all(promises)
    
    // 3. Finish
    setTimeout(() => {
       if (confirm(`全量测试完成！\n成功: ${batchStatus.value.success}, 失败: ${batchStatus.value.failed}, 编译错: ${batchStatus.value.compileError}\n是否前往【测试结果总览】查看详情？`)) {
          router.push('/dashboard')
       }
    }, 500)

  } catch (e) {
    console.error('Batch testing error:', e)
    alert('全量测试过程中发生错误')
    uploading.value = false
    batchTesting.value = false
  }
}

const fetchProjects = async () => {
  loading.value = true
  try {
    const params = store.portalProjectId
      ? { portal_project_id: store.portalProjectId }
      : {}
    const response = await axios.get('/api/project/list', { params })
    projects.value = response.data
  } catch (error) {
    console.error('Fetch projects failed:', error)
  } finally {
    loading.value = false
  }
}

const selectProject = (project) => {
  store.setProject(project.project_id, project.project_name, project.language, project.test_framework)
  router.push('/dashboard')
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
