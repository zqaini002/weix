<template>
  <div>
    <h2>工作流配置</h2>
    <el-button type="primary" @click="showDialog()" style="margin-bottom: 16px">新增工作流</el-button>
    <el-table :data="workflows" stripe>
      <el-table-column prop="name" label="名称" width="200" />
      <el-table-column prop="description" label="描述" />
      <el-table-column prop="trigger_intents" label="触发意图">
        <template #default="{ row }">{{ (row.trigger_intents || []).join(', ') }}</template>
      </el-table-column>
      <el-table-column label="状态数" width="80">
        <template #default="{ row }">{{ (row.states || []).length }}</template>
      </el-table-column>
      <el-table-column prop="enabled" label="启用" width="80">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" @change="toggleEnabled(row)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="180">
        <template #default="{ row }">
          <el-button size="small" @click="showDialog(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="handleDelete(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog :title="editing?.id ? '编辑工作流' : '新增工作流'" v-model="dialogVisible" width="600px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="form.description" /></el-form-item>
        <el-form-item label="触发意图">
          <el-tag v-for="(t, i) in form.trigger_intents" :key="i" closable @close="form.trigger_intents.splice(i, 1)" style="margin-right: 8px">{{ t }}</el-tag>
          <el-input v-model="newIntent" placeholder="意图关键词" style="width: 150px" @keyup.enter="addIntent" />
        </el-form-item>
        <el-form-item label="转发目标"><el-input v-model="form.forward_to" /></el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
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
import { getWorkflows, createWorkflow, updateWorkflow, deleteWorkflow } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const workflows = ref<any[]>([])
const dialogVisible = ref(false)
const editing = ref<any>(null)
const newIntent = ref('')
const form = reactive({ name: '', description: '', trigger_intents: [] as string[], forward_to: '', enabled: true, states: [] })

function addIntent() {
  if (newIntent.value) {
    form.trigger_intents.push(newIntent.value)
    newIntent.value = ''
  }
}

async function loadWorkflows() {
  const res = await getWorkflows()
  workflows.value = res.data
}

function showDialog(row?: any) {
  editing.value = row || null
  if (row) {
    Object.assign(form, {
      name: row.name,
      description: row.description,
      trigger_intents: [...(row.trigger_intents || [])],
      forward_to: row.forward_to || '',
      enabled: row.enabled,
      states: row.states || [],
    })
  } else {
    Object.assign(form, { name: '', description: '', trigger_intents: [], forward_to: '', enabled: true, states: [] })
  }
  dialogVisible.value = true
}

async function handleSave() {
  if (editing.value?.id) {
    await updateWorkflow(editing.value.id, form)
  } else {
    await createWorkflow(form)
  }
  dialogVisible.value = false
  ElMessage.success('保存成功')
  await loadWorkflows()
}

async function handleDelete(id: number) {
  await ElMessageBox.confirm('确定删除此工作流?', '提示', { type: 'warning' })
  await deleteWorkflow(id)
  ElMessage.success('删除成功')
  await loadWorkflows()
}

async function toggleEnabled(row: any) {
  try {
    await updateWorkflow(row.id, { enabled: !row.enabled })
    row.enabled = !row.enabled
    ElMessage.success(row.enabled ? '已启用' : '已停用')
  } catch {
    // 已由拦截器提示错误
  }
}

onMounted(loadWorkflows)
</script>
