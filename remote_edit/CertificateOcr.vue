<template>
  <div class="ocr_page">
    <header class="header">
      <div class="header_content">
        <h1>智能证书识别</h1>
        <p>支持批量上传证件，AI 自动提取关键信息，请务必在提交前核对内容准确性</p>
      </div>
      <div class="header_actions">
        <n-button secondary @click="go_back">
          <template #icon><ArrowLeft /></template>
          返回列表
        </n-button>
      </div>
    </header>

    <!-- 批量上传区域 -->
    <div class="upload_section">
      <!-- 证书类型预选（让AI使用专属识别模板，效果更准确） -->
      <div style="margin-bottom: 12px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
        <span style="color: #555; font-size: 13px; white-space: nowrap;">📌 上传前选择类型，AI识别更精准：</span>
        <n-select
          v-model:value="default_cert_type"
          :options="cert_type_select_opts"
          placeholder="自动识别（默认）"
          clearable
          style="width: 200px;"
        />
      </div>
      <n-upload
        multiple
        directory-dnd
        action=""
        :custom-request="handle_batch_upload"
        :show-file-list="false"
        accept=".jpg,.jpeg,.png"
      >
        <n-upload-dragger class="batch_dragger">
          <div class="dragger_content">
            <div class="icon_box">
              <n-icon size="48" :depth="3" color="#409eff">
                <CloudUpload />
              </n-icon>
            </div>
            <div class="text_box">
              <h3>点击或拖拽图片到此处上传</h3>
              <p>支持多张并发识别，单张限制 10MB (JPG/PNG)</p>
            </div>
          </div>
        </n-upload-dragger>
      </n-upload>
    </div>

    <!-- 识别卡片列表流 -->
    <div class="cards_stream">
      <n-grid :x-gap="16" :y-gap="16" cols="1 s:2 m:3 l:4" responsive="screen">
        <n-grid-item v-for="item in file_list" :key="item.id">
          <n-card hoverable class="ocr_card" :class="get_card_status_class(item)">
            <!-- 卡片头部：状态与操作 -->
            <template #header>
              <div class="card_header">
                <n-tag :type="get_status_type(item.status)" size="small">
                  {{ get_status_text(item.status) }}
                </n-tag>
                <div class="card_actions">
                  <n-button text circle size="small" @click="remove_file(item.id)">
                    <template #icon><Trash size="16" /></template>
                  </n-button>
                </div>
              </div>
            </template>
            
            <!-- 卡片主体：缩略图 -->
            <div class="card_preview" @click="open_verify_modal(item)">
              <div class="img_wrapper">
                <img 
                  :src="item.preview_url" 
                  :style="{ transform: `rotate(${item.rotation || 0}deg)` }"
                />
                <div class="overlay" v-if="item.status === 'processing'">
                  <n-spin size="medium" />
                </div>
                <!-- 即使是 processing 状态，如有需要也可以让他点击停止，这里只在非 processing 显示操作引导 -->
                <div class="overlay edit_hint" v-else>
                  <n-button type="primary" size="small">
                    <template #icon><Edit size="14"/></template>
                    {{ item.status === 'success' ? '核对信息' : '手动补全' }}
                  </n-button>
                </div>
              </div>
            </div>

            <!-- 卡片底部：摘要信息 -->
            <div class="card_footer">
              <div class="title_text" :title="item.data.title || '待识别成果'">
                {{ item.data.title || '未命名成果' }}
              </div>
              <div class="info_text">
                {{ format_date(item.data.date) }}
              </div>
            </div>
          </n-card>
        </n-grid-item>
      </n-grid>
      
      <!-- 空状态 -->
      <div v-if="file_list.length === 0" class="empty_state">
        <n-empty description="暂无上传记录，请上方拖拽文件开始识别" />
      </div>
    </div>

    <!-- 左右对照纠错模态框 -->
    <n-modal
      v-model:show="show_verify_modal"
      class="verify_modal"
      preset="card"
      :style="{ width: '95vw', height: '90vh', maxWidth: '1600px' }"
      title="信息核对与完善"
      :bordered="false"
      size="huge" 
    >
      <div class="verify_container" v-if="current_file">
        
        <!-- 左侧：图片查看器 -->
        <div class="left_panel">
          <div class="image_toolbar">
            <n-button-group size="small">
              <n-button ghost @click="rotate_img(-90)"><template #icon><Rotate2 /></template></n-button>
              <n-button ghost @click="rotate_img(90)"><template #icon><RotateClockwise2 /></template></n-button>
              <n-button ghost @click="zoom_img(0.1)"><template #icon><ZoomIn /></template></n-button>
              <n-button ghost @click="zoom_img(-0.1)"><template #icon><ZoomOut /></template></n-button>
              <n-button ghost @click="reset_img_transform">重置</n-button>
            </n-button-group>
          </div>
          
          <div 
            class="image_viewport" 
            ref="viewport_ref"
            @wheel.prevent="handle_wheel"
            @mousedown="handle_m_down"
          >
            <!-- 统一使用 horizontal 布局逻辑，如果原来是竖图，用户可以通过旋转变成横图 -->
            <!-- 增加 'checkered-bg' 类以显示透明背景网格，提升专业感 -->
            <div class="image_canvas">
                <img 
                :src="current_file.preview_url" 
                class="target_image"
                :style="image_transform_style"
                />
            </div>
          </div>
        </div>

        <!-- 右侧：表单编辑 -->
        <div class="right_panel">
          <div class="form_header">
            <h3>核心字段核对</h3>
            <div class="form_assist_text">标题由 AI 智能生成，请核对后再提交</div>
          </div>
          
          <n-form
            ref="verify_form_ref"
            :model="current_file.data"
            :rules="form_rules"
            label-placement="top"
            size="medium"
          >
             <n-form-item label="成果标题" path="title" class="title_form_item">
               <n-input
                 v-model:value="current_file.data.title"
                 placeholder="AI 已根据证书内容智能生成，请核对并修改"
                 size="large"
               />
             </n-form-item>

             <n-form-item label="参赛学生" path="student_name" class="participant_form_item" required>
               <div class="students-tags-container">
                 <template v-if="current_file.data.team_members && current_file.data.team_members.length > 0">
                   <template v-for="(student, index) in current_file.data.team_members" :key="index">
                     <n-input
                       v-if="editingStudentIndex === index"
                       v-model:value="editingStudentName"
                       size="small"
                       class="student-edit-input"
                       :autofocus="true"
                       @keyup.enter="confirmEditStudent(index)"
                       @blur="confirmEditStudent(index)"
                     />
                     <n-tag
                       v-else
                       type="info"
                       closable
                       class="student-tag-editable"
                       @close="removeStudent(index)"
                       @click="startEditStudent(index, student)"
                     >
                       {{ student }}
                     </n-tag>
                   </template>
                   <n-input
                     v-if="addingStudent"
                     v-model:value="newStudentName"
                     size="small"
                     class="student-edit-input"
                     placeholder="输入姓名"
                     :autofocus="true"
                     @keyup.enter="confirmAddStudent"
                     @blur="confirmAddStudent"
                   />
                   <n-button v-else text @click="startAddStudent" size="small">
                     <template #icon>
                       <n-icon><IconPlus /></n-icon>
                     </template>
                     添加参赛学生
                   </n-button>
                 </template>
                 <template v-else>
                   <n-input
                     v-if="addingStudent"
                     v-model:value="newStudentName"
                     size="small"
                     class="student-edit-input"
                     placeholder="输入姓名"
                     :autofocus="true"
                     @keyup.enter="confirmAddStudent"
                     @blur="confirmAddStudent"
                   />
                   <n-button v-else text @click="startAddStudent" size="small">
                     <template #icon>
                       <n-icon><IconPlus /></n-icon>
                     </template>
                     添加参赛学生
                   </n-button>
                 </template>
               </div>
               <n-input 
                 v-show="false"
                 v-model:value="current_file.data.student_name"
               />
             </n-form-item>

             <!-- 第一行：日期与类别 -->
             <n-grid :cols="2" :x-gap="12">
              <n-grid-item>
                <n-form-item label="获奖日期" path="date">
                   <n-date-picker 
                      v-model:value="current_file.data.date" 
                      type="date"
                      style="width: 100%"
                      clearable
                   />
                </n-form-item>
              </n-grid-item>
              <n-grid-item>
                <n-form-item label="成果类别" path="category">
                  <n-select v-model:value="current_file.data.category" :options="category_opts" />
                </n-form-item>
              </n-grid-item>
            </n-grid>

            <!-- 第二行：奖项与等级 -->
            <n-grid :cols="2" :x-gap="12">
              <n-grid-item>
                <n-form-item label="具体奖项" path="award">
                  <n-input v-model:value="current_file.data.award" placeholder="如：一等奖" />
                </n-form-item>
              </n-grid-item>
              <n-grid-item>
                <n-form-item label="奖项等级" path="level">
                  <n-select v-model:value="current_file.data.level" :options="level_opts" />
                </n-form-item>
              </n-grid-item>
            </n-grid>

            <n-form-item label="指导教师" path="teacher_ids" class="teacher_form_item" :required="!is_teacher_optional_category(current_file?.data?.category)">
              <n-select
                v-model:value="current_file.data.teacher_ids"
                :options="teacher_opts"
                multiple
                filterable
                placeholder="搜索并选择指导教师"
                :loading="loading_teachers"
                clearable
              />
              <div v-if="current_file.data.advisors_text" class="ocr-hint">
                <n-icon size="14" style="margin-right: 4px;"><IconInfoCircle /></n-icon>
                证书识别：{{ current_file.data.advisors_text }}
              </div>
            </n-form-item>

            <!-- 专利专属字段（当类别为专利时显示） -->
            <template v-if="current_file?.data?.category === 'patent'">
              <n-divider style="margin: 12px 0 8px">专利详细信息</n-divider>
              <n-grid :cols="2" :x-gap="12">
                <n-grid-item>
                  <n-form-item label="专利号 / 登记号">
                    <n-input
                      v-model:value="current_file.data.patent_number"
                      placeholder="如：CN123456789B"
                      clearable
                    />
                  </n-form-item>
                </n-grid-item>
                <n-grid-item>
                  <n-form-item label="发明人 / 著作权人">
                    <n-input
                      v-model:value="current_file.data.patent_inventors"
                      placeholder="多人用顿号分隔，如：张三、李四"
                      clearable
                    />
                  </n-form-item>
                </n-grid-item>
              </n-grid>
              <n-grid :cols="1" :x-gap="12">
                <n-grid-item>
                  <n-form-item label="专利权人 / 著作权人单位">
                    <n-input
                      v-model:value="current_file.data.patent_holder"
                      placeholder="如：广西警察学院"
                      clearable
                    />
                  </n-form-item>
                </n-grid-item>
              </n-grid>
            </template>

            <!-- 扩展信息：论文/科研/项目相关补充字段 -->
            <n-collapse v-if="['paper', 'research'].includes(current_file?.data?.category || '')" class="extra-fields-collapse">
              <n-collapse-item :title="current_file?.data?.category === 'paper' ? '📋 论文补充信息' : '📋 科研补充信息'" name="extra">
                <n-grid :cols="2" :x-gap="12">
                  <n-grid-item>
                    <n-form-item label="论文/作品题目">
                      <n-input 
                        v-model:value="current_file.data.paper_title" 
                        placeholder="如有论文，请填写完整题目"
                      />
                    </n-form-item>
                  </n-grid-item>
                  <n-grid-item>
                    <n-form-item label="期刊/会议名称">
                      <n-input 
                        v-model:value="current_file.data.journal_name"
                        placeholder="如 SCI一区、EI、核心期刊等"
                      />
                    </n-form-item>
                  </n-grid-item>
                  <n-grid-item>
                    <n-form-item label="项目名称">
                      <n-input 
                        v-model:value="current_file.data.project_name"
                        placeholder="若为科研/创业项目，填写项目全称"
                      />
                    </n-form-item>
                  </n-grid-item>
                  <n-grid-item>
                    <n-form-item label="承担角色">
                      <n-input 
                        v-model:value="current_file.data.role"
                        placeholder="如：第一作者、项目负责人、主要参与者"
                      />
                    </n-form-item>
                  </n-grid-item>
                </n-grid>
              </n-collapse-item>
            </n-collapse>

          </n-form>
          
          <div class="panel_footer">
            <n-alert v-if="current_file.status === 'error'" type="warning" class="mb-4" title="识别提示" closable>
              AI 未能完全识别所有字段，请人工补全信息后再提交。
            </n-alert>
            <n-space vertical>
              <n-button
                block
                secondary
                size="medium"
                :loading="current_file.status === 'processing'"
                @click="rerun_ocr(current_file)"
              >
                🔄 切换类型重新识别（当前：{{ category_opts.find(o => o.value === current_file.data.category)?.label || '自动' }}）
              </n-button>
              <n-button block type="primary" size="large" @click="submit_single(current_file)" :loading="submitting">
                确认无误，提交审核
              </n-button>
            </n-space>
          </div>
        </div>
      </div>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import type { UploadCustomRequestOptions } from 'naive-ui'
import { 
  IconArrowLeft as ArrowLeft, 
  IconCloudUpload as CloudUpload,
  IconTrash as Trash,
  IconEdit as Edit,
  IconRotate2 as Rotate2,
  IconRotateClockwise2 as RotateClockwise2,
  IconZoomIn as ZoomIn,
  IconZoomOut as ZoomOut,
  IconPlus,              // 🔥 新增：添加学生按钮
  IconInfoCircle         // 🔥 新增：提示图标
} from '@tabler/icons-vue'
import { recognizeCertificate, submitAchievement, getTeachers, getStudentMe } from '@/api'

// === 类型定义 ===
interface FileItem {
  id: string
  file: File
  preview_url: string
  // 增加 'ready_to_verify' 状态区分，防止 forever processing
  status: 'processing' | 'success' | 'error' | 'submitted' 
  rotation?: number
  data: {
    student_name: string  // 显示用，从team_members或当前用户获取
    title: string
    date: number | null
    category: string
    level: string
    award: string
    teacher_id: number | null  // 主要指导教师（必填）
    teacher_ids?: number[]     // 🔥 新增：所有指导教师ID列表
    advisors_text?: string     // 🔥 新增：所有指导老师姓名（显示用）
    evidence_url: string
    // 扩展字段
    cert_type_hint?: string    // 上传前用户指定的证书类型
    issuer?: string
    certificate_number?: string
    project_name?: string
    paper_title?: string
    journal_name?: string
    journal_level?: string
    publish_status?: string
    author_order?: string
    doi?: string
    authors?: string[]
    first_author?: string
    publish_date?: string
    issn?: string
    role?: string
    location?: string
    team_members?: string[]
    additional_info?: string
    // 专利专属字段
    patent_number?: string     // 专利号/登记号
    patent_inventors?: string  // 发明人（顿号分隔）
    patent_holder?: string     // 专利权人单位
  }
}

// === 状态管理 ===
const router = useRouter()
const message = useMessage()
const file_list = ref<FileItem[]>([])
const show_verify_modal = ref(false)
const default_cert_type = ref<string>('')   // '' = 自动识别
const current_file = ref<FileItem | null>(null)
const submitting = ref(false)
const verify_form_ref = ref()
const loading_teachers = ref(false)
const current_user_name = ref('')
const editingStudentIndex = ref<number | null>(null)
const editingStudentName = ref('')
const addingStudent = ref(false)
const newStudentName = ref('')

const startEditStudent = (index: number, name: string) => {
  editingStudentIndex.value = index
  editingStudentName.value = name
}

const confirmEditStudent = (index: number) => {
  if (!current_file.value) return
  const name = editingStudentName.value.trim()
  if (name) {
    current_file.value.data.team_members![index] = name
    current_file.value.data.student_name = current_file.value.data.team_members!.join('、')
  }
  editingStudentIndex.value = null
}

const startAddStudent = () => {
  newStudentName.value = ''
  addingStudent.value = true
}

const confirmAddStudent = () => {
  const name = newStudentName.value.trim()
  if (name && current_file.value) {
    if (!current_file.value.data.team_members) {
      current_file.value.data.team_members = []
    }
    current_file.value.data.team_members.push(name)
    current_file.value.data.student_name = current_file.value.data.team_members.join('、')
  }
  addingStudent.value = false
  newStudentName.value = ''
}

// 选项与规则
// 上传前类型预选选项
const cert_type_select_opts = [
  { label: '竞赛类', value: 'competition' },
  { label: '专利 / 软件著作权', value: 'patent' },
  { label: '科研成果', value: 'research' },
  { label: '项目类', value: 'project' },
  { label: '荣誉证书', value: 'certificate' },
]

const category_opts = [
  { label: '竞赛类', value: 'competition' },
  { label: '科研类', value: 'research' },
  { label: '项目类', value: 'project' },
  { label: '论文类', value: 'paper' },
  { label: '专利类', value: 'patent' },
  { label: '证书类', value: 'certificate' }
]
const level_opts = [
  { label: '国家级', value: 'international' },
  { label: '省部级', value: 'provincial' },
  { label: '校级', value: 'university' },
  { label: '院级', value: 'college' }
]
const teacher_opts = ref<{label: string, value: number}[]>([])

const is_teacher_optional_category = (category?: string | null) => category === 'patent'

const form_rules = {
  title: { required: true, message: '标题不能为空', trigger: 'blur' },
  student_name: {
    required: true,
    trigger: ['blur', 'change'],
    validator: (_rule: any, value: string) => {
      const names = current_file.value?.data?.team_members || []
      if (Array.isArray(names) && names.length > 0) return true
      if (typeof value === 'string' && value.trim()) return true
      return new Error('请至少填写一位参赛学生')
    }
  },
  date: { required: true, message: '日期必选', trigger: 'change', type: 'number' },
  category: { required: true, message: '类别必选', trigger: 'change' },
  level: {
    required: true, trigger: 'change',
    validator: (_rule: any, value: string) => {
      if (current_file.value?.data?.category === 'patent') return true
      if (!value) return new Error('等级必选')
      return true
    }
  },
  award: {
    required: true, trigger: 'blur',
    validator: (_rule: any, value: string) => {
      if (current_file.value?.data?.category === 'patent') return true
      if (!value) return new Error('奖项必填')
      return true
    }
  },
  teacher_ids: {
    required: false,
    trigger: 'change',
    validator: (_rule: any, value: number[] | undefined) => {
      if (is_teacher_optional_category(current_file.value?.data?.category)) return true
      if (!Array.isArray(value) || value.length === 0) {
        return new Error('请至少选择一位指导教师')
      }
      return true
    }
  }
}

// === 图片控制状态 ===
const img_state = reactive({
  scale: 1,
  rotate: 0,
  x: 0,
  y: 0,
  is_dragging: false,
  start_x: 0,
  start_y: 0
})

// === 方法 ===

const go_back = () => router.push('/student/achievement')

// 🔥 新增：移除学生
const removeStudent = (index: number) => {
  if (current_file.value && current_file.value.data.team_members) {
    current_file.value.data.team_members.splice(index, 1)
    // 同步更新student_name
    if (current_file.value.data.team_members.length > 0) {
      current_file.value.data.student_name = current_file.value.data.team_members.join('、')
    } else {
      current_file.value.data.student_name = current_user_name.value
    }
  }
}

// 1. 批量上传处理
const handle_batch_upload = async ({ file, onFinish }: UploadCustomRequestOptions) => {
  if (!file.file) return
  
  // 创建文件项
  const newItem: FileItem = {
    id: Math.random().toString(36).substr(2, 9),
    file: file.file,
    preview_url: URL.createObjectURL(file.file),
    status: 'processing',
    rotation: 0, // 默认不旋转
    data: {
      student_name: current_user_name.value || '加载中...',
      title: '',
      date: Date.now(),
      category: default_cert_type.value === 'patent' ? 'patent'
              : default_cert_type.value === 'research' ? 'research'
              : default_cert_type.value === 'project' ? 'project'
              : default_cert_type.value === 'certificate' ? 'certificate'
              : 'competition',
      level: 'university',
      award: '',
      teacher_id: null,
      teacher_ids: [],
      advisors_text: '',
      evidence_url: '',
      cert_type_hint: default_cert_type.value || undefined,
      issuer: '',
      certificate_number: '',
      project_name: '',
      paper_title: '',
      journal_name: '',
      role: '',
      location: '',
      team_members: [],
      additional_info: '',
      patent_number: '',
      patent_inventors: '',
      patent_holder: '',
    }
  }
  
  // 将新项添加到列表头部
  file_list.value.unshift(newItem)
  
  // 关键修复：直接使用 newItem 引用
  const reactiveItem = file_list.value.find(i => i.id === newItem.id) || newItem
  
  // 开始识别当前这个特定的文件
  process_ocr(reactiveItem)
  
  // 关键修复：通知 Naive UI 上传已结束，防止它认为任务挂起并在后续操作中重复触发
  if (onFinish) onFinish()
}

const process_ocr = async (item: FileItem) => {
  try {
    const certTypeHint = item.data.cert_type_hint || undefined
    let res = await recognizeCertificate(item.file, certTypeHint)

    console.log('📄 OCR识别响应 (cert_type=' + (certTypeHint || 'auto') + '):', res)

    // 如果未指定类型但自动检测到为专利/科研/项目，自动切换专属模板重新识别
    if (!certTypeHint && res?.recognized_data) {
      const autoType = res.recognized_data.suggested_type
      const retryTypes = ['patent', 'research', 'project']
      if (autoType && retryTypes.includes(autoType)) {
        console.log(`🔄 自动检测为 [${autoType}]，切换专属模板重新识别...`)
        const retryRes = await recognizeCertificate(item.file, autoType)
        if (retryRes?.recognized_data) {
          res = retryRes
          item.data.cert_type_hint = autoType
        }
      }
    }
    
    if (res && res.recognized_data) {
      item.status = 'success'
      const raw = res.recognized_data
      
      console.log('🔍 开始字段映射，原始数据:', raw)
      console.log('👨‍🏫 当前教师选项数量:', teacher_opts.value.length)
      
      // ========== 基础字段映射 ==========
      
      // 1. 保存文件URL（必须）
      item.data.evidence_url = res.file_url
      console.log('✅ evidence_url:', item.data.evidence_url)
      
      // 2. 标题 —— 无条件覆盖：后端已经过智能标题生成，直接采用
      // （不用 if(raw.title) 的原因：避免后端返回空字符串时使用旧初始值）
      item.data.title = raw.title || ''
      console.log('✅ title (from backend smart gen):', item.data.title)
      if (!item.data.title) {
        console.warn('⚠️ 后端返回的标题为空，请检查智能标题生成逻辑或OCR模型输出')
      }
      
      // 3. 日期处理
      if (raw.date) {
        const d = new Date(raw.date)
        if (!isNaN(d.getTime())) {
          item.data.date = d.getTime()
          console.log('✅ date:', new Date(item.data.date).toLocaleDateString())
        } else {
          console.warn('⚠️ 日期解析失败:', raw.date)
        }
      }
      
      // ========== 奖项级别智能识别 ==========
      
      if (raw.award_level) {
        let text = String(raw.award_level)
        console.log('🏆 开始识别奖项级别:', text)
        
        const nationalKeywords = ['全国', '教育部', '国家级', '中国', '中华', '国务院', '中央']
        const provincialKeywords = ['省', '厅', '自治区', '直辖市', '市', '省部'] 
        const collegeKeywords = ['系', '分院']

        const isNational = nationalKeywords.some(key => text.includes(key))
        const isProvincial = provincialKeywords.some(key => text.includes(key))

        let identifiedLevel = 'university' // 默认兜底

        if (text.includes('部') && !text.includes('系部') && !text.includes('俱乐部') && !text.includes('省部')) {
           identifiedLevel = 'international'
        }
        else if (isNational) {
           identifiedLevel = 'international'
        }
        else if (isProvincial) {
           identifiedLevel = 'provincial'
        }
        else {
            if (text.includes('大学') && (text.includes('学院') || text.includes('系'))) {
                identifiedLevel = 'college'
            }
            else if (collegeKeywords.some(key => text.includes(key))) {
                identifiedLevel = 'college'
            }
            else {
                if (text.includes('院级')) {
                    identifiedLevel = 'college'
                } else if (text.includes('校级') || text.includes('大学') || text.includes('校') || text.includes('学院')) {
                    identifiedLevel = 'university'
                } else {
                    identifiedLevel = 'university'
                }
            }
        }
        
        item.data.level = identifiedLevel
        console.log('✅ level:', item.data.level, '(从"' + text + '"识别)')
      }
      
      // ========== 具体奖项处理 ==========
      
      if (raw.award) {
        item.data.award = raw.award
        console.log('✅ award (直接):', item.data.award)
      } else if (raw.award_level) {
        // 容错：从award_level提取具体奖项
        const levelText = String(raw.award_level)
        
        // 🔑 完整的奖项关键词列表（按优先级排序）
        const awardKeywords = [
          '特等奖',        // 最高级别
          '一等奖',        // 常规等级奖
          '二等奖',
          '三等奖',
          '金奖',          // 金银铜奖
          '银奖',
          '铜奖',
          '优秀奖',        // 优秀类
          '优胜奖',        // 🔥 新增：优胜奖
          '鼓励奖',        // 鼓励类
          '入围奖',        // 入围类
          '参与奖',        // 参与类
          '最佳创意奖',    // 最佳类奖项
          '最佳设计奖',
          '最佳人气奖',
          '最佳组织奖',
          '单项奖',        // 单项类
          '提名奖'         // 提名类
        ]
        
        // 🔍 按顺序查找第一个匹配的奖项（优先级高的先匹配）
        const foundAward = awardKeywords.find(keyword => levelText.includes(keyword))
        
        if (foundAward) {
          item.data.award = foundAward
          console.log('✅ award (从award_level提取):', item.data.award)
        } else {
          console.warn('⚠️ 未能从award_level提取具体奖项:', levelText)
          // 如果实在提取不到，使用原始的award_level作为兜底
          item.data.award = levelText
          console.log('🔄 使用原始award_level作为奖项:', item.data.award)
        }
      } else {
        console.warn('⚠️ 后端未返回award字段')
      }
      
      // ========== 成果类别 ==========
      
      if (raw.suggested_type) {
        const found = category_opts.find(c => c.value === raw.suggested_type)
        if (found) {
          item.data.category = raw.suggested_type
          console.log('✅ category:', item.data.category)
        } else {
          console.warn('⚠️ suggested_type不在选项中:', raw.suggested_type)
        }
      }
      
      // ========== 参赛学生填充（🔥 优化：显示所有team_members） ==========
      
      if (raw.team_members && Array.isArray(raw.team_members) && raw.team_members.length > 0) {
        // 使用所有团队成员的名字
        item.data.student_name = raw.team_members.join('、')
        console.log('✅ student_name (团队):', item.data.student_name, `(${raw.team_members.length}人)`)
      } else if (raw.recipient_name) {
        // 如果没有团队成员，使用单个获奖人
        item.data.student_name = raw.recipient_name
        console.log('✅ student_name (个人):', item.data.student_name)
      } else {
        // 保持当前用户名作为默认值
        console.log('ℹ️ 使用当前用户作为参赛学生:', item.data.student_name)
      }
      
      // ========== 指导老师匹配（🔥 优化：匹配所有advisors） ==========
      
      const matchTeacherAsync = async () => {
        // 如果教师数据还没加载，等待一下
        if (teacher_opts.value.length === 0) {
          console.log('⏳ 教师数据未加载，等待500ms...')
          await new Promise(resolve => setTimeout(resolve, 500))
        }
        
        if (raw.advisors && Array.isArray(raw.advisors) && raw.advisors.length > 0) {
          console.log('👨‍🏫 开始匹配指导老师，后端返回:', raw.advisors)
          console.log('👨‍🏫 可选教师列表:', teacher_opts.value.map(t => t.label).join(', '))
          
          // 🔥 保存原始advisors文本用于显示
          item.data.advisors_text = raw.advisors.filter((a: any) => a && typeof a === 'string').join('、')
          console.log('✅ advisors_text:', item.data.advisors_text)
          
          // 🔥 匹配所有教师，而不只是第一个
          const matchedTeachers: number[] = []
          
          for (const advisorName of raw.advisors) {
            if (advisorName && typeof advisorName === 'string' && advisorName.trim()) {
              const match = teacher_opts.value.find(t => {
                const name = t.label.split('(')[0].trim()
                return advisorName.includes(name) || name.includes(advisorName)
              })
              
              if (match) {
                matchedTeachers.push(match.value)
                console.log('🎯 匹配成功:', advisorName, '←→', match.label)
              } else {
                console.warn('⚠️ 未找到匹配的教师:', advisorName)
              }
            }
          }
          
          // 设置所有指导教师
          if (matchedTeachers.length > 0) {
            item.data.teacher_ids = matchedTeachers
            console.log('✅ teacher_ids:', matchedTeachers, `(${matchedTeachers.length}人)`)
          } else {
            console.warn('⚠️ 所有教师均未匹配成功')
            console.warn('   可能原因：1) 教师不在系统中 2) 名字识别有误 3) 教师数据未加载')
          }
        } else {
          console.warn('⚠️ 后端未返回advisors或为空数组')
        }
      }
      
      // 异步执行教师匹配，不阻塞其他字段填充
      matchTeacherAsync().catch(err => {
        console.error('❌ 教师匹配失败:', err)
      })

      // ========== 补充信息字段 ==========
      
      if (raw.issuer) {
        item.data.issuer = raw.issuer
        console.log('✅ issuer:', item.data.issuer)
      }
      
      if (raw.certificate_number) {
        item.data.certificate_number = raw.certificate_number
        console.log('✅ certificate_number:', item.data.certificate_number)
      }
      
      if (raw.project_name) {
        item.data.project_name = raw.project_name
        console.log('✅ project_name:', item.data.project_name)
      }
      
      // ========== 新增扩展字段：学术成果详细信息 ==========
      
      if (raw.paper_title) {
        item.data.paper_title = raw.paper_title
        console.log('✅ paper_title:', item.data.paper_title)
      }
      
      if (raw.journal_name) {
        item.data.journal_name = raw.journal_name
        console.log('✅ journal_name:', item.data.journal_name)
      }
      
      if (raw.role) {
        item.data.role = raw.role
        console.log('✅ role:', item.data.role)
      }
      
      if (raw.location) {
        item.data.location = raw.location
        console.log('✅ location:', item.data.location)
      }
      
      if (raw.team_members && Array.isArray(raw.team_members)) {
        item.data.team_members = raw.team_members
        console.log('✅ team_members:', item.data.team_members.join(', '))
      }
      
      if (raw.additional_info) {
        item.data.additional_info = raw.additional_info
        console.log('✅ additional_info:', item.data.additional_info)
      }

      // ========== 论文专属字段 ==========

      if (raw.journal_level) {
        item.data.journal_level = raw.journal_level
        console.log('✅ journal_level:', item.data.journal_level)
      }

      if (raw.publish_status) {
        item.data.publish_status = raw.publish_status
        console.log('✅ publish_status:', item.data.publish_status)
      }

      if (raw.author_order) {
        item.data.author_order = raw.author_order
        console.log('✅ author_order:', item.data.author_order)
      }

      if (raw.doi) {
        item.data.doi = raw.doi
        console.log('✅ doi:', item.data.doi)
      }

      if (raw.authors && Array.isArray(raw.authors)) {
        item.data.authors = raw.authors
        console.log('✅ authors:', item.data.authors.join(', '))
      }

      if (raw.first_author) {
        item.data.first_author = raw.first_author
        console.log('✅ first_author:', item.data.first_author)
      }

      if (raw.publish_date) {
        item.data.publish_date = raw.publish_date
        console.log('✅ publish_date:', item.data.publish_date)
      }

      if (raw.issn) {
        item.data.issn = raw.issn
        console.log('✅ issn:', item.data.issn)
      }

      // ========== 专利专属字段 ==========
      if (item.data.category === 'patent') {
        // 专利号
        item.data.patent_number = (raw.patent_number || raw.certificate_number || '') as string
        // 发明人：优先 team_members，回退 recipient_name
        item.data.patent_inventors = Array.isArray(raw.team_members) && raw.team_members.length
          ? (raw.team_members as string[]).join('、')
          : ((raw.recipient_name || '') as string)
        // 专利权人
        item.data.patent_holder = (raw.patent_holder || '') as string
        // 专利类型 → 写入 award 字段（弹窗"具体奖项"即显示专利类型）
        if (!item.data.award) {
          item.data.award = (raw.patent_type || raw.award || '') as string
        }
        console.log('✅ patent_number:', item.data.patent_number)
        console.log('✅ patent_inventors:', item.data.patent_inventors)
        console.log('✅ patent_holder:', item.data.patent_holder)
      }

      console.log('📊 字段填充完成，最终数据:', item.data)

      // ========== 路由分发：论文类跳转至论文表单 ==========
      if (raw.document_type === 'paper') {
        console.log('📄 检测到论文文档，跳转至论文成果填写表单')
        const paper_ocr_data = {
          evidence_url: item.data.evidence_url,
          title: item.data.title || '',
          paper_title: raw.paper_title || '',
          journal_name: raw.journal_name || '',
          journal_level: raw.journal_level || '',
          publish_status: raw.publish_status || '',
          publish_date: raw.publish_date || '',
          authors: raw.authors || [],
          first_author: raw.first_author || '',
          author_order: raw.author_order || '',
          doi: raw.doi || '',
          issn: raw.issn || '',
          student_name: item.data.student_name || ''
        }
        localStorage.setItem('ocr_paper_data', JSON.stringify(paper_ocr_data))
        router.push('/student/achievement-collect')
        return
      }

      // 证书类：自动打开核对窗口
      open_verify_modal(item)
      
    } else {
      // 完全无法识别结构化数据
      console.error('❌ OCR识别失败，无recognized_data')
      item.status = 'error'
      item.data.evidence_url = res?.file_url || ''
      message.warning('未能识别有效信息，请手动填写')
      open_verify_modal(item)
    }
  } catch (e) {
    console.error('❌ OCR处理异常:', e)
    item.status = 'error'
  }
}

// 2. 卡片列表操作
const remove_file = (id: string) => {
  file_list.value = file_list.value.filter(i => i.id !== id)
}

const get_status_type = (s: string) => {
  if (s === 'processing') return 'info'
  if (s === 'success') return 'success'
  if (s === 'error') return 'warning'
  return 'default'
}

const get_status_text = (s: string) => {
  if (s === 'processing') return '识别中...'
  if (s === 'success') return '待核对'
  if (s === 'error') return '需补全'
  return '未知'
}

const format_date = (d: number | null) => {
  if (!d) return '--'
  return new Date(d).toLocaleDateString()
}

const get_card_status_class = (item: FileItem) => {
  return {
    'border-warning': item.status === 'error',
    'border-success': item.status === 'success'
  }
}

// 重新识别：用当前 category 作为 cert_type 重跑 OCR
const rerun_ocr = async (item: FileItem) => {
  if (!item.file) return
  const certType = item.data.category || undefined
  item.data.cert_type_hint = certType
  item.status = 'processing'
  await process_ocr(item)
  message.success('重新识别完成，请核对字段')
}

// 3. 模态框逻辑
const open_verify_modal = (item: FileItem) => {
  // 如果还在处理中，提示用户稍等，或者不响应（但上面按钮已经限制了）
  if (item.status === 'processing') {
      message.info('正在识别中，请稍候...')
      return
  }
  
  current_file.value = item
  show_verify_modal.value = true
  
  // 弹窗默认保持原图方向，用户可在工具栏中手动旋转
  Object.assign(img_state, { 
      scale: 1, 
      rotate: 0,
      x: 0, 
      y: 0 
  })
}

// 4. 图片交互逻辑
const image_transform_style = computed(() => ({
  transform: `translate(${img_state.x}px, ${img_state.y}px) rotate(${img_state.rotate}deg) scale(${img_state.scale})`,
  transition: img_state.is_dragging ? 'none' : 'transform 0.2s ease-out'
}))

const zoom_img = (delta: number) => {
  img_state.scale = Math.max(0.2, Math.min(5, img_state.scale + delta))
}
const rotate_img = (deg: number) => {
  img_state.rotate += deg
}
const reset_img_transform = () => {
  Object.assign(img_state, { scale: 1, rotate: 0, x: 0, y: 0 })
}
const handle_wheel = (e: WheelEvent) => {
  // 阻止默认滚动
  const delta = e.deltaY > 0 ? -0.1 : 0.1
  zoom_img(delta)
}
const handle_m_down = (e: MouseEvent) => {
  img_state.is_dragging = true
  img_state.start_x = e.clientX - img_state.x
  img_state.start_y = e.clientY - img_state.y
  
  const move = (ev: MouseEvent) => {
    img_state.x = ev.clientX - img_state.start_x
    img_state.y = ev.clientY - img_state.start_y
  }
  const up = () => {
    img_state.is_dragging = false
    document.removeEventListener('mousemove', move)
    document.removeEventListener('mouseup', up)
  }
  document.addEventListener('mousemove', move)
  document.addEventListener('mouseup', up)
}

// 5. 提交逻辑
const submit_single = async (item: FileItem) => {
  // 🔥 提交前验证和准备
  if (!is_teacher_optional_category(item.data.category) && (!item.data.teacher_ids || item.data.teacher_ids.length === 0)) {
    message.warning('请至少选择一位指导教师')
    return
  }
  
  // 🔥 从teacher_ids数组中取第一个作为主要教师ID
  item.data.teacher_id = item.data.teacher_ids && item.data.teacher_ids.length > 0
    ? item.data.teacher_ids[0]
    : null
  
  verify_form_ref.value?.validate(async (errors: any) => {
    if (!errors) {
      submitting.value = true
      try {
        console.log('📤 提交数据:', {
          title: item.data.title,
          teacher_id: item.data.teacher_id,
          teacher_ids: item.data.teacher_ids,
          team_members: item.data.team_members
        })
        
        await submitAchievement({
          title: item.data.title,
          type: item.data.category as any,
          evidence_url: item.data.evidence_url,
          teacher_id: item.data.teacher_id ?? undefined,
          content_json: {
            award: item.data.award,
            level: item.data.level,
            date: (() => {
              const d = new Date(item.data.date!)
              const yyyy = d.getFullYear()
              const mm = String(d.getMonth() + 1).padStart(2, '0')
              const dd = String(d.getDate()).padStart(2, '0')
              return `${yyyy}-${mm}-${dd}`
            })(),
            // 🔥 保存所有指导教师ID
            teacher_ids: item.data.teacher_ids,
            // 补充更多 OCR 识别出的元数据
            issuer: item.data.issuer,
            certificate_number: item.data.certificate_number,
            project_name: item.data.project_name,
            team_members: item.data.team_members,
            additional_info: item.data.additional_info
          }
        })
        message.success('提交成功！')
        // 移除已提交的项
        remove_file(item.id)
        show_verify_modal.value = false
      } catch (e: any) {
        console.error('❌ 提交失败:', e)
        message.error(e.message || '提交失败')
      } finally {
        submitting.value = false
      }
    } else {
      message.warning('请完善红框标注的必填信息')
    }
  })
}

// 6. 初始化
onMounted(async () => {
  // 加载教师
  loading_teachers.value = true
  try {
    const res = await getTeachers()
    if (res && Array.isArray(res)) {
       teacher_opts.value = res.map((t: any) => ({
         label: `${t.name} (${t.department || '未知学院'})`,
         value: t.id
       }))
    }
  } finally {
    loading_teachers.value = false
  }
  
  // 🔥 修复3：加载当前用户（兼容多种返回格式）
  try {
      const response = await getStudentMe()
      const u = response?.data || response
      
      if (u) {
          current_user_name.value = u.name || u.username || '未知用户'
      } else {
          current_user_name.value = '未知用户'
      }
  } catch(e) { 
      console.error('获取用户信息失败', e) 
      current_user_name.value = '未知用户'
  }
})

</script>

<style scoped>
.ocr_page {
  padding: 24px;
  max-width: 1600px;
  margin: 0 auto;
  min-height: 100vh;
  background-color: #f6f9fc;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}
.header_content h1 {
  font-size: 24px;
  color: #1f2937;
  margin: 0 0 8px 0;
}
.header_content p {
  color: #6b7280;
  margin: 0;
}

.upload_section {
  background: white;
  border-radius: 12px;
  padding: 24px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  margin-bottom: 24px;
}

.batch_dragger {
  background-color: #f8fafc;
  border: 2px dashed #e2e8f0;
  border-radius: 12px;
  height: 160px;
  display: flex;
  justify-content: center;
  align-items: center;
}
.batch_dragger:hover {
  border-color: #409eff;
  background-color: #f0f9ff;
}
.dragger_content {
  text-align: center;
}

/* === 卡片容器 - 严格固定尺寸 === */
.ocr_card {
  border-radius: 12px;
  transition: all 0.3s;
  overflow: hidden;
  width: 100%; 
  height: 380px; /* 🔑 稍微增加高度，防止底部被切断 */
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
}

/* Naive UI n-card 内容区域约束 */
.ocr_card :deep(.n-card__content) {
  padding: 16px;
  box-sizing: border-box;
  flex: 1; /* 🔑 自动填充剩余空间 */
  display: flex;
  flex-direction: column;
  overflow: hidden; /* 🔑 确保内容不溢出 */
  min-height: 0; /* 🔑 关键：允许 flex 子元素收缩 */
}

.ocr_card:hover {
  transform: translateY(-4px);
  box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
}
.ocr_card.border-warning {
  border: 2px solid #f59e0b;
}
.ocr_card.border-success {
  border: 2px solid #10b981;
}

/* 卡片头部 - 固定高度 */
.card_header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  height: 20px; /* 固定头部高度 */
  flex-shrink: 0; /* 防止被压缩 */
  margin-bottom: 12px;
}

/* === 卡片预览区域 - 绝对固定高度 === */
.card_preview {
  height: 220px; /* 固定高度，不使用 flex，完全锁定 */
  flex-shrink: 0; /* 防止收缩 */
  flex-grow: 0; /* 防止扩张 */
  background: #f8fafc;
  position: relative;
  cursor: pointer;
  overflow: hidden; /* 第1层隐藏超出 */
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  width: 100%; /* 固定宽度 */
}

/* 图片包装器 - 严格固定容器 */
.img_wrapper {
  width: 100%;
  height: 100%;
  max-width: 100%; /* 🔑 严格限制 */
  max-height: 100%; /* 🔑 严格限制 */
  display: flex;
  justify-content: center;
  align-items: center;
  position: relative;
  box-sizing: border-box;
  overflow: hidden; /* 🔑 第2层隐藏超出 */
}

/* 图片样式 - 超严格限制尺寸 */
.img_wrapper img {
  max-width: 80%; /* 🔑 从85%进一步减至80%，为旋转留更多空间 */
  max-height: 80%; /* 🔑 从85%进一步减至80%，为旋转留更多空间 */
  width: auto;
  height: auto;
  object-fit: contain; /* 保持完整显示 */
  transition: transform 0.3s ease;
  display: block; /* 防止行内元素带来的额外空间 */
}

/* 覆盖层 */
.overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0,0,0,0.3);
  display: flex;
  justify-content: center;
  align-items: center;
  opacity: 0;
  transition: opacity 0.2s;
}
.card_preview:hover .overlay {
  opacity: 1;
}
.img_wrapper .overlay:has(.n-spin) {
  opacity: 1;
  background: rgba(255,255,255,0.8);
}

.card_footer {
  padding: 16px 0 0 0; 
  box-sizing: border-box;
  flex-shrink: 0; 
  min-height: 70px; 
  overflow: visible; 
}
.title_text {
  font-weight: 600;
  color: #374151;
  font-size: 14px; 
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 6px; 
  line-height: 1.6;
}
.info_text {
  font-size: 13px; 
  color: #9ca3af;
  line-height: 1.6;
}

.verify_modal :deep(.n-card__content) {
  height: 80vh; 
  max-height: 800px; 
  min-height: 600px; 
  padding: 12px 8px; /* 上下12px，左右8px - 减小左右宽度 */
  overflow: hidden; 
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
}

.verify_container {
  display: flex;
  width: 100%; 
  max-width: 1300px; 
  height: 100%; 
  max-height: 680px; 
  min-height: 550px; 
  padding: 12px; 
  gap: 12px; 
  box-sizing: border-box; 
  overflow: hidden;  
  margin: 0 auto; 
}

.left_panel {
  flex: 0 0 auto; 
  width: 780px; 
  max-width: calc(100% - 436px); 
  height: 100%; 
  max-height: 100%; 
  background: #1d1e22; 
  border-radius: 8px;
  display: flex;
  align-items: center;    
  justify-content: center; 
  overflow: hidden;       
  position: relative;
  box-sizing: border-box;
  /* 网格背景，方便看透明图 */
  background-image: linear-gradient(45deg, #2a2b30 25%, transparent 25%), 
                    linear-gradient(-45deg, #2a2b30 25%, transparent 25%), 
                    linear-gradient(45deg, transparent 75%, #2a2b30 75%), 
                    linear-gradient(-45deg, transparent 75%, #2a2b30 75%);
  background-size: 20px 20px;
  background-position: 0 0, 0 10px, 10px -10px, -10px 0px;
}

/* 工具栏样式 */
.image_toolbar {
  position: absolute;
  top: 16px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 10;
  background: rgba(0,0,0,0.6);
  border-radius: 4px;
  padding: 4px;
}

/* 图片视口 - 简化结构 */
.image_viewport {
  width: 100%;
  height: 100%;
  display: flex;
  justify-content: center;
  align-items: center;
  cursor: grab;
  box-sizing: border-box;
  padding: 20px; /* 🔑 新增：给图片四周留出空间，防止溢出 */
}

.image_viewport:active {
  cursor: grabbing;
}

/* 图片画布容器 */
.image_canvas {
  max-width: 100%;
  max-height: 100%;
  display: flex;
  justify-content: center;
  align-items: center;
  box-sizing: border-box;
  overflow: hidden; /* 🔑 新增：防止内容溢出 */
}

/* 图片本身的控制 */
.target_image {
  max-width: 90%; /* 🔑 修改：从100%降到90%，为旋转留出更多空间 */
  max-height: 90%; /* 🔑 修改：从100%降到90%，为旋转留出更多空间 */
  width: auto;
  height: auto;
  object-fit: contain; /* 🔑 保证图片完整显示在格子内 */
  pointer-events: none; 
  user-select: none;
  box-shadow: 0 0 20px rgba(0,0,0,0.5);
}

/* === 4. 右侧表单面板 - 严格固定像素尺寸（紧凑版） === */
.right_panel {
  flex: 0 0 auto; /* 🔑 不使用百分比，改为固定尺寸 */
  width: 420px; /* 🔑 减小到420px */
  max-width: 420px; /* 🔑 严格限制最大宽度 */
  height: 100%; /* 🔑 固定高度填满父容器 */
  max-height: 100%; /* 🔑 严格限制最大高度 */
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 8px;
  overflow: hidden; /* 🔑 外层隐藏溢出 */
  border: 1px solid #e5e7eb;
  box-sizing: border-box;
}

/* 表单头部 - 固定不滚动 */
.form_header {
  flex-shrink: 0; /* 🔑 防止被压缩 */
  border-bottom: 1px solid #f3f4f6;
  padding: 16px 20px; /* 🔑 减小padding节省空间 */
  background: #fff;
  z-index: 1;
}
.form_header h3 {
  margin: 0 0 6px;
  color: #111827;
  font-size: 16px;
}
.form_header p {
  margin: 0;
  color: #6b7280;
  font-size: 12px;
}

/* 表单内容区域 - 可滚动 */
.right_panel :deep(.n-form) {
  flex: 1; /* 🔑 占据剩余空间 */
  overflow-y: auto; /* 🔑 关键：内容超出时显示滚动条 */
  overflow-x: hidden;
  padding: 16px 20px; /* 🔑 减小padding */
  box-sizing: border-box;
}

/* 表单底部 - 固定不滚动 */
.panel_footer {
  flex-shrink: 0; /* 🔑 防止被压缩 */
  padding: 16px 20px; /* 🔑 减小padding */
  background: #fff;
  border-top: 1px solid #f3f4f6;
  z-index: 1;
}
.mb-4 {
  margin-bottom: 12px;
}

/* 🎨 参赛学生标签容器样式 */
.students-tags-container {
  min-height: 44px;
  padding: 8px;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  background: #fff;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.students-tags-container:hover {
  border-color: #40a9ff;
}

.student-tag-editable {
  cursor: pointer;
}

.student-tag-editable:hover {
  border-color: #1890ff;
  background: #e6f4ff;
}

.student-edit-input {
  width: 120px;
}

.student-edit-input :deep(.n-input__input-el) {
  font-size: 13px;
}

.teacher_form_item :deep(.n-base-selection) {
  width: 100%;
}

/* OCR识别提示样式 */
.ocr-hint {
  margin-top: 4px;
  font-size: 12px;
  color: #999;
  display: flex;
  align-items: center;
}

/* 智能生成标题提示 */
.title-hint {
  margin-top: 6px;
  font-size: 12px;
  color: #1677ff;
  display: flex;
  align-items: center;
  gap: 4px;
  background: #f0f7ff;
  padding: 4px 8px;
  border-radius: 4px;
  border-left: 3px solid #1677ff;
}

.title_form_item :deep(.n-input__input-el) {
  font-weight: 600;
}

.participant_form_item,
.teacher_form_item {
  margin-bottom: 16px;
}

.form_assist_text {
  margin-top: 8px;
  font-size: 12px;
  color: #6b7280;
  line-height: 1.5;
}

/* 学术补充信息折叠面板 */
.extra-fields-collapse {
  margin-top: 12px;
  border: 1px dashed #d1d5db;
  border-radius: 8px;
  overflow: hidden;
}

.extra-fields-collapse :deep(.n-collapse-item__header) {
  background: #fafafa;
  padding: 8px 12px;
  font-size: 13px;
  color: #374151;
}

.extra-fields-collapse :deep(.n-collapse-item__content-inner) {
  padding: 12px;
}

/* 移动端适配 */
@media (max-width: 768px) {
  .verify_container {
    flex-direction: column;
  }
  .left_panel {
    height: 300px;
    flex: none;
  }
}
</style>
