from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, List
import uuid
import httpx
from datetime import datetime, timedelta
from database import get_db
from schemas import (
    OCRResponse, AchievementCreate, AchievementResponse,
    ChatRequest, ChatResponse, PersonaResponse
)
from utils import success_response, error_response
from models import (
    SysStudent, BizAchievement, AchievementStatus,
    AiChatSession, AiChatMessage, MessageRole, SysTeacher
)
from dependencies import require_student
from config import settings

router = APIRouter(prefix="/api/v1/student", tags=["Student"])


def _collect_candidate_names(payload: Optional[dict]) -> List[str]:
    if not payload or not isinstance(payload, dict):
        return []

    names: List[str] = []

    single_fields = [
        "recipient_name",
        "first_author",
        "first_author_cn",
        "team_leader",
        "student_name",
    ]
    list_fields = [
        "team_members",
        "authors",
        "authors_cn",
    ]

    for field in single_fields:
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            names.append(value.strip())

    for field in list_fields:
        value = payload.get(field)
        if isinstance(value, list):
            names.extend([str(item).strip() for item in value if str(item).strip()])

    patent_inventors = payload.get("patent_inventors")
    if isinstance(patent_inventors, str) and patent_inventors.strip():
        normalized = patent_inventors.replace("、", ",").replace("，", ",")
        names.extend([item.strip() for item in normalized.split(",") if item.strip()])

    return names


def _student_name_matches_payload(student_name: str, payload: Optional[dict]) -> bool:
    normalized_student_name = (student_name or "").strip()
    if not normalized_student_name:
        return False

    candidate_names = _collect_candidate_names(payload)
    return any(normalized_student_name == candidate for candidate in candidate_names)


@router.post("/ocr/recognize")
async def ocr_recognize(
    file: UploadFile = File(...),
    cert_type: Optional[str] = Form(None),
    student: SysStudent = Depends(require_student)
):
    """
    Certificate recognition with permanent storage (Step 1 of 2)
    - Accepts certificate image
    - Saves file permanently to student's directory
    - Calls AI vision model (qwen-vl-max) for extraction
    - Returns structured data + file URL
    - User can then confirm and submit achievement in Step 2
    """
    from services.file_manager import file_manager
    from services.certificate_recognition_openai import certificate_recognition_service_openai
    
    try:
        # Step 1: Save certificate permanently
        file_info = await file_manager.save_certificate_permanent(file, student.id)
        
        # Step 2: Validate Document Type (Pre-classification)
        # Use a lightweight call to ensure the image matches the expected major category
        doc_type = certificate_recognition_service_openai.classify_document(file_info["file_path"])
        
        # 允许"科研成果" (research) 也可上传论文 (paper)，自动切换为 paper 模板
        if cert_type == "research" and "paper" in doc_type:
            cert_type = "paper"

        print(f"[TYPE CHECK] cert_type={cert_type}, doc_type={doc_type}")
        # 全类型交叉校验：用户选择的成果类型 vs AI识别的文档类型
        TYPE_MATCH = {
            "competition":         {"competition"},
            "patent":              {"patent", "certificate", "software_copyright"},
            "software_copyright":  {"software_copyright", "certificate", "patent"},
            "paper":               {"paper"},
            "research":            {"certificate", "paper"},
            "project":             {"certificate", "competition"},
            "certificate":         {"certificate"},
        }
        TYPE_LABEL = {
            "competition":         "竞赛类",
            "patent":              "专利类",
            "software_copyright":  "软件著作权",
            "paper":               "论文类",
            "research":            "科研成果",
            "project":             "项目类",
            "certificate":         "证书类",
        }
        if cert_type and cert_type in TYPE_MATCH:
            allowed = TYPE_MATCH[cert_type]
            if doc_type not in allowed:
                label = TYPE_LABEL.get(cert_type, cert_type)
                return error_response(
                    msg=f"上传的图片与所选类型（{label}）不符，请退出重新选择正确的成果类型",
                    code=400
                )

        # Step 3: Recognize - 有 cert_type 则走类型专属模板，否则走自动分类
        if cert_type and cert_type in ("competition", "patent", "software_copyright", "research", "project", "certificate", "certification", "paper"):
            recognition_result = certificate_recognition_service_openai.recognize_by_type(
                file_info["file_path"], cert_type
            )
            validated_result = certificate_recognition_service_openai.validate_by_type(
                recognition_result, cert_type
            )
        else:
            recognition_result = certificate_recognition_service_openai.recognize_smart(
                file_info["file_path"]
            )
            validated_result = certificate_recognition_service_openai.validate_recognition_result(
                recognition_result
            )
        
        # Step 4: Return result
        if validated_result.get("success"):
            data = validated_result.get("data", {})

            if not _student_name_matches_payload(student.name, data):
                return error_response(
                    msg=f"识别失败：图片中未识别到您的姓名（{student.name}），无法自动回填该成果",
                    code=400
                )
            
            return success_response(data={
                "recognized_data": {
                    # 文档类型（两阶段OCR分类结果）
                    "document_type": data.get("document_type", doc_type),

                    # 标题：优先使用智能生成的 title（validate_recognition_result 已处理）
                    "title": data.get("title") or data.get("certificate_name"),
                    "date": data.get("issue_date") or data.get("publish_date"),
                    "issuer": data.get("issuing_organization"),
                    "suggested_type": data.get("category"),
                    "award_level": data.get("award_level"),
                    "award": data.get("award"),
                    "certificate_number": data.get("certificate_number"),
                    "recipient_name": data.get("recipient_name"),

                    # 证书增强字段
                    "project_name": data.get("project_name"),
                    "role": data.get("role"),
                    "team_members": data.get("team_members", []),
                    "advisors": data.get("advisors", []),
                    "additional_info": data.get("additional_info"),

                    # 论文专属字段（两阶段OCR paper路径）
                    "paper_title": data.get("paper_title"),
                    "journal_name": data.get("journal_name"),
                    "journal_level": data.get("journal_level"),
                    "publish_status": data.get("publish_status"),
                    "publish_date": data.get("publish_date"),
                    "authors": data.get("authors", []),
                    "first_author": data.get("first_author"),
                    "author_order": data.get("author_order"),
                    "doi": data.get("doi"),
                    "issn": data.get("issn"),

                    # 英文论文中文映射字段
                    "paper_title_cn": data.get("paper_title_cn"),
                    "journal_name_cn": data.get("journal_name_cn"),
                    "authors_cn": data.get("authors_cn"),
                    "first_author_cn": data.get("first_author_cn"),
                    "issuing_organization_cn": data.get("issuing_organization_cn"),

                    # 置信度评分
                    "recognition_confidence": data.get("recognition_confidence", {}),

                    # 专利专属字段
                    "patent_name": data.get("patent_name"),
                    "patent_number": data.get("patent_number"),
                    "patent_type": data.get("patent_type"),
                    "patent_holder": data.get("patent_holder"),
                    "application_date": data.get("application_date"),
                    # 科研专属字段
                    "achievement_name": data.get("achievement_name"),
                    "registration_number": data.get("registration_number"),
                    "achievement_type": data.get("achievement_type"),
                    "applicant_unit": data.get("applicant_unit"),
                    # 项目专属字段
                    "project_name": data.get("project_name"),
                    "team_leader": data.get("team_leader"),
                    "project_source": data.get("project_source"),
                    # 荣誉证书专属字段
                    "award_reason": data.get("award_reason"),
                    "valid_period": data.get("valid_period"),
                },
                "file_url": file_info["file_url"],
                "file_info": {
                    "filename": file_info["filename"],
                    "original_filename": file_info["original_filename"],
                    "size_bytes": file_info["size_bytes"]
                },
                "ai_metadata": {
                    "model_used": data.get("model_used"),
                    "recognition_time": data.get("recognition_time"),
                    "confidence": data.get("confidence")
                },
                "usage": validated_result.get("usage", {})
            })
        else:
            # Recognition failed, but file is still saved
            return error_response(
                msg=f"Certificate saved but recognition failed: {validated_result.get('error')}",
                code=500,
                data={
                    "file_url": file_info["file_url"],
                    "recognized_data": None
                }
            )
            
    except HTTPException as e:
        raise e
    except Exception as e:
        return error_response(msg=f"Error processing certificate: {str(e)}", code=500)


@router.post("/achievements")
async def create_achievement(
    achievement: AchievementCreate,
    student: SysStudent = Depends(require_student),
    db: Session = Depends(get_db)
):
    """
    Submit achievement (Step 2 of 2)
    - Creates new achievement record
    - Status defaults to 'pending'
    - Non-patent achievements must select an existing teacher
    - If evidence_url is provided, verifies student has access to that certificate
    """
    from services.file_manager import file_manager
    
    # Patents / software copyrights may omit teacher; other achievement types require it.
    if achievement.type != "patent" and not achievement.teacher_id:
        return error_response(msg="请至少选择一位指导教师", code=400)

    # Validate teacher exists when provided
    teacher = None
    if achievement.teacher_id:
        teacher = db.query(SysTeacher).filter(SysTeacher.id == achievement.teacher_id).first()
        if not teacher:
            return error_response(msg="Teacher not found", code=404)

    # Duplicate detection: same student + same title + same type
    existing = db.query(BizAchievement).filter(
        BizAchievement.student_id == student.id,
        BizAchievement.title == achievement.title,
        BizAchievement.type == achievement.type,
        BizAchievement.is_deleted == False
    ).first()
    if existing:
        return error_response(msg="您已提交过相同标题的成果，请勿重复提交", code=400)

    # Duplicate detection by DOI (for papers)
    if achievement.content_json and isinstance(achievement.content_json, dict):
        doi = achievement.content_json.get("doi")
        if doi and isinstance(doi, str) and doi.strip():
            doi_duplicate = db.query(BizAchievement).filter(
                BizAchievement.student_id == student.id,
                BizAchievement.is_deleted == False
            ).all()
            for ach in doi_duplicate:
                if ach.content_json and isinstance(ach.content_json, dict):
                    if ach.content_json.get("doi") == doi.strip():
                        return error_response(msg="该DOI对应的论文已提交过，请勿重复提交", code=400)


    # ===== 姓名校验：学生姓名必须出现在成果的人名字段中 =====
    if achievement.content_json and isinstance(achievement.content_json, dict):
        content_data = achievement.content_json
        if not _student_name_matches_payload(student.name, content_data):
            return error_response(
                msg=f"提交失败：证书中未包含您的姓名（{student.name.strip()}），请确认该成果与您相关",
                code=400
            )

    # If evidence_url is provided, verify access
    if achievement.evidence_url:
        if not file_manager.verify_certificate_access(
            achievement.evidence_url, 
            student.id, 
            is_admin=False
        ):
            return error_response(
                msg="Access denied: You can only use your own certificates",
                code=403
            )
    
    # Create achievement
    new_achievement = BizAchievement(
        student_id=student.id,
        teacher_id=achievement.teacher_id,
        title=achievement.title,
        type=achievement.type,
        content_json=achievement.content_json,
        evidence_url=achievement.evidence_url,
        status=AchievementStatus.PENDING
    )
    
    db.add(new_achievement)
    db.commit()
    db.refresh(new_achievement)
    
    # --- 团队成员自动同步 ---
    # 从 content_json 中读取 team_members，为每个匹配到的学生创建关联成果
    synced_members = []
    team_members = []
    if achievement.content_json and isinstance(achievement.content_json, dict):
        team_members = achievement.content_json.get("team_members", [])
    
    if team_members and isinstance(team_members, list):
        for member_name in team_members:
            if not member_name or not isinstance(member_name, str):
                continue
            member_name = member_name.strip()
            if not member_name:
                continue
            
            # 查找与该姓名匹配的学生（排除提交者自身）
            matched_student = db.query(SysStudent).filter(
                SysStudent.name == member_name,
                SysStudent.id != student.id
            ).first()
            
            if matched_student:
                # 构建关联成果的 content_json，标记来源
                member_content = dict(achievement.content_json) if achievement.content_json else {}
                member_content["source_achievement_id"] = new_achievement.id
                member_content["synced_from_student"] = student.name
                
                member_achievement = BizAchievement(
                    student_id=matched_student.id,
                    teacher_id=achievement.teacher_id,
                    title=achievement.title,
                    type=achievement.type,
                    content_json=member_content,
                    evidence_url=achievement.evidence_url,
                    status=AchievementStatus.PENDING
                )
                db.add(member_achievement)
                synced_members.append(member_name)
        
        if synced_members:
            # 在原始成果的 content_json 中也标记已同步的成员
            original_content = dict(new_achievement.content_json) if new_achievement.content_json else {}
            original_content["synced_to_members"] = synced_members
            new_achievement.content_json = original_content
            db.commit()
    
    return success_response(
        data={
            "id": new_achievement.id,
            "synced_members": synced_members
        },
        msg="Achievement submitted successfully"
    )


@router.get("/achievements")
async def get_my_achievements(
    status: Optional[str] = Query(None),
    student: SysStudent = Depends(require_student),
    db: Session = Depends(get_db)
):
    """
    Get my achievements
    - Returns only current student's achievements
    - Optional filter by status
    - Excludes soft-deleted achievements by default
    """
    query = db.query(BizAchievement).filter(
        BizAchievement.student_id == student.id,
        BizAchievement.is_deleted == False  # 排除已删除的成果
    )
    
    if status:
        try:
            status_enum = AchievementStatus(status)
            query = query.filter(BizAchievement.status == status_enum)
        except ValueError:
            return error_response(msg="Invalid status value", code=400)
    
    achievements = query.order_by(BizAchievement.created_at.desc()).all()
    
    achievement_list = []
    for ach in achievements:
        # 从 content_json 提取真实获奖日期（证书上的日期）
        award_date = None
        if ach.content_json and isinstance(ach.content_json, dict):
            award_date = (
                ach.content_json.get("date")
                or ach.content_json.get("issue_date")
                or ach.content_json.get("award_date")
            )
        
        achievement_list.append({
            "id": ach.id,
            "title": ach.title,
            "type": ach.type,
            "content_json": ach.content_json,
            "evidence_url": ach.evidence_url,
            "status": ach.status.value,
            "audit_comment": ach.audit_comment,
            "date": award_date,  # 证书上的真实获奖日期
            "created_at": ach.created_at.isoformat(),
            "teacher_name": ach.teacher.name if ach.teacher else None
        })
    
    return success_response(data=achievement_list)


@router.get("/achievements/{achievement_id}")
async def get_achievement_detail(
    achievement_id: int,
    student: SysStudent = Depends(require_student),
    db: Session = Depends(get_db)
):
    """
    Get achievement detail by ID
    - Returns detailed information of a specific achievement
    - Verifies the achievement belongs to the current student
    - Includes teacher information
    """
    # Query achievement and verify ownership
    achievement = db.query(BizAchievement).filter(
        BizAchievement.id == achievement_id,
        BizAchievement.student_id == student.id
    ).first()
    
    if not achievement:
        return error_response(msg="Achievement not found", code=404)
    
    # Build detailed response
    achievement_detail = {
        "id": achievement.id,
        "title": achievement.title,
        "type": achievement.type,
        "type_id": achievement.type,  # For compatibility
        "description": achievement.content_json.get("description", "") if achievement.content_json else "",
        "content_json": achievement.content_json,
        "evidence_url": achievement.evidence_url,
        "status": achievement.status.value if hasattr(achievement.status, 'value') else achievement.status,
        "audit_comment": achievement.audit_comment,
        "created_at": achievement.created_at.isoformat(),
        "awardedAt": achievement.content_json.get("date", achievement.created_at.isoformat()) if achievement.content_json else achievement.created_at.isoformat(),
        "year": achievement.content_json.get("year", "") if achievement.content_json else "",
        "level": achievement.content_json.get("award_level", "") if achievement.content_json else "",
    }
    
    # Add teacher information if exists
    if achievement.teacher:
        achievement_detail["teacher"] = {
            "id": achievement.teacher.id,
            "name": achievement.teacher.name,
            "contactEmail": getattr(achievement.teacher, 'email', ''),
            "contactPhone": getattr(achievement.teacher, 'phone', ''),
            "research_tent": getattr(achievement.teacher, 'research_direction', '')
        }
    else:
        achievement_detail["teacher"] = None
    
    return success_response(data=achievement_detail)


@router.delete("/achievements/{achievement_id}")
async def delete_achievement(
    achievement_id: int,
    student: SysStudent = Depends(require_student),
    db: Session = Depends(get_db)
):
    """
    Delete achievement (soft delete)
    - Verifies the achievement belongs to the current student
    - Marks achievement as deleted instead of hard deleting
    """
    # Query achievement and verify ownership
    achievement = db.query(BizAchievement).filter(
        BizAchievement.id == achievement_id,
        BizAchievement.student_id == student.id
    ).first()
    
    if not achievement:
        return error_response(msg="Achievement not found", code=404)
    
    # Soft delete: mark as deleted
    achievement.is_deleted = True
    db.commit()
    
    return success_response(msg="Achievement deleted successfully")


@router.get("/certificates")
async def get_my_certificates(
    student: SysStudent = Depends(require_student)
):
    """
    Get list of my certificates
    - Returns all certificate files uploaded by current student
    - Files are stored in student-specific directory
    """
    from services.file_manager import file_manager
    
    certificates = file_manager.get_student_certificates(student.id)
    
    return success_response(data={
        "certificates": certificates,
        "total": len(certificates)
    })


def _build_student_context(student: SysStudent, achievements, db: Session) -> dict:
    """Build enriched student context with pre-aggregated statistics."""
    # Type distribution
    type_map = {"竞赛": 0, "科研": 0, "项目": 0, "论文": 0, "专利": 0, "证书": 0}
    type_keywords = {
        "竞赛": ["竞赛", "competition"], "科研": ["科研", "research"],
        "项目": ["项目", "project"], "论文": ["论文", "paper"],
        "专利": ["专利", "patent"], "证书": ["证书", "certificate"]
    }
    level_dist = {"国家级": 0, "省部级": 0, "市级": 0, "校级": 0, "院级": 0}
    yearly_trend = {}

    approved_count = 0
    pending_count = 0
    rejected_count = 0

    for ach in achievements:
        # Type classification
        t = str(ach.type or "").lower()
        for cn_name, keywords in type_keywords.items():
            if any(kw in t for kw in keywords):
                type_map[cn_name] += 1
                break

        # Status counts
        if ach.status == AchievementStatus.APPROVED:
            approved_count += 1
        elif ach.status == AchievementStatus.PENDING:
            pending_count += 1
        elif ach.status == AchievementStatus.REJECTED:
            rejected_count += 1

        # Level distribution from content_json
        if ach.content_json and isinstance(ach.content_json, dict):
            award_level = ach.content_json.get("award_level", "")
            for level in level_dist:
                if level in str(award_level):
                    level_dist[level] += 1
                    break

        # Yearly trend
        if ach.created_at:
            year = str(ach.created_at.year)
            yearly_trend[year] = yearly_trend.get(year, 0) + 1

    total = len(achievements)

    return {
        "name": student.name,
        "major": student.major,
        "class_name": getattr(student, 'class_name', None),
        "type_distribution": type_map,
        "level_distribution": level_dist,
        "yearly_trend": yearly_trend,
        "achievements": [
            {
                "id": ach.id,
                "title": ach.title,
                "type": ach.type,
                "status": ach.status.value,
                "content_json": ach.content_json,
                "evidence_url": ach.evidence_url,
                "audit_comment": ach.audit_comment,
                "created_at": ach.created_at.isoformat() if ach.created_at else None,
                "teacher_name": ach.teacher.name if ach.teacher else None,
                "teacher_title": ach.teacher.title if ach.teacher else None,
                "teacher_department": ach.teacher.department if ach.teacher else None,
            }
            for ach in achievements
        ],
        "statistics": {
            "total_achievements": total,
            "approved_achievements": approved_count,
            "pending_achievements": pending_count,
            "rejected_achievements": rejected_count,
            "approval_rate": round(approved_count / total * 100, 2) if total > 0 else 0
        }
    }


def _get_chat_history_for_student(student_id: int, db: Session, limit: int = 30) -> list:
    """Get recent chat messages across all sessions for a student."""
    sessions = db.query(AiChatSession).filter(
        AiChatSession.student_id == student_id
    ).all()
    if not sessions:
        return []

    session_ids = [s.id for s in sessions]
    messages = db.query(AiChatMessage).filter(
        AiChatMessage.session_id.in_(session_ids)
    ).order_by(AiChatMessage.created_at.desc()).limit(limit).all()
    messages.reverse()

    return [
        {"role": msg.role.value, "content": msg.content}
        for msg in messages
    ]


@router.get("/persona")
async def get_persona(
    student: SysStudent = Depends(require_student),
    db: Session = Depends(get_db)
):
    """
    Get student persona.
    Returns cached persona if available, otherwise checks if chat history exists.
    """
    # Check if student has achievements (for persona generation)
    achievement_count = db.query(BizAchievement).filter(
        BizAchievement.student_id == student.id,
        BizAchievement.is_deleted == False
    ).count()

    if student.persona_cache:
        return success_response(data={
            "has_achievements": achievement_count > 0,
            "persona": student.persona_cache
        })

    return success_response(data={
        "has_achievements": achievement_count > 0,
        "persona": None
    })


@router.post("/persona/generate")
async def generate_persona(
    student: SysStudent = Depends(require_student),
    db: Session = Depends(get_db)
):
    """
    Force generate/regenerate student persona using AI.
    Requires chat history to exist.
    """
    from services.ai_chat_service import ai_chat_service

    # Build student context from real achievement data
    achievements = db.query(BizAchievement).filter(
        BizAchievement.student_id == student.id,
        BizAchievement.is_deleted == False
    ).order_by(BizAchievement.created_at.desc()).all()

    if not achievements:
        return error_response(msg="暂无成果数据，请先提交成果后再生成画像", code=400)

    student_context = _build_student_context(student, achievements, db)

    # Chat history is optional, used as supplementary context
    chat_history = _get_chat_history_for_student(student.id, db, limit=30)

    # Generate persona based on real data + optional chat history
    result = ai_chat_service.generate_persona(student_context, chat_history if chat_history else None)

    if result.get("success") and result.get("data"):
        student.persona_cache = result["data"]
        db.commit()
        return success_response(data={
            "has_chat_history": True,
            "persona": result["data"]
        })
    else:
        return error_response(msg="画像生成失败，请稍后重试", code=500)


@router.post("/ai/chat")
async def ai_chat(
    chat_req: ChatRequest,
    student: SysStudent = Depends(require_student),
    db: Session = Depends(get_db)
):
    """
    AI chat with context management
    - Backend manages conversation history
    - Implements RAG with student achievements
    - Stores messages in database
    - Uses real LLM API (Alibaba Cloud Qwen)
    """
    from services.ai_chat_service import ai_chat_service
    
    # Get or create session
    session_id = chat_req.session_id
    
    if not session_id:
        # Create new session
        session_id = str(uuid.uuid4())
        new_session = AiChatSession(
            id=session_id,
            student_id=student.id,
            title=chat_req.message[:50]  # Use first 50 chars as title
        )
        db.add(new_session)
        db.commit()
    else:
        # Validate session belongs to student
        session = db.query(AiChatSession).filter(
            AiChatSession.id == session_id,
            AiChatSession.student_id == student.id
        ).first()
        
        if not session:
            return error_response(msg="Session not found", code=404)
    
    # Store user message
    user_message = AiChatMessage(
        session_id=session_id,
        role=MessageRole.USER,
        content=chat_req.message
    )
    db.add(user_message)
    db.commit()
    
    # Retrieve chat history (last 10 messages for context)
    history_messages = db.query(AiChatMessage).filter(
        AiChatMessage.session_id == session_id
    ).order_by(AiChatMessage.created_at.desc()).limit(10).all()
    history_messages.reverse()  # Oldest first
    
    # Format history for LLM (exclude current message)
    chat_history = [
        {
            "role": msg.role.value,
            "content": msg.content
        }
        for msg in history_messages[:-1]
    ] if len(history_messages) > 1 else []
    
    # Retrieve all achievements for comprehensive AI analysis
    achievements = db.query(BizAchievement).filter(
        BizAchievement.student_id == student.id,
        BizAchievement.is_deleted == False
    ).order_by(BizAchievement.created_at.desc()).all()

    # Build enriched student context
    student_context = _build_student_context(student, achievements, db)
    
    # Call AI chat service
    ai_result = ai_chat_service.chat(
        user_message=chat_req.message,
        student_context=student_context,
        chat_history=chat_history,
        temperature=0.7,
        max_tokens=800
    )
    
    # Handle AI service response
    if not ai_result.get("success"):
        # Log the error for debugging
        error_msg = ai_result.get("error", "Unknown error")
        print(f"❌ AI Chat Service Error: {error_msg}")
        print(f"Full AI Result: {ai_result}")
        # Fallback response if AI service fails
        ai_response = "抱歉，AI助手暂时无法回复。请检查网络连接或稍后再试。"
    else:
        ai_response = ai_result.get("message", "抱歉，暂时无法生成回复。")
    
    # Store AI response
    assistant_message = AiChatMessage(
        session_id=session_id,
        role=MessageRole.ASSISTANT,
        content=ai_response
    )
    db.add(assistant_message)
    
    # Update session timestamp
    session = db.query(AiChatSession).filter(AiChatSession.id == session_id).first()
    session.updated_at = datetime.utcnow()
    
    db.commit()
    
    return success_response(data={
        "session_id": session_id,
        "message": ai_response,
        "usage": ai_result.get("usage") if ai_result.get("success") else None
    })


@router.get("/me")
async def get_student_me(
    student: SysStudent = Depends(require_student),
    db: Session = Depends(get_db)
):
    """
    获取当前登录学生的基本信息
    """
    user = student.user
    
    return success_response(data={
        "id": student.id,
        "student_id": student.student_number,  # Corrected from student_id to student_number
        "name": student.name,
        "class_name": getattr(student, 'class_name', None),  # Handle potential missing field if model updated
        "major": student.major,
        "college": student.college,
        "squad": student.squad,
        "email": getattr(student, 'email', None),
        "phone": getattr(student, 'phone', None),
        "user_id": user.id,
        "username": user.username,
        "avatar_url": user.avatar_url,
        "role": user.role.value
    })


@router.get("/profile")
async def get_student_profile(
    student: SysStudent = Depends(require_student),
    db: Session = Depends(get_db)
):
    """
    获取学生的详细档案信息
    包含基本信息、成果统计、证书统计等
    """
    user = student.user
    
    # 统计成果数量（排除已删除）
    total_achievements = db.query(BizAchievement).filter(
        BizAchievement.student_id == student.id,
        BizAchievement.is_deleted == False
    ).count()

    approved_achievements = db.query(BizAchievement).filter(
        BizAchievement.student_id == student.id,
        BizAchievement.is_deleted == False,
        BizAchievement.status == AchievementStatus.APPROVED
    ).count()

    pending_achievements = db.query(BizAchievement).filter(
        BizAchievement.student_id == student.id,
        BizAchievement.is_deleted == False,
        BizAchievement.status == AchievementStatus.PENDING
    ).count()

    # 获取最近的成果（排除已删除）
    recent_achievements = db.query(BizAchievement).filter(
        BizAchievement.student_id == student.id,
        BizAchievement.is_deleted == False
    ).order_by(BizAchievement.created_at.desc()).limit(5).all()
    
    return success_response(data={
        "basic_info": {
            "id": student.id,
            "student_id": student.student_number,  # Corrected from student_id to student_number
            "name": student.name,
            "class_name": getattr(student, "class_name", None),
            "major": student.major,
            "college": student.college,
            "squad": student.squad,
            "email": getattr(student, "email", None),
            "phone": getattr(student, "phone", None),
            "avatar_url": user.avatar_url
        },
        "statistics": {
            "total_achievements": total_achievements,
            "approved_achievements": approved_achievements,
            "pending_achievements": pending_achievements,
            "approval_rate": round(approved_achievements / total_achievements * 100, 2) if total_achievements > 0 else 0
        },
        "recent_achievements": [
            {
                "id": ach.id,
                "title": ach.title,
                "type": ach.type,
                "status": ach.status.value,
                "created_at": ach.created_at.isoformat()
            }
            for ach in recent_achievements
        ]
    })


@router.post("/avatar")
async def update_avatar(
    file: UploadFile = File(...),
    student: SysStudent = Depends(require_student),
    db: Session = Depends(get_db)
):
    """
    更新用户头像
    """
    import os
    from pathlib import Path

    # 验证文件类型
    allowed_types = ["image/jpeg", "image/png", "image/gif", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="仅支持 JPG/PNG/GIF/WEBP 格式的图片")

    # 限制文件大小 (2MB)
    contents = await file.read()
    if len(contents) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片大小不能超过 2MB")

    # 生成唯一文件名
    ext = os.path.splitext(file.filename or "avatar.jpg")[1] or ".jpg"
    unique_name = f"avatar_{student.user_id}_{uuid.uuid4().hex[:8]}{ext}"

    # 保存到 uploads/avatars 目录
    avatar_dir = Path(settings.UPLOAD_DIR) / "avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)

    file_path = avatar_dir / unique_name
    with open(file_path, "wb") as f:
        f.write(contents)

    # 更新数据库
    avatar_url = f"/uploads/avatars/{unique_name}"
    user = student.user
    user.avatar_url = avatar_url
    db.commit()

    return success_response(data={"avatar_url": avatar_url})
