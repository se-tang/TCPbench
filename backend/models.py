import datetime
from sqlalchemy import (
    Column,
    String,
    DateTime,
    Integer,
    Float,
    JSON,
    ForeignKey,
    UniqueConstraint,
)
from database import Base


class ReportBatch(Base):
    """一次测试批次，可同时包含 IPv4 和 IPv6 两份报告。"""
    __tablename__ = "report_batches"

    id = Column(String(16), primary_key=True)
    owner_token = Column(String(64), nullable=False, unique=True, index=True)
    hostname = Column(String(128))
    ip_masked = Column(String(64))
    submitted_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    measurement_version = Column(Integer, nullable=False, default=2, index=True)
    provider = Column(String(64), nullable=True)
    region = Column(String(64), nullable=True)
    plan = Column(String(128), nullable=True)
    note = Column(String(200), nullable=True)
    claim_deadline = Column(DateTime, nullable=True)
    prompted_at = Column(DateTime, nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    edit_count = Column(Integer, nullable=False, default=0)


class Report(Base):
    """一份测试报告"""
    __tablename__ = "reports"

    id = Column(String(16), primary_key=True)          # 短 ID，用于 /r/<id>
    owner_token = Column(String(32), nullable=True)      # 机主令牌，只有跑测试拿到的直链才带这个
    batch_id = Column(String(16), ForeignKey("report_batches.id"), nullable=True, index=True)
    measurement_version = Column(Integer, nullable=False, default=1, index=True)
    ip_family = Column(String(8), nullable=True, index=True)
    dns_server = Column(String(255), nullable=True)
    hostname = Column(String(128))
    ip_masked = Column(String(64))                       # 后两段打码后的 IP
    submitted_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    avg_all = Column(Float, nullable=True)                # 全部可达站点的平均延迟
    reachable = Column(Integer, default=0)
    total = Column(Integer, default=0)
    raw = Column(JSON)                                    # 每个站点的完整测试结果（含采样点）
    score_snapshot = Column(JSON, nullable=True)          # 提交时定格的每站评分快照（scored 数组）
    score_pct = Column(Float, nullable=True)              # 提交时定格的综合评分百分比
    grade = Column(String(4), nullable=True)              # 提交时定格的等级 S/A/B/C/D
    grade_label = Column(String(32), nullable=True)       # 提交时定格的等级标签


class SubmissionLog(Base):
    """提交记录，只用来做频率限制，不存业务数据"""
    __tablename__ = "submission_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ip = Column(String(64), index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)


class ReportMachineInfo(Base):
    """测速者补充的机器归属信息，独立成表，避免改动历史报告数据"""
    __tablename__ = "report_machine_info"

    report_id = Column(String(16), ForeignKey("reports.id", ondelete="CASCADE"), primary_key=True)
    provider = Column(String(64), nullable=True)
    region = Column(String(64), nullable=True)
    plan = Column(String(128), nullable=True)
    note = Column(String(200), nullable=True)
    claim_deadline = Column(DateTime, nullable=True)
    prompted_at = Column(DateTime, nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    edit_count = Column(Integer, nullable=False, default=0)


class SiteStatV2(Base):
    """v2 按地址族保存的站点 P50 基准。"""
    __tablename__ = "site_stats_v2"
    __table_args__ = (UniqueConstraint("name", "ip_family", name="uq_site_stats_v2_name_family"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False, index=True)
    ip_family = Column(String(8), nullable=False, index=True)
    p50_reference = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)
