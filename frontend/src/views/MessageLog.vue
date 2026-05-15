<template>
  <div>
    <h2>消息日志</h2>
    <el-card style="margin-bottom: 16px">
      <el-form :inline="true" :model="filter" label-width="80px">
        <el-form-item label="群聊">
          <el-input v-model="filter.room_id" placeholder="群聊ID" clearable />
        </el-form-item>
        <el-form-item label="用户">
          <el-input v-model="filter.user_id" placeholder="wxid" clearable />
        </el-form-item>
        <el-form-item label="时间范围">
          <el-date-picker
            v-model="filter.dateRange"
            type="daterange"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            value-format="YYYY-MM-DD"
            style="width: 300px"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="loadMessages">查询</el-button>
          <el-button @click="resetFilter">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-table :data="messages" stripe v-loading="loading" border style="width: 100%">
      <el-table-column prop="msg_id" label="消息ID" width="180" show-overflow-tooltip />
      <el-table-column prop="sender_name" label="发送者" width="120" />
      <el-table-column prop="room_name" label="群聊" width="150" show-overflow-tooltip />
      <el-table-column label="类型" width="80">
        <template #default="{ row }">
          <el-tag size="small">{{ typeLabel(row.msg_type) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="content" label="内容" show-overflow-tooltip min-width="300" />
      <el-table-column prop="create_time" label="时间" width="170" />
      <el-table-column label="操作" width="100">
        <template #default="{ row }">
          <el-button size="small" @click="viewDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="pagination.page"
      :page-size="pagination.size"
      :total="pagination.total"
      layout="total, prev, pager, next"
      style="margin-top: 16px; justify-content: flex-end"
      @current-change="loadMessages"
    />

    <el-dialog title="消息详情" v-model="detailVisible" width="500px">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="消息ID">{{ detail.msg_id }}</el-descriptions-item>
        <el-descriptions-item label="发送者">{{ detail.sender_name }} ({{ detail.sender_wxid }})</el-descriptions-item>
        <el-descriptions-item label="群聊">{{ detail.room_name || '私聊' }} ({{ detail.room_id }})</el-descriptions-item>
        <el-descriptions-item label="类型">{{ typeLabel(detail.msg_type) }}</el-descriptions-item>
        <el-descriptions-item label="时间">{{ detail.create_time }}</el-descriptions-item>
        <el-descriptions-item label="内容">
          <div style="white-space: pre-wrap; max-height: 200px; overflow: auto">{{ detail.content }}</div>
        </el-descriptions-item>
      </el-descriptions>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { getMessages } from '../api'

const messages = ref<any[]>([])
const loading = ref(false)
const detailVisible = ref(false)
const detail = ref<any>({})

const filter = reactive<any>({
  room_id: '',
  user_id: '',
  dateRange: null,
})

const pagination = reactive({ page: 1, size: 20, total: 0 })

function typeLabel(type: number) {
  const map: Record<number, string> = { 1: '文本', 3: '图片', 34: '语音', 43: '视频', 49: '卡片', 10000: '系统' }
  return map[type] || '其他'
}

async function loadMessages() {
  loading.value = true
  try {
    const params: any = { page: pagination.page, size: pagination.size }
    if (filter.room_id) params.room_id = filter.room_id
    if (filter.user_id) params.user_id = filter.user_id
    if (filter.dateRange) {
      params.start_date = filter.dateRange[0]
      params.end_date = filter.dateRange[1]
    }
    const res = await getMessages(params)
    messages.value = res.data.items || res.data
    pagination.total = res.data.total || 0
  } finally {
    loading.value = false
  }
}

function resetFilter() {
  filter.room_id = ''
  filter.user_id = ''
  filter.dateRange = null
  pagination.page = 1
  loadMessages()
}

function viewDetail(row: any) {
  detail.value = row
  detailVisible.value = true
}

onMounted(loadMessages)
</script>
