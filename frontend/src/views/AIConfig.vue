<template>
  <div>
    <h2>AI 模型配置</h2>
    <el-card>
      <el-form :model="form" label-width="140px">
        <el-form-item label="Provider">
          <el-select v-model="form.provider">
            <el-option label="DeepSeek" value="deepseek" />
            <el-option label="DashScope (阿里云)" value="dashscope" />
            <el-option label="OpenAI" value="openai" />
            <el-option label="硅基流动 (SiliconFlow)" value="siliconflow" />
          </el-select>
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="form.api_key" type="password" show-password />
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="form.base_url" />
        </el-form-item>
        <el-form-item label="模型">
          <el-input v-model="form.model" placeholder="deepseek-v4-pro, deepseek-v4-flash, etc." />
        </el-form-item>
        <el-form-item label="Temperature">
          <el-slider v-model="form.temperature" :min="0" :max="2" :step="0.1" show-input />
        </el-form-item>
        <el-form-item label="Max Tokens">
          <el-input-number v-model="form.max_tokens" :min="100" :max="8000" />
        </el-form-item>
        <el-form-item label="Persona Mode">
          <el-select v-model="form.persona_mode">
            <el-option label="按场景切换" value="contextual" />
          </el-select>
        </el-form-item>
        <el-form-item label="Persona 天数">
          <el-input-number v-model="form.persona_since_days" :min="1" :max="3650" />
        </el-form-item>
        <el-form-item label="Persona 消息数">
          <el-input-number v-model="form.persona_message_limit" :min="100" :max="20000" />
        </el-form-item>
        <el-form-item label="System Prompt">
          <el-input v-model="form.system_prompt" type="textarea" :rows="4" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="saveConfig">保存配置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

  </div>
</template>

<script setup lang="ts">
import { reactive, onMounted } from 'vue'
import { getAIConfig, updateAIConfig } from '../api'
import { ElMessage } from 'element-plus'

const form = reactive<any>({
  provider: 'deepseek',
  api_key: '',
  base_url: 'https://api.deepseek.com',
  model: 'deepseek-v4-pro',
  temperature: 0.7,
  max_tokens: 2000,
  persona_mode: 'contextual',
  persona_since_days: 90,
  persona_message_limit: 3000,
  system_prompt: '你是一个友好的微信助手。',
})

async function saveConfig() {
  await updateAIConfig(form)
  ElMessage.success('AI 配置已保存')
}

onMounted(async () => {
  try {
    const res = await getAIConfig()
    if (res.data) {
      // 保留表单已有的值作为默认值，仅覆盖服务端返回的非掩码字段
      for (const [k, v] of Object.entries(res.data)) {
        if (k === 'api_key' && typeof v === 'string' && v.startsWith('***')) {
          continue  // 掩码值不覆盖表单，保留表单中的原值
        }
        form[k] = v
      }
    }
  } catch {}
})
</script>
