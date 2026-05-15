<template>
  <el-container style="height: 100vh">
    <el-aside width="220px" style="background: #1f2d3d">
      <div style="padding: 20px; color: #fff; font-size: 20px; font-weight: bold">
        🤖 Weix 管理
      </div>
      <el-menu
        :default-active="activeMenu"
        router
        background-color="#1f2d3d"
        text-color="#bfcbd9"
        active-text-color="#409EFF"
        style="border-right: none"
      >
        <el-menu-item index="/dashboard">
          <el-icon><DataAnalysis /></el-icon> 仪表盘
        </el-menu-item>
        <el-menu-item index="/statistics">
          <el-icon><TrendCharts /></el-icon> 统计报告
        </el-menu-item>
        <el-menu-item index="/messages">
          <el-icon><ChatLineSquare /></el-icon> 消息日志
        </el-menu-item>
        <el-sub-menu index="config">
          <template #title>
            <el-icon><Setting /></el-icon> 系统配置
          </template>
          <el-menu-item index="/chat-config">聊天配置</el-menu-item>
          <el-menu-item index="/auto-reply">自动回复规则</el-menu-item>
          <el-menu-item index="/templates">消息模板</el-menu-item>
          <el-menu-item index="/workflows">工作流</el-menu-item>
          <el-menu-item index="/forward-rules">转发规则</el-menu-item>
          <el-menu-item index="/ai-config">AI 配置</el-menu-item>
          <el-menu-item index="/persona-skill">本人 Skill</el-menu-item>
          <el-menu-item index="/schedule">定时任务</el-menu-item>
          <el-menu-item index="/system-config">系统设置</el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header style="background: #fff; border-bottom: 1px solid #e6e6e6; display: flex; align-items: center; justify-content: flex-end; padding: 0 20px">
        <span style="margin-right: 16px">管理员</span>
        <el-button type="danger" size="small" @click="handleLogout">退出</el-button>
      </el-header>
      <el-main style="background: #f0f2f5">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const activeMenu = computed(() => route.path)

function handleLogout() {
  auth.logout()
  router.replace('/login')
}
</script>
