from fastapi import FastAPI, Depends, HTTPException, status, File, UploadFile, Body
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List, Optional
from datetime import datetime, timedelta
import shutil
import os
import uuid
import random
import string
import base64
import hashlib
from captcha.image import ImageCaptcha
from . import models, schemas, auth, database
from .routers import teacher, courses, approvals, student, upload
from .services.video_score import get_video_score
from .services.score_service import verify_activity, get_sunshine_stats

# 初始化数据库表
models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="Campus Sports Health MVP")

# CORS配置 - 必须在路由注册之前添加
# 开发环境允许所有来源
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源（开发环境）
    allow_credentials=False,  # 使用 * 时必须设为 False
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

app.include_router(teacher.router)
app.include_router(courses.router)
app.include_router(approvals.router)
app.include_router(student.router)
app.include_router(upload.router)

# Import run_groups router
from .routers import run_groups
app.include_router(run_groups.router)

# Mount uploads directory
if not os.path.exists("uploads"):
    os.makedirs("uploads")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Dependency
get_db = database.get_db

# --- Common Interfaces ---
CAPTCHA_SECRET = "lingxi_sports_mvp_secret_salt_2026"

@app.get("/common/captcha")
def get_captcha():
    # Generate 4 random chars
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    
    # Generate image
    image = ImageCaptcha(width=120, height=50)
    data = image.generate(code)
    
    # Encode to base64
    base64_str = base64.b64encode(data.getvalue()).decode('utf-8')
    image_data = f"data:image/png;base64,{base64_str}"
    
    # Generate key (simple stateless verification)
    # Key = MD5(Upper(Code) + Secret)
    key_src = code.upper() + CAPTCHA_SECRET
    key = hashlib.md5(key_src.encode('utf-8')).hexdigest()
    
    return {"image": image_data, "key": key}

# --- Auth Interfaces ---

@app.post("/auth/register", response_model=schemas.Token)
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    """
    注册逻辑重构：学生采用“档案激活”模式。
    - 学生：必须在 StudentProfile 中存在对应档案，且姓名匹配，且未被激活
    - 教师/其他角色：保持原有自由注册逻辑
    """
    # 1. Verify Captcha（测试环境允许 code=TEST + 对应 key）
    verify_src = user.captcha_code.upper() + CAPTCHA_SECRET
    expected_key = hashlib.md5(verify_src.encode("utf-8")).hexdigest()
    if expected_key != user.captcha_key:
        raise HTTPException(status_code=400, detail="验证码错误")

    # 2. 手机号唯一
    db_user = db.query(models.User).filter(models.User.phone == user.phone).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Phone already registered")

    hashed_password = auth.get_password_hash(user.password)

    # 3. 学生：走档案激活流程
    if user.role == "student":
        if not user.student_id:
            raise HTTPException(
                status_code=400,
                detail="学生注册必须提供学号",
            )

        profile = (
            db.query(models.StudentProfile)
            .filter(models.StudentProfile.student_id == user.student_id)
            .first()
        )
        if not profile:
            raise HTTPException(
                status_code=403,
                detail="档案库中无此信息，请联系管理员",
            )

        # 姓名严格匹配档案
        if profile.full_name.strip() != user.name.strip():
            raise HTTPException(
                status_code=403,
                detail="姓名与档案不符，请确认后重试",
            )

        if profile.is_activated:
            raise HTTPException(
                status_code=400,
                detail="该学号已被注册",
            )

        # 根据档案中的班级名称找到或创建班级
        db_class = (
            db.query(models.Class)
            .filter(models.Class.name == profile.class_name)
            .first()
        )
        if not db_class:
            db_class = models.Class(name=profile.class_name)
            db.add(db_class)
            db.commit()
            db.refresh(db_class)

        new_user = models.User(
            phone=user.phone,
            name=profile.full_name,
            role="student",
            password_hash=hashed_password,
            student_id=profile.student_id,
            gender=profile.gender,
            class_id=db_class.id,
            major=profile.major,
        )
        db.add(new_user)
        # 标记档案已激活
        profile.is_activated = True
        db.commit()
        db.refresh(new_user)

    else:
        # 非学生角色仍然允许常规注册（如教师）
        new_user = models.User(
            phone=user.phone,
            name=user.name,
            role=user.role,
            password_hash=hashed_password,
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

    access_token = auth.create_access_token(
        data={"sub": new_user.phone, "role": new_user.role}
    )
    class_name = new_user.student_class.name if getattr(new_user, "student_class", None) else None
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": new_user.role,
        "user_id": new_user.id,
        "name": new_user.name,
        "class_name": class_name,
        "student_id": new_user.student_id,
        "major": getattr(new_user, "major", None),
    }


@app.post("/auth/refresh")
def refresh_token(current_user: models.User = Depends(auth.get_current_user)):
    access_token = auth.create_access_token(
        data={"sub": current_user.phone, "role": current_user.role}
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/auth/login", response_model=schemas.Token)
def login(user_data: schemas.UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.phone == user_data.phone).first()
    if not user or not auth.verify_password(user_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect phone or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = auth.create_access_token(
        data={"sub": user.phone, "role": user.role}
    )
    class_name = None
    if getattr(user, "student_class", None):
        class_name = user.student_class.name
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user.role,
        "user_id": user.id,
        "name": user.name,
        "class_name": class_name,
        "student_id": user.student_id,
        "major": getattr(user, "major", None),
    }

@app.get("/users/profile", response_model=schemas.UserProfile)
def get_my_profile(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    # Ensure fresh data
    db.refresh(current_user)
    
    profile_data = {
        "id": current_user.id,
        "name": current_user.name,
        "phone": current_user.phone,
        "role": current_user.role,
        "student_id": current_user.student_id,
        "group_name": current_user.group_name,
        "health_status": current_user.health_status,
        "signature": getattr(current_user, 'signature', None),
        "avatar_url": getattr(current_user, 'avatar_url', None),
        "class_name": current_user.student_class.name if current_user.student_class else None
    }
    return profile_data

@app.put("/users/profile")
def update_my_profile(
    profile_update: schemas.UserProfileUpdate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """Update user profile (name, signature, avatar)"""
    
    if profile_update.name:
        current_user.name = profile_update.name
    
    if profile_update.signature is not None:
        # Add signature field if not exists
        if not hasattr(current_user, 'signature'):
            from sqlalchemy import Column, String
            # This is a temporary solution, should add to models.py
        current_user.signature = profile_update.signature
    
    if profile_update.avatar_url is not None:
        current_user.avatar_url = profile_update.avatar_url
    
    db.commit()
    db.refresh(current_user)
    
    return {"success": True, "message": "Profile updated successfully"}

# --- Student Interfaces ---

@app.post("/activity/finish", response_model=schemas.ActivityOut)
def finish_activity(
    activity_in: schemas.ActivityFinish,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    # 1. Create Activity
    # 任务来源的提交默认进入待教师审核/打分队列
    initial_status = "pending_review" if activity_in.source == "task" else "finished"
    db_activity = models.Activity(
        user_id=current_user.id,
        type=activity_in.type,
        source=activity_in.source,
        status=initial_status,  # free: finished；task: pending_review
        started_at=activity_in.started_at,
        ended_at=activity_in.ended_at
    )
    db.add(db_activity)
    db.commit()
    db.refresh(db_activity)

    # 2. Create Metrics
    metrics_data = activity_in.metrics.dict()
    
    # 确保 video_url 字段存在，即使为 None
    if 'video_url' not in metrics_data:
        metrics_data['video_url'] = None
    
    # 如果有视频URL且活动类型为test或run，进行AI评分
    # TODO: 暂时注释掉AI评分，避免接口未配置导致错误
    # if metrics_data.get('video_url') and activity_in.type in ["test", "run"]:
    #     try:
    #         score, score_detail = get_video_score(metrics_data['video_url'])
    #         metrics_data['score'] = score
    #         metrics_data['score_detail'] = score_detail
    #     except Exception as e:
    #         # 评分失败不影响活动提交，记录错误但继续处理
    #         print(f"视频评分失败: {str(e)}")
    
    # 暂时使用模拟数据
    if metrics_data.get('video_url') and activity_in.type == "test":
        import random
        metrics_data['count'] = random.randint(8, 15)  # 模拟次数
        metrics_data['qualified'] = metrics_data['count'] >= 10  # 10次及格
        metrics_data['score'] = random.randint(70, 95)  # 模拟评分
        metrics_data['score_detail'] = f"动作标准度: {random.randint(80, 95)}%, 完成质量: 良好"
    
    db_metrics = models.ActivityMetrics(
        activity_id=db_activity.id,
        **metrics_data
    )
    db.add(db_metrics)

    # 3. Create Evidence (if any)
    for evidence in activity_in.evidence:
        db_evidence = models.ActivityEvidence(
            activity_id=db_activity.id,
            **evidence.dict()
        )
        db.add(db_evidence)
    
    # 4. 阳光跑自动审核（仅针对跑步）
    if db_activity.type == "run":
        is_valid, fail_reason, face_verified = verify_activity(current_user, db_activity, db)
        db_activity.is_valid = is_valid
        db_activity.fail_reason = fail_reason or None
        db_activity.face_verified = face_verified
        db.commit()
        db.refresh(db_activity)
    else:
        db.commit()
        db.refresh(db_activity)

    # 5. 计算本次提交后的今日达标状态，用于前端结算页提示
    try:
        stats = get_sunshine_stats(current_user, db)
        setattr(db_activity, "today_completed", stats.get("today_status") == "success")
    except Exception:
        # 统计失败不影响本次活动记录
        setattr(db_activity, "today_completed", None)

    return db_activity

@app.get("/activity/history", response_model=schemas.ActivityListResponse)
def get_history(
    page: int = 1,
    size: int = 20,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    query = db.query(models.Activity).filter(models.Activity.user_id == current_user.id)
    total = query.count()
    activities = query.order_by(models.Activity.started_at.desc()).offset((page - 1) * size).limit(size).all()
    
    return {
        "items": activities,
        "total": total,
        "page": page,
        "size": size
    }

# --- Class Interfaces ---

def _teacher_class_ids(db: Session, teacher_id: int):
    """教师管辖的班级 ID 列表（基于 TeacherClass）"""
    return _teacher_class_ids_by_binding(db, teacher_id)

def _teacher_can_access_class(db: Session, teacher_id: int, class_id: int) -> bool:
    return class_id in _teacher_class_ids(db, teacher_id)

@app.get("/teacher/available_classes", response_model=List[schemas.ClassOut])
def get_available_classes(
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    bound_names = [r.class_name for r in db.query(models.TeacherClass).filter(
        models.TeacherClass.teacher_id == current_user.id
    ).all()]
    if bound_names:
        classes = db.query(models.Class).filter(~models.Class.name.in_(bound_names)).all()
    else:
        classes = db.query(models.Class).all()
    results = []
    for c in classes:
        c_dict = {k: v for k, v in c.__dict__.items() if not k.startswith("_")}
        c_dict["student_count"] = len(c.students)
        results.append(c_dict)
    return results

@app.post("/teacher/classes/bind")
def bind_classes(
    bind_data: schemas.ClassBind,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    for class_id in bind_data.class_ids:
        cls = db.query(models.Class).filter(models.Class.id == class_id).first()
        if not cls:
            continue
        if db.query(models.TeacherClass).filter(
            models.TeacherClass.teacher_id == current_user.id,
            models.TeacherClass.class_name == cls.name
        ).first():
            continue
        db.add(models.TeacherClass(teacher_id=current_user.id, class_name=cls.name))
    db.commit()
    return {"ok": True}

@app.get("/teacher/classes", response_model=List[schemas.ClassOut])
def get_classes(
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    bound_names = [r.class_name for r in db.query(models.TeacherClass).filter(
        models.TeacherClass.teacher_id == current_user.id
    ).all()]
    if not bound_names:
        return []
    classes = db.query(models.Class).filter(models.Class.name.in_(bound_names)).all()
    results = []
    for c in classes:
        c_dict = {k: v for k, v in c.__dict__.items() if not k.startswith("_")}
        c_dict["student_count"] = len(c.students)
        results.append(c_dict)
    return results

@app.delete("/teacher/classes/{class_id}")
def unbind_class(
    class_id: int,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    if not _teacher_can_access_class(db, current_user.id, class_id):
        raise HTTPException(status_code=404, detail="Class not found")
    cls = db.query(models.Class).filter(models.Class.id == class_id).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found")
    db.query(models.TeacherClass).filter(
        models.TeacherClass.teacher_id == current_user.id,
        models.TeacherClass.class_name == cls.name
    ).delete(synchronize_session=False)
    db.commit()
    return {"ok": True}

def _get_class_if_teacher_can_access(db: Session, class_id: int, teacher_id: int):
    if not _teacher_can_access_class(db, teacher_id, class_id):
        return None
    return db.query(models.Class).filter(models.Class.id == class_id).first()

@app.get("/teacher/classes/{class_id}", response_model=schemas.ClassDetail)
def get_class_detail(
    class_id: int,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    cls = _get_class_if_teacher_can_access(db, class_id, current_user.id)
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found")
    c_dict = {k: v for k, v in cls.__dict__.items() if not k.startswith("_")}
    c_dict["student_count"] = len(cls.students)
    c_dict["students"] = cls.students
    return c_dict

@app.put("/teacher/classes/{class_id}", response_model=schemas.ClassOut)
def update_class(
    class_id: int,
    class_in: schemas.ClassCreate,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    cls = _get_class_if_teacher_can_access(db, class_id, current_user.id)
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found")
    cls.name = class_in.name
    db.commit()
    db.refresh(cls)
    c_dict = {k: v for k, v in cls.__dict__.items() if not k.startswith("_")}
    c_dict["student_count"] = len(cls.students)
    return c_dict

@app.get("/teacher/classes/{class_id}/students", response_model=List[schemas.StudentDetail])
def get_class_students(
    class_id: int,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    cls = _get_class_if_teacher_can_access(db, class_id, current_user.id)
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found")
    return cls.students

@app.post("/teacher/classes/{class_id}/students")
def add_student_to_class(
    class_id: int,
    phone: str = Body(..., embed=True),
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    cls = _get_class_if_teacher_can_access(db, class_id, current_user.id)
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found")
    student = db.query(models.User).filter(models.User.phone == phone, models.User.role == "student").first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    student.class_id = class_id
    db.commit()
    return {"ok": True}

@app.delete("/teacher/classes/{class_id}/students/{student_id}")
def remove_student_from_class(
    class_id: int,
    student_id: int,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    cls = _get_class_if_teacher_can_access(db, class_id, current_user.id)
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found")
    student = db.query(models.User).filter(models.User.id == student_id, models.User.class_id == class_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found in this class")
    student.class_id = None
    db.commit()
    return {"ok": True}

@app.get("/teacher/classes/{class_id}/stats")
def get_class_stats(
    class_id: int,
    task_id: Optional[int] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    cls = _get_class_if_teacher_can_access(db, class_id, current_user.id)
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found")
    
    # Pre-fetch task if provided to filter by task type/window
    task = None
    if task_id:
        task = db.query(models.Task).filter(models.Task.id == task_id).first()

    # 简单统计：总里程，总运动次数，人均数据
    total_distance = 0.0
    total_activities = 0
    student_stats = []
    
    for student in cls.students:
        query = db.query(models.Activity).filter(models.Activity.user_id == student.id)
        
        # Filter by Task
        if task:
            query = query.filter(models.Activity.type == task.type)
            if task.created_at:
                 query = query.filter(models.Activity.started_at >= task.created_at)
            if task.deadline:
                query = query.filter(models.Activity.started_at <= task.deadline)

        # Filter by Time Range
        if start_date:
            query = query.filter(models.Activity.started_at >= start_date)
        if end_date:
             query = query.filter(models.Activity.started_at <= end_date)

        activities = query.all()
        s_dist = 0.0
        s_count = len(activities)
        for act in activities:
            if act.metrics and act.metrics.distance:
                s_dist += act.metrics.distance
        
        total_distance += s_dist
        total_activities += s_count
        student_stats.append({
            "id": student.id,
            "name": student.name,
            "distance": s_dist,
            "count": s_count
        })
        
    return {
        "class_name": cls.name,
        "student_count": len(cls.students),
        "total_distance": total_distance,
        "total_activities": total_activities,
        "student_stats": student_stats
    }

# --- Teacher Interfaces ---

@app.get("/teacher/dashboard/stats", response_model=schemas.TeacherDashboardOut)
def get_teacher_dashboard_stats(
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    # --- 1. Stats Calculation ---
    # 使用 TeacherClass 表获取教师管辖班级（与 /teacher/stats、异常处理等一致）
    tc_rows = db.query(models.TeacherClass).filter(
        models.TeacherClass.teacher_id == current_user.id
    ).all()
    class_names = [r.class_name for r in tc_rows]
    if class_names:
        teacher_class_ids = [
            c.id for c in db.query(models.Class).filter(models.Class.name.in_(class_names)).all()
        ]
    else:
        teacher_class_ids = []
    
    # 1. Student Count (only in teacher's classes)
    if teacher_class_ids:
        student_count = db.query(models.User).filter(
            models.User.role == "student",
            models.User.class_id.in_(teacher_class_ids)
        ).count()
    else:
        student_count = 0
    
    # 2. Pending Approvals (Health Requests from teacher's students + Pending Activities)
    if teacher_class_ids:
        pending_health = db.query(models.HealthRequest).join(
            models.User, models.HealthRequest.student_id == models.User.id
        ).filter(
            models.HealthRequest.status == "pending",
            models.User.class_id.in_(teacher_class_ids)
        ).count()
        
        pending_activities = db.query(models.Activity).join(
            models.User, models.Activity.user_id == models.User.id
        ).filter(
            models.Activity.status == "pending_review",
            models.User.class_id.in_(teacher_class_ids)
        ).count()
    else:
        pending_health = 0
        pending_activities = 0
    
    pending_approvals = pending_health + pending_activities
    
    # 3. Abnormal Count (students with non-normal health status in teacher's classes)
    if teacher_class_ids:
        abnormal_count = db.query(models.User).filter(
            models.User.role == "student",
            models.User.class_id.in_(teacher_class_ids),
            models.User.health_status != "normal"
        ).count()
    else:
        abnormal_count = 0
    
    # 4. Today Check-in (Activities today from teacher's students)
    today = datetime.utcnow().date()
    today_start = datetime(today.year, today.month, today.day)
    
    if teacher_class_ids:
        today_checkin = db.query(models.Activity).join(
            models.User, models.Activity.user_id == models.User.id
        ).filter(
            models.Activity.started_at >= today_start,
            models.User.class_id.in_(teacher_class_ids)
        ).count()
    else:
        today_checkin = 0
    
    # 5. Task Count (tasks created by this teacher)
    task_count = db.query(models.Task).filter(
        models.Task.created_by == current_user.id
    ).count()
    
    # 6. Calculate qualified_rate and completion_rate
    qualified_rate = 0
    completion_rate = 0
    avg_pace = "--"
    
    if teacher_class_ids and student_count > 0:
        # Get all activities from teacher's students
        activities = db.query(models.Activity).join(
            models.User, models.Activity.user_id == models.User.id
        ).filter(
            models.User.class_id.in_(teacher_class_ids)
        ).all()
        
        # Calculate average pace from running activities
        total_pace_seconds = 0
        pace_count = 0
        
        for activity in activities:
            if activity.metrics and activity.metrics.distance and activity.metrics.duration:
                if activity.metrics.distance > 0:
                    # Calculate pace (seconds per km)
                    pace = activity.metrics.duration / activity.metrics.distance
                    total_pace_seconds += pace
                    pace_count += 1
        
        if pace_count > 0:
            avg_pace_seconds = total_pace_seconds / pace_count
            minutes = int(avg_pace_seconds // 60)
            seconds = int(avg_pace_seconds % 60)
            avg_pace = f"{minutes}'{seconds:02d}\""
        
        # Calculate completion rate (students who have activities this week)
        week_start = today_start - timedelta(days=today.weekday())
        active_students = db.query(models.Activity.user_id).join(
            models.User, models.Activity.user_id == models.User.id
        ).filter(
            models.User.class_id.in_(teacher_class_ids),
            models.Activity.started_at >= week_start
        ).distinct().count()
        
        completion_rate = round((active_students / student_count) * 100) if student_count > 0 else 0
        
        # Calculate qualified rate (students meeting minimum standards)
        # Standard: at least 3km running per week
        qualified_students = 0
        for class_id in teacher_class_ids:
            students = db.query(models.User).filter(
                models.User.class_id == class_id,
                models.User.role == "student"
            ).all()
            
            for student in students:
                week_activities = db.query(models.Activity).filter(
                    models.Activity.user_id == student.id,
                    models.Activity.started_at >= week_start,
                    models.Activity.type == "run"
                ).all()
                
                total_distance = sum(
                    act.metrics.distance for act in week_activities 
                    if act.metrics and act.metrics.distance
                )
                
                if total_distance >= 3.0:  # 3km minimum
                    qualified_students += 1
        
        qualified_rate = round((qualified_students / student_count) * 100) if student_count > 0 else 0
    
    # --- 2. Todos Generation ---
    todos = []
    
    # A. Pending Health Requests (only from teacher's students)
    if teacher_class_ids:
        pending_reqs = db.query(models.HealthRequest).join(
            models.User, models.HealthRequest.student_id == models.User.id
        ).filter(
            models.HealthRequest.status == "pending",
            models.User.class_id.in_(teacher_class_ids)
        ).order_by(models.HealthRequest.created_at.desc()).limit(5).all()
        
        for req in pending_reqs:
            student = db.query(models.User).filter(models.User.id == req.student_id).first()
            student_name = student.name if student else "Unknown"
            todos.append({
                "id": f"health_{req.id}",
                "title": "请假审批",
                "type": "approval",
                "desc": f"{student_name} 申请 {req.type} ({req.reason})",
                "time": req.created_at.strftime("%H:%M"),
                "path": "/pages/teacher/students/students?action=approval",
                "priority": "high"
            })

    # A2. Pending Activities (only from teacher's students)
    if teacher_class_ids:
        pending_acts = db.query(models.Activity).join(
            models.User, models.Activity.user_id == models.User.id
        ).filter(
            models.Activity.status == "pending_review",
            models.User.class_id.in_(teacher_class_ids)
        ).order_by(models.Activity.started_at.desc()).limit(5).all()
        
        for act in pending_acts:
            student = db.query(models.User).filter(models.User.id == act.user_id).first()
            student_name = student.name if student else "Unknown"
            todos.append({
                "id": f"activity_{act.id}",
                "title": "运动审批",
                "type": "approval",
                "desc": f"{student_name} 提交 {act.type} 记录",
                "time": act.started_at.strftime("%H:%M"),
                "path": "/pages/teacher/approve/approve",
                "priority": "high"
            })
        
    # B. Tasks nearing deadline (< 24h) - only for tasks created by this teacher
    tomorrow = datetime.utcnow() + timedelta(days=1)
    near_deadline_tasks = db.query(models.Task).filter(
        models.Task.created_by == current_user.id,  # 只显示该教师创建的任务
        models.Task.deadline != None,
        models.Task.deadline > datetime.utcnow(),
        models.Task.deadline < tomorrow
    ).all()
    
    for task in near_deadline_tasks:
        todos.append({
            "id": f"task_{task.id}",
            "title": "任务即将截止",
            "type": "task",
            "desc": f"任务 '{task.title}' 将于24小时内截止",
            "time": task.deadline.strftime("%m-%d %H:%M"),
            "path": f"/pages/teacher/tasks/detail?id={task.id}",
            "priority": "medium"
        })
    
    # C. Abnormal Students (if any)
    if abnormal_count > 0:
        todos.append({
            "id": "abnormal_alert",
            "title": "异常状态关注",
            "type": "task",
            "desc": f"有 {abnormal_count} 名学生处于异常/请假状态，请关注",
            "time": datetime.utcnow().strftime("%H:%M"),
            "path": "/pages/teacher/students/students?filter=abnormal",
            "priority": "high"
        })

    return {
        "stats": {
            "student_count": student_count,
            "today_checkin": today_checkin,
            "abnormal_count": abnormal_count,
            "pending_approvals": pending_approvals,
            "pending_health": pending_health,
            "pending_activities": pending_activities,
            "avg_pace": avg_pace,
            "task_count": task_count,
            "compliance_rate": 92,  # Mock - can be calculated if needed
            "qualified_rate": qualified_rate,
            "completion_rate": completion_rate
        },
        "todos": todos
    }

@app.get("/teacher/activities", response_model=schemas.ActivityListResponse)
def get_teacher_activities(
    page: int = 1,
    size: int = 20,
    status: str = "pending_review",
    source: str = "task",
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    teacher_class_ids = _teacher_class_ids(db, current_user.id)
    if not teacher_class_ids:
        return {
            "items": [],
            "total": 0,
            "page": page,
            "size": size
        }

    # Only show activities from students in teacher's classes
    query = db.query(models.Activity).join(
        models.User, models.Activity.user_id == models.User.id
    ).filter(
        models.User.class_id.in_(teacher_class_ids)
    )
    if status and status != "all":
        query = query.filter(models.Activity.status == status)
    if source and source != "all":
        query = query.filter(models.Activity.source == source)

    total = query.count()
    activities = query.order_by(models.Activity.started_at.desc()).offset((page - 1) * size).limit(size).all()

    return {
        "items": activities,
        "total": total,
        "page": page,
        "size": size
    }


@app.get("/student/sunshine-stats")
def get_sunshine_dashboard(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """
    阳光跑看板数据：
    - total_valid_count: 历史达标次数
    - score: 阶梯计分
    - today_status: 'not_started' | 'success' | 'failed'
    - today_fail_reason: 最近一次失败原因
    """
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Only students can access sunshine stats")

    return get_sunshine_stats(current_user, db)


def _teacher_class_ids_by_binding(db: Session, teacher_id: int):
    """从 TeacherClass 表获取教师管辖的班级 ID 列表（分配管辖班级后可见数据）"""
    tc_rows = db.query(models.TeacherClass).filter(
        models.TeacherClass.teacher_id == teacher_id
    ).all()
    class_names = [r.class_name for r in tc_rows]
    if not class_names:
        return []
    return [
        c.id
        for c in db.query(models.Class).filter(models.Class.name.in_(class_names)).all()
    ]


def _compute_sunshine_score(total_valid_count: int) -> int:
    """阳光跑积分阶梯算法（10-20-40）：
    - 0-9 次：0 分
    - 10-19 次：从 40 起，每次 +2，最高 60
    - 20-40 次：从 60 起，每次 +2，最高 100
    """
    if total_valid_count < 10:
        return 0
    if total_valid_count < 20:
        return min(60, 40 + (total_valid_count - 10) * 2)
    capped = min(total_valid_count, 40)
    return min(100, 60 + (capped - 20) * 2)


@app.get("/teacher/weekly-sunshine-trend")
def get_weekly_sunshine_trend(
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """本周阳光跑趋势：每天完成有效跑步的学生人数（去重）"""
    from sqlalchemy import func as sqlfunc
    from datetime import date, timedelta, datetime as dt
    teacher_class_ids = _teacher_class_ids_by_binding(db, current_user.id)
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=7)

    if not teacher_class_ids:
        return [
            {"day": (week_start + timedelta(days=i)).strftime("%m-%d"), "value": 0}
            for i in range(7)
        ]

    # 按 (日期, user_id) 去重后统计每天人数
    rows = (
        db.query(
            sqlfunc.date(models.Activity.started_at).label("d"),
            models.Activity.user_id
        )
        .join(models.User, models.Activity.user_id == models.User.id)
        .filter(
            models.Activity.type == "run",
            models.Activity.is_valid.is_(True),
            models.User.class_id.in_(teacher_class_ids),
            models.Activity.started_at >= dt.combine(week_start, dt.min.time()),
            models.Activity.started_at < dt.combine(week_end, dt.min.time()),
        )
        .group_by(sqlfunc.date(models.Activity.started_at), models.Activity.user_id)
        .all()
    )

    day_to_count: dict = {}
    for row in rows:
        d = row.d
        day_to_count[d] = day_to_count.get(d, 0) + 1

    result = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        result.append({"day": d.strftime("%m-%d"), "value": day_to_count.get(d, 0)})
    return result


@app.get("/teacher/class-member-details", response_model=schemas.ClassMemberSunshineList)
def get_class_member_details(
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """班级成员阳光跑明细：本周次数、历史积分，按积分降序"""
    from sqlalchemy import func as sqlfunc
    from datetime import date, timedelta, datetime as dt
    teacher_class_ids = _teacher_class_ids_by_binding(db, current_user.id)
    if not teacher_class_ids:
        return {"items": []}

    students = (
        db.query(models.User)
        .join(models.Class, models.User.class_id == models.Class.id)
        .filter(
            models.User.role == "student",
            models.User.class_id.in_(teacher_class_ids),
        )
        .all()
    )
    if not students:
        return {"items": []}

    student_ids = [s.id for s in students]
    class_by_id = {
        c.id: c
        for c in db.query(models.Class).filter(models.Class.id.in_(teacher_class_ids)).all()
    }

    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=7)

    # 本周有效跑步次数
    weekly_counts: dict = {sid: 0 for sid in student_ids}
    for uid, cnt in (
        db.query(models.Activity.user_id, sqlfunc.count(models.Activity.id))
        .filter(
            models.Activity.type == "run",
            models.Activity.is_valid.is_(True),
            models.Activity.user_id.in_(student_ids),
            models.Activity.started_at >= dt.combine(week_start, dt.min.time()),
            models.Activity.started_at < dt.combine(week_end, dt.min.time()),
        )
        .group_by(models.Activity.user_id)
        .all()
    ):
        weekly_counts[uid] = cnt

    # 历史有效跑步总次数
    total_counts: dict = {sid: 0 for sid in student_ids}
    for uid, cnt in (
        db.query(models.Activity.user_id, sqlfunc.count(models.Activity.id))
        .filter(
            models.Activity.type == "run",
            models.Activity.is_valid.is_(True),
            models.Activity.user_id.in_(student_ids),
        )
        .group_by(models.Activity.user_id)
        .all()
    ):
        total_counts[uid] = cnt

    items = []
    for s in students:
        total_valid = total_counts.get(s.id, 0)
        cls = class_by_id.get(s.class_id)
        items.append({
            "user_id": s.id,
            "student_id": s.student_id or "",
            "name": s.name,
            "class_name": cls.name if cls else "",
            "weekly_count": weekly_counts.get(s.id, 0),
            "total_score": _compute_sunshine_score(total_valid),
        })

    items.sort(key=lambda x: x["total_score"], reverse=True)
    return {"items": items}


@app.get("/teacher/tests/live")
def get_live_tests(
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """Get currently ongoing tests with video uploads（按分配管辖班级过滤）"""
    teacher_class_ids = _teacher_class_ids_by_binding(db, current_user.id)
    if not teacher_class_ids:
        return {"items": []}
    
    # Get recent test activities (last 2 hours) from teacher's students
    two_hours_ago = datetime.utcnow() - timedelta(hours=2)
    
    activities = db.query(models.Activity).join(
        models.User, models.Activity.user_id == models.User.id
    ).filter(
        models.User.class_id.in_(teacher_class_ids),
        models.Activity.type == "test",
        models.Activity.started_at >= two_hours_ago
    ).order_by(models.Activity.started_at.desc()).limit(20).all()
    
    result = []
    for activity in activities:
        student = db.query(models.User).filter(models.User.id == activity.user_id).first()
        metrics = activity.metrics
        
        if metrics:
            result.append({
                "id": activity.id,
                "student_name": student.name if student else "Unknown",
                "student_id": student.student_id if student else "",
                "action": "体能测试",
                "video_url": metrics.video_url,
                "score": metrics.score or 0,
                "score_detail": metrics.score_detail,
                "is_abnormal": (metrics.score or 0) < 60,
                "confidence": metrics.score or 0,
                "started_at": activity.started_at.isoformat()
            })
    
    return {"items": result}


@app.get("/teacher/tests/stats")
def get_test_stats(
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """Get test statistics for data analysis（按分配管辖班级过滤）"""
    teacher_class_ids = _teacher_class_ids_by_binding(db, current_user.id)
    if not teacher_class_ids:
        return {
            "class_skills": [],
            "class_comparison": [],
            "pass_rates": []
        }
    
    # Get all test activities from teacher's students
    activities = db.query(models.Activity).join(
        models.User, models.Activity.user_id == models.User.id
    ).filter(
        models.User.class_id.in_(teacher_class_ids),
        models.Activity.type == "test"
    ).all()
    
    if not activities:
        return {
            "class_skills": [],
            "class_comparison": [],
            "pass_rates": []
        }
    
    # Calculate score distribution
    scores = [act.metrics.score for act in activities if act.metrics and act.metrics.score]
    
    excellent = len([s for s in scores if s >= 90])
    good = len([s for s in scores if 80 <= s < 90])
    pass_score = len([s for s in scores if 60 <= s < 80])
    fail = len([s for s in scores if s < 60])
    
    total = len(scores) if scores else 1
    
    # Calculate average scores by test type (mock data for now, can be enhanced)
    avg_score = sum(scores) / len(scores) if scores else 0
    
    return {
        "class_skills": [
            {"name": "爆发力", "val": min(100, int(avg_score * 0.95)), "color": "#ff6b6b"},
            {"name": "耐力", "val": min(100, int(avg_score * 0.85)), "color": "#4dabf7"},
            {"name": "柔韧性", "val": min(100, int(avg_score * 0.80)), "color": "#ffd43b"},
            {"name": "协调性", "val": min(100, int(avg_score * 1.05)), "color": "#20C997"},
            {"name": "核心力量", "val": min(100, int(avg_score * 0.90)), "color": "#a55eea"}
        ],
        "class_comparison": [
            {"label": "优秀", "value": excellent, "percent": int(excellent / total * 100), "color": "#20C997"},
            {"label": "良好", "value": good, "percent": int(good / total * 100), "color": "#4dabf7"},
            {"label": "及格", "value": pass_score, "percent": int(pass_score / total * 100), "color": "#ffd43b"},
            {"label": "不及格", "value": fail, "percent": int(fail / total * 100), "color": "#ff6b6b"}
        ],
        "pass_rates": [
            {"name": "综合体能", "rate": int((total - fail) / total * 100) if total > 0 else 0}
        ]
    }


@app.get("/teacher/tests/history")
def get_test_history(
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """Get historical test records grouped by date（按分配管辖班级过滤）"""
    teacher_class_ids = _teacher_class_ids_by_binding(db, current_user.id)
    if not teacher_class_ids:
        return {"items": []}
    
    # Get all test activities from teacher's students
    activities = db.query(models.Activity).join(
        models.User, models.Activity.user_id == models.User.id
    ).filter(
        models.User.class_id.in_(teacher_class_ids),
        models.Activity.type == "test"
    ).order_by(models.Activity.started_at.desc()).all()
    
    # Group by date
    from collections import defaultdict
    grouped = defaultdict(list)
    
    for activity in activities:
        date_key = activity.started_at.date().isoformat()
        if activity.metrics and activity.metrics.score:
            grouped[date_key].append(activity.metrics.score)
    
    # Calculate stats for each date
    result = []
    for date_str, scores in sorted(grouped.items(), reverse=True)[:10]:  # Last 10 test dates
        total_count = len(scores)
        pass_count = len([s for s in scores if s >= 60])
        pass_rate = int(pass_count / total_count * 100) if total_count > 0 else 0
        
        result.append({
            "date": date_str,
            "test_name": "体能测试",
            "count": total_count,
            "pass_rate": pass_rate
        })
    
    return {"items": result}


@app.get("/teacher/stats", response_model=schemas.TeacherStatsOut)
def get_teacher_stats(
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """
    兼容旧接口：教师工作台统计，但现在基于 TeacherClass 权限过滤。
    若未绑定任何班级，则视为 0。
    """
    tc_rows = db.query(models.TeacherClass).filter(
        models.TeacherClass.teacher_id == current_user.id
    ).all()
    class_names = [r.class_name for r in tc_rows]
    if class_names:
        class_ids = [
            c.id
            for c in db.query(models.Class)
            .filter(models.Class.name.in_(class_names))
            .all()
        ]
    else:
        class_ids = []

    if class_ids:
        # 1. Student Count
        student_count = db.query(models.User).filter(
            models.User.role == "student",
            models.User.class_id.in_(class_ids),
        ).count()

        # 2. Pending Approvals (Health Requests)
        pending_approvals = (
            db.query(models.HealthRequest)
            .join(models.User, models.HealthRequest.student_id == models.User.id)
            .filter(
                models.HealthRequest.status == "pending",
                models.User.class_id.in_(class_ids),
            )
            .count()
        )

        # 3. Abnormal Count
        abnormal_count = db.query(models.User).filter(
            models.User.role == "student",
            models.User.class_id.in_(class_ids),
            models.User.health_status != "normal",
        ).count()

        # 4. Today Check-in (Activities today)
        today = datetime.utcnow().date()
        today_start = datetime(today.year, today.month, today.day)
        today_checkin = (
            db.query(models.Activity)
            .join(models.User, models.Activity.user_id == models.User.id)
            .filter(
                models.Activity.started_at >= today_start,
                models.User.class_id.in_(class_ids),
            )
            .count()
        )
    else:
        student_count = 0
        pending_approvals = 0
        abnormal_count = 0
        today_checkin = 0

    # 5. Task Count（仍然按教师本人创建的任务计算）
    task_count = db.query(models.Task).filter(
        models.Task.created_by == current_user.id
    ).count()

    return {
        "student_count": student_count,
        "today_checkin": today_checkin,
        "abnormal_count": abnormal_count,
        "pending_approvals": pending_approvals,
        "pending_health": pending_approvals,
        "avg_pace": "5'45\"",  # 此处仍保持简单占位
        "task_count": task_count,
        "compliance_rate": 92 if class_ids else 0,  # 占位字段，前端主要用于展示
        "qualified_rate": 0,
        "completion_rate": 0,
    }

@app.get("/teacher/student/{student_id}/activities", response_model=schemas.ActivityListResponse)
def get_student_activities_for_teacher(
    student_id: int,
    page: int = 1,
    size: int = 20,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    query = db.query(models.Activity).filter(models.Activity.user_id == student_id)
    total = query.count()
    activities = query.order_by(models.Activity.started_at.desc()).offset((page - 1) * size).limit(size).all()
    
    return {
        "items": activities,
        "total": total,
        "page": page,
        "size": size
    }



# --- Advanced Student Management ---

@app.get("/teacher/students", response_model=List[schemas.StudentDetail])
def get_all_students(
    page: int = 1,
    size: int = 20,
    class_id: Optional[int] = None,
    name: Optional[str] = None,
    student_id: Optional[str] = None,
    status: Optional[str] = None, # 'normal', 'leave', 'injured'
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    # Only show students in classes bound via TeacherClass
    tc_rows = db.query(models.TeacherClass).filter(
        models.TeacherClass.teacher_id == current_user.id
    ).all()
    class_names = [r.class_name for r in tc_rows]

    if not class_names:
        return []

    query = db.query(models.User).join(models.Class, models.User.class_id == models.Class.id).filter(
        models.User.role == "student",
        models.Class.name.in_(class_names)
    )
    
    if class_id:
        query = query.filter(models.User.class_id == class_id)
    if name:
        query = query.filter(models.User.name.contains(name))
    if student_id:
        query = query.filter(models.User.student_id.contains(student_id))
    if status:
        query = query.filter(models.User.health_status == status)
    
    total = query.count()
    students = query.offset((page - 1) * size).limit(size).all()
    return students

@app.get("/teacher/students/abnormal", response_model=List[schemas.StudentDetail])
def get_abnormal_students(
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    tc_rows = db.query(models.TeacherClass).filter(
        models.TeacherClass.teacher_id == current_user.id
    ).all()
    class_names = [r.class_name for r in tc_rows]

    if not class_names:
        return []

    students = db.query(models.User).join(models.Class, models.User.class_id == models.Class.id).filter(
        models.User.role == "student",
        models.Class.name.in_(class_names),
        models.User.health_status != "normal"
    ).all()
    return students

@app.get("/teacher/students/{student_id}", response_model=schemas.StudentDetail)
def get_student_detail(
    student_id: int,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    student = db.query(models.User).filter(models.User.id == student_id, models.User.role == "student").first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return student

@app.put("/teacher/students/{student_id}", response_model=schemas.StudentDetail)
def update_student(
    student_id: int,
    student_in: schemas.StudentUpdate,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    student = db.query(models.User).filter(models.User.id == student_id, models.User.role == "student").first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
        
    if student_in.student_id is not None:
        student.student_id = student_in.student_id
    if student_in.health_status is not None:
        student.health_status = student_in.health_status
    if student_in.abnormal_reason is not None:
        student.abnormal_reason = student_in.abnormal_reason
    if student_in.group_name is not None:
        student.group_name = student_in.group_name
    if student_in.class_id is not None:
        student.class_id = student_in.class_id
        
    db.commit()
    db.refresh(student)
    return student

@app.post("/teacher/students/bulk-update", status_code=200)
def bulk_update_students(
    update_in: schemas.BulkUpdateStudent,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    query = db.query(models.User).filter(
        models.User.id.in_(update_in.student_ids),
        models.User.role == "student"
    )
    
    update_data = {}
    if update_in.health_status is not None:
        update_data[models.User.health_status] = update_in.health_status
    if update_in.abnormal_reason is not None:
        update_data[models.User.abnormal_reason] = update_in.abnormal_reason
    if update_in.group_name is not None:
        update_data[models.User.group_name] = update_in.group_name
    if update_in.class_id is not None:
        update_data[models.User.class_id] = update_in.class_id
        
    if not update_data:
        raise HTTPException(status_code=400, detail="No update data provided")
        
    # Using synchronize_session=False for efficiency, but we need to verify if any rows matched
    rows_updated = query.update(update_data, synchronize_session=False)
    db.commit()
    
    return {"message": "Success", "updated_count": rows_updated}

@app.post("/teacher/students/export")
def export_students(
    export_in: schemas.BulkUpdateStudent, # Reusing this schema as it has student_ids and filters
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    import csv
    import io
    from fastapi.responses import StreamingResponse

    query = db.query(models.User).filter(models.User.role == "student")
    
    # If specific IDs are provided, export those
    if export_in.student_ids:
        query = query.filter(models.User.id.in_(export_in.student_ids))
    else:
        # Otherwise apply filters
        if export_in.class_id:
            query = query.filter(models.User.class_id == export_in.class_id)
        if export_in.health_status:
            query = query.filter(models.User.health_status == export_in.health_status)
        if export_in.group_name:
            query = query.filter(models.User.group_name == export_in.group_name)
        
    students = query.all()
    
    # Generate CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(['ID', 'Name', 'Student ID', 'Phone', 'Class ID', 'Group', 'Health Status', 'Abnormal Reason'])
    
    for s in students:
        writer.writerow([
            s.id,
            s.name,
            s.student_id or '',
            s.phone,
            s.class_id or '',
            s.group_name or '',
            s.health_status,
            s.abnormal_reason or ''
        ])
        
    output.seek(0)
    
    response = StreamingResponse(iter([output.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=students_export.csv"
    return response



# --- Health Status Management ---

@app.post("/student/health/request")
def create_health_request(
    request_in: schemas.HealthRequestCreate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Only students can submit health requests")
        
    # Check if there is already a pending request
    pending = db.query(models.HealthRequest).filter(
        models.HealthRequest.student_id == current_user.id,
        models.HealthRequest.status == "pending"
    ).first()
    
    if pending:
        raise HTTPException(status_code=400, detail="You already have a pending request")
    
    # Convert attachments list to JSON string
    import json
    attachments_json = json.dumps(request_in.attachments) if request_in.attachments else None
        
    # 校验请假时间（仅请假类型需要）
    start_date = request_in.start_date
    end_date = request_in.end_date
    if request_in.type == "leave":
        if not start_date or not end_date:
            raise HTTPException(status_code=400, detail="Leave requests must include start_date and end_date")
        if end_date < start_date:
            raise HTTPException(status_code=400, detail="end_date must be after start_date")

    new_req = models.HealthRequest(
        student_id=current_user.id,
        type=request_in.type,
        reason=request_in.reason,
        attachments=attachments_json,
        start_date=start_date,
        end_date=end_date,
        status="pending"
    )
    db.add(new_req)
    db.commit()
    db.refresh(new_req)
    
    # Convert back to dict with parsed attachments
    return {
        "id": new_req.id,
        "student_id": new_req.student_id,
        "type": new_req.type,
        "reason": new_req.reason,
        "start_date": new_req.start_date,
        "end_date": new_req.end_date,
        "attachments": json.loads(new_req.attachments) if new_req.attachments else [],
        "status": new_req.status,
        "created_at": new_req.created_at,
        "updated_at": new_req.updated_at
    }

@app.get("/student/health/requests")
def get_my_health_requests(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    import json
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Only students can view their health requests")
        
    requests = db.query(models.HealthRequest).filter(
        models.HealthRequest.student_id == current_user.id
    ).order_by(models.HealthRequest.created_at.desc()).all()
    
    # Convert attachments from JSON string to list
    result = []
    for req in requests:
        result.append({
            "id": req.id,
            "student_id": req.student_id,
            "type": req.type,
            "reason": req.reason,
            "start_date": req.start_date,
            "end_date": req.end_date,
            "attachments": json.loads(req.attachments) if req.attachments else [],
            "status": req.status,
            "created_at": req.created_at,
            "updated_at": req.updated_at
        })
    
    return result

@app.get("/teacher/health/requests", response_model=List[schemas.HealthRequestOut])
def get_pending_health_requests(
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    teacher_class_ids = _teacher_class_ids(db, current_user.id)
    if not teacher_class_ids:
        return []
    
    # Only show health requests from students in teacher's classes
    requests = db.query(models.HealthRequest).join(
        models.User, models.HealthRequest.student_id == models.User.id
    ).filter(
        models.HealthRequest.status == "pending",
        models.User.class_id.in_(teacher_class_ids)
    ).all()
    return requests

@app.put("/teacher/health/requests/{request_id}/review")
def review_health_request(
    request_id: int,
    status: str = Body(..., embed=True), # 'approved' or 'rejected'
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    if status not in ["approved", "rejected"]:
        raise HTTPException(status_code=400, detail="Invalid status")
        
    req = db.query(models.HealthRequest).filter(models.HealthRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    student = db.query(models.User).filter(models.User.id == req.student_id).first()
    if not student or not student.class_id or student.class_id not in _teacher_class_ids(db, current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized to review this request")
        
    req.status = status
    
    # If approved, update student status
    if status == "approved":
        student = db.query(models.User).filter(models.User.id == req.student_id).first()
        if student:
            if req.type == "leave":
                student.health_status = "leave"
                student.abnormal_reason = req.reason
            elif req.type == "injury":
                student.health_status = "injured"
                student.abnormal_reason = req.reason
                
    db.commit()
    return {"ok": True}

@app.post("/teacher/activity/{activity_id}/approve", response_model=schemas.ActivityOut)
def approve_activity(
    activity_id: int,
    approval: schemas.ApproveRequest, # 虽然只允许 approved，但为了扩展性还是接收个 body 吧，或者直接忽略 body
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    activity = db.query(models.Activity).filter(models.Activity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    
    # Check if already reviewed
    if activity.review:
        raise HTTPException(status_code=400, detail="Activity already reviewed")

    # Update activity status
    activity.status = "approved"
    
    # Create Review Record
    review = models.ActivityReview(
        activity_id=activity.id,
        teacher_id=current_user.id,
        result="approved"
    )
    db.add(review)
    db.commit()
    db.refresh(activity)
    
    return activity

@app.post("/teacher/tasks", response_model=schemas.TaskOut)
def create_task(
    task_in: schemas.TaskCreate,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    new_task = models.Task(
        title=task_in.title,
        type=task_in.type,
        min_distance=task_in.min_distance,
        min_duration=task_in.min_duration,
        min_count=task_in.min_count,
        deadline=task_in.deadline,
        description=task_in.description,
        target_group=task_in.target_group,
        class_id=task_in.class_id,
        created_by=current_user.id,
        video_url=task_in.video_url  # 第二阶段新增：支持体测视频
    )
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    return new_task

@app.get("/teacher/tasks", response_model=schemas.TaskListResponse)
def get_teacher_tasks(
    page: int = 1,
    size: int = 20,
    status: Optional[str] = None, # 'active', 'ended'
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    query = db.query(models.Task).filter(models.Task.created_by == current_user.id)
    
    # Default to active tasks if status is not provided or explicitly 'active'
    if status == 'ended':
        query = query.filter(models.Task.deadline < datetime.utcnow())
    elif status == 'active' or status is None:
        # Show tasks with no deadline OR deadline in future
        query = query.filter(or_(models.Task.deadline == None, models.Task.deadline > datetime.utcnow()))
        
    total = query.count()
    tasks = query.order_by(models.Task.id.desc()).offset((page - 1) * size).limit(size).all()
    
    # Calculate stats for each task
    # Note: This is N+1 query, but acceptable for MVP with pagination (20 items)
    tasks_with_stats = []
    
    # Get all students count once
    total_students = db.query(models.User).filter(models.User.role == "student").count()
    
    for task in tasks:
        # Reusing similar logic to get_task_detail but optimized for counting only
        # We need to find how many students completed this task
        
        # Get all activities that match this task type and time range
        act_query = db.query(models.Activity).filter(
            models.Activity.type == task.type
        )
        if task.created_at:
             act_query = act_query.filter(models.Activity.started_at >= task.created_at)
        if task.deadline:
            act_query = act_query.filter(models.Activity.started_at <= task.deadline)
            
        activities = act_query.all()
        
        # Group by user_id to find unique completers
        # A student might have multiple activities, we only need to know if ONE of them qualifies
        student_activities = {}
        for act in activities:
            if act.user_id not in student_activities:
                student_activities[act.user_id] = []
            student_activities[act.user_id].append(act)
            
        completed_count = 0
        
        for student_id, acts in student_activities.items():
            is_completed = False
            for act in acts:
                metrics = act.metrics
                if not metrics:
                    continue
                
                if task.min_distance and task.min_distance > 0:
                    if (metrics.distance or 0) < task.min_distance:
                        continue
                
                if task.min_duration and task.min_duration > 0:
                    if (metrics.duration or 0) < (task.min_duration * 60):
                        continue
                
                if task.min_count and task.min_count > 0:
                    if (metrics.count or 0) < task.min_count:
                        continue
                        
                is_completed = True
                break
            
            if is_completed:
                completed_count += 1
        
        task_dict = task.__dict__
        task_dict["total_students"] = total_students
        task_dict["completed_count"] = completed_count
        tasks_with_stats.append(task_dict)

    return {
        "items": tasks_with_stats,
        "total": total,
        "page": page,
        "size": size
    }

@app.delete("/teacher/tasks/{task_id}")
def delete_task(
    task_id: int,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    db.delete(task)
    db.commit()
    return {"ok": True}

@app.get("/teacher/tasks/{task_id}", response_model=schemas.TaskDetailStats)
def get_task_detail(
    task_id: int,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Get all students
    # In a real app, we might filter by target_group
    students = db.query(models.User).filter(models.User.role == "student").all()
    
    student_statuses = []
    completed_count = 0
    
    for student in students:
        # Find qualifying activity
        act_query = db.query(models.Activity).filter(
            models.Activity.user_id == student.id,
            models.Activity.type == task.type
        )
        
        if task.created_at:
             act_query = act_query.filter(models.Activity.started_at >= task.created_at)
        
        if task.deadline:
            act_query = act_query.filter(models.Activity.started_at <= task.deadline)
            
        activities = act_query.all()
        
        is_completed = False
        completed_activity = None
        
        for act in activities:
            metrics = act.metrics
            if not metrics:
                continue
                
            # Distance (km)
            if task.min_distance and task.min_distance > 0:
                if (metrics.distance or 0) < task.min_distance:
                    continue
            
            # Duration (minutes vs seconds)
            if task.min_duration and task.min_duration > 0:
                if (metrics.duration or 0) < (task.min_duration * 60):
                    continue
            
            # Count
            if task.min_count and task.min_count > 0:
                if (metrics.count or 0) < task.min_count:
                    continue
                    
            is_completed = True
            completed_activity = act
            break
        
        status = "pending"
        metric_val = "-"
        completed_at = None
        activity_id = None  # 第三阶段新增
        teacher_score = None  # 第三阶段新增
        
        if is_completed:
            status = "completed"
            completed_count += 1
            completed_at = completed_activity.ended_at
            activity_id = completed_activity.id  # 第三阶段新增
            teacher_score = completed_activity.metrics.teacher_score  # 第三阶段新增
            if task.type == 'run':
                metric_val = f"{completed_activity.metrics.distance} km"
            else:
                metric_val = f"{completed_activity.metrics.count} 次"
        
        student_statuses.append(schemas.StudentCompletionStatus(
            student_id=student.id,
            student_name=student.name,
            status=status,
            completed_at=completed_at,
            metric_value=metric_val,
            activity_id=activity_id,  # 第三阶段新增
            teacher_score=teacher_score  # 第三阶段新增
        ))
        
    return {
        **task.__dict__,
        "total_students": len(students),
        "completed_count": completed_count,
        "student_statuses": student_statuses
    }

# 第三阶段新增：教师打分API
@app.post("/teacher/activities/{activity_id}/score")
def score_activity(
    activity_id: int,
    score_data: schemas.TeacherScoreCreate,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """教师对学生活动进行打分"""
    from datetime import datetime
    
    # 获取活动
    activity = db.query(models.Activity).filter(models.Activity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="活动不存在")
    
    # 获取活动指标
    metrics = db.query(models.ActivityMetrics).filter(
        models.ActivityMetrics.activity_id == activity_id
    ).first()
    
    if not metrics:
        raise HTTPException(status_code=404, detail="活动指标不存在")
    
    # 更新打分
    metrics.teacher_score = score_data.score
    metrics.teacher_comment = score_data.comment
    metrics.scored_at = datetime.utcnow()
    metrics.scored_by = current_user.id
    
    # 更新学生平时分（简单累加，可根据需求调整算法）
    student = db.query(models.User).filter(models.User.id == activity.user_id).first()
    if student:
        # 计算该学生所有已打分活动的平均分作为平时分
        all_scored_activities = db.query(models.ActivityMetrics).join(
            models.Activity
        ).filter(
            models.Activity.user_id == student.id,
            models.ActivityMetrics.teacher_score.isnot(None)
        ).all()
        
        if all_scored_activities:
            total_score = sum(m.teacher_score for m in all_scored_activities)
            student.regular_score = round(total_score / len(all_scored_activities), 2)
    
    db.commit()
    db.refresh(metrics)
    
    return {
        "message": "打分成功",
        "activity_id": activity_id,
        "score": metrics.teacher_score,
        "regular_score": student.regular_score if student else None
    }

@app.put("/teacher/activities/{activity_id}/score")
def update_activity_score(
    activity_id: int,
    score_data: schemas.TeacherScoreUpdate,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """修改活动打分"""
    from datetime import datetime
    
    # 获取活动指标
    metrics = db.query(models.ActivityMetrics).filter(
        models.ActivityMetrics.activity_id == activity_id
    ).first()
    
    if not metrics:
        raise HTTPException(status_code=404, detail="活动指标不存在")
    
    # 更新打分
    metrics.teacher_score = score_data.score
    metrics.teacher_comment = score_data.comment
    metrics.scored_at = datetime.utcnow()
    metrics.scored_by = current_user.id
    
    # 重新计算学生平时分
    activity = db.query(models.Activity).filter(models.Activity.id == activity_id).first()
    if activity:
        student = db.query(models.User).filter(models.User.id == activity.user_id).first()
        if student:
            all_scored_activities = db.query(models.ActivityMetrics).join(
                models.Activity
            ).filter(
                models.Activity.user_id == student.id,
                models.ActivityMetrics.teacher_score.isnot(None)
            ).all()
            
            if all_scored_activities:
                total_score = sum(m.teacher_score for m in all_scored_activities)
                student.regular_score = round(total_score / len(all_scored_activities), 2)
    
    db.commit()
    db.refresh(metrics)
    
    return {
        "message": "打分更新成功",
        "activity_id": activity_id,
        "score": metrics.teacher_score
    }

@app.get("/teacher/tasks/{task_id}/scores")
def get_task_scores(
    task_id: int,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """获取任务的所有打分记录"""
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 获取任务相关的所有学生活动和打分
    students = []
    if task.class_id:
        students = db.query(models.User).filter(
            models.User.class_id == task.class_id,
            models.User.role == "student"
        ).all()
    else:
        students = db.query(models.User).filter(models.User.role == "student").all()
    
    scores = []
    for student in students:
        # 查找该学生完成该任务的活动
        activities = db.query(models.Activity).filter(
            models.Activity.user_id == student.id,
            models.Activity.type == task.type
        ).all()
        
        for activity in activities:
            metrics = db.query(models.ActivityMetrics).filter(
                models.ActivityMetrics.activity_id == activity.id
            ).first()
            
            if metrics and metrics.teacher_score is not None:
                scores.append({
                    "activity_id": activity.id,
                    "student_id": student.id,
                    "student_name": student.name,
                    "score": metrics.teacher_score,
                    "comment": metrics.teacher_comment,
                    "scored_at": metrics.scored_at
                })
    
    return {"scores": scores}

@app.get("/teacher/students/{student_id}/scores")
def get_student_scores(
    student_id: int,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """获取学生的所有打分记录和平时分"""
    student = db.query(models.User).filter(models.User.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="学生不存在")
    
    # 获取所有已打分的活动
    scored_activities = db.query(models.Activity, models.ActivityMetrics).join(
        models.ActivityMetrics
    ).filter(
        models.Activity.user_id == student_id,
        models.ActivityMetrics.teacher_score.isnot(None)
    ).all()
    
    score_records = []
    for activity, metrics in scored_activities:
        # 尝试找到关联的任务
        task = db.query(models.Task).filter(
            models.Task.type == activity.type,
            models.Task.class_id == student.class_id
        ).first()
        
        score_records.append({
            "task_id": task.id if task else 0,
            "task_title": task.title if task else "自由练习",
            "activity_id": activity.id,
            "score": metrics.teacher_score,
            "comment": metrics.teacher_comment,
            "scored_at": metrics.scored_at
        })
    
    return {
        "student_id": student.id,
        "student_name": student.name,
        "regular_score": student.regular_score,
        "score_records": score_records
    }

# 第四阶段新增：数据统计API
@app.get("/teacher/stats/task-completion")
def get_task_completion_stats(
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """获取任务完成情况统计"""
    from datetime import datetime, timedelta
    
    # 获取教师创建的所有任务
    tasks = db.query(models.Task).filter(
        models.Task.created_by == current_user.id
    ).all()
    
    stats = []
    for task in tasks:
        # 获取目标学生
        if task.class_id:
            students = db.query(models.User).filter(
                models.User.class_id == task.class_id,
                models.User.role == "student"
            ).all()
        else:
            students = db.query(models.User).filter(
                models.User.role == "student"
            ).all()
        
        total_students = len(students)
        completed_count = 0
        
        # 统计完成情况
        for student in students:
            activities = db.query(models.Activity).join(
                models.ActivityMetrics
            ).filter(
                models.Activity.user_id == student.id,
                models.Activity.type == task.type
            ).all()
            
            for activity in activities:
                metrics = activity.metrics
                is_completed = False
                
                if task.type == 'run':
                    if (metrics.distance or 0) >= (task.min_distance or 0):
                        is_completed = True
                else:
                    if (metrics.count or 0) >= (task.min_count or 0):
                        is_completed = True
                
                if is_completed:
                    completed_count += 1
                    break
        
        completion_rate = round((completed_count / total_students * 100), 2) if total_students > 0 else 0
        
        stats.append({
            "task_id": task.id,
            "task_title": task.title,
            "task_type": task.type,
            "total_students": total_students,
            "completed_count": completed_count,
            "completion_rate": completion_rate,
            "deadline": task.deadline.isoformat() if task.deadline else None
        })
    
    return {"tasks": stats}

@app.get("/teacher/stats/score-trend")
def get_score_trend_stats(
    days: int = 30,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """获取平时分变化趋势"""
    from datetime import datetime, timedelta
    
    students = []
    if current_user.role == "teacher":
        cids = _teacher_class_ids(db, current_user.id)
        if cids:
            students = db.query(models.User).filter(
                models.User.class_id.in_(cids),
                models.User.role == "student"
            ).all()
    
    # 按日期统计平时分
    start_date = datetime.utcnow() - timedelta(days=days)
    
    trend_data = []
    for i in range(days):
        date = start_date + timedelta(days=i)
        date_str = date.strftime('%Y-%m-%d')
        
        # 获取该日期的平均平时分
        if students:
            total_score = sum(s.regular_score for s in students)
            avg_score = round(total_score / len(students), 2)
        else:
            avg_score = 0
        
        trend_data.append({
            "date": date_str,
            "avg_score": avg_score
        })
    
    return {"trend": trend_data}

@app.get("/teacher/stats/scoring-trend")
def get_scoring_trend_stats(
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """获取教师打分趋势分析"""
    from datetime import datetime, timedelta
    
    # 获取最近30天的打分记录
    start_date = datetime.utcnow() - timedelta(days=30)
    
    scored_activities = db.query(models.ActivityMetrics).filter(
        models.ActivityMetrics.scored_by == current_user.id,
        models.ActivityMetrics.scored_at >= start_date
    ).all()
    
    # 按分数段统计
    score_distribution = {
        "90-100": 0,
        "80-89": 0,
        "70-79": 0,
        "60-69": 0,
        "0-59": 0
    }
    
    total_score = 0
    for metrics in scored_activities:
        score = metrics.teacher_score
        total_score += score
        
        if score >= 90:
            score_distribution["90-100"] += 1
        elif score >= 80:
            score_distribution["80-89"] += 1
        elif score >= 70:
            score_distribution["70-79"] += 1
        elif score >= 60:
            score_distribution["60-69"] += 1
        else:
            score_distribution["0-59"] += 1
    
    avg_score = round(total_score / len(scored_activities), 2) if scored_activities else 0
    
    return {
        "total_scored": len(scored_activities),
        "avg_score": avg_score,
        "distribution": score_distribution
    }

@app.get("/teacher/export/scores")
def export_scores(
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """导出成绩数据"""
    students = []
    if current_user.role == "teacher":
        cids = _teacher_class_ids(db, current_user.id)
        if cids:
            students = db.query(models.User).filter(
                models.User.class_id.in_(cids),
                models.User.role == "student"
            ).all()
    
    export_data = []
    for student in students:
        # 获取学生的打分记录
        scored_activities = db.query(models.Activity, models.ActivityMetrics).join(
            models.ActivityMetrics
        ).filter(
            models.Activity.user_id == student.id,
            models.ActivityMetrics.teacher_score.isnot(None)
        ).all()
        
        score_count = len(scored_activities)
        
        export_data.append({
            "student_id": student.id,
            "student_name": student.name,
            "student_no": student.student_id or student.phone,
            "class_name": student.student_class.name if student.student_class else "未分配",
            "regular_score": student.regular_score,
            "score_count": score_count
        })
    
    return {"data": export_data}

@app.get("/teacher/export/tasks")
def export_tasks(
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """导出任务数据"""
    tasks = db.query(models.Task).filter(
        models.Task.created_by == current_user.id
    ).all()
    
    export_data = []
    for task in tasks:
        # 获取目标学生
        if task.class_id:
            students = db.query(models.User).filter(
                models.User.class_id == task.class_id,
                models.User.role == "student"
            ).all()
        else:
            students = db.query(models.User).filter(
                models.User.role == "student"
            ).all()
        
        total_students = len(students)
        completed_count = 0
        
        for student in students:
            activities = db.query(models.Activity).join(
                models.ActivityMetrics
            ).filter(
                models.Activity.user_id == student.id,
                models.Activity.type == task.type
            ).all()
            
            for activity in activities:
                metrics = activity.metrics
                is_completed = False
                
                if task.type == 'run':
                    if (metrics.distance or 0) >= (task.min_distance or 0):
                        is_completed = True
                else:
                    if (metrics.count or 0) >= (task.min_count or 0):
                        is_completed = True
                
                if is_completed:
                    completed_count += 1
                    break
        
        completion_rate = round((completed_count / total_students * 100), 2) if total_students > 0 else 0
        
        export_data.append({
            "task_id": task.id,
            "task_title": task.title,
            "task_type": "跑步任务" if task.type == "run" else "体测任务",
            "deadline": task.deadline.strftime('%Y-%m-%d') if task.deadline else "无限制",
            "total_students": total_students,
            "completed_count": completed_count,
            "completion_rate": f"{completion_rate}%"
        })
    
    return {"data": export_data}

@app.get("/teacher/activities/abnormal")
def get_abnormal_activities(
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """获取异常运动数据（防作弊）"""
    # 获取教师负责的班级学生
    students = []
    if current_user.class_id:
        students = db.query(models.User).filter(
            models.User.class_id == current_user.class_id,
            models.User.role == "student"
        ).all()
    else:
        # 如果教师没有班级，获取所有学生
        students = db.query(models.User).filter(
            models.User.role == "student"
        ).all()
    
    student_ids = [s.id for s in students]
    
    # 查询异常活动（这里定义异常规则）
    abnormal_activities = []
    
    # 获取最近的活动
    activities = db.query(models.Activity).filter(
        models.Activity.user_id.in_(student_ids),
        models.Activity.status == "finished"
    ).order_by(models.Activity.started_at.desc()).limit(100).all()
    
    for activity in activities:
        metrics = db.query(models.ActivityMetrics).filter(
            models.ActivityMetrics.activity_id == activity.id
        ).first()
        
        if not metrics:
            continue
        
        student = db.query(models.User).filter(models.User.id == activity.user_id).first()
        reason = None
        
        # 检测异常规则
        if activity.type == "run":
            # 1. 配速异常（过快或过慢）
            if metrics.pace and (metrics.pace < 3.0 or metrics.pace > 10.0):
                reason = f"配速异常：{metrics.pace:.1f}分/公里（正常范围：3-10分/公里）"
            
            # 2. 距离异常（与GPS轨迹不符）
            if metrics.distance and metrics.distance > 20:
                reason = f"距离异常：{metrics.distance:.2f}公里（单次跑步距离过长）"
            
            # 3. 心率异常
            if metrics.avg_heart_rate:
                if metrics.avg_heart_rate < 60 or metrics.avg_heart_rate > 200:
                    reason = f"心率异常：{metrics.avg_heart_rate}次/分（正常范围：60-200次/分）"
        
        if reason:
            abnormal_activities.append({
                "activity_id": activity.id,
                "student_id": student.id if student else None,
                "student_name": student.name if student else "未知",
                "type": activity.type,
                "created_at": activity.started_at.isoformat() if activity.started_at else None,
                "reason": reason,
                "abnormal_value": f"{metrics.pace:.1f}分/公里" if metrics.pace else str(metrics.avg_heart_rate) if metrics.avg_heart_rate else f"{metrics.distance:.2f}公里",
                "standard_range": "3-10分/公里" if "配速" in reason else "60-200次/分" if "心率" in reason else "0-20公里"
            })
    
    return abnormal_activities

@app.post("/teacher/activities/{activity_id}/ignore")
def ignore_abnormal_activity(
    activity_id: int,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """忽略异常活动"""
    activity = db.query(models.Activity).filter(models.Activity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="活动不存在")
    
    # 可以添加一个标记字段表示已忽略
    # 这里简单返回成功
    return {"message": "已忽略该异常"}

@app.post("/teacher/activities/{activity_id}/handle")
def handle_abnormal_activity(
    activity_id: int,
    handle_data: dict,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """处理异常活动"""
    activity = db.query(models.Activity).filter(models.Activity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="活动不存在")
    
    action = handle_data.get("action")
    reason = handle_data.get("reason")
    
    # 根据不同的处理方式更新活动状态
    if action == "invalid":
        # 标记为无效成绩
        activity.status = "invalid"
    elif action == "device_error":
        # 标记为设备故障
        activity.status = "device_error"
    elif action == "retest":
        # 要求重测
        activity.status = "retest_required"
    
    db.commit()
    
    return {"message": f"已处理：{action}", "activity_id": activity_id}

@app.post("/teacher/students/{student_id}/notify")
def notify_student(
    student_id: int,
    notify_data: dict,
    current_user: models.User = Depends(auth.get_current_teacher),
    db: Session = Depends(get_db)
):
    """通知学生"""
    student = db.query(models.User).filter(
        models.User.id == student_id,
        models.User.role == "student"
    ).first()
    
    if not student:
        raise HTTPException(status_code=404, detail="学生不存在")
    
    message = notify_data.get("message", "")
    
    # TODO: 实现实际的通知功能（推送、短信等）
    # 这里简单返回成功
    return {"message": "通知已发送", "student_id": student_id}

@app.get("/student/tasks", response_model=schemas.StudentTaskListResponse)
def get_student_tasks(
    page: int = 1,
    size: int = 20,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    from datetime import datetime
    now = datetime.utcnow()

    query = db.query(models.Task)
    
    # 仅显示本班老师发布的任务或全局任务（class_id 为 NULL）
    if current_user.class_id:
        cls = db.query(models.Class).filter(models.Class.id == current_user.class_id).first()
        teacher_id = cls.teacher_id if cls else None
        
        # 显示本班任务或全局任务
        query = query.filter(or_(models.Task.class_id == current_user.class_id, models.Task.class_id == None))
        
        # 如果有班主任，只显示班主任创建的本班任务，但保留所有全局任务
        if teacher_id:
            query = query.filter(
                or_(
                    models.Task.created_by == teacher_id,  # 本班老师创建的任务
                    models.Task.class_id == None  # 或全局任务
                )
            )
    else:
        # 没有班级的学生不显示任何任务（避免新注册学生看到全局任务）
        # 如果需要显示全局任务，改为: query = query.filter(models.Task.class_id == None)
        query = query.filter(models.Task.id == -1)  # 返回空结果

    total = query.count()
    tasks = query.order_by(models.Task.id.desc()).offset((page - 1) * size).limit(size).all()
    
    student_tasks = []
    
    for task in tasks:
        status = "pending"
        progress = 0.0
        
        if task.deadline and task.deadline < now:
            status = "expired"
        
        # Check completion
        # Activity started after task creation (or check all if created_at is null/old)
        # Use task.created_at if available, else ignore start time check (or use task logic)
        act_query = db.query(models.Activity).filter(
            models.Activity.user_id == current_user.id,
            models.Activity.type == task.type
        )
        
        if task.created_at:
             act_query = act_query.filter(models.Activity.started_at >= task.created_at)
        
        if task.deadline:
            act_query = act_query.filter(models.Activity.started_at <= task.deadline)
            
        activities = act_query.all()
        
        is_completed = False
        
        for act in activities:
            # Check requirements
            metrics = act.metrics
            if not metrics:
                continue
                
            # Distance (km)
            if task.min_distance and task.min_distance > 0:
                if (metrics.distance or 0) < task.min_distance:
                    continue
            
            # Duration (minutes vs seconds)
            if task.min_duration and task.min_duration > 0:
                if (metrics.duration or 0) < (task.min_duration * 60):
                    continue
            
            # Count
            if task.min_count and task.min_count > 0:
                if (metrics.count or 0) < task.min_count:
                    continue
                    
            is_completed = True
            break
        
        if is_completed:
            status = "completed"
            progress = 100.0
            
        # Construct response object
        # We need to copy task data + extra fields
        task_dict = {
            "id": task.id,
            "title": task.title,
            "type": task.type,
            "min_distance": task.min_distance,
            "min_duration": task.min_duration,
            "min_count": task.min_count,
            "deadline": task.deadline,
            "description": task.description,
            "target_group": task.target_group,
            "created_by": task.created_by,
            "status": status,
            "progress": progress
        }
        student_tasks.append(task_dict)

    return {
        "items": student_tasks,
        "total": total,
        "page": page,
        "size": size
    }

# --- Checkpoint Interfaces ---

@app.get("/checkpoints", response_model=List[schemas.CheckpointOut])
def get_checkpoints(
    db: Session = Depends(get_db)
):
    return db.query(models.Checkpoint).all()

@app.post("/checkpoints", response_model=schemas.CheckpointOut)
def create_checkpoint(
    checkpoint_in: schemas.CheckpointCreate,
    current_user: models.User = Depends(auth.get_current_teacher), # Only teacher can create
    db: Session = Depends(get_db)
):
    db_checkpoint = models.Checkpoint(**checkpoint_in.dict())
    db.add(db_checkpoint)
    db.commit()
    db.refresh(db_checkpoint)
    return db_checkpoint

@app.post("/activity/checkin")
def check_in(
    checkin_in: schemas.CheckInRequest,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    # Validate range
    checkpoint = db.query(models.Checkpoint).filter(models.Checkpoint.id == checkin_in.checkpoint_id).first()
    if not checkpoint:
        raise HTTPException(status_code=404, detail="Checkpoint not found")
        
    # Simple distance check (Haversine approx or simple euclidean for small distances)
    # Using Haversine formula for accuracy
    import math
    R = 6371e3
    dLat = (checkin_in.lat - checkpoint.latitude) * math.pi / 180
    dLng = (checkin_in.lng - checkpoint.longitude) * math.pi / 180
    a = math.sin(dLat/2) * math.sin(dLat/2) + \
        math.cos(checkpoint.latitude * math.pi / 180) * math.cos(checkin_in.lat * math.pi / 180) * \
        math.sin(dLng/2) * math.sin(dLng/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    distance = R * c
    
    if distance > checkpoint.radius:
        return {"success": False, "message": "Not in range", "distance": distance}
        
    return {"success": True, "message": "Check-in successful", "distance": distance, "timestamp": datetime.utcnow()}

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    UPLOAD_DIR = "uploads"
    # Ensure directory exists (already done in startup but good to be safe)
    if not os.path.exists(UPLOAD_DIR):
        os.makedirs(UPLOAD_DIR)
    
    # Generate unique filename to prevent overwrite
    file_extension = os.path.splitext(file.filename)[1]
    if not file_extension:
        file_extension = ".jpg" # Default to jpg if no extension
        
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not upload file: {str(e)}")
        
    # Return URL (relative path)
    return {
        "url": f"/uploads/{unique_filename}", 
        "filename": unique_filename,
        "original_filename": file.filename
    }

@app.post("/activity/score/recalc")
def recalculate_activity_score(
    request: dict = Body(...),
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """
    重新计算活动评分
    
    Args:
        request: 包含activity_id或metrics_id的请求体
        current_user: 当前登录用户
        db: 数据库会话
        
    Returns:
        dict: 更新后的评分信息
    """
    activity_id = request.get('activity_id')
    metrics_id = request.get('metrics_id')
    
    if not activity_id and not metrics_id:
        raise HTTPException(status_code=400, detail="必须提供activity_id或metrics_id")
    
    # 查找活动指标
    if metrics_id:
        metrics = db.query(models.ActivityMetrics).filter(
            models.ActivityMetrics.id == metrics_id
        ).first()
    else:
        activity = db.query(models.Activity).filter(
            models.Activity.id == activity_id,
            models.Activity.user_id == current_user.id
        ).first()
        if not activity:
            raise HTTPException(status_code=404, detail="活动不存在")
        metrics = activity.metrics
    
    if not metrics:
        raise HTTPException(status_code=404, detail="活动指标不存在")
    
    if not metrics.video_url:
        raise HTTPException(status_code=400, detail="该活动没有关联的视频文件")
    
    try:
        # 重新计算评分
        score, score_detail = get_video_score(metrics.video_url)
        
        # 更新数据库
        metrics.score = score
        metrics.score_detail = score_detail
        db.commit()
        
        return {
            "message": "评分更新成功",
            "score": score,
            "score_detail": score_detail,
            "video_url": metrics.video_url
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"评分计算失败: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    # Allow access from all IPs for local testing
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
