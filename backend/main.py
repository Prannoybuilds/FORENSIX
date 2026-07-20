import os
import json
from collections import Counter, defaultdict
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List
import db
import rag
import agents

app = FastAPI(title="Incident Postmortem Generator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db.init_db()

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(BACKEND_DIR, "sample_data")
FRONTEND_DIR = os.path.join(BACKEND_DIR, "..", "frontend")


class ArtifactIn(BaseModel):
    type: str  # alert_log | deploy_record | oncall_note | slack | metric
    content: str


class IncidentIn(BaseModel):
    title: str
    artifacts: List[ArtifactIn]


@app.post("/api/incidents")
def create_incident(payload: IncidentIn):
    incident_id = db.create_incident(payload.title)
    for a in payload.artifacts:
        artifact_id = db.add_artifact(incident_id, a.type, a.content)
        rag.index_artifact(incident_id, artifact_id, a.type, a.content)
    return {"incident_id": incident_id}


@app.get("/api/incidents")
def list_incidents():
    return db.list_incidents()


@app.post("/api/incidents/{incident_id}/generate")
def generate_report(incident_id: str):
    artifacts = db.get_artifacts(incident_id)
    if not artifacts:
        raise HTTPException(404, "No artifacts found for this incident")
    incidents = {i["id"]: i for i in db.list_incidents()}
    title = incidents.get(incident_id, {}).get("title", "Untitled Incident")

    report = agents.run_pipeline(incident_id, title)
    db.save_report(incident_id, report)
    return report


@app.get("/api/incidents/{incident_id}/generate/stream")
def generate_report_stream(incident_id: str):
    """Server-Sent Events version of /generate. Emits one JSON event per
    agent (running -> done) as the pipeline actually progresses, then a
    final {"event": "complete", "report": {...}} event once everything is
    assembled and saved. The frontend reads this via EventSource so the
    pipeline UI reflects real backend progress instead of a paced estimate.
    """
    artifacts = db.get_artifacts(incident_id)
    if not artifacts:
        raise HTTPException(404, "No artifacts found for this incident")
    incidents = {i["id"]: i for i in db.list_incidents()}
    title = incidents.get(incident_id, {}).get("title", "Untitled Incident")

    def event_generator():
        try:
            for update in agents.run_pipeline_stream(incident_id, title):
                if update.get("event") == "complete":
                    db.save_report(incident_id, update["report"])
                yield f"data: {json.dumps(update)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/incidents/{incident_id}/report")
def get_report(incident_id: str):
    report = db.get_latest_report(incident_id)
    if not report:
        raise HTTPException(404, "No report generated yet")
    return report


@app.get("/api/incidents/{incident_id}")
def get_incident_detail(incident_id: str):
    incident = db.get_incident(incident_id)
    if not incident:
        raise HTTPException(404, "Incident not found")
    incident["artifacts"] = db.get_artifacts(incident_id)
    incident["report"] = db.get_latest_report(incident_id)
    return incident


@app.get("/api/sample-data")
def sample_data():
    """Reads the real sample artifact files bundled with the repo so the
    'New Investigation' UI can one-click load a realistic demo incident
    instead of the user typing logs by hand."""
    mapping = {
        "alert_log": "alert_log.txt",
        "deploy_record": "deploy_record.txt",
        "oncall_note": "oncall_notes.txt",
        "slack": "slack_thread.txt",
    }
    out = {}
    for artifact_type, filename in mapping.items():
        path = os.path.join(SAMPLE_DIR, filename)
        if os.path.exists(path):
            with open(path) as f:
                out[artifact_type] = f.read()
    return out


@app.get("/api/analytics")
def analytics():
    """Aggregates real rows from the reports/incidents tables into the shapes
    the Analytics dashboard's charts and heatmap need. Nothing here is mocked
    — an empty database returns empty/zeroed structures."""
    reports = db.list_all_reports()
    incidents = db.list_incidents()

    severity_counts = Counter()
    confidences = []
    completeness = []
    consistency = []
    by_day = defaultdict(int)

    for r in reports:
        rep = r.get("report", {}) or {}
        impact = rep.get("impact", {}) or {}
        root_cause = rep.get("root_cause", {}) or {}
        quality = rep.get("quality_review", {}) or {}

        sev = (impact.get("severity") or "UNKNOWN").upper()
        severity_counts[sev] += 1

        conf = root_cause.get("confidence")
        if isinstance(conf, (int, float)):
            confidences.append(conf)

        comp = quality.get("completeness_score")
        if isinstance(comp, (int, float)):
            completeness.append(comp)

        cons = quality.get("consistency_score")
        if isinstance(cons, (int, float)):
            consistency.append(cons)

        day = datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m-%d")
        by_day[day] += 1

    def avg(xs):
        return round(sum(xs) / len(xs), 3) if xs else None

    return {
        "total_incidents": len(incidents),
        "total_reports": len(reports),
        "severity_counts": dict(severity_counts),
        "avg_confidence": avg(confidences),
        "avg_completeness": avg(completeness),
        "avg_consistency": avg(consistency),
        "incidents_by_day": [{"date": d, "count": c} for d, c in sorted(by_day.items())],
        "recent_reports": [
            {
                "incident_id": r["incident_id"],
                "title": r["title"],
                "severity": (r.get("report", {}).get("impact", {}) or {}).get("severity"),
                "confidence": (r.get("report", {}).get("root_cause", {}) or {}).get("confidence"),
                "completeness": (r.get("report", {}).get("quality_review", {}) or {}).get("completeness_score"),
                "created_at": r["created_at"],
            }
            for r in reports[:10]
        ],
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Serve the frontend (frontend/index.html) at "/" from the same FastAPI
# process, so the whole thing — API + UI — is one site on one port with
# no CORS setup needed. Declared last so it never shadows the /api/* routes
# above.
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
