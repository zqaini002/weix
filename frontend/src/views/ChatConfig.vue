<template>
  <div>
    <h2>聊天配置</h2>
    <el-card>
      <el-form :model="form" label-width="140px">
        <el-form-item label="启用机器人">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <el-form-item label="群消息回复权限">
          <el-radio-group v-model="form.group_chat_mode">
            <el-radio label="all">所有人</el-radio>
            <el-radio label="whitelist">仅白名单</el-radio>
            <el-radio label="none">禁用</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="群白名单/转发目标">
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
          <div style="width: 100%; color: #606266; margin-bottom: 8px">
            私聊选“所有人”无需填写私人白名单。收消息依赖本机数据库；视觉模型负责发送定位。
          </div>
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
          <div style="display: flex; gap: 8px; width: 100%; margin-top: 8px">
            <el-input
              v-model="manualUser"
              placeholder="联系人列表不可用时，填写微信聊天窗口的标题"
              @keyup.enter="addManualUser"
            />
            <el-button @click="addManualUser">添加标题</el-button>
          </div>
        </el-form-item>
        <el-divider content-position="left">自助点单</el-divider>
        <el-form-item label="启用私聊点单"><el-switch v-model="form.order_intake.enabled" /></el-form-item>
        <template v-if="form.order_intake.enabled">
          <el-alert :closable="false" type="info" style="margin-bottom: 18px" title="点单遵循上方私聊权限。将转发目标加入群白名单，群消息回复可保持禁用。其他私聊内容回复等待主人在线。" />
          <el-form-item label="模板触发词"><el-select v-model="form.order_intake.trigger_keywords" multiple filterable allow-create default-first-option style="width: 100%" /></el-form-item>
          <el-form-item label="回复表格"><el-input v-model="form.order_intake.template" type="textarea" :rows="9" maxlength="5000" /></el-form-item>
          <el-form-item label="群聊填写简称">
            <div style="width: 100%">
              <div v-for="(row, index) in aliasRows" :key="index" style="display: flex; gap: 8px; margin-bottom: 8px">
                <el-input v-model="row.alias" placeholder="例如：女1" style="max-width: 180px" />
                <el-select v-model="row.room_id" placeholder="选择允许转发的群" style="flex: 1"><el-option v-for="room in allowedOrderRooms" :key="room.room_id" :label="room.name || room.room_id" :value="room.room_id" /></el-select>
                <el-button @click="aliasRows.splice(index, 1)">删除</el-button>
              </div>
              <el-button @click="aliasRows.push({alias: '', room_id: ''})">添加简称</el-button>
              <div style="margin-top: 8px; color: #606266">例如填写“女1”，精确发送到选定的群。也可填写完整群名，重名群会停发。</div>
            </div>
          </el-form-item>
          <el-form-item label="单次最多目标"><el-input-number v-model="form.order_intake.max_targets" :min="1" :max="10" /></el-form-item>
        </template>
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
import { reactive, ref, computed, onMounted } from 'vue'
import { getChatConfig, updateChatConfig, getContacts, searchChatrooms, searchContactsApi } from '../api'
import { ElMessage } from 'element-plus'

const orderDefaults = {"enabled": false, "trigger_keywords": ["在吗", "在吗？", "在吗?", "点单", "下单"], "template": "如需点单 请自助填写\n发送群聊：\n发送内容：\n发送群聊：\n发送内容：\n（特殊要求备注在发送内容里）\n我是主人的小助手 其他信息辛苦等待主人在线后回复哟。", "max_targets": 5, "group_aliases": {}}
const aliasRows = ref<{alias: string; room_id: string}[]>([])
const form = reactive<any>({
  enabled: true,
  group_chat_mode: 'whitelist',
  group_whitelist: [],
  private_chat_mode: 'all',
  private_whitelist: [],
  reply_mode: 'all',
  order_intake: { ...orderDefaults },
})

// 全量数据：用于已选标签的名称展示
const allChatrooms = ref<any[]>([])
const allContacts = ref<any[]>([])
const allowedOrderRooms = computed(() => allChatrooms.value.filter(room => form.group_whitelist.includes(room.room_id) || form.group_whitelist.includes(room.name)))

// 下拉选项：仅展示搜索结果（默认空，避免渲染数千 DOM）
const filteredRooms = ref<any[]>([])
const filteredContacts = ref<any[]>([])

const selectedRoom = ref('')
const selectedUser = ref('')
const manualUser = ref('')
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

function addManualUser() {
  const title = manualUser.value.trim()
  if (title && !form.private_whitelist.includes(title)) {
    form.private_whitelist.push(title)
  }
  manualUser.value = ''
}

function removeUser(user: string) {
  form.private_whitelist = form.private_whitelist.filter((u: string) => u !== user)
}

onMounted(async () => {
  try {
    const res = await getChatConfig()
    if (res.data) {
      Object.assign(form, res.data)
      form.order_intake = { ...orderDefaults, ...res.data.order_intake }
      aliasRows.value = Object.entries(form.order_intake.group_aliases || {}).map(([alias, room_id]) => ({alias, room_id: String(room_id)}))
    }
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
  const aliases: Record<string, string> = {}
  for (const row of aliasRows.value) {
    const alias = row.alias.trim()
    if (!alias || !row.room_id || aliases[alias]) { ElMessage.error('请完整填写群简称和目标，且简称不能重复'); return }
    if (!allowedOrderRooms.value.some(room => room.room_id === row.room_id)) { ElMessage.error('群简称目标必须在群白名单中'); return }
    aliases[alias] = row.room_id
  }
  form.order_intake.group_aliases = aliases
  saving.value = true
  try {
    const res = await updateChatConfig(form)
    if (res.data?.auto_reply_running) {
      ElMessage.success('配置已保存，自动回复监听已更新')
    } else {
      ElMessage.warning(res.data?.reason || '配置已保存，自动回复未启动')
    }
  } finally {
    saving.value = false
  }
}
</script>
