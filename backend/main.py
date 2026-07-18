import os
import secrets
import datetime

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import engine, get_db, Base
from models import Report, SubmissionLog
from schemas import ReportIn, ReportOut

# 首次启动自动建表（表已存在时不会重复创建，很安全）
Base.metadata.create_all(bind=engine)

app = FastAPI(title="TCP Bench")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

RUN_SH_PATH = os.path.join(BASE_DIR, "scripts", "run.sh")

# ── 配置（部署时通过 .env / systemd Environment= 覆盖）──────────
SITE_URL = os.getenv("SITE_URL", "https://tcpbench.com")
REPORT_PORT = int(os.getenv("REPORT_PORT", "443"))     # 仅用于报告页展示，需和 run.sh 里的 PORT 保持一致
REPORT_ROUNDS = int(os.getenv("REPORT_ROUNDS", "60"))  # 仅用于报告页展示，需和 run.sh 里的 ROUNDS 保持一致
BLOG_URL = os.getenv("BLOG_URL", "")                    # 报告页左上角显示的博客署名链接，留空则不显示
MAX_BODY_BYTES = 2 * 1024 * 1024      # 单次上报最大 2MB
RATE_LIMIT_COUNT = int(os.getenv("RATE_LIMIT_COUNT", "5"))       # 每 IP 每小时最多提交次数
RATE_LIMIT_WINDOW_MIN = int(os.getenv("RATE_LIMIT_WINDOW_MIN", "60"))


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
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.*.*"
    return ip


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


# ── 路由 ─────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"site_url": SITE_URL})


@app.get("/run.sh", response_class=PlainTextResponse)
def run_script():
    """curl -sL <你的域名>/run.sh | bash 就是拉这个。
    仓库里 run.sh 用的是占位符 __BACKEND_URL__，这里用 .env 里的 SITE_URL 动态替换，
    这样代码传到 GitHub 上不会写死你的真实域名，部署到哪台服务器就自动指向哪个域名。"""
    with open(RUN_SH_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    return content.replace("__BACKEND_URL__", SITE_URL)


@app.post("/api/report", response_model=ReportOut)
def create_report(payload: ReportIn, request: Request, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    check_rate_limit(db, ip)

    # 无论后面成不成功，先记一条提交日志用于限流统计
    db.add(SubmissionLog(ip=ip, created_at=datetime.datetime.utcnow()))

    ok_results = [r for r in payload.results if r.avg is not None]
    avg_all = round(sum(r.avg for r in ok_results) / len(ok_results), 2) if ok_results else None

    report_id = secrets.token_urlsafe(8).replace("_", "").replace("-", "")[:8]
    while db.query(Report).filter(Report.id == report_id).first():
        report_id = secrets.token_urlsafe(8).replace("_", "").replace("-", "")[:8]

    report = Report(
        id=report_id,
        hostname=payload.hostname[:128],
        ip_masked=mask_ip(payload.ip),
        submitted_at=datetime.datetime.utcnow(),
        avg_all=avg_all,
        reachable=len(ok_results),
        total=len(payload.results),
        raw=[r.dict() for r in payload.results],
    )
    db.add(report)
    db.commit()

    return ReportOut(id=report_id, url=f"{SITE_URL}/r/{report_id}")


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


@app.get("/r/{report_id}", response_class=HTMLResponse)
def view_report(report_id: str, request: Request, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在或已过期")

    sorted_results = sorted(
        report.raw, key=lambda r: (r["avg"] is None, r["avg"] if r["avg"] is not None else 0)
    )
    ok = [r for r in sorted_results if r["avg"] is not None]
    overall_avg = round(sum(r["avg"] for r in ok) / len(ok), 2) if ok else None

    rows = []
    for i, r in enumerate(sorted_results):
        loss = r["loss_pct"]
        rows.append({
            "rank": i + 1,
            "name": r["name"],
            "avg_str": f"{r['avg']:.2f}ms" if r["avg"] is not None else "—",
            "min_str": f"{r['min']:.3f}ms" if r["min"] is not None else "—",
            "max_str": f"{r['max']:.3f}ms" if r["max"] is not None else "—",
            "success": r["success"],
            "loss_str": f"{loss:.2f}%" if loss > 0 else "0%",
            "loss_cls": "high" if loss >= 50 else ("mid" if loss > 0 else ""),
            "cls": lat_cls(r["avg"]),
            "spark": spark_path(r["samples"]),
        })

    return templates.TemplateResponse(
        request,
        "report.html",
        {
            "report": report,
            "rows": rows,
            "overall_avg": overall_avg,
            "overall_cls": lat_cls(overall_avg),
            "reachable": len(ok),
            "total": len(sorted_results),
            "best": ok[0] if ok else None,
            "worst": ok[-1] if ok else None,
            "port": REPORT_PORT,
            "rounds": REPORT_ROUNDS,
            "blog_url": BLOG_URL,
        },
    )


@app.get("/health")
def health():
    return {"ok": True}
