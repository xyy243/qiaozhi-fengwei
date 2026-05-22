import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from database import get_detection, get_latest_detections, get_rechecks, init_db, insert_detection, insert_recheck
from detector import analyze_expansion_joint, load_image, make_analysis_image
from report import admin_page as render_admin_page
from report import file_url
from report import report_page as render_report_page


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
FRONTEND_DIR = PROJECT_DIR / "frontend"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="桥智缝卫云端系统",
    description="桥梁伸缩缝智能巡检云端识别与报告系统",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_detect_id(prefix: str = "QZFW") -> str:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    short = uuid.uuid4().hex[:6]
    return f"{prefix}-{timestamp}-{short}"


@app.get("/", response_class=JSONResponse)
def root():
    return {
        "project": "桥智缝卫",
        "status": "云服务器运行正常",
        "message": "QZFW cloud server is running",
        "version": "2.1.0",
        "mobile": "/mobile",
        "admin": "/admin",
        "health": "/health",
        "api_detect": "/api/detect",
    }


@app.get("/health", response_class=JSONResponse)
def health():
    return {"project": "桥智缝卫", "status": "ONLINE", "time": now_text(), "message": "云端服务正常，FastAPI 已运行。"}


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/mobile", response_class=HTMLResponse)
def mobile():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/manifest.json")
def manifest():
    return FileResponse(FRONTEND_DIR / "manifest.json")


@app.get("/service-worker.js")
def service_worker():
    return FileResponse(FRONTEND_DIR / "service-worker.js", media_type="application/javascript")


@app.post("/api/detect")
async def api_detect(file: UploadFile = File(...)):
    file_bytes = await file.read()
    image = load_image(file_bytes)
    detect_id = make_detect_id("QZFW")
    raw_filename = f"{detect_id}_raw.png"
    analysis_filename = f"{detect_id}_analysis.jpg"
    image.save(UPLOAD_DIR / raw_filename)
    result = analyze_expansion_joint(image)
    make_analysis_image(image, result).save(UPLOAD_DIR / analysis_filename, quality=92)
    result.update(
        {
            "id": detect_id,
            "created_at": now_text(),
            "raw_filename": raw_filename,
            "analysis_filename": analysis_filename,
            "raw_url": file_url(raw_filename),
            "analysis_url": file_url(analysis_filename),
            "report_url": f"/report/{detect_id}",
        }
    )
    insert_detection(result)
    return JSONResponse(result)


@app.post("/upload")
async def upload_compat(file: UploadFile = File(...)):
    return await api_detect(file)


@app.post("/api/recheck")
async def api_recheck(detection_id: str = Form(...), file: UploadFile = File(...)):
    before = get_detection(detection_id)
    if before is None:
        raise HTTPException(status_code=404, detail="未找到原始检测记录。")

    image = load_image(await file.read())
    recheck_id = make_detect_id("RECHECK")
    after_filename = f"{recheck_id}_after.png"
    after_analysis_filename = f"{recheck_id}_after_analysis.jpg"
    image.save(UPLOAD_DIR / after_filename)
    after_result = analyze_expansion_joint(image)
    make_analysis_image(image, after_result).save(UPLOAD_DIR / after_analysis_filename, quality=92)

    before_area = float(before["area_ratio"])
    after_area = float(after_result["area_ratio"])
    improvement = 0.0 if before_area <= 0.001 else max(0.0, (before_area - after_area) / before_area * 100.0)
    if improvement >= 50:
        acceptance = "验收通过"
        suggestion = "修复后疑似病害面积明显下降，建议保存报告并定期复查。"
    elif improvement >= 20:
        acceptance = "建议复查"
        suggestion = "修复后有所改善，但改善幅度一般，建议再次观察或人工复核。"
    else:
        acceptance = "修复效果不明显"
        suggestion = "修复前后变化不明显，建议人工复核或重新执行局部处理。"

    result = {
        "id": recheck_id,
        "detection_id": detection_id,
        "created_at": now_text(),
        "after_filename": after_filename,
        "after_analysis_filename": after_analysis_filename,
        "after_url": file_url(after_filename),
        "after_analysis_url": file_url(after_analysis_filename),
        "before_area_ratio": round(before_area, 4),
        "after_area_ratio": round(after_area, 4),
        "improvement_rate": round(improvement, 2),
        "acceptance_result": acceptance,
        "suggestion": suggestion,
        "report_url": f"/report/{detection_id}",
    }
    insert_recheck(result)
    return JSONResponse(result)


@app.get("/report/{detect_id}", response_class=HTMLResponse)
def report_page(detect_id: str):
    row = get_detection(detect_id)
    if row is None:
        return HTMLResponse("<h2>未找到报告</h2><p>检测编号不存在。</p>", status_code=404)
    return HTMLResponse(render_report_page(row, get_rechecks(detect_id)))


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return HTMLResponse(render_admin_page(get_latest_detections(50)))
