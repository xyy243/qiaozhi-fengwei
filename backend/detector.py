import io
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    from fastapi import HTTPException
except ModuleNotFoundError:
    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str):
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)


def load_image(file_bytes: bytes) -> Image.Image:
    try:
        return Image.open(io.BytesIO(file_bytes)).convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="图片读取失败，请上传 jpg/png/jpeg 图片。") from exc


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
    for font_path in candidates:
        try:
            if Path(font_path).exists():
                return ImageFont.truetype(font_path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


FONT_TITLE = load_chinese_font(30)
FONT_MID = load_chinese_font(22)
FONT_SMALL = load_chinese_font(18)


def _joint_roi(width: int, height: int) -> tuple[int, int, int, int]:
    roi_w = int(width * 0.50)
    center_x = width // 2
    x1 = max(0, center_x - roi_w // 2)
    x2 = min(width, center_x + roi_w // 2)
    y1 = int(height * 0.06)
    y2 = int(height * 0.98)
    return x1, y1, x2, y2


def _large_white_mask(gray: np.ndarray, saturation: np.ndarray, roi_area: int) -> np.ndarray:
    white = ((gray > 188) & (saturation < 88)).astype(np.uint8) * 255
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)), iterations=1)
    filtered = np.zeros_like(white)
    contours, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < max(80, roi_area * 0.003):
            continue
        x, y, w, h = cv2.boundingRect(contour)
        aspect = max(w, h) / max(1, min(w, h))
        if area > roi_area * 0.009 or aspect > 3.0:
            cv2.drawContours(filtered, [contour], -1, 255, thickness=-1)
    return cv2.dilate(filtered, cv2.getStructuringElement(cv2.MORPH_RECT, (13, 13)), iterations=1)


def _estimate_joint_edges(gray: np.ndarray, white_guard: np.ndarray) -> tuple[int, int]:
    roi_h, roi_w = gray.shape[:2]
    y1 = int(roi_h * 0.12)
    y2 = int(roi_h * 0.92)
    work = gray[y1:y2].copy()
    guard = white_guard[y1:y2]
    work[guard > 0] = int(np.median(work))

    grad_x = cv2.Sobel(work, cv2.CV_32F, 1, 0, ksize=3)
    score = np.mean(np.abs(grad_x), axis=0)
    dark_bonus = 255.0 - np.mean(work, axis=0)
    score = cv2.GaussianBlur((score + dark_bonus * 0.18).reshape(1, -1), (1, 17), 0).ravel()

    lo = int(roi_w * 0.14)
    hi = int(roi_w * 0.86)
    left_slice = score[lo : roi_w // 2]
    right_slice = score[roi_w // 2 : hi]
    if left_slice.size == 0 or right_slice.size == 0:
        return int(roi_w * 0.35), int(roi_w * 0.65)

    left_edge = lo + int(np.argmax(left_slice))
    right_edge = roi_w // 2 + int(np.argmax(right_slice))
    if right_edge - left_edge < int(roi_w * 0.15):
        return int(roi_w * 0.35), int(roi_w * 0.65)
    return left_edge, right_edge


def _double_edge_mask(roi_w: int, roi_h: int, left_edge: int, right_edge: int) -> np.ndarray:
    mask = np.zeros((roi_h, roi_w), dtype=np.uint8)
    outer_w = max(42, int(roi_w * 0.32))
    inner_w = max(12, int(roi_w * 0.08))
    mask[:, max(0, left_edge - outer_w) : min(roi_w, left_edge + inner_w)] = 255
    mask[:, max(0, right_edge - inner_w) : min(roi_w, right_edge + outer_w)] = 255
    return mask


def _contour_solidity(contour: np.ndarray, area: float) -> float:
    hull = cv2.convexHull(contour)
    return area / max(1.0, cv2.contourArea(hull))


def _edge_coverage(boxes: list[dict[str, float | int]], roi_h: int, edge_x: int, tolerance: int) -> float:
    covered = np.zeros(roi_h, dtype=np.uint8)
    for box in boxes:
        cx = int((box["local_x1"] + box["local_x2"]) / 2)
        if abs(cx - edge_x) <= tolerance:
            covered[int(box["local_y1"]) : int(box["local_y2"])] = 1
    return float(np.count_nonzero(covered)) / max(1, roi_h)


def _rust_like_mask(hsv: np.ndarray, gray: np.ndarray) -> np.ndarray:
    hue = hsv[:, :, 0]
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    rust = (((hue <= 28) | (hue >= 165)) & (sat > 45) & (val < 185) & (gray < 180)).astype(np.uint8) * 255
    return cv2.morphologyEx(rust, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)), iterations=1)


def analyze_expansion_joint(image_rgb: Image.Image) -> dict[str, Any]:
    rgb = np.array(image_rgb.convert("RGB"))
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    height, width = bgr.shape[:2]
    roi_x1, roi_y1, roi_x2, roi_y2 = _joint_roi(width, height)

    roi = bgr[roi_y1:roi_y2, roi_x1:roi_x2]
    roi_h, roi_w = roi.shape[:2]
    roi_area = max(1, roi_h * roi_w)

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    white_guard = _large_white_mask(gray, saturation, roi_area)
    left_edge, right_edge = _estimate_joint_edges(gray, white_guard)
    edge_focus = _double_edge_mask(roi_w, roi_h, left_edge, right_edge)

    enhanced = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
    blackhat = cv2.morphologyEx(enhanced, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (17, 17)))
    gradient = cv2.morphologyEx(enhanced, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
    _, blackhat_mask = cv2.threshold(blackhat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    dark_gap = (gray < 162).astype(np.uint8) * 255
    rough_spall = ((gradient > 24) & (gray < 192)).astype(np.uint8) * 255
    rust_like = _rust_like_mask(hsv, gray)

    mask = cv2.bitwise_or(blackhat_mask, dark_gap)
    mask = cv2.bitwise_or(mask, rough_spall)
    mask = cv2.bitwise_or(mask, rust_like)
    mask = cv2.bitwise_and(mask, edge_focus)
    strong_white_guard = cv2.bitwise_and(white_guard, cv2.bitwise_not(rough_spall))
    strong_white_guard = cv2.bitwise_and(strong_white_guard, cv2.bitwise_not(dark_gap))
    mask[strong_white_guard > 0] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)), iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (6, 6)), iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[dict[str, float | int]] = []
    suspect_area = 0.0
    raw_damage_area = 0.0
    rebar_area = 0.0
    dark_gap_area = 0.0
    rejected_white_area = 0.0

    for contour in contours:
        area = cv2.contourArea(contour)
        if area < max(18, roi_area * 0.00015):
            continue

        x, y, box_w, box_h = cv2.boundingRect(contour)
        if box_w <= 2 or box_h <= 2:
            continue

        box_area = box_w * box_h
        aspect = max(box_w, box_h) / max(1, min(box_w, box_h))
        fill_ratio = area / max(1, box_area)
        solidity = _contour_solidity(contour, area)
        local_gray = gray[y : y + box_h, x : x + box_w]
        local_sat = saturation[y : y + box_h, x : x + box_w]
        local_white = white_guard[y : y + box_h, x : x + box_w]
        local_rust = rust_like[y : y + box_h, x : x + box_w]
        local_gradient = gradient[y : y + box_h, x : x + box_w]
        mean_gray = float(np.mean(local_gray)) if local_gray.size else 255.0
        mean_sat = float(np.mean(local_sat)) if local_sat.size else 255.0
        mean_gradient = float(np.mean(local_gradient)) if local_gradient.size else 0.0
        white_ratio = float(np.count_nonzero(local_white)) / max(1, box_area)
        rust_ratio = float(np.count_nonzero(local_rust)) / max(1, box_area)
        dark_ratio = float(np.count_nonzero(local_gray < 132)) / max(1, box_area)
        cx = x + box_w / 2.0
        near_joint_edge = min(abs(cx - left_edge), abs(cx - right_edge)) <= max(24, roi_w * 0.16)
        irregular = solidity < 0.86 or fill_ratio < 0.58
        dark_or_rust = mean_gray < 176 or rust_ratio > 0.035

        is_white_line_edge = white_ratio > 0.12 or (mean_gray > 174 and mean_sat < 90)
        is_regular_cover_shadow = aspect > 12 and fill_ratio < 0.34 and box_h > roi_h * 0.22
        is_big_regular_rect = area > roi_area * 0.035 and fill_ratio > 0.60 and solidity > 0.88 and mean_gray > 135
        is_marking_piece = box_area > roi_area * 0.040 and (aspect > 2.6 or fill_ratio > 0.44) and mean_gray > 155
        is_large_left_anchor_spall = (
            cx < left_edge + roi_w * 0.12
            and area > roi_area * 0.035
            and mean_gray < 155
            and (dark_ratio > 0.22 or rust_ratio > 0.05)
        )
        is_uniform_top_shadow = (
            y < roi_h * 0.12
            and area > roi_area * 0.010
            and mean_gray < 150
            and cx > right_edge - roi_w * 0.12
            and rust_ratio < 0.03
            and fill_ratio > 0.55
        )

        if is_white_line_edge or is_regular_cover_shadow or is_big_regular_rect or is_marking_piece or is_uniform_top_shadow:
            rejected_white_area += area
            continue
        if mean_gray > 192 or not near_joint_edge:
            continue
        if area > roi_area * 0.14 and not ((irregular and dark_or_rust) or is_large_left_anchor_spall):
            rejected_white_area += area
            continue

        exposed_rebar_like = (aspect >= 4.0 and (mean_gray < 150 or rust_ratio > 0.04)) or rust_ratio > 0.08
        severe_spall_like = area > roi_area * 0.004 and irregular and dark_or_rust

        severity_weight = 1.0
        if irregular:
            severity_weight += 0.35
        if mean_gray < 150:
            severity_weight += 0.25
        if dark_ratio > 0.20:
            severity_weight += 0.25
        if rust_ratio > 0.04:
            severity_weight += 0.35
        if exposed_rebar_like:
            severity_weight += 0.55
        if severe_spall_like:
            severity_weight += 0.45
        if area < 85:
            severity_weight *= 0.72

        weighted_area = area * severity_weight
        boxes.append(
            {
                "x1": int(roi_x1 + x),
                "y1": int(roi_y1 + y),
                "x2": int(roi_x1 + x + box_w),
                "y2": int(roi_y1 + y + box_h),
                "local_x1": int(x),
                "local_x2": int(x + box_w),
                "local_y1": int(y),
                "local_y2": int(y + box_h),
                "area": float(area),
                "weighted_area": float(weighted_area),
                "aspect": float(aspect),
                "mean_gray": float(mean_gray),
                "mean_gradient": float(mean_gradient),
                "rust_ratio": float(rust_ratio),
                "dark_ratio": float(dark_ratio),
                "exposed_rebar_like": int(exposed_rebar_like),
            }
        )
        suspect_area += weighted_area
        raw_damage_area += area
        if exposed_rebar_like:
            rebar_area += area
        dark_gap_area += area * min(1.0, dark_ratio * 2.0)

    suspect_count = len(boxes)
    focus_area = max(1, int(np.count_nonzero(edge_focus)))
    damage_area_ratio = raw_damage_area / focus_area * 100.0
    weighted_damage_ratio = suspect_area / focus_area * 100.0
    white_mark_penalty = min(2.0, rejected_white_area / max(1, roi_area) * 18.0)
    run_tolerance = max(26, int(roi_w * 0.16))
    left_coverage = _edge_coverage(boxes, roi_h, left_edge, run_tolerance)
    right_coverage = _edge_coverage(boxes, roi_h, right_edge, run_tolerance)
    edge_continuity_score = min(4.0, max(left_coverage, right_coverage) * 8.0 + min(suspect_count, 12) * 0.08)
    exposed_rebar_score = min(4.0, rebar_area / focus_area * 140.0 + sum(int(b["exposed_rebar_like"]) for b in boxes) * 0.35)
    dark_gap_score = min(4.0, dark_gap_area / focus_area * 130.0)
    severity_score = max(
        0.0,
        min(weighted_damage_ratio, 16.0) * 0.55
        + edge_continuity_score * 0.95
        + exposed_rebar_score * 1.10
        + dark_gap_score * 0.85
        - white_mark_penalty,
    )

    severe_anchor_damage = (
        severity_score >= 4.2
        and damage_area_ratio >= 0.85
        and (edge_continuity_score >= 1.35 or exposed_rebar_score >= 0.85 or dark_gap_score >= 1.10)
    ) or (
        damage_area_ratio >= 2.2
        and edge_continuity_score >= 1.2
        and (exposed_rebar_score >= 0.45 or dark_gap_score >= 0.85)
    )
    moderate_edge_damage = severity_score >= 1.65 or damage_area_ratio >= 0.55 or edge_continuity_score >= 0.9

    if suspect_count == 0 or severity_score < 0.35 or damage_area_ratio < 0.04:
        disease_type = "未见明显病害"
        disease_level = "基本正常"
        risk_level = "低风险"
        decision = "继续巡检"
        repairable = False
        suggestion = "当前伸缩缝锚固区和双边缘 ROI 未发现明显破损，建议继续巡检并保留图像记录。"
        confidence = 0.76
    elif severe_anchor_damage:
        disease_type = "伸缩缝锚固区混凝土破碎 / 剥落露筋"
        disease_level = "严重病害"
        risk_level = "高风险"
        decision = "需人工复核"
        repairable = False
        suggestion = "建议交通限速或管制，安排人工专业维修或更换伸缩缝装置，禁止小车执行简单修复。"
        confidence = min(0.96, 0.84 + min(severity_score, 8.0) * 0.015)
    elif moderate_edge_damage:
        disease_type = "伸缩缝边缘破损 / 混凝土剥落"
        disease_level = "中度病害"
        risk_level = "中风险"
        decision = "建议局部修复"
        repairable = True
        suggestion = "伸缩缝边缘或锚固区存在不规则破损、剥落、缺角或坑槽，建议局部清理、填补或整平，修复后再次上传复检。"
        confidence = min(0.92, 0.76 + min(severity_score, 5.0) * 0.025)
    elif damage_area_ratio < 0.45 and suspect_count <= 5:
        disease_type = "轻微边缘磨损"
        disease_level = "轻微病害"
        risk_level = "低风险"
        decision = "记录并复查"
        repairable = False
        suggestion = "仅发现少量边缘磨损特征，面积占比较低，建议记录归档并定期复查。"
        confidence = min(0.86, 0.70 + damage_area_ratio * 0.18 + suspect_count * 0.012)
    else:
        disease_type = "疑似严重病害，需人工复核"
        disease_level = "需复核"
        risk_level = "中风险"
        decision = "需人工复核"
        repairable = False
        suggestion = "疑似区域较多或形态复杂，建议人工复核后再确定是否维修。"
        confidence = min(0.88, 0.76 + min(severity_score, 5.0) * 0.02)

    if risk_level == "高风险" and white_mark_penalty > 1.2 and damage_area_ratio < 1.8:
        disease_type = "疑似严重病害，需人工复核"
        disease_level = "需复核"
        risk_level = "中风险"
        decision = "需人工复核"
        repairable = False
        suggestion = "画面中存在较大白色标线或箭头干扰，系统已降低风险等级，建议人工复核。"
        confidence = min(confidence, 0.84)

    return {
        "disease_type": disease_type,
        "disease_level": disease_level,
        "risk_level": risk_level,
        "confidence": round(float(confidence), 2),
        "suspect_count": int(suspect_count),
        "area_ratio": round(float(damage_area_ratio), 4),
        "decision": decision,
        "repairable": bool(repairable),
        "suggestion": suggestion,
        "severity_score": round(float(severity_score), 4),
        "severity_metrics": {
            "damage_area_ratio": round(float(damage_area_ratio), 4),
            "edge_continuity_score": round(float(edge_continuity_score), 4),
            "exposed_rebar_score": round(float(exposed_rebar_score), 4),
            "dark_gap_score": round(float(dark_gap_score), 4),
            "white_mark_penalty": round(float(white_mark_penalty), 4),
        },
        "roi": {
            "x1": roi_x1,
            "y1": roi_y1,
            "x2": roi_x2,
            "y2": roi_y2,
            "left_edge_x": int(roi_x1 + left_edge),
            "right_edge_x": int(roi_x1 + right_edge),
            "description": "默认覆盖画面中间约 50% 宽度，自动估计伸缩缝左右锚固边缘；重点检测混凝土破碎、剥落、缺角、坑槽和疑似露筋，同时过滤白色箭头、标线和规则盖板阴影。",
        },
        "boxes": boxes,
    }


def make_analysis_image(image_rgb: Image.Image, result: dict[str, Any]) -> Image.Image:
    rgb = np.array(image_rgb.convert("RGB"))
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    roi = result["roi"]

    cv2.rectangle(bgr, (roi["x1"], roi["y1"]), (roi["x2"], roi["y2"]), (0, 215, 255), 3)
    if "left_edge_x" in roi:
        cv2.line(bgr, (roi["left_edge_x"], roi["y1"]), (roi["left_edge_x"], roi["y2"]), (0, 180, 255), 1)
    if "right_edge_x" in roi:
        cv2.line(bgr, (roi["right_edge_x"], roi["y1"]), (roi["right_edge_x"], roi["y2"]), (0, 180, 255), 1)

    for box in result.get("boxes", []):
        cv2.rectangle(bgr, (box["x1"], box["y1"]), (box["x2"], box["y2"]), (0, 0, 255), 2)

    pil = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).convert("RGBA")
    panel = Image.new("RGBA", pil.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    panel_w = min(pil.size[0] - 20, 820)
    draw.rounded_rectangle(
        (10, 10, panel_w, 192),
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
        f"疑似破损：{result['suspect_count']}  面积占比：{result['area_ratio']}%  严重度：{result.get('severity_score', 0)}",
        font=FONT_SMALL,
        fill=(210, 230, 255, 255),
    )
    draw.text(
        (roi["x1"] + 8, max(roi["y1"] - 28, 5)),
        "伸缩缝锚固区双边缘 ROI",
        font=FONT_SMALL,
        fill=(255, 215, 80, 255),
    )
    return Image.alpha_composite(pil, panel).convert("RGB")
