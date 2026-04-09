<template>
  <div class="sunshine-dashboard">
    <el-card v-if="!embedded" class="title-card">
      <template #header>阳光跑全校看板</template>
      <p class="title-card__desc">
        当前看板数据来自真实运动记录，统计口径为有效阳光跑：
        <code>activities.type = run</code> 且 <code>is_valid = true</code>。
      </p>
    </el-card>

    <el-row :gutter="16" class="summary-row">
      <el-col :xs="24" :sm="12" :lg="6" v-for="item in summaryCards" :key="item.label">
        <el-card shadow="hover" class="summary-card">
          <div class="summary-card__label">{{ item.label }}</div>
          <div class="summary-card__value">{{ item.value }}</div>
          <div class="summary-card__hint">{{ item.hint }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :xs="24" :lg="14">
        <el-card class="panel-card">
          <template #header>班级达标排行</template>
          <div ref="classChartRef" class="chart-box"></div>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="10">
        <el-card class="panel-card">
          <template #header>专业活跃度分布</template>
          <div ref="majorChartRef" class="chart-box"></div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :xs="24">
        <el-card class="panel-card">
          <template #header>学院活跃度对比</template>
          <div ref="collegeChartRef" class="chart-box chart-box--short"></div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :xs="24" :xl="12">
        <el-card class="panel-card">
          <template #header>专业分析明细</template>
          <el-table :data="majorActivity" stripe border max-height="360">
            <el-table-column prop="major" label="专业" min-width="180" />
            <el-table-column prop="total_count" label="学生数" width="88" align="right" />
            <el-table-column prop="active_students" label="活跃人数" width="96" align="right" />
            <el-table-column prop="total_valid_runs" label="有效跑步" width="104" align="right" />
            <el-table-column prop="pass_rate" label="达标率" width="92" align="right">
              <template #default="{ row }">{{ row.pass_rate }}%</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :xs="24" :xl="12">
        <el-card class="panel-card">
          <template #header>学院分析明细</template>
          <el-table :data="collegeActivity" stripe border max-height="360">
            <el-table-column prop="college" label="学院" min-width="180" />
            <el-table-column prop="total_count" label="学生数" width="88" align="right" />
            <el-table-column prop="active_students" label="活跃人数" width="96" align="right" />
            <el-table-column prop="total_valid_runs" label="有效跑步" width="104" align="right" />
            <el-table-column prop="pass_rate" label="达标率" width="92" align="right">
              <template #default="{ row }">{{ row.pass_rate }}%</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="panel-card">
      <template #header>班级明细</template>
      <el-table :data="classStats" stripe border>
        <el-table-column prop="class_name" label="班级" min-width="160" />
        <el-table-column prop="total_count" label="学生数" width="92" align="right" />
        <el-table-column prop="active_students" label="活跃人数" width="92" align="right" />
        <el-table-column prop="total_valid_runs" label="有效跑步" width="108" align="right" />
        <el-table-column prop="avg_score" label="平均分" width="100" align="right" />
        <el-table-column prop="passed_20_count" label="达标人数" width="92" align="right" />
        <el-table-column prop="pass_rate" label="达标率" width="92" align="right">
          <template #default="{ row }">{{ row.pass_rate }}%</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { getSunshineClassStats } from '../api/index.js'

const props = defineProps({
  embedded: {
    type: Boolean,
    default: false,
  },
  dashboardData: {
    type: Object,
    default: null,
  },
})

const overview = ref({})
const classStats = ref([])
const majorActivity = ref([])
const collegeActivity = ref([])

const classChartRef = ref(null)
const majorChartRef = ref(null)
const collegeChartRef = ref(null)

let classChart = null
let majorChart = null
let collegeChart = null

const summaryCards = computed(() => [
  {
    label: '统计学生数',
    value: overview.value.total_students ?? 0,
    hint: '纳入阳光跑统计的学生人数',
  },
  {
    label: '有效跑步次数',
    value: overview.value.total_valid_runs ?? 0,
    hint: '全校累计有效阳光跑记录',
  },
  {
    label: '整体达标率',
    value: `${overview.value.pass_rate ?? 0}%`,
    hint: '有效跑步达到 20 次的学生占比',
  },
  {
    label: '专业 / 学院维度',
    value: `${overview.value.major_count ?? 0} / ${overview.value.college_count ?? 0}`,
    hint: '当前看板支持的分析维度数量',
  },
])

const applyData = async (data) => {
  overview.value = data?.overview || {}
  classStats.value = data?.class_stats || []
  majorActivity.value = data?.major_activity || []
  collegeActivity.value = data?.college_activity || []
  await nextTick()
  drawCharts()
}

const load = async () => {
  if (props.dashboardData) {
    await applyData(props.dashboardData)
    return
  }
  const data = await getSunshineClassStats()
  await applyData(data)
}

function drawClassChart() {
  if (!classChartRef.value) return
  if (!classChart) classChart = echarts.init(classChartRef.value)

  if (!classStats.value.length) {
    classChart.setOption({ title: { text: '暂无数据', left: 'center', top: 'center' } })
    return
  }

  const topClasses = classStats.value.slice(0, 10)
  classChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { top: 0 },
    grid: { left: '8%', right: '6%', bottom: '10%', top: '14%', containLabel: true },
    xAxis: {
      type: 'category',
      data: topClasses.map((item) => item.class_name),
      axisLabel: { rotate: 25 },
    },
    yAxis: [
      { type: 'value', name: '达标率', max: 100, axisLabel: { formatter: '{value}%' } },
      { type: 'value', name: '有效跑步' },
    ],
    series: [
      {
        name: '达标率',
        type: 'bar',
        data: topClasses.map((item) => item.pass_rate),
        itemStyle: { color: '#409eff' },
      },
      {
        name: '有效跑步',
        type: 'line',
        yAxisIndex: 1,
        smooth: true,
        data: topClasses.map((item) => item.total_valid_runs),
        itemStyle: { color: '#67c23a' },
      },
    ],
  })
}

function drawMajorChart() {
  if (!majorChartRef.value) return
  if (!majorChart) majorChart = echarts.init(majorChartRef.value)

  const list = majorActivity.value.filter((item) => item.total_valid_runs > 0).slice(0, 8)
  if (!list.length) {
    majorChart.setOption({ title: { text: '暂无数据', left: 'center', top: 'center' } })
    return
  }

  majorChart.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0 },
    series: [
      {
        name: '专业有效跑步',
        type: 'pie',
        radius: ['38%', '70%'],
        center: ['50%', '45%'],
        data: list.map((item) => ({ name: item.major, value: item.total_valid_runs })),
        emphasis: {
          itemStyle: {
            shadowBlur: 10,
            shadowOffsetX: 0,
            shadowColor: 'rgba(0, 0, 0, 0.2)',
          },
        },
      },
    ],
  })
}

function drawCollegeChart() {
  if (!collegeChartRef.value) return
  if (!collegeChart) collegeChart = echarts.init(collegeChartRef.value)

  const list = collegeActivity.value.slice(0, 10)
  if (!list.length) {
    collegeChart.setOption({ title: { text: '暂无数据', left: 'center', top: 'center' } })
    return
  }

  collegeChart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: '18%', right: '8%', bottom: '8%', top: '10%', containLabel: true },
    xAxis: { type: 'value', name: '有效跑步' },
    yAxis: {
      type: 'category',
      data: list.map((item) => item.college),
    },
    series: [
      {
        name: '学院有效跑步',
        type: 'bar',
        data: list.map((item) => item.total_valid_runs),
        itemStyle: { color: '#fa8c16' },
        label: { show: true, position: 'right' },
      },
    ],
  })
}

function drawCharts() {
  drawClassChart()
  drawMajorChart()
  drawCollegeChart()
}

function resizeCharts() {
  classChart?.resize()
  majorChart?.resize()
  collegeChart?.resize()
}

watch(
  () => props.dashboardData,
  async (value) => {
    if (value) {
      await applyData(value)
    }
  },
  { deep: true }
)

onMounted(async () => {
  try {
    await load()
  } catch (error) {
    console.error('Failed to load sunshine dashboard:', error)
  }
  window.addEventListener('resize', resizeCharts)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeCharts)
  classChart?.dispose()
  majorChart?.dispose()
  collegeChart?.dispose()
})
</script>

<style scoped>
.sunshine-dashboard {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.title-card,
.summary-card,
.panel-card {
  border-radius: 16px;
}

.title-card__desc {
  margin: 0;
  color: #606266;
  line-height: 1.7;
}

.summary-row {
  margin-bottom: 0;
}

.summary-card {
  min-height: 116px;
}

.summary-card__label {
  color: #909399;
  font-size: 13px;
}

.summary-card__value {
  font-size: 30px;
  font-weight: 700;
  margin-top: 10px;
}

.summary-card__hint {
  color: #606266;
  font-size: 12px;
  margin-top: 6px;
}

.chart-box {
  height: 360px;
}

.chart-box--short {
  height: 320px;
}
</style>
