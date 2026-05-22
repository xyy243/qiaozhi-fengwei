import os
import io
import json
import uuid
import html
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware


# =========================================================
# 桥智缝卫云端系统 V2.1
# 修改内容：
# 小车控制按钮布局改为：
# LED开 / 前进 / LED关
# 左转 / 停止 / 右转
# 拍照采集 / 后退 / 复位
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
DB_PATH = BASE_DIR / "qzfw_cloud.db"

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


# =========================================================
# 数据库
# =========================================================

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS detections (
            id TEXT PRIMARY KEY,
            created_at TEXT,
            raw_filename TEXT,
            analysis_filename TEXT,
            disease_type TEXT,
            disease_level TEXT,
            risk_level TEXT,
            confidence REAL,
            suspect_count INTEGER,
            area_ratio REAL,
            decision TEXT,
            repairable INTEGER,
            suggestion TEXT,
            roi_json TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS rechecks (
            id TEXT PRIMARY KEY,
            detection_id TEXT,
            created_at TEXT,
            after_filename TEXT,
            after_analysis_filename TEXT,
            before_area_ratio REAL,
            after_area_ratio REAL,
            improvement_rate REAL,
            acceptance_result TEXT,
            suggestion TEXT
        )
        """
    )

    conn.commit()
    conn.close()


init_db()


def insert_detection(result: Dict[str, Any]):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT OR REPLACE INTO detections (
            id, created_at, raw_filename, analysis_filename,
            disease_type, disease_level, risk_level, confidence,
            suspect_count, area_ratio, decision, repairable,
            suggestion, roi_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result["id"],
            result["created_at"],
            result["raw_filename"],
            result["analysis_filename"],
            result["disease_type"],
            result["disease_level"],
            result["risk_level"],
            float(result["confidence"]),
            int(result["suspect_count"]),
            float(result["area_ratio"]),
            result["decision"],
            1 if result["repairable"] else 0,
            result["suggestion"],
            json.dumps(result["roi"], ensure_ascii=False),
        ),
    )

    conn.commit()
    conn.close()


def insert_recheck(result: Dict[str, Any]):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT OR REPLACE INTO rechecks (
            id, detection_id, created_at, after_filename,
            after_analysis_filename, before_area_ratio,
            after_area_ratio, improvement_rate,
            acceptance_result, suggestion
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result["id"],
            result["detection_id"],
            result["created_at"],
            result["after_filename"],
            result["after_analysis_filename"],
            float(result["before_area_ratio"]),
            float(result["after_area_ratio"]),
            float(result["improvement_rate"]),
            result["acceptance_result"],
            result["suggestion"],
        ),
    )

    conn.commit()
    conn.close()


def get_detection(detect_id: str) -> Optional[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    row = cur.execute("SELECT * FROM detections WHERE id = ?", (detect_id,)).fetchone()
    conn.close()
    return row


def get_latest_detections(limit: int = 50):
    conn = get_conn()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT * FROM detections ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return rows


def get_rechecks(detect_id: str):
    conn = get_conn()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT * FROM rechecks WHERE detection_id = ? ORDER BY created_at DESC",
        (detect_id,),
    ).fetchall()
    conn.close()
    return rows


# =========================================================
# 工具函数
# =========================================================

def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_detect_id(prefix: str = "QZFW") -> str:
    t = datetime.now().strftime("%Y%m%d%H%M%S")
    short = uuid.uuid4().hex[:6]
    return f"{prefix}-{t}-{short}"


def safe_filename(name: str) -> str:
    return name.replace("/", "_").replace("\\", "_").replace("..", "_")


def file_url(filename: str) -> str:
    return f"/uploads/{safe_filename(filename)}"


def load_chinese_font(size: int = 24):
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]

    for p in candidates:
        try:
            if Path(p).exists():
                return ImageFont.truetype(p, size=size)
        except Exception:
            pass

    return ImageFont.load_default()


FONT_TITLE = load_chinese_font(30)
FONT_MID = load_chinese_font(22)
FONT_SMALL = load_chinese_font(18)


# =========================================================
# OpenCV ROI V2.0 识别算法
# =========================================================

def analyze_expansion_joint(image_rgb: Image.Image) -> Dict[str, Any]:
    rgb = np.array(image_rgb.convert("RGB"))
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    h, w = bgr.shape[:2]

    roi_x1 = int(w * 0.30)
    roi_x2 = int(w * 0.70)
    roi_y1 = int(h * 0.05)
    roi_y2 = int(h * 0.98)

    roi = bgr[roi_y1:roi_y2, roi_x1:roi_x2]
    roi_h, roi_w = roi.shape[:2]
    roi_area = max(1, roi_h * roi_w)

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    saturation = hsv[:, :, 1]
    white_mask = ((gray > 190) & (saturation < 80)).astype(np.uint8) * 255

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    kernel_black = cv2.getStructuringElement(cv2.MORPH_RECT, (19, 19))
    blackhat = cv2.morphologyEx(enhanced, cv2.MORPH_BLACKHAT, kernel_black)

    _, mask_black = cv2.threshold(
        blackhat,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )

    edges = cv2.Canny(enhanced, 60, 160)
    dark_mask = (gray < 175).astype(np.uint8) * 255

    mask = cv2.bitwise_or(mask_black, edges)
    mask = cv2.bitwise_and(mask, dark_mask)
    mask[white_mask > 0] = 0

    kernel_open = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    kernel_close = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_open, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel_close, iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    suspect_area = 0.0

    for c in contours:
        area = cv2.contourArea(c)

        if area < 28:
            continue

        if area > roi_area * 0.18:
            continue

        x, y, bw, bh = cv2.boundingRect(c)

        if bw <= 1 or bh <= 1:
            continue

        box_area = bw * bh
        aspect = max(bw, bh) / max(1, min(bw, bh))
        fill_ratio = area / max(1, box_area)

        if fill_ratio > 0.72 and area > 260:
            continue

        if aspect < 2.0 and area < 450:
            continue

        local_gray = gray[y:y + bh, x:x + bw]
        mean_gray = float(np.mean(local_gray)) if local_gray.size else 255

        if mean_gray > 185:
            continue

        gx1 = roi_x1 + x
        gy1 = roi_y1 + y
        gx2 = roi_x1 + x + bw
        gy2 = roi_y1 + y + bh

        boxes.append(
            {
                "x1": int(gx1),
                "y1": int(gy1),
                "x2": int(gx2),
                "y2": int(gy2),
                "area": float(area),
                "aspect": float(aspect),
                "mean_gray": float(mean_gray),
            }
        )

        suspect_area += area

    suspect_count = len(boxes)
    area_ratio = suspect_area / roi_area * 100.0

    if suspect_count == 0 or area_ratio < 0.03:
        disease_type = "未见明显病害"
        disease_level = "基本正常"
        risk_level = "低风险"
        decision = "继续巡检"
        repairable = False
        suggestion = "当前 ROI 区域未发现明显裂缝或破损，建议继续巡检并保留图像记录。"
        confidence = 0.72

    elif area_ratio < 0.35 and suspect_count <= 6:
        disease_type = "疑似轻微裂缝 / 边缘细小破损"
        disease_level = "轻微病害"
        risk_level = "低风险"
        decision = "记录并复查"
        repairable = False
        suggestion = "发现轻微疑似病害，建议记录归档，后续定期复查。"
        confidence = min(0.90, 0.68 + area_ratio * 0.35 + suspect_count * 0.015)

    elif area_ratio < 2.8 and suspect_count <= 18:
        disease_type = "伸缩缝边缘破损 / 疑似裂缝"
        disease_level = "中度病害"
        risk_level = "中风险"
        decision = "建议局部修复"
        repairable = True
        suggestion = "建议小车停止巡检，机械臂执行局部清理、注胶填补或表面整平，修复完成后再次拍照上传云端复检。"
        confidence = min(0.94, 0.76 + area_ratio * 0.04 + suspect_count * 0.006)

    else:
        disease_type = "疑似严重裂缝 / 大面积破损"
        disease_level = "疑似严重病害"
        risk_level = "高风险"
        decision = "需人工复核"
        repairable = False
        suggestion = "疑似存在较明显病害，不建议小车机械臂强行修复，应生成报告并建议人工专业复查。"
        confidence = min(0.96, 0.82 + min(area_ratio, 6) * 0.02)

    return {
        "disease_type": disease_type,
        "disease_level": disease_level,
        "risk_level": risk_level,
        "confidence": round(float(confidence), 2),
        "suspect_count": int(suspect_count),
        "area_ratio": round(float(area_ratio), 4),
        "decision": decision,
        "repairable": bool(repairable),
        "suggestion": suggestion,
        "roi": {
            "x1": roi_x1,
            "y1": roi_y1,
            "x2": roi_x2,
            "y2": roi_y2,
            "description": "默认分析画面中间 40% 宽度区域，用于聚焦桥梁伸缩缝。"
        },
        "boxes": boxes,
    }


def make_analysis_image(image_rgb: Image.Image, result: Dict[str, Any]) -> Image.Image:
    rgb = np.array(image_rgb.convert("RGB"))
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    roi = result["roi"]

    cv2.rectangle(
        bgr,
        (roi["x1"], roi["y1"]),
        (roi["x2"], roi["y2"]),
        (0, 215, 255),
        3,
    )

    for box in result.get("boxes", []):
        cv2.rectangle(
            bgr,
            (box["x1"], box["y1"]),
            (box["x2"], box["y2"]),
            (0, 0, 255),
            2,
        )

    out_rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    pil = Image.fromarray(out_rgb).convert("RGBA")
    panel = Image.new("RGBA", pil.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)

    panel_w = min(pil.size[0] - 20, 720)
    panel_h = 185

    draw.rounded_rectangle(
        (10, 10, panel_w, panel_h),
        radius=18,
        fill=(0, 0, 0, 175),
        outline=(232, 190, 88, 230),
        width=3,
    )

    risk_color = {
        "低风险": (83, 255, 160, 255),
        "中风险": (255, 205, 72, 255),
        "高风险": (255, 90, 90, 255),
    }.get(result["risk_level"], (255, 255, 255, 255))

    draw.text((28, 24), "桥智缝卫 云端病害识别 V2.1", font=FONT_TITLE, fill=(255, 220, 120, 255))
    draw.text((28, 68), f"病害类型：{result['disease_type']}", font=FONT_MID, fill=(255, 255, 255, 255))
    draw.text((28, 102), f"风险等级：{result['risk_level']}", font=FONT_MID, fill=risk_color)
    draw.text(
        (28, 136),
        f"疑似区域：{result['suspect_count']}  面积占比：{result['area_ratio']}%  置信度：{result['confidence']}",
        font=FONT_SMALL,
        fill=(210, 230, 255, 255),
    )

    draw.text(
        (roi["x1"] + 8, max(roi["y1"] - 28, 5)),
        "伸缩缝 ROI 分析区域",
        font=FONT_SMALL,
        fill=(255, 215, 80, 255),
    )

    merged = Image.alpha_composite(pil, panel).convert("RGB")
    return merged


def save_image_from_upload(file_bytes: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        return img
    except Exception:
        raise HTTPException(status_code=400, detail="图片读取失败，请上传 jpg/png/jpeg 图片。")


# =========================================================
# 手机端页面
# =========================================================

MOBILE_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>桥智缝卫 - 云端巡检系统</title>
<style>
*{box-sizing:border-box}
body{
    margin:0;
    min-height:100vh;
    background:
      radial-gradient(circle at 10% 10%, rgba(255,197,90,.18), transparent 25%),
      radial-gradient(circle at 90% 15%, rgba(0,180,255,.18), transparent 26%),
      linear-gradient(135deg,#05070b,#0b111d 45%,#090807);
    color:#f7f0d6;
    font-family:"Microsoft YaHei","PingFang SC",Arial,sans-serif;
}
.header{
    padding:22px 18px 12px;
    text-align:center;
}
.title{
    font-size:34px;
    font-weight:900;
    letter-spacing:6px;
    color:#ffd36a;
    text-shadow:0 0 18px rgba(255,210,100,.75),0 0 42px rgba(255,178,55,.28);
    animation:glow 2.6s ease-in-out infinite alternate;
}
.sub{
    margin-top:8px;
    color:#9bdcff;
    font-size:14px;
    letter-spacing:2px;
}
@keyframes glow{
    from{filter:brightness(1)}
    to{filter:brightness(1.25)}
}
.status-bar{
    margin:12px auto 0;
    display:flex;
    gap:10px;
    flex-wrap:wrap;
    justify-content:center;
}
.badge{
    padding:8px 12px;
    border:1px solid rgba(255,211,106,.35);
    border-radius:999px;
    background:rgba(0,0,0,.35);
    box-shadow:0 0 16px rgba(255,211,106,.12) inset;
    font-size:13px;
}
.dot{
    display:inline-block;
    width:9px;
    height:9px;
    border-radius:50%;
    background:#40ff9c;
    box-shadow:0 0 12px #40ff9c;
    margin-right:6px;
    animation:pulse 1.4s infinite;
}
@keyframes pulse{
    50%{opacity:.35;transform:scale(.82)}
}
.container{
    width:min(1380px,96vw);
    margin:12px auto 40px;
    display:grid;
    grid-template-columns:1.2fr .8fr;
    gap:18px;
}
.card{
    border:1px solid rgba(255,211,106,.25);
    background:linear-gradient(180deg,rgba(12,18,30,.88),rgba(4,7,12,.88));
    border-radius:22px;
    box-shadow:0 18px 50px rgba(0,0,0,.32),0 0 28px rgba(255,195,80,.08) inset;
    padding:16px;
    position:relative;
    overflow:hidden;
}
.card h2{
    margin:0 0 14px;
    font-size:18px;
    color:#ffd36a;
    letter-spacing:1px;
}
.video-wrap{
    position:relative;
    width:100%;
    aspect-ratio:16/9;
    border-radius:18px;
    overflow:hidden;
    background:#02050a;
    border:1px solid rgba(83,198,255,.35);
}
.video-wrap img{
    width:100%;
    height:100%;
    object-fit:cover;
    display:none;
}
.video-placeholder{
    position:absolute;
    inset:0;
    display:flex;
    align-items:center;
    justify-content:center;
    flex-direction:column;
    color:#8fcfff;
    background:
      linear-gradient(rgba(255,255,255,.03) 1px, transparent 1px),
      linear-gradient(90deg,rgba(255,255,255,.03) 1px, transparent 1px);
    background-size:32px 32px;
}
.ring{
    width:70px;
    height:70px;
    border-radius:50%;
    border:3px solid rgba(255,211,106,.25);
    border-top-color:#ffd36a;
    animation:spin 1.2s linear infinite;
    margin-bottom:14px;
}
@keyframes spin{
    to{transform:rotate(360deg)}
}
.scanline{
    position:absolute;
    left:0;
    right:0;
    top:-30%;
    height:34%;
    background:linear-gradient(transparent,rgba(80,210,255,.18),transparent);
    animation:scan 3s infinite;
    pointer-events:none;
}
@keyframes scan{
    to{top:105%}
}
.input-row{
    display:flex;
    gap:10px;
    margin-top:12px;
}
input,select{
    flex:1;
    border:1px solid rgba(255,211,106,.28);
    border-radius:12px;
    padding:12px;
    background:rgba(0,0,0,.38);
    color:#fff;
    outline:none;
}
button{
    border:none;
    border-radius:14px;
    padding:12px 16px;
    cursor:pointer;
    color:#120b00;
    font-weight:900;
    background:linear-gradient(135deg,#ffd36a,#ff9d2e);
    box-shadow:0 0 18px rgba(255,185,70,.22);
    transition:.18s transform,.18s filter;
}
button:hover{
    transform:translateY(-2px);
    filter:brightness(1.12);
}
button.dark{
    color:#dff6ff;
    background:linear-gradient(135deg,#123451,#091729);
    border:1px solid rgba(83,198,255,.45);
}
button.danger{
    color:white;
    background:linear-gradient(135deg,#ff4141,#7b0000);
}

/* 这里是按你图一改好的 3×3 按钮布局 */
.control-grid{
    display:grid;
    grid-template-columns:repeat(3,1fr);
    grid-template-areas:
        "ledon forward ledoff"
        "left stop right"
        "capture backward reset";
    gap:12px;
}
.control-grid button{
    min-height:54px;
    font-size:15px;
}
.btn-ledon{grid-area:ledon}
.btn-forward{grid-area:forward}
.btn-ledoff{grid-area:ledoff}
.btn-left{grid-area:left}
.btn-stop{grid-area:stop}
.btn-right{grid-area:right}
.btn-capture{grid-area:capture}
.btn-backward{grid-area:backward}
.btn-reset{grid-area:reset}

.upload-zone{
    border:1px dashed rgba(255,211,106,.42);
    border-radius:18px;
    padding:14px;
    background:rgba(0,0,0,.25);
}
.result-grid{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:14px;
    margin-top:14px;
}
.img-box{
    border-radius:16px;
    overflow:hidden;
    background:rgba(0,0,0,.38);
    border:1px solid rgba(255,211,106,.18);
    min-height:220px;
    display:flex;
    align-items:center;
    justify-content:center;
    color:#789;
}
.img-box img{
    width:100%;
    display:none;
}
.report-panel{
    margin-top:14px;
    padding:14px;
    border-radius:16px;
    background:rgba(0,0,0,.28);
    border:1px solid rgba(83,198,255,.24);
    color:#dfefff;
    line-height:1.8;
}
.risk{
    display:inline-block;
    padding:5px 10px;
    border-radius:999px;
    font-weight:900;
}
.risk-low{
    background:rgba(66,255,156,.15);
    color:#42ff9c;
    border:1px solid #42ff9c;
}
.risk-mid{
    background:rgba(255,205,72,.15);
    color:#ffcd48;
    border:1px solid #ffcd48;
}
.risk-high{
    background:rgba(255,78,78,.15);
    color:#ff6b6b;
    border:1px solid #ff6b6b;
}
.flow{
    display:grid;
    grid-template-columns:repeat(4,1fr);
    gap:10px;
    margin-top:14px;
}
.step{
    padding:12px 8px;
    border-radius:14px;
    text-align:center;
    background:rgba(255,255,255,.04);
    border:1px solid rgba(255,211,106,.2);
    color:#cbdfff;
    font-size:13px;
}
.toast{
    position:fixed;
    left:50%;
    bottom:28px;
    transform:translateX(-50%);
    background:rgba(0,0,0,.82);
    color:#fff;
    border:1px solid rgba(255,211,106,.38);
    padding:12px 18px;
    border-radius:999px;
    box-shadow:0 0 25px rgba(255,211,106,.15);
    display:none;
    z-index:99;
}
@media(max-width:950px){
    .container{grid-template-columns:1fr}
    .result-grid{grid-template-columns:1fr}
    .title{font-size:28px}
    .flow{grid-template-columns:repeat(2,1fr)}
}
</style>
</head>
<body>
<div class="header">
    <div class="title">桥智缝卫</div>
    <div class="sub">桥梁伸缩缝智能巡检云端系统 · 巡检 / 识别 / 修复 / 复检 / 报告</div>
    <div class="status-bar">
        <div class="badge"><span class="dot"></span>云端服务在线</div>
        <div class="badge" id="healthBadge">等待检测</div>
        <div class="badge">设备编号：QZFW-CAR-01</div>
    </div>
</div>

<div class="container">
    <div class="card">
        <h2>实时视频窗口</h2>
        <div class="video-wrap">
            <img id="videoImg">
            <div class="video-placeholder" id="videoPlaceholder">
                <div class="ring"></div>
                <div>输入 ESP32-CAM IP 后连接视频</div>
                <div style="font-size:12px;margin-top:8px;color:#6c91ad">
                    云端页面能打开，即代表服务器先通；视频需手机/电脑与小车在同一 WiFi
                </div>
            </div>
            <div class="scanline"></div>
        </div>

        <div class="input-row">
            <input id="carIp" value="192.168.84.154" placeholder="输入 ESP32-CAM IP，例如 192.168.84.154">
            <select id="streamMode" style="max-width:155px">
                <option value="81">视频端口:81</option>
                <option value="80">同端口/stream</option>
            </select>
            <button onclick="connectVideo()">连接视频</button>
            <button class="dark" onclick="checkHealth()">检测云端</button>
        </div>

        <div class="flow">
            <div class="step">1 巡检移动</div>
            <div class="step">2 拍照采集</div>
            <div class="step">3 云端识别</div>
            <div class="step">4 复检报告</div>
        </div>
    </div>

    <div class="card">
        <h2>小车控制台</h2>

        <!-- 按照你图一手绘位置重新排列 -->
        <div class="control-grid">
            <button class="btn-ledon" onclick="sendCar('led/on')">LED开</button>
            <button class="btn-forward" onclick="sendCar('forward')">前进</button>
            <button class="btn-ledoff" onclick="sendCar('led/off')">LED关</button>

            <button class="dark btn-left" onclick="sendCar('left')">左转</button>
            <button class="danger btn-stop" onclick="sendCar('stop')">停止</button>
            <button class="dark btn-right" onclick="sendCar('right')">右转</button>

            <button class="dark btn-capture" onclick="sendCar('capture')">拍照采集</button>
            <button class="dark btn-backward" onclick="sendCar('backward')">后退</button>
            <button class="dark btn-reset" onclick="sendCar('reset')">复位</button>
        </div>

        <div class="report-panel">
            <b>控制说明：</b><br>
            按钮位置已按你的图一布局调整：<br>
            第一行：LED开、前进、LED关<br>
            第二行：左转、停止、右转<br>
            第三行：拍照采集、后退、复位<br>
            控制按钮需要 ESP32-CAM 已开机，并且手机/电脑与小车处于同一个 WiFi 或热点。
        </div>
    </div>

    <div class="card" style="grid-column:1/-1">
        <h2>一键采集上传识别</h2>

        <div class="upload-zone">
            <input type="file" id="fileInput" accept="image/*">
            <button onclick="uploadDetect()">上传云端识别</button>
            <button class="dark" onclick="clearResult()">清空结果</button>
        </div>

        <div class="result-grid">
            <div>
                <h2 style="margin-top:14px">原始巡检图</h2>
                <div class="img-box">
                    <span id="rawEmpty">等待上传图片</span>
                    <img id="rawImg">
                </div>
            </div>
            <div>
                <h2 style="margin-top:14px">云端分析图</h2>
                <div class="img-box">
                    <span id="anaEmpty">等待识别结果</span>
                    <img id="analysisImg">
                </div>
            </div>
        </div>

        <div class="report-panel" id="resultPanel">
            <b>病害分析报告：</b><br>
            暂无识别结果。
        </div>
    </div>
</div>

<div class="toast" id="toast"></div>

<script>
function toast(msg){
    const t=document.getElementById('toast');
    t.innerText=msg;
    t.style.display='block';
    setTimeout(()=>{t.style.display='none'},2200);
}

async function checkHealth(){
    try{
        const res=await fetch('/health');
        const data=await res.json();
        document.getElementById('healthBadge').innerHTML='云端检测：'+data.status;
        toast('云端服务正常');
    }catch(e){
        document.getElementById('healthBadge').innerHTML='云端检测：异常';
        toast('云端检测失败');
    }
}

function getCarIp(){
    return document.getElementById('carIp').value.trim().replace('http://','').replace('https://','').replace('/','');
}

function connectVideo(){
    const ip=getCarIp();
    if(!ip){
        toast('请先输入小车 IP');
        return;
    }

    const mode=document.getElementById('streamMode').value;
    const url = mode === '81' ? `http://${ip}:81/stream` : `http://${ip}/stream`;

    const img=document.getElementById('videoImg');
    img.src=url+'?t='+Date.now();
    img.style.display='block';
    document.getElementById('videoPlaceholder').style.display='none';

    toast('正在连接视频：'+url);
}

async function sendCar(path){
    const ip=getCarIp();

    if(!ip){
        toast('请先输入小车 IP');
        return;
    }

    const url=`http://${ip}/${path}`;

    try{
        await fetch(url,{mode:'no-cors'});
        toast('指令已发送：'+path);
    }catch(e){
        toast('指令发送失败，请检查小车 IP 和 WiFi');
    }
}

function riskClass(risk){
    if(risk==='高风险') return 'risk risk-high';
    if(risk==='中风险') return 'risk risk-mid';
    return 'risk risk-low';
}

async function uploadDetect(){
    const input=document.getElementById('fileInput');

    if(!input.files || input.files.length===0){
        toast('请先选择一张伸缩缝图片');
        return;
    }

    const fd=new FormData();
    fd.append('file',input.files[0]);

    document.getElementById('resultPanel').innerHTML='<b>云端分析中：</b><br>图片上传中，请稍等...';
    toast('正在上传识别');

    try{
        const res=await fetch('/api/detect',{method:'POST',body:fd});
        const data=await res.json();

        if(!res.ok){
            document.getElementById('resultPanel').innerHTML='<b>识别失败：</b><br>'+JSON.stringify(data);
            toast('识别失败');
            return;
        }

        const raw=document.getElementById('rawImg');
        raw.src=data.raw_url+'?t='+Date.now();
        raw.style.display='block';
        document.getElementById('rawEmpty').style.display='none';

        const ana=document.getElementById('analysisImg');
        ana.src=data.analysis_url+'?t='+Date.now();
        ana.style.display='block';
        document.getElementById('anaEmpty').style.display='none';

        document.getElementById('resultPanel').innerHTML=`
            <b>病害分析报告：</b><br>
            巡检编号：${data.id}<br>
            病害类型：${data.disease_type}<br>
            病害等级：${data.disease_level}<br>
            风险等级：<span class="${riskClass(data.risk_level)}">${data.risk_level}</span><br>
            疑似区域数量：${data.suspect_count}<br>
            面积占比：${data.area_ratio}%<br>
            识别置信度：${data.confidence}<br>
            修复决策：${data.decision}<br>
            是否建议修复：${data.repairable ? '是' : '否'}<br>
            处理建议：${data.suggestion}<br>
            <a style="color:#ffd36a" href="${data.report_url}" target="_blank">打开检测报告</a>
        `;

        toast('识别完成');
    }catch(e){
        document.getElementById('resultPanel').innerHTML='<b>上传失败：</b><br>请检查服务器是否运行。';
        toast('上传失败');
    }
}

function clearResult(){
    document.getElementById('fileInput').value='';
    document.getElementById('rawImg').style.display='none';
    document.getElementById('analysisImg').style.display='none';
    document.getElementById('rawEmpty').style.display='inline';
    document.getElementById('anaEmpty').style.display='inline';
    document.getElementById('resultPanel').innerHTML='<b>病害分析报告：</b><br>暂无识别结果。';
}

checkHealth();
</script>
</body>
</html>
"""


# =========================================================
# API 与页面
# =========================================================

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
    return {
        "project": "桥智缝卫",
        "status": "ONLINE",
        "time": now_text(),
        "message": "云端服务正常，FastAPI 已运行。",
    }


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/mobile", response_class=HTMLResponse)
def mobile():
    return HTMLResponse(MOBILE_HTML)


@app.post("/api/detect")
async def api_detect(file: UploadFile = File(...)):
    file_bytes = await file.read()

    image = save_image_from_upload(file_bytes)
    detect_id = make_detect_id("QZFW")

    raw_filename = f"{detect_id}_raw.png"
    analysis_filename = f"{detect_id}_analysis.jpg"

    raw_path = UPLOAD_DIR / raw_filename
    analysis_path = UPLOAD_DIR / analysis_filename

    image.save(raw_path)

    result = analyze_expansion_joint(image)
    analysis_img = make_analysis_image(image, result)
    analysis_img.save(analysis_path, quality=92)

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
async def api_recheck(
    detection_id: str = Form(...),
    file: UploadFile = File(...),
):
    before = get_detection(detection_id)

    if before is None:
        raise HTTPException(status_code=404, detail="未找到原始检测记录。")

    file_bytes = await file.read()
    image = save_image_from_upload(file_bytes)

    recheck_id = make_detect_id("RECHECK")

    after_filename = f"{recheck_id}_after.png"
    after_analysis_filename = f"{recheck_id}_after_analysis.jpg"

    after_path = UPLOAD_DIR / after_filename
    after_analysis_path = UPLOAD_DIR / after_analysis_filename

    image.save(after_path)

    after_result = analyze_expansion_joint(image)
    after_analysis_img = make_analysis_image(image, after_result)
    after_analysis_img.save(after_analysis_path, quality=92)

    before_area = float(before["area_ratio"])
    after_area = float(after_result["area_ratio"])

    if before_area <= 0.001:
        improvement = 0.0
    else:
        improvement = max(0.0, (before_area - after_area) / before_area * 100.0)

    if improvement >= 50:
        acceptance = "验收通过"
        suggestion = "修复后疑似病害面积明显下降，建议保存报告并定期复查。"
    elif improvement >= 20:
        acceptance = "建议复查"
        suggestion = "修复后有所改善，但改善幅度一般，建议再次观察或人工复查。"
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
        return HTMLResponse(
            "<h2>未找到报告</h2><p>检测编号不存在。</p>",
            status_code=404,
        )

    rechecks = get_rechecks(detect_id)

    risk_class = "low"

    if row["risk_level"] == "中风险":
        risk_class = "mid"
    elif row["risk_level"] == "高风险":
        risk_class = "high"

    recheck_html = ""

    if rechecks:
        for r in rechecks:
            recheck_html += f"""
            <div class="recheck">
                <h3>复检记录：{html.escape(r["id"])}</h3>
                <p>复检时间：{html.escape(r["created_at"])}</p>
                <p>修复前面积占比：{r["before_area_ratio"]}%</p>
                <p>修复后面积占比：{r["after_area_ratio"]}%</p>
                <p>改善率：{r["improvement_rate"]}%</p>
                <p>验收结果：{html.escape(r["acceptance_result"])}</p>
                <p>建议：{html.escape(r["suggestion"])}</p>
                <img src="{file_url(r["after_analysis_filename"])}">
            </div>
            """
    else:
        recheck_html = "<p>暂无复检记录。后续可通过 /api/recheck 上传修复后图片。</p>"

    page = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
    <meta charset="UTF-8">
    <title>桥智缝卫检测报告 - {html.escape(detect_id)}</title>
    <style>
    body{{
        margin:0;
        background:linear-gradient(135deg,#05070b,#0b111d,#070604);
        color:#f7f0d6;
        font-family:"Microsoft YaHei",Arial,sans-serif;
        padding:28px;
    }}
    .wrap{{
        max-width:1100px;
        margin:auto;
        border:1px solid rgba(255,211,106,.25);
        border-radius:24px;
        background:rgba(0,0,0,.35);
        padding:24px;
        box-shadow:0 0 38px rgba(255,190,70,.12);
    }}
    h1{{
        color:#ffd36a;
        text-shadow:0 0 18px rgba(255,211,106,.38);
        letter-spacing:2px;
    }}
    .grid{{
        display:grid;
        grid-template-columns:1fr 1fr;
        gap:18px;
        margin-top:18px;
    }}
    img{{
        width:100%;
        border-radius:18px;
        border:1px solid rgba(255,211,106,.25);
    }}
    .panel{{
        background:rgba(255,255,255,.04);
        border:1px solid rgba(83,198,255,.22);
        border-radius:18px;
        padding:18px;
        line-height:1.9;
    }}
    .risk{{
        display:inline-block;
        padding:5px 12px;
        border-radius:999px;
        font-weight:900;
    }}
    .low{{color:#42ff9c;border:1px solid #42ff9c;background:rgba(66,255,156,.12)}}
    .mid{{color:#ffcd48;border:1px solid #ffcd48;background:rgba(255,205,72,.12)}}
    .high{{color:#ff6b6b;border:1px solid #ff6b6b;background:rgba(255,78,78,.12)}}
    .recheck{{
        margin-top:18px;
        padding:16px;
        border-radius:16px;
        background:rgba(0,0,0,.28);
        border:1px solid rgba(255,211,106,.18);
    }}
    a{{color:#ffd36a}}
    @media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}
    </style>
    </head>
    <body>
    <div class="wrap">
        <h1>桥智缝卫伸缩缝智能巡检报告</h1>

        <div class="panel">
            <p>巡检编号：{html.escape(row["id"])}</p>
            <p>巡检时间：{html.escape(row["created_at"])}</p>
            <p>病害类型：{html.escape(row["disease_type"])}</p>
            <p>病害等级：{html.escape(row["disease_level"])}</p>
            <p>风险等级：<span class="risk {risk_class}">{html.escape(row["risk_level"])}</span></p>
            <p>疑似区域数量：{row["suspect_count"]}</p>
            <p>面积占比：{row["area_ratio"]}%</p>
            <p>识别置信度：{row["confidence"]}</p>
            <p>修复决策：{html.escape(row["decision"])}</p>
            <p>是否建议修复：{"是" if row["repairable"] else "否"}</p>
            <p>处理建议：{html.escape(row["suggestion"])}</p>
        </div>

        <div class="grid">
            <div>
                <h2>原始巡检图</h2>
                <img src="{file_url(row["raw_filename"])}">
            </div>
            <div>
                <h2>云端分析图</h2>
                <img src="{file_url(row["analysis_filename"])}">
            </div>
        </div>

        <h2>复检记录</h2>
        {recheck_html}

        <p style="margin-top:24px">
            <a href="/mobile">返回手机端页面</a>　
            <a href="/admin">进入后台记录</a>
        </p>
    </div>
    </body>
    </html>
    """

    return HTMLResponse(page)


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    rows = get_latest_detections(50)

    cards = ""

    for r in rows:
        risk = r["risk_level"]
        color = "#42ff9c"

        if risk == "中风险":
            color = "#ffcd48"
        elif risk == "高风险":
            color = "#ff6b6b"

        cards += f"""
        <tr>
            <td>{html.escape(r["created_at"])}</td>
            <td>{html.escape(r["id"])}</td>
            <td>{html.escape(r["disease_type"])}</td>
            <td style="color:{color};font-weight:900">{html.escape(r["risk_level"])}</td>
            <td>{r["suspect_count"]}</td>
            <td>{r["area_ratio"]}%</td>
            <td><a href="/report/{html.escape(r["id"])}" target="_blank">查看报告</a></td>
        </tr>
        """

    if not cards:
        cards = """
        <tr>
            <td colspan="7" style="text-align:center;color:#8aa">暂无检测记录，请先到 /mobile 上传图片。</td>
        </tr>
        """

    page = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
    <meta charset="UTF-8">
    <title>桥智缝卫后台</title>
    <style>
    body{{
        margin:0;
        background:#060912;
        color:#f7f0d6;
        font-family:"Microsoft YaHei",Arial,sans-serif;
        padding:24px;
    }}
    h1{{color:#ffd36a}}
    table{{
        width:100%;
        border-collapse:collapse;
        background:rgba(255,255,255,.04);
        border-radius:16px;
        overflow:hidden;
    }}
    th,td{{
        border-bottom:1px solid rgba(255,255,255,.08);
        padding:12px;
        text-align:left;
        font-size:14px;
    }}
    th{{
        color:#ffd36a;
        background:rgba(255,211,106,.08);
    }}
    a{{color:#9bdcff}}
    .top{{
        display:flex;
        justify-content:space-between;
        align-items:center;
        gap:12px;
        flex-wrap:wrap;
    }}
    .btn{{
        color:#120b00;
        background:#ffd36a;
        padding:10px 14px;
        border-radius:12px;
        text-decoration:none;
        font-weight:900;
    }}
    </style>
    </head>
    <body>
        <div class="top">
            <h1>桥智缝卫云端后台记录</h1>
            <a class="btn" href="/mobile">返回手机端</a>
        </div>

        <table>
            <thead>
                <tr>
                    <th>时间</th>
                    <th>巡检编号</th>
                    <th>病害类型</th>
                    <th>风险等级</th>
                    <th>疑似区域</th>
                    <th>面积占比</th>
                    <th>报告</th>
                </tr>
            </thead>
            <tbody>
                {cards}
            </tbody>
        </table>
    </body>
    </html>
    """

    return HTMLResponse(page)


# 启动命令：
# uvicorn main:app --host 0.0.0.0 --port 8000
