<template>
  <div class="dashboard-page">
    <el-row :gutter="16" class="stats-row">
      <el-col :xs="24" :sm="12" :lg="6" v-for="item in stats" :key="item.label">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-card__content">
            <el-icon :size="34" :color="item.color">
              <component :is="item.icon" />
            </el-icon>
            <div>
              <div class="stat-card__value">{{ item.value }}</div>
              <div class="stat-card__label">{{ item.label }}</div>
              <div class="stat-card__hint">{{ item.hint }}</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="quick-card">
      <template #header>快捷操作</template>
      <el-space wrap>
        <el-button type="primary" @click="$router.push('/classes')">班级管理</el-button>
        <el-button type="primary" @click="$router.push('/users')">账号管理</el-button>
        <el-button type="primary" @click="$router.push('/import')">批量导入</el-button>
        <el-button @click="$router.push('/sunshine')">查看阳光跑看板</el-button>
      </el-space>
    </el-card>

    <el-card class="sunshine-card">
      <template #header>
        <div class="sunshine-card__header">
          <span>阳光跑总览</span>
          <el-button link type="primary" @click="$router.push('/sunshine')">查看完整看板</el-button>
        </div>
      </template>
      <SunshineDashboard v-if="sunshineData" embedded :dashboard-data="sunshineData" />
      <el-skeleton v-else :rows="8" animated />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { getDashboardStats, getSunshineClassStats } from '../api/index.js'
import SunshineDashboard from './SunshineDashboard.vue'

const dashboardStats = ref({})
const sunshineData = ref(null)

const stats = computed(() => [
  {
    label: '学生总数',
    value: dashboardStats.value.total_students ?? 0,
    hint: '学生账号总量',
    icon: 'UserFilled',
    color: '#409eff',
  },
  {
    label: '教师总数',
    value: dashboardStats.value.total_teachers ?? 0,
    hint: '教师账号总量',
    icon: 'Avatar',
    color: '#67c23a',
  },
  {
    label: '班级总数',
    value: dashboardStats.value.total_classes ?? 0,
    hint: '已创建班级数量',
    icon: 'School',
    color: '#e6a23c',
  },
  {
    label: '待审批',
    value: dashboardStats.value.pending_approvals ?? 0,
    hint: `健康申请 ${dashboardStats.value.pending_health ?? 0} / 活动审批 ${dashboardStats.value.pending_activities ?? 0}`,
    icon: 'Bell',
    color: '#f56c6c',
  },
])

const load = async () => {
  const [dashboard, sunshine] = await Promise.all([
    getDashboardStats(),
    getSunshineClassStats(),
  ])
  dashboardStats.value = dashboard || {}
  sunshineData.value = sunshine || null
}

onMounted(async () => {
  try {
    await load()
  } catch (error) {
    console.error('Failed to load dashboard data:', error)
  }
})
</script>

<style scoped>
.dashboard-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.stats-row {
  margin-bottom: 0;
}

.stat-card,
.quick-card,
.sunshine-card {
  border-radius: 16px;
}

.stat-card {
  margin-bottom: 16px;
}

.stat-card__content {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 84px;
}

.stat-card__value {
  font-size: 28px;
  font-weight: 700;
  line-height: 1.1;
}

.stat-card__label {
  color: #303133;
  font-size: 14px;
  margin-top: 6px;
}

.stat-card__hint {
  color: #909399;
  font-size: 12px;
  margin-top: 4px;
}

.sunshine-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
