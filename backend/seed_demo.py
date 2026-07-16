"""
Run this after `uvicorn main:app --reload` is up, to create a demo incident
from the sample_data/ folder and generate a postmortem in one shot.

    python seed_demo.py
"""
import requests

BASE = "http://localhost:8000"

files = {
    "alert_log": "sample_data/alert_log.txt",
    "deploy_record": "sample_data/deploy_record.txt",
    "oncall_note": "sample_data/oncall_notes.txt",
    "slack": "sample_data/slack_thread.txt",
}

artifacts = []
for artifact_type, path in files.items():
    with open(path) as f:
        artifacts.append({"type": artifact_type, "content": f.read()})

resp = requests.post(
    f"{BASE}/api/incidents",
    json={"title": "checkout-service 5xx spike (INC-2291)", "artifacts": artifacts},
)
resp.raise_for_status()
incident_id = resp.json()["incident_id"]
print(f"Created incident: {incident_id}")

print("Generating postmortem (this calls the LLM pipeline, ~10-20s)...")
report_resp = requests.post(f"{BASE}/api/incidents/{incident_id}/generate")
report_resp.raise_for_status()
print("Done. Open the frontend and load incident:", incident_id)
print(report_resp.json())
