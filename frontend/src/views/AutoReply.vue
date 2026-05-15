<template>
  <div>
    <h2>自动回复规则</h2>
    <el-button type="primary" @click="showDialog()" style="margin-bottom: 16px">新增规则</el-button>
    <el-table :data="rules" stripe>
      <el-table-column prop="name" label="规则名称" width="150" />
      <el-table-column prop="type" label="类型" width="100">
        <template #default="{ row }">
          <el-tag size="small">{{ ruleTypeLabels[row.type as RuleType] || row.type }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="patterns" label="匹配模式">
        <template #default="{ row }">{{ (row.patterns || []).join(', ') }}</template>
      </el-table-column>
      <el-table-column prop="reply" label="回复内容" show-overflow-tooltip />
      <el-table-column prop="priority" label="优先级" width="80" />
      <el-table-column prop="enabled" label="状态" width="80">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" @change="toggleRule(row)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150">
        <template #default="{ row }">
          <el-button size="small" @click="showDialog(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="handleDelete(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog :title="editing?.id ? '编辑规则' : '新增规则'" v-model="dialogVisible" width="600px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="规则名称">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.type">
            <el-option label="关键词" value="keyword" />
            <el-option label="正则" value="regex" />
            <el-option label="意图" value="intent" />
          </el-select>
        </el-form-item>
        <el-form-item label="匹配模式">
          <el-tag v-for="(p, i) in form.patterns" :key="i" closable @close="form.patterns.splice(i, 1)" style="margin-right: 8px">{{ p }}</el-tag>
          <el-input v-model="newPattern" placeholder="输入模式" style="width: 150px" @keyup.enter="addPattern" />
        </el-form-item>
        <el-form-item label="回复内容">
          <el-input v-model="form.reply" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="触发工作流" v-if="form.type === 'intent'">
          <el-input v-model="form.workflow" placeholder="工作流名称" />
        </el-form-item>
        <el-form-item label="优先级">
          <el-input-number v-model="form.priority" :min="0" :max="100" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
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
import { getRules, createRule, updateRule, deleteRule } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const rules = ref<any[]>([])
const dialogVisible = ref(false)
const editing = ref<any>(null)
const newPattern = ref('')
const form = reactive<any>({ name: '', type: 'keyword', patterns: [], reply: '', workflow: '', priority: 0, enabled: true })
type RuleType = 'keyword' | 'regex' | 'intent'
const ruleTypeLabels: Record<RuleType, string> = {
  keyword: '关键词',
  regex: '正则',
  intent: '意图',
}

function showDialog(row?: any) {
  editing.value = row || null
  if (row) {
    Object.assign(form, { ...row })
  } else {
    Object.assign(form, { name: '', type: 'keyword', patterns: [], reply: '', workflow: '', priority: 0, enabled: true })
  }
  dialogVisible.value = true
}

function addPattern() {
  if (newPattern.value) {
    form.patterns.push(newPattern.value)
    newPattern.value = ''
  }
}

async function loadRules() {
  const res = await getRules()
  rules.value = res.data
}

async function handleSave() {
  if (editing.value?.id) {
    await updateRule(editing.value.id, form)
  } else {
    await createRule(form)
  }
  dialogVisible.value = false
  ElMessage.success('保存成功')
  await loadRules()
}

async function handleDelete(id: number) {
  await ElMessageBox.confirm('确定删除此规则?', '提示', { type: 'warning' })
  await deleteRule(id)
  ElMessage.success('删除成功')
  await loadRules()
}

async function toggleRule(row: any) {
  await updateRule(row.id, { enabled: !row.enabled })
  await loadRules()
}

onMounted(loadRules)
</script>
