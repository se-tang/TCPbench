"""
数据库连接。默认走 PostgreSQL，连接串从环境变量 DATABASE_URL 读取。
本地没配置的话会用下面这个默认值（部署时务必在 .env 里改掉）。
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://tcpbench:tcpbench@127.0.0.1:5432/tcpbench",
)

# pool_pre_ping=True：每次取连接前先探活一下，避免 PostgreSQL 断连后报错
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI 依赖注入用：每个请求一个 session，用完自动关闭"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
