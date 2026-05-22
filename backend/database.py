import json
import sqlite3
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "qzfw_cloud.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
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


def insert_detection(result: dict[str, Any]) -> None:
    conn = get_conn()
    conn.execute(
        """
        INSERT OR REPLACE INTO detections (
            id, created_at, raw_filename, analysis_filename,
            disease_type, disease_level, risk_level, confidence,
            suspect_count, area_ratio, decision, repairable,
            suggestion, roi_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result["id"], result["created_at"], result["raw_filename"], result["analysis_filename"],
            result["disease_type"], result["disease_level"], result["risk_level"], float(result["confidence"]),
            int(result["suspect_count"]), float(result["area_ratio"]), result["decision"],
            1 if result["repairable"] else 0, result["suggestion"], json.dumps(result["roi"], ensure_ascii=False),
        ),
    )
    conn.commit()
    conn.close()


def insert_recheck(result: dict[str, Any]) -> None:
    conn = get_conn()
    conn.execute(
        """
        INSERT OR REPLACE INTO rechecks (
            id, detection_id, created_at, after_filename,
            after_analysis_filename, before_area_ratio,
            after_area_ratio, improvement_rate,
            acceptance_result, suggestion
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result["id"], result["detection_id"], result["created_at"], result["after_filename"],
            result["after_analysis_filename"], float(result["before_area_ratio"]), float(result["after_area_ratio"]),
            float(result["improvement_rate"]), result["acceptance_result"], result["suggestion"],
        ),
    )
    conn.commit()
    conn.close()


def get_detection(detect_id: str) -> sqlite3.Row | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM detections WHERE id = ?", (detect_id,)).fetchone()
    conn.close()
    return row


def get_latest_detections(limit: int = 50) -> list[sqlite3.Row]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM detections ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return rows


def get_rechecks(detect_id: str) -> list[sqlite3.Row]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM rechecks WHERE detection_id = ? ORDER BY created_at DESC", (detect_id,)).fetchall()
    conn.close()
    return rows
