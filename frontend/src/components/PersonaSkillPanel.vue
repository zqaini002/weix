<template>
  <el-card>
    <template #header>
      <div class="persona-header">
        <span>本人 Skill</span>
        <div>
          <el-button :loading="loading" @click="loadPersona">刷新</el-button>
          <el-button v-if="persona.ready && !editing" :loading="loading" @click="startEdit">
            编辑
          </el-button>
          <el-button v-if="editing" type="primary" :loading="loading" @click="savePersonaEdit">
            保存
          </el-button>
          <el-button v-if="editing" :disabled="loading" @click="cancelEdit">
            取消
          </el-button>
          <el-button type="primary" :loading="loading" @click="runPersonaAnalyze">
            生成
          </el-button>
          <el-button type="danger" :loading="loading" @click="clearPersonaCache">
            清除
          </el-button>
        </div>
      </div>
    </template>

    <el-descriptions :column="3" border>
      <el-descriptions-item label="状态">
        <el-tag :type="persona.ready ? 'success' : 'info'">
          {{ persona.ready ? '已生成' : '未生成' }}
        </el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="模式">
        {{ persona.mode || 'contextual' }}
      </el-descriptions-item>
      <el-descriptions-item label="名称">
        {{ persona.meta?.name || '-' }}
      </el-descriptions-item>
    </el-descriptions>

    <el-tabs v-if="persona.ready" style="margin-top: 16px">
      <el-tab-pane label="Self Memory">
        <el-input v-model="form.self_memory" type="textarea" :rows="8" :readonly="!editing" />
      </el-tab-pane>
      <el-tab-pane label="Persona">
        <el-input v-model="form.persona" type="textarea" :rows="8" :readonly="!editing" />
      </el-tab-pane>
      <el-tab-pane label="私聊 Prompt">
        <el-input v-model="form.private_prompt" type="textarea" :rows="8" :readonly="!editing" />
      </el-tab-pane>
      <el-tab-pane label="群聊 Prompt">
        <el-input v-model="form.group_prompt" type="textarea" :rows="8" :readonly="!editing" />
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { analyzePersona, clearPersona, getPersona, updatePersona } from '../api'

const loading = ref(false)
const editing = ref(false)
const persona = reactive<any>({
  ready: false,
  mode: 'contextual',
  meta: {},
  self_memory: '',
  persona: '',
  private_prompt: '',
  group_prompt: '',
})
const form = reactive<any>({
  self_memory: '',
  persona: '',
  private_prompt: '',
  group_prompt: '',
})

function syncForm() {
  Object.assign(form, {
    self_memory: persona.self_memory || '',
    persona: persona.persona || '',
    private_prompt: persona.private_prompt || '',
    group_prompt: persona.group_prompt || '',
  })
}

async function loadPersona() {
  loading.value = true
  try {
    const res = await getPersona()
    Object.assign(persona, res.data)
    syncForm()
    editing.value = false
  } catch {
    editing.value = false
  } finally {
    loading.value = false
  }
}

async function runPersonaAnalyze() {
  loading.value = true
  try {
    const res = await analyzePersona(true)
    if (res.data?.success) {
      Object.assign(persona, {
        ready: true,
        mode: res.data.mode,
        meta: res.data.meta,
        self_memory: res.data.self_memory,
        persona: res.data.persona,
        private_prompt: res.data.private_prompt,
        group_prompt: res.data.group_prompt,
      })
      syncForm()
      editing.value = false
      ElMessage.success(`本人 Skill 已生成，样本 ${res.data.sample_size} 条`)
    } else {
      ElMessage.error(res.data?.error || '生成失败')
    }
  } catch {
    // 全局 axios interceptor 已提示错误。
  } finally {
    loading.value = false
  }
}

function startEdit() {
  syncForm()
  editing.value = true
}

function cancelEdit() {
  syncForm()
  editing.value = false
}

async function savePersonaEdit() {
  loading.value = true
  try {
    const res = await updatePersona({
      meta: persona.meta || {},
      mode: persona.mode || 'contextual',
      self_memory: form.self_memory,
      persona: form.persona,
      private_prompt: form.private_prompt,
      group_prompt: form.group_prompt,
    })
    if (res.data?.success) {
      Object.assign(persona, {
        ready: true,
        mode: res.data.mode,
        meta: res.data.meta,
        self_memory: res.data.self_memory,
        persona: res.data.persona,
        private_prompt: res.data.private_prompt,
        group_prompt: res.data.group_prompt,
      })
      syncForm()
      editing.value = false
      ElMessage.success('本人 Skill 已保存')
    } else {
      ElMessage.error(res.data?.error || '保存失败')
    }
  } catch {
    // 全局 axios interceptor 已提示错误。
  } finally {
    loading.value = false
  }
}

async function clearPersonaCache() {
  loading.value = true
  try {
    await clearPersona()
    Object.assign(persona, {
      ready: false,
      mode: 'contextual',
      meta: {},
      self_memory: '',
      persona: '',
      private_prompt: '',
      group_prompt: '',
    })
    syncForm()
    editing.value = false
    ElMessage.success('本人 Skill 已清除')
  } catch {
    // 全局 axios interceptor 已提示错误。
  } finally {
    loading.value = false
  }
}

onMounted(loadPersona)
</script>

<style scoped>
.persona-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
</style>
