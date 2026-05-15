<template>
  <div>
    <h2>统计报告</h2>
    <el-card style="margin-bottom: 16px">
      <el-form :inline="true">
        <el-form-item label="周期">
          <el-select v-model="period" style="width: 120px" @change="onPeriodChange">
            <el-option label="今日" value="day" />
            <el-option label="本周" value="week" />
            <el-option label="本月" value="month" />
          </el-select>
        </el-form-item>
        <el-form-item label="群聊">
          <el-input v-model="roomId" placeholder="留空为全部" clearable @change="onRoomChange" />
        </el-form-item>
      </el-form>
    </el-card>
    <el-tabs v-model="activeTab" @tab-change="onTabChange">
      <el-tab-pane label="发言排行" name="ranking">
        <el-card>
          <el-table :data="ranking" stripe>
            <el-table-column type="index" label="排名" width="60" />
            <el-table-column prop="user_name" label="用户" />
            <el-table-column prop="message_count" label="消息数" width="120" sortable />
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="时段分析" name="timeline">
        <el-card>
          <v-chart :option="timelineChartOption" style="height: 300px" autoresize />
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="关键词" name="keywords">
        <el-card>
          <el-table :data="keywords" stripe>
            <el-table-column type="index" label="#" width="60" />
            <el-table-column prop="word" label="关键词" />
            <el-table-column prop="count" label="出现次数" width="120" sortable />
            <el-table-column prop="score" label="TF-IDF" width="120" sortable />
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { use } from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import { getRanking, getTimeline, getKeywords } from '../api'

use([BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const activeTab = ref('ranking')
const period = ref('day')
const roomId = ref('')
const ranking = ref<any[]>([])
const timeline = ref<any[]>([])
const keywords = ref<any[]>([])

const timelineChartOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: timeline.value.map((t: any) => `${t.hour}:00`) },
  yAxis: { type: 'value', name: '消息数' },
  series: [{ data: timeline.value.map((t: any) => t.count), type: 'line', smooth: true, areaStyle: {} }],
}))

function roomParams() {
  return roomId.value ? { room_id: roomId.value } : {}
}

async function loadRanking() {
  const res = await getRanking({ period: period.value, ...roomParams() })
  ranking.value = res.data.ranking
}

async function loadTimeline() {
  const res = await getTimeline({ period: period.value, ...roomParams() })
  timeline.value = res.data.timeline
}

async function loadKeywords() {
  const res = await getKeywords({ period: period.value, ...roomParams() })
  keywords.value = res.data.keywords
}

function onPeriodChange() {
  if (activeTab.value === 'ranking') loadRanking()
  else if (activeTab.value === 'timeline') loadTimeline()
  else if (activeTab.value === 'keywords') loadKeywords()
}

function onRoomChange() {
  onPeriodChange()
}

function onTabChange(tab: string) {
  if (tab === 'ranking') loadRanking()
  else if (tab === 'timeline') loadTimeline()
  else if (tab === 'keywords') loadKeywords()
}

onMounted(async () => {
  await loadRanking()
  await loadTimeline()
  await loadKeywords()
})
</script>
