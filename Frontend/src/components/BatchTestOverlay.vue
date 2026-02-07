<template>
  <div v-if="visible" class="fixed inset-0 bg-slate-900/50 flex items-center justify-center z-50 backdrop-blur-sm">
    <div class="bg-white rounded-xl shadow-2xl w-full max-w-2xl p-6 space-y-6">
      <div class="flex justify-between items-center">
        <h3 class="font-bold text-slate-800 text-lg flex items-center gap-2">
          <Zap class="w-5 h-5 text-yellow-500" />
          全量自动测试进度
        </h3>
        <button v-if="isFinished" @click="close" class="p-1 hover:bg-slate-100 rounded-full">
          <X class="w-5 h-5 text-slate-400" />
        </button>
      </div>

      <!-- Main Progress Bar -->
      <div class="space-y-2">
         <div class="flex justify-between text-sm text-slate-600 font-bold">
            <span>总进度</span>
            <span>{{ totalProgress }}%</span>
         </div>
         <div class="w-full bg-slate-200 rounded-full h-3 overflow-hidden">
            <div class="bg-primary-600 h-full transition-all duration-500 ease-out" :style="{ width: totalProgress + '%' }"></div>
         </div>
         <div class="text-xs text-slate-400 text-right font-mono">{{ batchStatus.completed }}/{{ batchStatus.total }} 任务</div>
      </div>
      
      <!-- Stages -->
      <div class="grid grid-cols-3 gap-3 text-sm">
        <div class="p-3 bg-slate-50 rounded-lg border border-slate-200 transition-colors" :class="{'border-primary-500 bg-primary-50': batchStage >= 1}">
          <div class="font-bold text-slate-700 mb-1">1. 解析</div>
          <div class="text-slate-500 text-xs" v-if="batchStage === 0">等待中...</div>
          <div class="text-primary-600 text-xs font-bold animate-pulse" v-if="batchStage === 1">正在解析函数列表...</div>
          <div class="text-green-600 text-xs font-bold" v-if="batchStage > 1">完成 ({{ batchStatus.total }}个必测函数)</div>
        </div>
         <div class="p-3 bg-slate-50 rounded-lg border border-slate-200 transition-colors" :class="{'border-primary-500 bg-primary-50': batchStage >= 2}">
          <div class="font-bold text-slate-700 mb-1">2. 生成</div>
           <div class="text-slate-500 text-xs" v-if="batchStage < 2">等待中...</div>
           <div class="text-primary-600 text-xs font-bold" v-if="batchStage === 2">生成中 {{ batchStatus.generated }}/{{ batchStatus.total }}</div>
           <div class="text-green-600 text-xs font-bold" v-if="batchStage > 2">完成</div>
        </div>
         <div class="p-3 bg-slate-50 rounded-lg border border-slate-200 transition-colors" :class="{'border-primary-500 bg-primary-50': batchStage >= 2}">
          <div class="font-bold text-slate-700 mb-1">3. 执行</div>
           <div class="text-slate-500 text-xs" v-if="batchStage < 2">等待中...</div>
           <div class="text-primary-600 text-xs font-bold" v-if="batchStage === 2">执行中 {{ batchStatus.executed }}/{{ batchStatus.total }}</div>
           <div class="text-green-600 text-xs font-bold" v-if="batchStage > 2">完成</div>
        </div>
      </div>
      
      <!-- Stats -->
      <div class="flex items-center justify-between text-sm bg-slate-50 p-4 rounded-lg border border-slate-200">
         <div class="flex gap-4">
           <span class="text-green-600 font-bold flex items-center gap-1"><CheckCircle2 class="w-4 h-4"/> 成功: {{ batchStatus.success }}</span>
           <span class="text-red-500 font-bold flex items-center gap-1"><XCircle class="w-4 h-4"/> 失败: {{ batchStatus.failed }}</span>
           <span class="text-orange-500 font-bold flex items-center gap-1"><AlertTriangle class="w-4 h-4"/> 编译错: {{ batchStatus.compileError }}</span>
         </div>
         <div class="text-slate-400 font-mono text-xs">并发度: {{ concurrency }}</div>
      </div>

      <!-- Warning -->
       <div v-if="batchStatus.compileError > (batchStatus.total * 0.3) && batchStatus.total > 5" class="p-3 bg-yellow-50 border border-yellow-200 rounded-lg text-xs text-yellow-700 flex items-start gap-2">
          <AlertTriangle class="w-4 h-4 shrink-0 mt-0.5" />
          <span>检测到较多编译/链接失败 (>30%)，建议检查项目是否缺少依赖（如 zlib/openssl）。</span>
       </div>

       <!-- Actions -->
       <div v-if="isFinished" class="flex justify-end gap-3 pt-2">
          <button @click="close" class="px-4 py-2 text-slate-600 hover:bg-slate-100 font-bold rounded-lg transition-colors">
            关闭
          </button>
          <button @click="goToDashboard" class="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white font-bold rounded-lg transition-colors shadow-lg flex items-center gap-2">
            <LayoutDashboard class="w-4 h-4" />
            前往结果总览
          </button>
       </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import axios from 'axios'
import { Zap, X, CheckCircle2, XCircle, AlertTriangle, LayoutDashboard } from 'lucide-vue-next'

const props = defineProps({
  projectId: String,
  concurrency: {
    type: Number,
    default: 4
  }
})

const emit = defineEmits(['close'])
const router = useRouter()

const visible = ref(false)
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

const isFinished = computed(() => batchStage.value > 2 || (batchStage.value === 2 && batchStatus.value.completed === batchStatus.value.total && batchStatus.value.total > 0))

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

// Task Queue Logic
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

const start = async () => {
  if (!props.projectId) return
  visible.value = true
  batchStage.value = 1
  
  // Reset stats
  batchStatus.value = {
    total: 0, generated: 0, executed: 0, completed: 0,
    success: 0, failed: 0, compileError: 0
  }

  try {
    // 1. Get Test Targets
    const targetsRes = await axios.get(`/api/project/${props.projectId}/test-targets`)
    const mustTest = targetsRes.data.must_test || []
    
    batchStatus.value.total = mustTest.length
    if (mustTest.length === 0) {
      alert('未找到必测函数（外部接口）。')
      visible.value = false
      return
    }

    batchStage.value = 2 // Running
    const queue = new TaskQueue(props.concurrency)
    const promises = []

    // 2. Queue Generate & Execute Tasks
    for (const func of mustTest) {
      const p = queue.add(async () => {
         try {
            // Generate
            const genRes = await axios.post('/api/testcase/generate', {
              project_id: props.projectId,
              function_id: func.function_id,
              test_framework: 'unity'
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
    batchStage.value = 3 // Done

  } catch (e) {
    console.error('Batch testing error:', e)
    alert('全量测试过程中发生错误')
    visible.value = false
  }
}

const close = () => {
  visible.value = false
  emit('close')
}

const goToDashboard = () => {
  close()
  router.push('/dashboard')
}

defineExpose({ start })
</script>
