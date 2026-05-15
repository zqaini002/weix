<template>
  <div>
    <h2>聊天配置</h2>
    <el-card>
      <el-form :model="form" label-width="140px">
        <el-form-item label="启用机器人">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <el-form-item label="群聊权限">
          <el-radio-group v-model="form.group_chat_mode">
            <el-radio label="all">所有人</el-radio>
            <el-radio label="whitelist">仅白名单</el-radio>
            <el-radio label="none">禁用</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="群聊白名单">
          <div style="margin-bottom: 8px">
            <el-tag v-for="room in form.group_whitelist" :key="room" closable @close="removeRoom(room)" style="margin-right: 8px; margin-bottom: 4px">
              {{ roomName(room) }}
            </el-tag>
          </div>
          <el-select
            v-model="selectedRoom"
            filterable
            remote
            :remote-method="filterRooms"
            placeholder="输入关键词搜索群聊"
            style="width: 100%"
            @change="addRoom"
          >
            <el-option
              v-for="room in filteredRooms"
              :key="room.room_id"
              :label="room.name && room.name !== room.room_id ? room.name : room.room_id"
              :value="room.room_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="私聊权限">
          <el-radio-group v-model="form.private_chat_mode">
            <el-radio label="all">所有人</el-radio>
            <el-radio label="whitelist">仅白名单</el-radio>
            <el-radio label="none">禁用</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="私聊白名单">
          <div style="margin-bottom: 8px">
            <el-tag v-for="user in form.private_whitelist" :key="user" closable @close="removeUser(user)" style="margin-right: 8px; margin-bottom: 4px">
              {{ userName(user) }}
            </el-tag>
          </div>
          <el-select
            v-model="selectedUser"
            filterable
            remote
            :remote-method="filterContacts"
            placeholder="输入关键词搜索用户"
            style="width: 100%"
            @change="addUser"
          >
            <el-option
              v-for="c in filteredContacts"
              :key="c.wxid"
              :label="c.nickname || c.remark || c.alias || c.wxid"
              :value="c.wxid"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="回复模式">
          <el-radio-group v-model="form.reply_mode">
            <el-radio label="keyword">仅关键词</el-radio>
            <el-radio label="ai">AI 自动回复</el-radio>
            <el-radio label="all">全部回复</el-radio>
          </el-radio-group>
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
import { getChatConfig, updateChatConfig, getContacts, searchChatrooms, searchContactsApi } from '../api'
import { ElMessage } from 'element-plus'

const form = reactive<any>({
  enabled: true,
  group_chat_mode: 'whitelist',
  group_whitelist: [],
  private_chat_mode: 'all',
  private_whitelist: [],
  reply_mode: 'all',
})

// 全量数据：用于已选标签的名称展示
const allChatrooms = ref<any[]>([])
const allContacts = ref<any[]>([])

// 下拉选项：仅展示搜索结果（默认空，避免渲染数千 DOM）
const filteredRooms = ref<any[]>([])
const filteredContacts = ref<any[]>([])

const selectedRoom = ref('')
const selectedUser = ref('')
const saving = ref(false)

function roomName(id: string) {
  const found = allChatrooms.value.find((r: any) => r.room_id === id)
  if (!found) return id
  return found.name && found.name !== found.room_id ? found.name : found.room_id
}

function userName(id: string) {
  const found = allContacts.value.find((c: any) => c.wxid === id)
  if (!found) return id
  return found.nickname || found.remark || found.alias || id
}

function matchRoom(keyword: string, room: any) {
  const kw = keyword.toLowerCase()
  return (room.name || '').toLowerCase().includes(kw) ||
    (room.room_id || '').toLowerCase().includes(kw)
}

function matchContact(keyword: string, c: any) {
  const kw = keyword.toLowerCase()
  return (c.nickname || '').toLowerCase().includes(kw) ||
    (c.remark || '').toLowerCase().includes(kw) ||
    (c.alias || '').toLowerCase().includes(kw) ||
    (c.wxid || '').toLowerCase().includes(kw)
}

let searchTimer: ReturnType<typeof setTimeout> | null = null

function filterRooms(keyword: string) {
  if (!keyword) { filteredRooms.value = []; return }
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(async () => {
    try {
      const res = await searchChatrooms(keyword)
      filteredRooms.value = (res.data?.chatrooms || []).slice(0, 50)
    } catch {
      filteredRooms.value = allChatrooms.value
        .filter((r: any) => matchRoom(keyword, r))
        .slice(0, 50)
    }
  }, 300)
}

function filterContacts(keyword: string) {
  if (!keyword) { filteredContacts.value = []; return }
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(async () => {
    try {
      const res = await searchContactsApi(keyword)
      filteredContacts.value = (res.data?.contacts || []).slice(0, 50)
    } catch {
      filteredContacts.value = allContacts.value
        .filter((c: any) => matchContact(keyword, c))
        .slice(0, 50)
    }
  }, 300)
}

function addRoom(roomId: string) {
  if (roomId && !form.group_whitelist.includes(roomId)) {
    form.group_whitelist.push(roomId)
  }
  selectedRoom.value = ''
  filteredRooms.value = []
}

function removeRoom(room: string) {
  form.group_whitelist = form.group_whitelist.filter((r: string) => r !== room)
}

function addUser(wxid: string) {
  if (wxid && !form.private_whitelist.includes(wxid)) {
    form.private_whitelist.push(wxid)
  }
  selectedUser.value = ''
  filteredContacts.value = []
}

function removeUser(user: string) {
  form.private_whitelist = form.private_whitelist.filter((u: string) => u !== user)
}

onMounted(async () => {
  try {
    const res = await getChatConfig()
    if (res.data) Object.assign(form, res.data)
  } catch {
    ElMessage.error('加载配置失败')
  }

  try {
    const res = await getContacts('all')
    if (res.data.error) {
      ElMessage.warning(res.data.error)
    }
    allChatrooms.value = res.data.chatrooms || []
    allContacts.value = res.data.contacts || []
  } catch {
    ElMessage.error('加载联系人列表失败，请确认后端服务已启动')
  }
})

async function saveConfig() {
  saving.value = true
  try {
    await updateChatConfig(form)
    ElMessage.success('配置已保存')
  } finally {
    saving.value = false
  }
}
</script>
