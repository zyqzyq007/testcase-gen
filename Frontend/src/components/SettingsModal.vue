<template>
  <div
    v-if="visible"
    class="fixed inset-0 bg-slate-900/50 flex items-center justify-center z-50 backdrop-blur-sm"
    @click.self="close"
  >
    <div class="bg-white rounded-xl shadow-2xl w-full max-w-xl p-6 space-y-5">
      <!-- Header -->
      <div class="flex justify-between items-center">
        <h3 class="font-bold text-slate-800 text-lg flex items-center gap-2">
          <Settings class="w-5 h-5 text-primary-600" />
          LLM 接入配置
        </h3>
        <button
          @click="close"
          class="p-1 hover:bg-slate-100 rounded-full transition-colors"
        >
          <X class="w-5 h-5 text-slate-400" />
        </button>
      </div>

      <p class="text-xs text-slate-500 leading-relaxed">
        所有兼容 OpenAI <code class="bg-slate-100 px-1 rounded">/chat/completions</code> 协议的服务都可使用
      </p>

      <!-- Loading state -->
      <div v-if="loading" class="flex justify-center py-8">
        <Loader2 class="w-6 h-6 text-primary-600 animate-spin" />
      </div>

      <!-- Form -->
      <div v-else class="space-y-4">
        <div>
          <label class="block text-sm font-medium text-slate-700 mb-1">
            Base URL
          </label>
          <input
            v-model="form.base_url"
            type="text"
            placeholder="https://api.deepseek.com"
            class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm font-mono focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
          />
          <p class="text-xs text-slate-400 mt-1">
            阿里百炼私有版示例：<code>https://&lt;your-host&gt;/compatible-mode/v1</code>
          </p>
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-700 mb-1">
            API Key
          </label>
          <div class="relative">
            <input
              v-model="form.api_key"
              :type="showKey ? 'text' : 'password'"
              placeholder="sk-..."
              class="w-full px-3 py-2 pr-20 border border-slate-300 rounded-lg text-sm font-mono focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            />
            <button
              type="button"
              @click="showKey = !showKey"
              class="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-slate-500 hover:text-slate-800 px-2 py-1 rounded"
            >
              {{ showKey ? '隐藏' : '显示' }}
            </button>
          </div>
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-700 mb-1">
            Model
          </label>
          <input
            v-model="form.model"
            type="text"
            placeholder="deepseek-chat"
            class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm font-mono focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
          />
          <p class="text-xs text-slate-400 mt-1">
            常见值：<code>deepseek-chat</code> / <code>gpt-4o-mini</code> / <code>qwen-max</code>
          </p>
        </div>
      </div>

      <!-- Test result -->
      <div
        v-if="testResult"
        class="text-sm p-3 rounded-lg border"
        :class="testResult.ok
          ? 'bg-green-50 border-green-200 text-green-700'
          : 'bg-red-50 border-red-200 text-red-700'"
      >
        <div class="flex items-start gap-2">
          <CheckCircle2 v-if="testResult.ok" class="w-4 h-4 mt-0.5 flex-shrink-0" />
          <XCircle v-else class="w-4 h-4 mt-0.5 flex-shrink-0" />
          <div class="flex-1 break-all">
            <div v-if="testResult.ok">
              连接成功 — model: <code>{{ testResult.model }}</code>
              <span v-if="testResult.reply"> · 返回: <code>{{ testResult.reply }}</code></span>
            </div>
            <div v-else>{{ testResult.error || '连接失败' }}</div>
          </div>
        </div>
      </div>

      <!-- Footer buttons -->
      <div class="flex justify-between pt-2 border-t border-slate-100">
        <button
          @click="testConnection"
          :disabled="loading || saving || testing"
          class="px-4 py-2 text-sm font-medium text-slate-700 bg-slate-100 hover:bg-slate-200 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
        >
          <Loader2 v-if="testing" class="w-4 h-4 animate-spin" />
          <Zap v-else class="w-4 h-4" />
          测试连接
        </button>

        <div class="flex gap-2">
          <button
            @click="close"
            class="px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 rounded-lg transition-colors"
          >
            取消
          </button>
          <button
            @click="save"
            :disabled="loading || saving"
            class="px-4 py-2 text-sm font-medium text-white bg-primary-600 hover:bg-primary-700 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            <Loader2 v-if="saving" class="w-4 h-4 animate-spin" />
            <Save v-else class="w-4 h-4" />
            保存
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import axios from 'axios'
import { Settings, X, Save, Loader2, CheckCircle2, XCircle, Zap } from 'lucide-vue-next'

const props = defineProps({
  visible: { type: Boolean, default: false }
})
const emit = defineEmits(['close', 'saved'])

const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const showKey = ref(false)
const testResult = ref(null)

const form = ref({
  api_key: '',
  base_url: '',
  model: '',
})

async function loadConfig() {
  loading.value = true
  testResult.value = null
  try {
    const { data } = await axios.get('/api/config/llm')
    form.value = {
      api_key: data.api_key || '',
      base_url: data.base_url || '',
      model: data.model || '',
    }
  } catch (e) {
    testResult.value = { ok: false, error: '加载当前配置失败: ' + (e.message || e) }
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  testResult.value = null
  try {
    const payload = {
      api_key: form.value.api_key,
      base_url: form.value.base_url,
      model: form.value.model,
    }
    await axios.put('/api/config/llm', payload)
    emit('saved')
    close()
  } catch (e) {
    testResult.value = {
      ok: false,
      error: '保存失败: ' + (e.response?.data?.detail || e.message || e)
    }
  } finally {
    saving.value = false
  }
}

async function testConnection() {
  testing.value = true
  testResult.value = null
  try {
    // Save current form first so the probe uses the values in the inputs,
    // not whatever was last persisted.
    await axios.put('/api/config/llm', {
      api_key: form.value.api_key,
      base_url: form.value.base_url,
      model: form.value.model,
    })
    const { data } = await axios.post('/api/config/llm/test')
    testResult.value = data
  } catch (e) {
    testResult.value = {
      ok: false,
      error: e.response?.data?.detail || e.message || String(e)
    }
  } finally {
    testing.value = false
  }
}

function close() {
  emit('close')
}

watch(
  () => props.visible,
  (v) => {
    if (v) {
      loadConfig()
    } else {
      testResult.value = null
      showKey.value = false
    }
  }
)
</script>
