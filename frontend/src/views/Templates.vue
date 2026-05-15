<template>
  <div>
    <h2>消息模板</h2>
    <el-button type="primary" @click="showDialog()" style="margin-bottom: 16px">新增模板</el-button>
    <el-row :gutter="20">
      <el-col :span="8" v-for="tpl in templates" :key="tpl.id" style="margin-bottom: 20px">
        <el-card shadow="hover">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center">
              <span>{{ tpl.name }}</span>
              <el-tag size="small">{{ tpl.type }}</el-tag>
            </div>
          </template>
          <div style="white-space: pre-wrap; font-size: 13px; max-height: 200px; overflow: auto">{{ tpl.content }}</div>
          <template #footer>
            <el-button size="small" @click="showDialog(tpl)">编辑</el-button>
            <el-button size="small" type="danger" @click="handleDelete(tpl.id)">删除</el-button>
          </template>
        </el-card>
      </el-col>
    </el-row>

    <el-dialog :title="editing?.id ? '编辑模板' : '新增模板'" v-model="dialogVisible" width="600px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="模板名称">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.type">
            <el-option label="文本" value="text" /><el-option label="卡片" value="card" />
            <el-option label="表单" value="form" /><el-option label="列表" value="list" />
          </el-select>
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="form.title" />
        </el-form-item>
        <el-form-item label="内容">
          <el-input v-model="form.content" type="textarea" :rows="6" placeholder="支持 {var_name} 变量替换" />
        </el-form-item>
        <el-form-item label="底部">
          <el-input v-model="form.footer" />
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
import { getTemplates, createTemplate, updateTemplate, deleteTemplate } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const templates = ref<any[]>([])
const dialogVisible = ref(false)
const editing = ref<any>(null)
const form = reactive({ name: '', type: 'text', title: '', content: '', footer: '' })

function showDialog(tpl?: any) {
  editing.value = tpl || null
  Object.assign(form, tpl ? { ...tpl } : { name: '', type: 'text', title: '', content: '', footer: '' })
  dialogVisible.value = true
}

async function loadTemplates() {
  const res = await getTemplates()
  templates.value = res.data
}

async function handleSave() {
  if (editing.value?.id) {
    await updateTemplate(editing.value.id, form)
  } else {
    await createTemplate(form)
  }
  dialogVisible.value = false
  ElMessage.success('保存成功')
  await loadTemplates()
}

async function handleDelete(id: number) {
  await ElMessageBox.confirm('确定删除此模板?', '提示', { type: 'warning' })
  await deleteTemplate(id)
  ElMessage.success('删除成功')
  await loadTemplates()
}

onMounted(loadTemplates)
</script>
