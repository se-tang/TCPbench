import datetime
from sqlalchemy import Column, String, DateTime, Integer, Float, JSON
from database import Base


class Report(Base):
    """一份测试报告"""
    __tablename__ = "reports"

    id = Column(String(16), primary_key=True)          # 短 ID，用于 /r/<id>
    hostname = Column(String(128))
    ip_masked = Column(String(64))                       # 后两段打码后的 IP
    submitted_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    avg_all = Column(Float, nullable=True)                # 全部可达站点的平均延迟
    reachable = Column(Integer, default=0)
    total = Column(Integer, default=0)
    raw = Column(JSON)                                    # 每个站点的完整测试结果（含采样点）


class SubmissionLog(Base):
    """提交记录，只用来做频率限制，不存业务数据"""
    __tablename__ = "submission_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ip = Column(String(64), index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
