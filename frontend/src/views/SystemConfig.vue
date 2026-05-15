<template>
  <div>
    <h2>系统设置</h2>
    <el-card>
      <el-form :model="form" label-width="140px" v-loading="loading">
        <el-divider content-position="left">基本信息</el-divider>
        <el-form-item label="系统名称">
          <el-input v-model="form.system_name" />
        </el-form-item>
        <el-form-item label="系统版本">
          <el-input :model-value="form.system_version" disabled />
        </el-form-item>
        <el-form-item label="管理员邮箱">
          <el-input v-model="form.admin_email" placeholder="admin@example.com" />
        </el-form-item>

        <el-divider content-position="left">运行参数</el-divider>
        <el-form-item label="日志级别">
          <el-select v-model="form.log_level">
            <el-option label="DEBUG" value="DEBUG" />
            <el-option label="INFO" value="INFO" />
            <el-option label="WARNING" value="WARNING" />
            <el-option label="ERROR" value="ERROR" />
          </el-select>
        </el-form-item>
        <el-form-item label="数据保留天数">
          <el-input-number v-model="form.data_retention_days" :min="1" :max="365" />
        </el-form-item>
        <el-form-item label="每页默认条数">
          <el-input-number v-model="form.page_size" :min="10" :max="100" :step="10" />
        </el-form-item>

        <el-divider content-position="left">通知设置</el-divider>
        <el-form-item label="异常告警">
          <el-switch v-model="form.alert_enabled" active-value="true" inactive-value="false" />
        </el-form-item>
        <el-form-item label="告警通知群聊">
          <el-input v-model="form.alert_room_id" placeholder="群聊ID，留空则不推送" />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" @click="saveConfig" :loading="saving">保存配置</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import { getSystemConfig, updateSystemConfig } from '../api'
import { ElMessage } from 'element-plus'

const loading = ref(false)
const saving = ref(false)
const form = reactive<Record<string, string>>({
  system_name: 'Weix 微信助手',
  system_version: '0.1.0',
  admin_email: '',
  log_level: 'INFO',
  data_retention_days: '30',
  page_size: '20',
  alert_enabled: 'true',
  alert_room_id: '',
})

async function loadConfig() {
  loading.value = true
  try {
    const res = await getSystemConfig()
    if (Array.isArray(res.data)) {
      for (const item of res.data) {
        if (item.key in form) form[item.key] = item.value
      }
    }
  } finally {
    loading.value = false
  }
}

async function saveConfig() {
  saving.value = true
  try {
    const items = Object.entries(form).map(([key, value]) => ({ key, value: String(value) }))
    await updateSystemConfig({ items })
    ElMessage.success('系统配置已保存')
  } finally {
    saving.value = false
  }
}

onMounted(loadConfig)
</script>
