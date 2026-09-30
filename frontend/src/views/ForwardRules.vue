<template>
  <div>
    <h2>转发规则</h2>
    <el-button type="primary" @click="showDialog()" style="margin-bottom: 16px">新增规则</el-button>
    <el-table :data="rules" stripe>
      <el-table-column prop="name" label="规则名称" width="150" />
      <el-table-column prop="trigger" label="触发方式" width="150">
        <template #default="{ row }">
          <el-tag size="small" :type="row.trigger?.startsWith('workflow') ? 'success' : ''">
            {{ row.trigger?.startsWith('workflow') ? '工作流事件' : '关键词' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="trigger" label="触发条件" show-overflow-tooltip />
      <el-table-column label="转发目标">
        <template #default="{ row }">{{ (row.targets || []).join(', ') }}</template>
      </el-table-column>
      <el-table-column prop="template" label="模板" width="150" />
      <el-table-column label="操作" width="180">
        <template #default="{ row }">
          <el-button size="small" @click="showDialog(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="handleDelete(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog :title="editing?.id ? '编辑转发规则' : '新增转发规则'" v-model="dialogVisible" width="600px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="规则名称">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="触发方式">
          <el-radio-group v-model="triggerType">
            <el-radio label="keyword">关键词</el-radio>
            <el-radio label="workflow">工作流事件</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="触发条件" v-if="triggerType === 'keyword'">
          <el-tag v-for="(k, i) in triggerKeywords" :key="i" closable @close="triggerKeywords.splice(i, 1)" style="margin-right: 8px">{{ k }}</el-tag>
          <el-input v-model="newKeyword" placeholder="输入关键词" style="width: 150px" @keyup.enter="addKeyword" />
        </el-form-item>
        <el-form-item label="工作流事件" v-if="triggerType === 'workflow'">
          <el-input v-model="form.trigger" placeholder="workflow:peiwang_order_flow.FORWARD" />
        </el-form-item>
        <el-form-item label="转发目标">
          <el-tag v-for="(t, i) in form.targets" :key="i" closable @close="form.targets.splice(i, 1)" style="margin-right: 8px">{{ t }}</el-tag>
          <el-input v-model="newTarget" placeholder="群聊ID (xxx@chatroom)" style="width: 200px" @keyup.enter="addTarget" />
        </el-form-item>
        <el-form-item label="消息模板">
          <el-select v-model="form.template" clearable placeholder="选择模板（可选）">
            <el-option v-for="tpl in templates" :key="tpl.name" :label="tpl.name" :value="tpl.name" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { getForwardRules, createForwardRule, updateForwardRule, deleteForwardRule, getTemplates } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const rules = ref<any[]>([])
const templates = ref<any[]>([])
const dialogVisible = ref(false)
const editing = ref<any>(null)
const triggerType = ref('keyword')
const newKeyword = ref('')
const newTarget = ref('')
const triggerKeywords = ref<string[]>([])
const form = reactive<any>({ name: '', trigger: '', targets: [], template: '' })

function showDialog(row?: any) {
  editing.value = row || null
  if (row) {
    Object.assign(form, { ...row })
    if (row.trigger?.startsWith('keyword:')) {
      triggerType.value = 'keyword'
      triggerKeywords.value = row.trigger.replace('keyword:', '').split(',')
    } else {
      triggerType.value = 'workflow'
    }
  } else {
    Object.assign(form, { name: '', trigger: '', targets: [], template: '' })
    triggerType.value = 'keyword'
    triggerKeywords.value = []
  }
  dialogVisible.value = true
}

function addKeyword() {
  if (newKeyword.value) {
    triggerKeywords.value.push(newKeyword.value)
    newKeyword.value = ''
  }
}

function addTarget() {
  if (newTarget.value && !form.targets.includes(newTarget.value)) {
    form.targets.push(newTarget.value)
    newTarget.value = ''
  }
}

async function loadRules() {
  const res = await getForwardRules()
  rules.value = res.data
}

async function loadTemplates() {
  try {
    const res = await getTemplates()
    templates.value = res.data
  } catch {}
}

async function handleSave() {
  if (triggerType.value === 'keyword') {
    form.trigger = 'keyword:' + triggerKeywords.value.join(',')
  }
  if (editing.value?.id) {
    await updateForwardRule(editing.value.id, form)
  } else {
    await createForwardRule(form)
  }
  dialogVisible.value = false
  ElMessage.success('保存成功')
  await loadRules()
}

async function handleDelete(id: number) {
  await ElMessageBox.confirm('确定删除此转发规则?', '提示', { type: 'warning' })
  await deleteForwardRule(id)
  ElMessage.success('删除成功')
  await loadRules()
}

onMounted(() => {
  loadRules()
  loadTemplates()
})
</script>
