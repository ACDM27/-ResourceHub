import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# 数据库连接配置
# 优先从环境变量获取，否则使用SQLite作为默认数据库
# 
# 使用PostgreSQL（推荐生产环境）:
# export SQLALCHEMY_DATABASE_URL="postgresql://username:password@localhost:5432/campus_db"
# 
# 使用SQLite（开发环境）:
# 默认使用SQLite，无需配置
DEFAULT_DB_URL = "sqlite:///./campus_sports.db"
SQLALCHEMY_DATABASE_URL = os.getenv("SQLALCHEMY_DATABASE_URL", DEFAULT_DB_URL)

# SQLite需要特殊配置
connect_args = {}
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args=connect_args
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
