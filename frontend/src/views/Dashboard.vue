<template>
  <div>
    <h2>仪表盘</h2>
    <el-row :gutter="20" style="margin-bottom: 20px">
      <el-col :span="6" v-for="card in cards" :key="card.label">
        <el-card shadow="hover">
          <div style="text-align: center">
            <div style="color: #909399; font-size: 14px">{{ card.label }}</div>
            <div style="font-size: 28px; font-weight: bold; margin-top: 8px" :style="{ color: card.color }">
              {{ card.value }}
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="20">
      <el-col :span="12">
        <el-card>
          <template #header>平台状态</template>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="运行平台">{{ overview.platform === 'darwin' ? 'macOS' : 'Windows' }}</el-descriptions-item>
            <el-descriptions-item label="微信在线">
              <el-tag :type="overview.wechat_online ? 'success' : 'danger'">
                {{ overview.wechat_online ? '在线' : '离线' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="今日消息">{{ overview.today_messages }}</el-descriptions-item>
            <el-descriptions-item label="活跃群聊">{{ overview.active_rooms }}</el-descriptions-item>
            <el-descriptions-item label="待处理订单">{{ overview.pending_orders }}</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card>
          <template #header>快捷操作</template>
          <el-space direction="vertical" style="width: 100%">
            <el-button type="primary" style="width: 100%" @click="$router.push('/auto-reply')">管理自动回复规则</el-button>
            <el-button type="success" style="width: 100%" @click="$router.push('/templates')">编辑消息模板</el-button>
            <el-button type="warning" style="width: 100%" @click="$router.push('/statistics')">查看统计报告</el-button>
            <el-button style="width: 100%" @click="$router.push('/messages')">查看消息日志</el-button>
          </el-space>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { getDashboard } from '../api'

const overview = ref<any>({
  platform: 'unknown',
  wechat_online: false,
  today_messages: 0,
  active_rooms: 0,
  pending_orders: 0,
  ai_calls: 0,
})

const cards = computed(() => [
  { label: '今日消息', value: overview.value.today_messages, color: '#409EFF' },
  { label: '活跃群聊', value: overview.value.active_rooms, color: '#67C23A' },
  { label: 'AI 调用', value: overview.value.ai_calls, color: '#E6A23C' },
  { label: '待处理订单', value: overview.value.pending_orders, color: '#F56C6C' },
])

onMounted(async () => {
  try {
    const res = await getDashboard()
    overview.value = res.data
  } catch {}
})
</script>
