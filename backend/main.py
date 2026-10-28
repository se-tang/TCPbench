import os
import secrets
import datetime
import math
import ipaddress
import io
import urllib.parse
from functools import lru_cache

from dotenv import load_dotenv
load_dotenv(os.getenv("TCPBENCH_ENV_FILE") or None)

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi import Query
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import func
from PIL import Image, ImageDraw, ImageFont

from database import engine, get_db, Base
from models import Report, ReportBatch, ReportMachineInfo, SiteStatV2, SubmissionLog
from schemas import MachineClaimIn, ReportIn, ReportOut

# 首次启动自动建表（表已存在时不会重复创建，很安全）
Base.metadata.create_all(bind=engine)

app = FastAPI(title="TCP Bench")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

RUN_SH_PATH = os.path.join(BASE_DIR, "scripts", "run.sh")

# ── 配置（部署时通过 .env / systemd Environment= 覆盖）──────────
SITE_URL = os.getenv("SITE_URL", "http://localhost:8000")
SITE_HOST = urllib.parse.urlparse(SITE_URL).netloc or "localhost"
REPORT_PORT = int(os.getenv("REPORT_PORT", "443"))     # 仅用于报告页展示，需和 run.sh 里的 PORT 保持一致
REPORT_ROUNDS = int(os.getenv("REPORT_ROUNDS", "60"))  # 仅用于报告页展示，需和 run.sh 里的 ROUNDS 保持一致
BLOG_URL = os.getenv("BLOG_URL", "").rstrip("/")
BLOG_NAME = os.getenv("BLOG_NAME", "")
MAX_BODY_BYTES = 2 * 1024 * 1024      # 单次上报最大 2MB
LEADERBOARD_PAGE_SIZE = int(os.getenv("LEADERBOARD_PAGE_SIZE", "20"))  # 排行榜每页展示条数
CN_TZ_OFFSET = datetime.timedelta(hours=8)   # 中国不实行夏令时，固定 UTC+8 换算就够用，不用引入 zoneinfo


def to_cn_str(dt: datetime.datetime) -> str:
    """数据库里存的是 UTC，这里统一换算成北京时间用于展示"""
    return (dt + CN_TZ_OFFSET).strftime("%Y-%m-%d %H:%M")


def build_page_list(current: int, total: int):
    """生成分页导航要显示的页码列表，页数多的时候用 None 表示省略号"""
    if total <= 7:
        return list(range(1, total + 1))
    pages = {1, total, current}
    for d in (-1, 0, 1):
        p = current + d
        if 1 <= p <= total:
            pages.add(p)
    pages = sorted(pages)
    result, prev = [], None
    for p in pages:
        if prev is not None and p - prev > 1:
            result.append(None)
        result.append(p)
        prev = p
    return result
RATE_LIMIT_COUNT = int(os.getenv("RATE_LIMIT_COUNT", "5"))       # 每 IP 每小时最多提交次数
RATE_LIMIT_WINDOW_MIN = int(os.getenv("RATE_LIMIT_WINDOW_MIN", "60"))
MACHINE_CLAIM_WINDOW_MIN = int(os.getenv("MACHINE_CLAIM_WINDOW_MIN", "30"))
MACHINE_EDIT_LIMIT = 3

templates.env.globals["umami_script_url"] = os.getenv("UMAMI_SCRIPT_URL", "").strip()
templates.env.globals["umami_website_id"] = os.getenv("UMAMI_WEBSITE_ID", "").strip()
templates.env.globals["blog_url"] = BLOG_URL
templates.env.globals["blog_name"] = BLOG_NAME

OG_IMAGE_WIDTH = 1200
OG_IMAGE_HEIGHT = 630
OG_FONT_REGULAR = os.getenv(
    "OG_FONT_REGULAR",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)
OG_FONT_BOLD = os.getenv(
    "OG_FONT_BOLD",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
)
GRADE_COLORS = {
    "S": "#2fa879",
    "A": "#3d7e60",
    "B": "#627c3a",
    "C": "#a8642d",
    "D": "#a8642d",
}
GRADE_LABELS = {
    "S": "极速冲浪节点",
    "A": "优秀体验",
    "B": "日常可用",
    "C": "将就使用",
    "D": "延迟偏高",
}


def get_client_ip(request: Request) -> str:
    """OpenResty 反代后，真实 IP 在 X-Forwarded-For / X-Real-IP 里"""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real
    return request.client.host if request.client else "unknown"


def mask_ip(ip: str) -> str:
    """保留 IPv4 前两段或 IPv6 前两组，公开页面不暴露后续地址。"""
    try:
        address = ipaddress.ip_address(ip.strip())
    except ValueError:
        return "unknown"
    if address.version == 4:
        parts = address.exploded.split(".")
        return f"{parts[0]}.{parts[1]}.*.*"
    parts = address.exploded.split(":")
    return f"{parts[0]}:{parts[1]}:*"


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    """挡掉过大的请求体，防止有人硬塞垃圾数据撑爆数据库"""
    if request.method == "POST":
        cl = request.headers.get("content-length")
        if cl and int(cl) > MAX_BODY_BYTES:
            return PlainTextResponse("请求体过大", status_code=413)
    return await call_next(request)


def check_rate_limit(db: Session, ip: str):
    since = datetime.datetime.utcnow() - datetime.timedelta(minutes=RATE_LIMIT_WINDOW_MIN)
    count = (
        db.query(func.count(SubmissionLog.id))
        .filter(SubmissionLog.ip == ip, SubmissionLog.created_at >= since)
        .scalar()
    )
    if count >= RATE_LIMIT_COUNT:
        raise HTTPException(
            status_code=429,
            detail=f"提交太频繁了，每小时最多 {RATE_LIMIT_COUNT} 次，请稍后再试",
        )


def machine_map(db: Session, report_ids):
    """批量读取报告对应的机器归属信息，避免排行榜逐行查询。"""
    if not report_ids:
        return {}
    rows = (
        db.query(ReportMachineInfo)
        .filter(ReportMachineInfo.report_id.in_(list(report_ids)))
        .all()
    )
    return {row.report_id: row for row in rows}


def batch_map(db: Session, batch_ids):
    if not batch_ids:
        return {}
    rows = (
        db.query(ReportBatch)
        .filter(ReportBatch.id.in_(list(batch_ids)))
        .all()
    )
    return {row.id: row for row in rows}


def report_machine_map(db: Session, reports):
    legacy_ids = [r.id for r in reports if not r.batch_id]
    batch_ids = [r.batch_id for r in reports if r.batch_id]
    legacy = machine_map(db, legacy_ids)
    batches = batch_map(db, batch_ids)
    return {
        report.id: batches.get(report.batch_id) if report.batch_id else legacy.get(report.id)
        for report in reports
    }


def generate_report_id(db: Session):
    value = secrets.token_urlsafe(8).replace("_", "").replace("-", "")[:8]
    while db.query(Report).filter(Report.id == value).first():
        value = secrets.token_urlsafe(8).replace("_", "").replace("-", "")[:8]
    return value


def generate_batch_id(db: Session):
    value = secrets.token_urlsafe(8).replace("_", "").replace("-", "")[:8]
    while db.query(ReportBatch).filter(ReportBatch.id == value).first():
        value = secrets.token_urlsafe(8).replace("_", "").replace("-", "")[:8]
    return value


def latency_tone(avg):
    if avg is None:
        return "slow"
    if avg < 20:
        return "excellent"
    if avg < 50:
        return "good"
    if avg < 80:
        return "fair"
    return "slow"


# ── 路由 ─────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    latest = (
        db.query(Report)
        .filter(
            Report.raw.isnot(None),
            Report.measurement_version == 2,
            Report.ip_family == "v4",
        )
        .order_by(Report.submitted_at.desc())
        .first()
    )
    if latest is None:
        latest = (
            db.query(Report)
            .filter(Report.raw.isnot(None))
            .order_by(Report.submitted_at.desc())
            .first()
        )
    coverage_cells = []
    if latest and latest.raw:
        for item in latest.raw:
            coverage_cells.append(
                {
                    "name": item.get("name", ""),
                    "avg": item.get("avg"),
                    "tone": latency_tone(item.get("avg")),
                }
            )

    top_query = (
        db.query(Report)
        .filter(
            Report.score_pct.isnot(None),
            Report.measurement_version == 2,
            Report.ip_family == "v4",
        )
        .order_by(Report.score_pct.desc(), Report.avg_all.asc().nullslast())
        .limit(5)
        .all()
    )
    if not top_query:
        top_query = (
            db.query(Report)
            .filter(Report.score_pct.isnot(None))
            .order_by(Report.score_pct.desc(), Report.avg_all.asc().nullslast())
            .limit(5)
            .all()
        )
    top_machine = report_machine_map(db, top_query)
    top_reports = []
    for row in top_query:
        info = top_machine.get(row.id)
        top_reports.append(
            {
                "id": row.id,
                "hostname": row.hostname,
                "provider": info.provider if info else None,
                "region": info.region if info else None,
                "plan": info.plan if info else None,
                "score": row.score_pct,
                "avg": row.avg_all,
            }
        )

    latest_avg = latest.avg_all if latest else None
    latest_reachable = latest.reachable if latest else 0
    latest_total = latest.total if latest else 0
    latest_score = latest.score_pct if latest else None
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "site_url": SITE_URL,
            "coverage_cells": coverage_cells,
            "top_reports": top_reports,
            "total_count": db.query(func.count(Report.id)).scalar() or 0,
            "latest_report_url": f"/r/{latest.id}" if latest else "/leaderboard",
            "latest_avg": latest_avg,
            "latest_reachable": latest_reachable,
            "latest_total": latest_total,
            "latest_score": latest_score,
        },
    )


@app.get("/run.sh", response_class=PlainTextResponse)
def run_script():
    """curl -sL <你的域名>/run.sh | bash 就是拉这个。
    仓库里 run.sh 用的是占位符 __BACKEND_URL__，这里用 .env 里的 SITE_URL 动态替换，
    这样代码传到 GitHub 上不会写死你的真实域名，部署到哪台服务器就自动指向哪个域名。"""
    with open(RUN_SH_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    return content.replace("__BACKEND_URL__", SITE_URL)


def _calc_scores(sorted_results, stats_map):
    """根据历史平均值基准计算每站分数和综合评分（供提交快照与查看共用）"""
    total_score = 0.0
    max_score = len(sorted_results) * 5
    scored = []

    for r in sorted_results:
        name = r["name"]
        lat = r["avg"]
        avg_ref = stats_map.get(name)

        if lat is None:
            site_score = 0.0
        elif avg_ref is None or avg_ref <= 0:
            site_score = 3.0  # 无历史数据，默认基准分
        else:
            ratio = lat / avg_ref
            if ratio <= 0.75:   site_score = 5.0
            elif ratio <= 0.9:  site_score = 4.0
            elif ratio <= 1.1:  site_score = 3.0
            elif ratio <= 1.4:  site_score = 2.0
            elif ratio <= 2.0:  site_score = 1.0
            else:               site_score = 0.0

        total_score += site_score
        scored.append(site_score)

    pct = round(total_score / max_score * 100, 1) if max_score > 0 else 0
    if pct >= 80:     grade, gradel = "S", "🚀 极速冲浪节点"
    elif pct >= 65:   grade, gradel = "A", "👍 优秀体验"
    elif pct >= 50:   grade, gradel = "B", "👌 日常可用"
    elif pct >= 35:   grade, gradel = "C", "🤔 将就使用"
    else:             grade, gradel = "D", "🐌 延迟偏高"

    return scored, total_score, pct, grade, gradel


def _absolute_latency_score(p50):
    if p50 <= 5:
        return 5.0
    if p50 <= 10:
        return 4.5
    if p50 <= 20:
        return 4.0
    if p50 <= 40:
        return 3.2
    if p50 <= 80:
        return 2.2
    if p50 <= 150:
        return 1.2
    if p50 <= 300:
        return 0.5
    return 0.0


def _baseline_adjustment(ratio):
    if ratio <= 0.75:
        return 0.5
    if ratio <= 0.90:
        return 0.25
    if ratio <= 1.10:
        return 0.0
    if ratio <= 1.40:
        return -0.15
    if ratio <= 2.00:
        return -0.5
    return -1.0


def _calc_scores_v2(sorted_results, stats_map):
    """v2: absolute P50 is primary; monthly history only adjusts the score."""
    supported = [r for r in sorted_results if r.get("supported", True)]
    scored = []
    total_score = 0.0

    for r in sorted_results:
        if not r.get("supported", True):
            scored.append(None)
            continue

        latency = r.get("p50")
        ref = stats_map.get(r["name"])
        if latency is None:
            site_score = 0.0
        else:
            site_score = _absolute_latency_score(float(latency))
            if ref and ref > 0:
                site_score += _baseline_adjustment(float(latency) / float(ref))
            site_score = max(0.0, min(5.0, site_score))

        loss_ratio = max(0.0, min(1.0, float(r.get("loss_pct") or 0) / 100.0))
        site_score *= 1.0 - loss_ratio
        total_score += site_score
        scored.append(round(site_score, 2))

    max_score = len(supported) * 5
    pct = round(total_score / max_score * 100, 1) if max_score > 0 else 0
    if pct >= 80:
        grade, gradel = "S", "🚀 极速冲浪节点"
    elif pct >= 65:
        grade, gradel = "A", "👍 优秀体验"
    elif pct >= 50:
        grade, gradel = "B", "👌 日常可用"
    elif pct >= 35:
        grade, gradel = "C", "🤔 将就使用"
    else:
        grade, gradel = "D", "🐌 延迟偏高"
    return scored, total_score, pct, grade, gradel


def _stats_map(db):
    from sqlalchemy import text as sa_text
    rows = db.execute(sa_text("SELECT name, avg_lat FROM site_stats")).fetchall()
    return {r[0]: r[1] for r in rows}


def _stats_map_v2(db, ip_family):
    rows = (
        db.query(SiteStatV2)
        .filter(SiteStatV2.ip_family == ip_family)
        .all()
    )
    if rows:
        return {row.name: row.p50_reference for row in rows}

    # No v2 baseline yet: use the neutral 3/5 per-site default, never legacy data.
    return {}


def _validate_v2_results(payload: ReportIn):
    """校验客户端采样与汇总值，挡住格式正确但内容伪造的上传。"""
    for item in payload.results:
        if not item.supported:
            if item.total != 0 or item.success != 0:
                raise HTTPException(status_code=422, detail=f"{item.name}: 不支持地址族的数据无效")
            continue
        if item.sampling_policy == "full60" and (
            item.attempted != 60 or item.precheck_status is None
        ):
            raise HTTPException(status_code=422, detail=f"{item.name}: 完整采样必须实际执行 60 次并记录预检状态")
        if not item.target_ip:
            raise HTTPException(status_code=422, detail=f"{item.name}: 缺少实际测试目标 IP")
        try:
            target = ipaddress.ip_address(item.target_ip)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"{item.name}: 目标 IP 格式无效")
        if target.version != (4 if payload.ip_family == "v4" else 6):
            raise HTTPException(status_code=422, detail=f"{item.name}: 目标 IP 与测试地址族不一致")
        if len(item.samples) != 60:
            raise HTTPException(status_code=422, detail=f"{item.name}: 必须提交 60 个正式样本")
        valid = sorted(float(v) for v in item.samples if v is not None)
        if len(valid) != item.success:
            raise HTTPException(status_code=422, detail=f"{item.name}: 成功次数与样本数量不一致")
        expected_loss = round((60 - item.success) / 60 * 100, 2)
        if abs(float(item.loss_pct) - expected_loss) > 0.011:
            raise HTTPException(status_code=422, detail=f"{item.name}: 丢包率与样本数量不一致")
        if not valid:
            if item.p50 is not None:
                raise HTTPException(status_code=422, detail=f"{item.name}: 无有效样本时不能提交 P50")
            continue
        mid = len(valid) // 2
        expected_p50 = valid[mid] if len(valid) % 2 else (valid[mid - 1] + valid[mid]) / 2
        expected_avg = sum(valid) / len(valid)
        checks = (
            ("P50", item.p50, expected_p50),
            ("平均值", item.avg, expected_avg),
            ("最小值", item.min, valid[0]),
            ("最大值", item.max, valid[-1]),
        )
        for label, supplied, expected in checks:
            if supplied is None or abs(float(supplied) - expected) > 0.02:
                raise HTTPException(status_code=422, detail=f"{item.name}: {label}与原始样本不一致")


@app.post("/api/report", response_model=ReportOut)
def create_report(payload: ReportIn, request: Request, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    check_rate_limit(db, ip)
    submitted_at = datetime.datetime.utcnow()

    # 无论后面成不成功，先记一条提交日志用于限流统计
    db.add(SubmissionLog(ip=ip, created_at=submitted_at))

    is_v2 = payload.measurement_version == 2
    if is_v2 and payload.ip_family not in ("v4", "v6"):
        raise HTTPException(status_code=422, detail="v2 测试必须指定 v4 或 v6")

    raw_results = [r.dict() for r in payload.results]
    if is_v2:
        _validate_v2_results(payload)
        supported_results = [r for r in raw_results if r.get("supported", True)]
        ok_results = [r for r in supported_results if r.get("p50") is not None]
        if not supported_results:
            raise HTTPException(status_code=400, detail="当前地址族没有可测试的站点")
        if not ok_results or all((r.get("p50") or 0) == 0 for r in ok_results):
            raise HTTPException(status_code=400, detail="测试失败：所有支持站点均未取得有效延迟")
        avg_all = round(sum(r["p50"] for r in ok_results) / len(ok_results), 2)
        sorted_for_score = sorted(
            raw_results,
            key=lambda r: (
                not r.get("supported", True),
                r.get("p50") is None,
                r.get("p50") if r.get("p50") is not None else 0,
            ),
        )
        scored, _, score_pct, grade, gradel = _calc_scores_v2(
            sorted_for_score,
            _stats_map_v2(db, payload.ip_family),
        )
        reachable_count = len(ok_results)
        report_total = len(supported_results)
    else:
        ok_results = [r for r in raw_results if r.get("avg") is not None]
        if not ok_results or all(r.get("avg") == 0 for r in ok_results):
            raise HTTPException(status_code=400, detail="测试失败：所有站点延迟为 0ms，请检查网络后重试")
        avg_all = round(sum(r["avg"] for r in ok_results) / len(ok_results), 2)
        sorted_for_score = sorted(
            raw_results,
            key=lambda r: (r.get("avg") is None, r.get("avg") if r.get("avg") is not None else 0),
        )
        scored, _, score_pct, grade, gradel = _calc_scores(sorted_for_score, _stats_map(db))
        reachable_count = len(ok_results)
        report_total = len(raw_results)

    batch = None
    owner_token = None
    batch_id = None

    if is_v2:
        if payload.batch_id:
            batch = (
                db.query(ReportBatch)
                .filter(ReportBatch.id == payload.batch_id)
                .first()
            )
            if not batch:
                raise HTTPException(status_code=404, detail="测试批次不存在")
            if not payload.owner_token or payload.owner_token != batch.owner_token:
                raise HTTPException(status_code=403, detail="批次 token 无效")
            exists = (
                db.query(Report)
                .filter(Report.batch_id == batch.id, Report.ip_family == payload.ip_family)
                .first()
            )
            if exists:
                raise HTTPException(status_code=409, detail="该批次已存在相同地址族报告")
        else:
            batch_id = generate_batch_id(db)
            owner_token = secrets.token_urlsafe(16)
            batch = ReportBatch(
                id=batch_id,
                owner_token=owner_token,
                hostname=payload.hostname[:128],
                ip_masked=mask_ip(payload.ip),
                submitted_at=submitted_at,
                measurement_version=2,
                claim_deadline=submitted_at + datetime.timedelta(minutes=MACHINE_CLAIM_WINDOW_MIN),
            )
            db.add(batch)
            db.flush()

        batch_id = batch.id
        owner_token = batch.owner_token

    report_id = generate_report_id(db)
    if not owner_token:
        owner_token = secrets.token_urlsafe(16)

    report = Report(
        id=report_id,
        owner_token=owner_token,
        batch_id=batch_id,
        measurement_version=2 if is_v2 else 1,
        ip_family=payload.ip_family if is_v2 else "mixed",
        dns_server=payload.dns_server[:255] if payload.dns_server else None,
        hostname=payload.hostname[:128],
        ip_masked=mask_ip(payload.ip),
        submitted_at=submitted_at,
        avg_all=avg_all,
        reachable=reachable_count,
        total=report_total,
        raw=raw_results,
        score_snapshot=scored,
        score_pct=score_pct,
        grade=grade,
        grade_label=gradel,
    )
    db.add(report)
    db.flush()
    if not is_v2:
        db.add(
            ReportMachineInfo(
                report_id=report_id,
                claim_deadline=submitted_at + datetime.timedelta(minutes=MACHINE_CLAIM_WINDOW_MIN),
            )
        )
    db.commit()

    # 统计今日（北京时间自然日）和累计的提交次数，供 run.sh 在报告链接上方展示
    now_beijing = datetime.datetime.utcnow() + CN_TZ_OFFSET
    today_start_beijing = now_beijing.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start_beijing - CN_TZ_OFFSET
    today_count = db.query(func.count(Report.id)).filter(Report.submitted_at >= today_start_utc).scalar()
    total_count = db.query(func.count(Report.id)).scalar()

    return ReportOut(
        id=report_id,
        url=f"{SITE_URL}/r/{report_id}?token={owner_token}",
        batch_id=batch_id,
        owner_token=owner_token,
        ip_family=payload.ip_family if is_v2 else "mixed",
        measurement_version=2 if is_v2 else 1,
        today_count=today_count,
        total_count=total_count,
    )


def spark_path(samples, w=130, h=32):
    """还原初版脚本里的 sparkline 绘制逻辑：SVG path 的 M/L 命令，遇到 None 就断开"""
    valid = [v for v in samples if v is not None]
    if len(valid) < 2:
        return ""
    mn, mx = min(valid), max(valid)
    rng = (mx - mn) or 1
    step = w / (len(samples) - 1) if len(samples) > 1 else 0
    d, first = [], True
    for i, v in enumerate(samples):
        if v is None:
            first = True
            continue
        x = round(i * step, 1)
        y = round(h - 2 - ((v - mn) / rng) * (h - 4), 1)
        d.append(f"{'M' if first else 'L'}{x},{y}")
        first = False
    return "".join(d)


def lat_cls(avg):
    if avg is None:
        return "gray"
    if avg < 50:
        return "green"
    if avg < 150:
        return "orange"
    return "red"


def _load_og_font(size, bold=False):
    path = OG_FONT_BOLD if bold else OG_FONT_REGULAR
    try:
        return ImageFont.truetype(path, size=size, index=0)
    except OSError:
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
        except OSError:
            return ImageFont.load_default(size=size)


@lru_cache(maxsize=512)
def _render_score_card(cache_key, grade, grade_label, latency_text, loss_text, score_text, family):
    del cache_key
    color = GRADE_COLORS.get(grade, "#2fa879")
    image = Image.new("RGBA", (OG_IMAGE_WIDTH, OG_IMAGE_HEIGHT), color)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    white = (255, 255, 255, 255)
    white_soft = (255, 255, 255, 218)
    white_muted = (255, 255, 255, 166)

    draw.text(
        (72, 48),
        "TCPBench",
        font=_load_og_font(42, bold=True),
        fill=white,
    )
    draw.text(
        (1128, 64),
        family.upper(),
        font=_load_og_font(24, bold=True),
        fill=white_soft,
        anchor="ra",
    )

    draw.text(
        (64, 132),
        grade,
        font=_load_og_font(194, bold=True),
        fill=white,
        anchor="la",
    )
    draw.rounded_rectangle(
        (315, 187, 319, 360),
        radius=2,
        fill=(255, 255, 255, 105),
    )
    draw.text(
        (354, 185),
        grade_label,
        font=_load_og_font(58, bold=True),
        fill=white,
    )
    draw.text(
        (356, 265),
        "Network performance score",
        font=_load_og_font(28),
        fill=white_muted,
    )

    metrics = (
        ("P50 LATENCY", latency_text),
        ("PACKET LOSS", loss_text),
        ("SCORE", score_text),
    )
    x_positions = (72, 454, 836)
    for x, (label, value) in zip(x_positions, metrics):
        draw.text(
            (x, 424),
            label,
            font=_load_og_font(24, bold=True),
            fill=white_muted,
        )
        draw.text(
            (x, 462),
            value,
            font=_load_og_font(62, bold=True),
            fill=white,
        )
        if label == "SCORE":
            draw.text(
                (x + 190, 493),
                "/ 100",
                font=_load_og_font(23),
                fill=white_muted,
            )

    draw.line((72, 562, 1128, 562), fill=(255, 255, 255, 90), width=2)
    draw.text(
        (72, 580),
        SITE_HOST,
        font=_load_og_font(24, bold=True),
        fill=white_soft,
    )
    draw.text(
        (1128, 580),
        "TCP SYN → ACK · P50",
        font=_load_og_font(22),
        fill=white_muted,
        anchor="ra",
    )

    image = Image.alpha_composite(image, overlay).convert("RGB")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


@app.get("/og/{report_id}.png")
def score_card_image(report_id: str, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report or report.score_pct is None:
        raise HTTPException(status_code=404, detail="报告不存在")

    rows = [
        item
        for item in (report.raw or [])
        if item.get("supported", True)
    ]
    losses = [float(item.get("loss_pct") or 0) for item in rows]
    avg_loss = sum(losses) / len(losses) if losses else 0
    grade = report.grade or "D"
    label = GRADE_LABELS.get(grade, report.grade_label or "")
    latency_text = f"{report.avg_all:.0f}ms" if report.avg_all is not None else "—"
    loss_text = f"{avg_loss:.1f}%"
    score_text = f"{report.score_pct:.1f}"
    family = report.ip_family or "mixed"
    cache_key = (
        report.id,
        grade,
        latency_text,
        loss_text,
        score_text,
        family,
    )
    image_bytes = _render_score_card(
        cache_key,
        grade,
        label,
        latency_text,
        loss_text,
        score_text,
        family,
    )
    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Content-Disposition": "inline",
        },
    )


@app.get("/leaderboard", response_class=HTMLResponse)
def leaderboard(
    request: Request,
    family: str = None,
    sort: str = "score",
    page: int = 1,
    db: Session = Depends(get_db),
):
    if sort not in ("score", "time"):
        sort = "score"
    if page < 1:
        page = 1

    base_query = db.query(Report)
    if family not in ("v4", "v6", "legacy"):
        has_v2_v4 = (
            db.query(Report.id)
            .filter(Report.measurement_version == 2, Report.ip_family == "v4")
            .first()
        )
        family = "v4" if has_v2_v4 else "legacy"

    if family == "legacy":
        base_query = base_query.filter(Report.measurement_version == 1)
    else:
        base_query = base_query.filter(
            Report.measurement_version == 2,
            Report.ip_family == family,
        )

    total_count = base_query.count()
    total_pages = max(1, math.ceil(total_count / LEADERBOARD_PAGE_SIZE))
    if page > total_pages:
        page = total_pages

    if sort == "time":
        ordered_query = base_query.order_by(Report.submitted_at.desc())
    else:
        ordered_query = base_query.order_by(
            Report.score_pct.desc().nullslast(),
            Report.avg_all.asc().nullslast(),
            Report.submitted_at.desc(),
        )

    rows = (
        ordered_query.offset((page - 1) * LEADERBOARD_PAGE_SIZE)
        .limit(LEADERBOARD_PAGE_SIZE)
        .all()
    )
    rank_offset = (page - 1) * LEADERBOARD_PAGE_SIZE
    machine = report_machine_map(db, rows)
    now = datetime.datetime.utcnow()

    items = [
        {
            "rank": rank_offset + i + 1,
            "id": r.id,
            "hostname": r.hostname,
            "ip_masked": r.ip_masked,
            "avg_all": r.avg_all,
            "reachable": r.reachable,
            "total": r.total,
            "submitted_at_str": to_cn_str(r.submitted_at),
            "cls": lat_cls(r.avg_all),
            "score": r.score_pct,
            "grade": r.grade,
            "family": family,
            "provider": machine.get(r.id).provider if machine.get(r.id) else None,
            "region": machine.get(r.id).region if machine.get(r.id) else None,
            "plan": machine.get(r.id).plan if machine.get(r.id) else None,
            "claim_open": bool(
                not (
                    machine.get(r.id)
                    and machine.get(r.id).provider
                    and machine.get(r.id).region
                    and machine.get(r.id).plan
                )
                and now
                < (
                    machine.get(r.id).claim_deadline
                    if machine.get(r.id) and machine.get(r.id).claim_deadline
                    else r.submitted_at
                    + datetime.timedelta(minutes=MACHINE_CLAIM_WINDOW_MIN)
                )
            ),
        }
        for i, r in enumerate(rows)
    ]

    if family == "legacy":
        identified_count = (
            db.query(func.count(ReportMachineInfo.report_id))
            .filter(
                ReportMachineInfo.provider.isnot(None),
                ReportMachineInfo.region.isnot(None),
                ReportMachineInfo.plan.isnot(None),
            )
            .scalar()
            or 0
        )
    else:
        identified_count = (
            db.query(func.count(func.distinct(ReportBatch.id)))
            .join(Report, Report.batch_id == ReportBatch.id)
            .filter(
                Report.measurement_version == 2,
                Report.ip_family == family,
                ReportBatch.provider.isnot(None),
                ReportBatch.region.isnot(None),
                ReportBatch.plan.isnot(None),
            )
            .scalar()
            or 0
        )
    average_score = db.query(func.avg(Report.score_pct)).filter(
        Report.score_pct.isnot(None),
        Report.measurement_version == (1 if family == "legacy" else 2),
        *([] if family == "legacy" else [Report.ip_family == family]),
    ).scalar()
    family_counts = {
        "v4": db.query(func.count(Report.id)).filter(
            Report.measurement_version == 2,
            Report.ip_family == "v4",
        ).scalar() or 0,
        "v6": db.query(func.count(Report.id)).filter(
            Report.measurement_version == 2,
            Report.ip_family == "v6",
        ).scalar() or 0,
        "legacy": db.query(func.count(Report.id)).filter(
            Report.measurement_version == 1,
        ).scalar() or 0,
    }

    return templates.TemplateResponse(
        request,
        "leaderboard.html",
        {
            "items": items,
            "family": family,
            "sort": sort,
            "site_url": SITE_URL,
            "total_count": total_count,
            "page": page,
            "total_pages": total_pages,
            "identified_count": identified_count,
            "average_score": round(average_score, 1) if average_score is not None else None,
            "family_counts": family_counts,
            "page_list": build_page_list(page, total_pages),
        },
    )


@app.get("/r/{report_id}", response_class=HTMLResponse)
def view_report(report_id: str, request: Request, token: str = None, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在或已过期")

    is_v2 = report.measurement_version == 2
    batch = (
        db.query(ReportBatch)
        .filter(ReportBatch.id == report.batch_id)
        .one_or_none()
        if report.batch_id
        else None
    )
    if is_v2 and not batch:
        raise HTTPException(status_code=500, detail="测试批次数据缺失")

    machine = batch if is_v2 else (
        db.query(ReportMachineInfo)
        .filter(ReportMachineInfo.report_id == report.id)
        .one_or_none()
    )
    owner_token = batch.owner_token if batch else report.owner_token
    is_owner = bool(token) and bool(owner_token) and token == owner_token
    now = datetime.datetime.utcnow()
    claim_open = False
    claim_remaining_seconds = 0

    if is_owner:
        changed = False
        derived_deadline = (batch.submitted_at if batch else report.submitted_at) + datetime.timedelta(
            minutes=MACHINE_CLAIM_WINDOW_MIN
        )
        if not is_v2 and machine is None and now < derived_deadline:
            machine = ReportMachineInfo(
                report_id=report.id,
                claim_deadline=derived_deadline,
            )
            db.add(machine)
            changed = True
        elif not is_v2 and machine is not None and not (machine.provider and machine.region and machine.plan):
            if machine.claim_deadline is None:
                machine.claim_deadline = derived_deadline
                changed = True

        if changed:
            db.commit()
            db.refresh(machine)

        if machine is not None and machine.claim_deadline:
            claim_remaining_seconds = max(
                0,
                int((machine.claim_deadline - now).total_seconds()),
            )
            claim_open = claim_remaining_seconds > 0

    def result_latency(item):
        if is_v2:
            return item.get("p50") if item.get("supported", True) else None
        return item.get("avg")

    sorted_results = sorted(
        report.raw,
        key=lambda r: (
            not r.get("supported", True),
            result_latency(r) is None,
            result_latency(r) if result_latency(r) is not None else 0,
        ),
    )
    supported_results = [r for r in sorted_results if r.get("supported", True)] if is_v2 else sorted_results
    ok = [r for r in supported_results if result_latency(r) is not None]
    overall_avg = (
        round(sum(result_latency(r) for r in ok) / len(ok), 2)
        if ok
        else None
    )
    unsupported_sites = [
        r["name"] for r in sorted_results if is_v2 and not r.get("supported", True)
    ]

    # 动态评分：优先使用提交时定格的快照（历史报告分数不随基准更新变化）
    if report.score_snapshot:
        scored = list(report.score_snapshot)
        pct = report.score_pct if report.score_pct is not None else 0
        grade = report.grade or "S"
        gradel = report.grade_label or ""
    else:
        # 旧报告没有快照：用当前基准补算一次
        if is_v2:
            scored, _, pct, grade, gradel = _calc_scores_v2(
                sorted_results,
                _stats_map_v2(db, report.ip_family),
            )
        else:
            scored, _, pct, grade, gradel = _calc_scores(sorted_results, _stats_map(db))

    rows = []
    for i, r in enumerate(sorted_results):
        loss = r.get("loss_pct") or 0
        latency = result_latency(r)
        supported = r.get("supported", True)
        rows.append({
            "rank": i + 1,
            "name": r["name"],
            "host": r.get("host"),
            "target_ip": r.get("target_ip"),
            "dns_ms": r.get("dns_ms"),
            "dns_str": f"{r['dns_ms']:.1f}ms" if r.get("dns_ms") is not None else "—",
            "supported": supported,
            "precheck_status": r.get("precheck_status"),
            "sampling_policy": r.get("sampling_policy"),
            "attempted": r.get("attempted"),
            "avg_str": f"{latency:.2f}ms" if latency is not None else ("不支持" if not supported else ("正式失败" if r.get("sampling_policy") == "full60" else "—")),
            "avg": latency,
            "p50": r.get("p50"),
            "p50_str": f"{r['p50']:.2f}ms" if r.get("p50") is not None else "—",
            "min_str": f"{r['min']:.3f}ms" if r.get("min") is not None else "—",
            "max_str": f"{r['max']:.3f}ms" if r.get("max") is not None else "—",
            "success": r.get("success", 0),
            "loss_str": f"{loss:.2f}%" if loss > 0 else "0%",
            "loss_pct": loss,
            "loss_cls": "high" if loss >= 50 else ("mid" if loss > 0 else ""),
            "cls": lat_cls(latency),
            "spark": spark_path(r.get("samples") or []),
            "score": scored[i],
        })

    family_links = {}
    if batch:
        sibling_reports = (
            db.query(Report)
            .filter(Report.batch_id == batch.id)
            .order_by(Report.submitted_at.asc())
            .all()
        )
        for sibling in sibling_reports:
            suffix = f"?token={token}" if is_owner and token else ""
            family_links[sibling.ip_family] = {
                "url": f"/r/{sibling.id}{suffix}",
                "label": "IPv4" if sibling.ip_family == "v4" else "IPv6",
                "active": sibling.id == report.id,
            }

    best_item = ok[0] if ok else None
    worst_item = ok[-1] if ok else None
    dns_servers = [
        value.strip()
        for value in (report.dns_server or "").split(",")
        if value.strip()
    ]
    dns_server_short = (
        dns_servers[0]
        if len(dns_servers) == 1
        else (f"{dns_servers[0]} +{len(dns_servers) - 1}" if dns_servers else "")
    )
    supported_rows = [row for row in rows if row["supported"]]
    avg_loss = (
        sum(row["loss_pct"] for row in supported_rows) / len(supported_rows)
        if supported_rows
        else 0
    )
    public_report_url = f"{SITE_URL.rstrip('/')}/r/{report.id}"
    og_image_url = (
        f"{SITE_URL.rstrip('/')}/og/{report.id}.png"
        f"?v={int((report.score_pct or 0) * 10)}-{int(avg_loss * 10)}"
    )
    og_title = (
        f"TCPBench · {grade} · {float(report.score_pct or 0):.1f}/100"
    )
    og_description = (
        f"P50 延迟 {overall_avg:.0f}ms · 丢包 {avg_loss:.1f}% · "
        f"{GRADE_LABELS.get(grade, gradel)}"
        if overall_avg is not None
        else f"综合评分 {float(report.score_pct or 0):.1f}/100"
    )

    return templates.TemplateResponse(
        request,
        "report.html",
        {
            "report": report,
            "rows": rows,
            "is_v2": is_v2,
            "ip_family": report.ip_family or "mixed",
            "family_links": family_links,
            "unsupported_sites": unsupported_sites,
            "unsupported_count": len(unsupported_sites),
            "supported_count": len(supported_results),
            "total_sites": len(sorted_results),
            "overall_avg": overall_avg,
            "overall_cls": lat_cls(overall_avg),
            "reachable": len(ok),
            "total": len(supported_results) if is_v2 else len(sorted_results),
            "best": best_item,
            "worst": worst_item,
            "best_name": best_item["name"] if best_item else "",
            "best_value": result_latency(best_item) if best_item else None,
            "worst_name": worst_item["name"] if worst_item else "",
            "worst_value": result_latency(worst_item) if worst_item else None,
            "port": REPORT_PORT,
            "rounds": REPORT_ROUNDS,
            "blog_url": BLOG_URL,
            "is_owner": is_owner,
            "machine": machine,
            "claim_open": claim_open,
            "claim_remaining_seconds": claim_remaining_seconds,
            "claim_window_minutes": MACHINE_CLAIM_WINDOW_MIN,
            "machine_edit_limit": MACHINE_EDIT_LIMIT,
            "machine_edits_remaining": max(
                0,
                MACHINE_EDIT_LIMIT - ((machine.edit_count or 0) if machine else 0),
            ),
            "machine_claimed_at_str": to_cn_str(machine.claimed_at)
            if machine and machine.claimed_at
            else "",
            "submitted_at_str": to_cn_str(report.submitted_at),
            "dns_server": report.dns_server or "",
            "dns_server_count": len(dns_servers),
            "dns_server_short": dns_server_short,
            "grade": grade,
            "grade_label": gradel,
            "score_pct": pct,
            "avg_loss": avg_loss,
            "public_report_url": public_report_url,
            "og_image_url": og_image_url,
            "og_title": og_title,
            "og_description": og_description,
        },
    )


@app.post("/api/reports/{report_id}/machine")
def claim_machine(
    report_id: str,
    payload: MachineClaimIn,
    db: Session = Depends(get_db),
):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    now = datetime.datetime.utcnow()

    if report.measurement_version == 2:
        info = (
            db.query(ReportBatch)
            .filter(ReportBatch.id == report.batch_id)
            .one_or_none()
        )
        if info is None:
            raise HTTPException(status_code=500, detail="测试批次数据缺失")
        expected_token = info.owner_token
        derived_deadline = info.submitted_at + datetime.timedelta(
            minutes=MACHINE_CLAIM_WINDOW_MIN
        )
    else:
        info = (
            db.query(ReportMachineInfo)
            .filter(ReportMachineInfo.report_id == report_id)
            .one_or_none()
        )
        expected_token = report.owner_token
        derived_deadline = report.submitted_at + datetime.timedelta(
            minutes=MACHINE_CLAIM_WINDOW_MIN
        )

    if not expected_token or payload.token != expected_token:
        raise HTTPException(status_code=403, detail="token 无效")

    deadline = info.claim_deadline if info and info.claim_deadline else derived_deadline
    if deadline is None or now >= deadline:
        raise HTTPException(status_code=410, detail="机器信息填写窗口已关闭")
    if info is None and report.measurement_version != 2:
        info = ReportMachineInfo(
            report_id=report_id,
            claim_deadline=deadline,
        )
        db.add(info)
        db.flush()

    was_complete = bool(info.provider and info.region and info.plan)
    if was_complete and (info.edit_count or 0) >= MACHINE_EDIT_LIMIT:
        raise HTTPException(status_code=409, detail=f"机器信息最多可编辑 {MACHINE_EDIT_LIMIT} 次")

    provider = payload.provider.strip()
    region = payload.region.strip()
    plan = payload.plan.strip()
    note = payload.note.strip()
    if len(provider) < 2 or len(region) < 2 or len(plan) < 2:
        raise HTTPException(status_code=422, detail="请完整填写商家、地区与产品套餐")

    info.provider = provider
    info.region = region
    info.plan = plan
    info.note = note
    info.claimed_at = now
    if was_complete:
        info.edit_count = (info.edit_count or 0) + 1
    db.commit()
    return {"ok": True}


@app.get("/api/machine-options")
def machine_options(db: Session = Depends(get_db)):
    legacy_provider_rows = (
        db.query(ReportMachineInfo.provider)
        .filter(ReportMachineInfo.provider.isnot(None), ReportMachineInfo.provider != "")
        .distinct()
        .all()
    )
    legacy_region_rows = (
        db.query(ReportMachineInfo.region)
        .filter(ReportMachineInfo.region.isnot(None), ReportMachineInfo.region != "")
        .distinct()
        .all()
    )
    batch_provider_rows = (
        db.query(ReportBatch.provider)
        .filter(ReportBatch.provider.isnot(None), ReportBatch.provider != "")
        .distinct()
        .all()
    )
    batch_region_rows = (
        db.query(ReportBatch.region)
        .filter(ReportBatch.region.isnot(None), ReportBatch.region != "")
        .distinct()
        .all()
    )
    providers = sorted(
        {
            row[0].strip()
            for row in legacy_provider_rows + batch_provider_rows
            if row[0] and row[0].strip()
        },
        key=str.casefold,
    )
    regions = sorted(
        {
            row[0].strip()
            for row in legacy_region_rows + batch_region_rows
            if row[0] and row[0].strip()
        },
        key=str.casefold,
    )
    return {"providers": providers, "regions": regions}



import threading
import urllib.request

_favicon_cache = {}
_cache_lock = threading.Lock()

FAVICON_DIRECT_URLS = {
    "copilot.microsoft.com": "https://copilot.microsoft.com/favicon.ico",
}


def _normalize_favicon(raw):
    with Image.open(io.BytesIO(raw)) as source:
        source.load()
        icon = source.convert("RGBA")
    icon.thumbnail((32, 32), Image.Resampling.LANCZOS)
    if icon.getchannel("A").getbbox() is None:
        raise ValueError("favicon is fully transparent")
    canvas = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    canvas.alpha_composite(
        icon,
        ((32 - icon.width) // 2, (32 - icon.height) // 2),
    )
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _fallback_favicon():
    icon = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle((2, 2, 30, 30), radius=7, fill=(112, 127, 119, 255))
    draw.ellipse((8, 8, 24, 24), outline=(255, 255, 255, 235), width=2)
    draw.line((12, 16, 20, 16), fill=(255, 255, 255, 235), width=2)
    draw.line((16, 12, 16, 20), fill=(255, 255, 255, 235), width=2)
    output = io.BytesIO()
    icon.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _download_favicon(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=6) as resp:
        return resp.read()

def _fetch_favicon(domain):
    urls = []
    direct_url = FAVICON_DIRECT_URLS.get(domain.lower().strip())
    if direct_url:
        urls.append(direct_url)
    encoded_domain = urllib.parse.quote(domain, safe="")
    urls.append(
        f"https://www.google.com/s2/favicons?domain={encoded_domain}&sz=32"
    )
    for url in urls:
        try:
            return _normalize_favicon(_download_favicon(url))
        except Exception:
            continue
    return _fallback_favicon()

@app.get("/favicon-proxy")
def favicon_proxy(domain: str = Query("google.com")):
    cache_key = domain.lower().strip()
    with _cache_lock:
        if cache_key in _favicon_cache:
            content = _favicon_cache[cache_key]
        else:
            try:
                content = _fetch_favicon(domain)
            except Exception:
                content = _fallback_favicon()
            _favicon_cache[cache_key] = content
    return Response(
        content=content,
        media_type="image/png",
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=604800, immutable"}
    )


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/site-stats")
def get_site_stats(db: Session = Depends(get_db)):
    from sqlalchemy import text
    rows = db.execute(text("SELECT name, avg_lat, min_lat, max_lat, sample_count FROM site_stats ORDER BY name")).fetchall()
    return {r[0]: {"avg": r[1], "min": r[2], "max": r[3], "samples": r[4]} for r in rows}
