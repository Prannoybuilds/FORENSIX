"""
Lightweight SQLite persistence layer.
In the pitch deck: "SQLite for the hackathon demo, PostgreSQL in production."
"""
import sqlite3
import json
import time
import uuid
from contextlib import contextmanager

DB_PATH = "postmortem.db"


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY,
                title TEXT,
                status TEXT DEFAULT 'ingested',
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                incident_id TEXT,
                type TEXT,
                content TEXT,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY,
                incident_id TEXT,
                report_json TEXT,
                created_at REAL
            );
            """
        )


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def create_incident(title: str) -> str:
    incident_id = str(uuid.uuid4())[:8]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO incidents (id, title, created_at) VALUES (?, ?, ?)",
            (incident_id, title, time.time()),
        )
    return incident_id


def add_artifact(incident_id: str, artifact_type: str, content: str) -> str:
    artifact_id = str(uuid.uuid4())[:8]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO artifacts (id, incident_id, type, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (artifact_id, incident_id, artifact_type, content, time.time()),
        )
    return artifact_id


def get_artifacts(incident_id: str):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM artifacts WHERE incident_id = ?", (incident_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def save_report(incident_id: str, report: dict) -> str:
    report_id = str(uuid.uuid4())[:8]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO reports (id, incident_id, report_json, created_at) VALUES (?, ?, ?, ?)",
            (report_id, incident_id, json.dumps(report), time.time()),
        )
        conn.execute(
            "UPDATE incidents SET status = 'generated' WHERE id = ?", (incident_id,)
        )
    return report_id


def get_latest_report(incident_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM reports WHERE incident_id = ? ORDER BY created_at DESC LIMIT 1",
            (incident_id,),
        ).fetchone()
    return dict(row) if row else None


def list_incidents():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM incidents ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_incident(incident_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()
    return dict(row) if row else None


def list_all_reports():
    """Every report joined with its incident title, newest first. Used by /api/analytics."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT reports.id as report_id, reports.incident_id, reports.report_json,
                   reports.created_at, incidents.title, incidents.status
            FROM reports
            JOIN incidents ON incidents.id = reports.incident_id
            ORDER BY reports.created_at DESC
            """
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["report"] = json.loads(d.pop("report_json"))
        except (json.JSONDecodeError, TypeError):
            d["report"] = {}
            d.pop("report_json", None)
        out.append(d)
    return out
