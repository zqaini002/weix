<template>
  <div>
    <h2>定时任务</h2>
    <el-table :data="jobs" stripe v-loading="loading">
      <el-table-column prop="id" label="任务ID" width="200" />
      <el-table-column prop="name" label="任务名称" width="180" />
      <el-table-column prop="trigger" label="触发规则" width="200">
        <template #default="{ row }">
          <el-tag size="small" type="info">{{ row.trigger }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="next_run_time" label="下次执行" width="200" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.paused ? 'warning' : 'success'" size="small">
            {{ row.paused ? '暂停' : '运行中' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="220">
        <template #default="{ row }">
          <el-button size="small" @click="triggerJob(row)">手动触发</el-button>
          <el-button size="small" :type="row.paused ? 'success' : 'warning'" @click="toggleJob(row)">
            {{ row.paused ? '恢复' : '暂停' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getJobs, updateJob, triggerJob as triggerJobApi } from '../api'
import { ElMessage } from 'element-plus'

const jobs = ref<any[]>([])
const loading = ref(false)

async function loadJobs() {
  loading.value = true
  try {
    const res = await getJobs()
    jobs.value = res.data
  } finally {
    loading.value = false
  }
}

async function toggleJob(row: any) {
  try {
    await updateJob(row.id, { paused: !row.paused })
    row.paused = !row.paused
    ElMessage.success(row.paused ? '已暂停' : '已恢复')
  } catch {}
}

async function triggerJob(row: any) {
  try {
    await triggerJobApi(row.id)
    ElMessage.success(`任务 "${row.name}" 已触发`)
  } catch {}
}

onMounted(loadJobs)
</script>
