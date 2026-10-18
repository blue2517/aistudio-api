<script setup lang="ts">
import { ref, watch } from 'vue'
import { Clock, Save } from 'lucide-vue-next'
import { systemApi } from '@/api/system.ts'
import { useToastStore } from '@/stores/toast.ts'
import type { SystemConfig } from '@/types/system.ts'
import Button from '@/components/ui/Button.vue'

const props = defineProps<{ initialConfig: SystemConfig }>()
const emit = defineEmits<{ saved: [] }>()
const toast = useToastStore()
const streamSeconds = ref(props.initialConfig.timeout_stream)
const replaySeconds = ref(props.initialConfig.timeout_replay)
const saving = ref(false)

watch(() => props.initialConfig, (config) => {
  streamSeconds.value = config.timeout_stream
  replaySeconds.value = config.timeout_replay
})

async function handleSave() {
  const values = [streamSeconds.value, replaySeconds.value]
  if (values.some((value) => !Number.isInteger(value) || value < 1 || value > 86400)) {
    toast.error('超时时间请输入 1 至 86400 的整数秒')
    return
  }
  saving.value = true
  try {
    await systemApi.updateRequestTimeouts({
      timeout_stream: streamSeconds.value,
      timeout_replay: replaySeconds.value,
    })
    toast.success('请求超时配置已保存，后续请求立即生效')
    emit('saved')
  } catch (err: unknown) {
    toast.error(err instanceof Error ? err.message : '保存请求超时配置失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <form
    class="bg-white border border-gray-200/80 rounded-2xl p-6 shadow-xs space-y-5"
    @submit.prevent="handleSave"
  >
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-4">
      <h3 class="font-semibold text-gray-900 text-sm flex items-center gap-2">
        <Clock class="w-5 h-5 text-brand-600" />
        请求超时设置
      </h3>
      <Button
        type="submit"
        variant="primary"
        size="sm"
        :loading="saving"
      >
        <Save class="w-3.5 h-3.5" />
        保存超时设置
      </Button>
    </div>
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <label class="space-y-2 text-xs font-semibold text-gray-700">
        <span>流式请求总超时（秒）</span>
        <input
          v-model.number="streamSeconds"
          type="number"
          min="1"
          max="86400"
          step="1"
          required
          :disabled="saving"
          class="block w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl font-mono outline-none focus:border-brand-500"
        >
      </label>
      <label class="space-y-2 text-xs font-semibold text-gray-700">
        <span>非流式请求总超时（秒）</span>
        <input
          v-model.number="replaySeconds"
          type="number"
          min="1"
          max="86400"
          step="1"
          required
          :disabled="saving"
          class="block w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl font-mono outline-none focus:border-brand-500"
        >
      </label>
    </div>
    <p class="text-xs text-gray-500 leading-relaxed">
      从开始生成计时，收到流式数据不重置时限。超时立即终止上游请求并返回 504 / DEADLINE_EXCEEDED，
      不自动重试、不切换账号、不冷却或禁用凭据。保存后持久生效，正在执行的请求保留原时限。
    </p>
  </form>
</template>
