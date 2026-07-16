"""
Agent pipeline.

Each 'agent' is a specialized prompt + retrieval scope over the same
underlying LLM (Claude). This is a legitimate, hackathon-buildable version
of a multi-agent system: specialized roles, shared retrieved context,
sequential handoff, structured JSON contracts between stages.

Pipeline:
  TimelineBuilderAgent -> RootCauseAgent -> ImpactAnalyzerAgent
  -> RemediationAgent -> QualityReviewerAgent -> final assembled report
"""
import json
import os
import re
from anthropic import Anthropic
from rag import retrieve

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
MODEL = "claude-sonnet-4-6"


def _call_llm(system: str, user: str) -> str:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if hasattr(b, "text"))


def _extract_json(text: str) -> dict:
    """Strip markdown fences etc, then parse. Falls back gracefully."""
    cleaned = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return {"raw": text}


def _context_block(incident_id: str, query: str) -> str:
    hits = retrieve(incident_id, query, k=8)
    return "\n\n".join(f"[{meta['type']}] {doc}" for doc, meta in hits)


def timeline_builder_agent(incident_id: str) -> dict:
    context = _context_block(incident_id, "incident timeline sequence of events alerts deploys")
    system = (
        "You are the Timeline Builder Agent in an SRE postmortem system. "
        "Reconstruct a chronological incident timeline strictly from the provided evidence. "
        "Never invent timestamps you cannot support. Respond ONLY with JSON: "
        '{"timeline": [{"time": "...", "event": "...", "source": "alert|deploy|oncall|slack"}]}'
    )
    return _extract_json(_call_llm(system, f"Evidence:\n{context}"))


def root_cause_agent(incident_id: str, timeline: dict) -> dict:
    context = _context_block(incident_id, "error root cause failure deployment change")
    system = (
        "You are the Root Cause Investigator Agent. Given the timeline and evidence, "
        "determine the most likely root cause with supporting reasoning. "
        "Distinguish trigger vs underlying cause. Include a confidence score 0-1. "
        'Respond ONLY with JSON: {"root_cause": "...", "trigger": "...", '
        '"reasoning": "...", "confidence": 0.0}'
    )
    user = f"Timeline:\n{json.dumps(timeline)}\n\nEvidence:\n{context}"
    return _extract_json(_call_llm(system, user))


def impact_analyzer_agent(incident_id: str) -> dict:
    context = _context_block(incident_id, "impact users affected downtime severity services")
    system = (
        "You are the Impact Analyzer Agent. Quantify blast radius from the evidence only. "
        'Respond ONLY with JSON: {"severity": "SEV1|SEV2|SEV3|SEV4", '
        '"services_affected": ["..."], "user_impact": "...", "duration_minutes": 0}'
    )
    return _extract_json(_call_llm(system, f"Evidence:\n{context}"))


def remediation_agent(incident_id: str, root_cause: dict) -> dict:
    context = _context_block(incident_id, "resolution fix mitigation rollback action taken")
    system = (
        "You are the Remediation Planner Agent. Based on the root cause and evidence, "
        "list what was done to resolve it and propose concrete preventive measures "
        "(each actionable, owner-assignable, not generic advice). "
        'Respond ONLY with JSON: {"resolution": "...", '
        '"preventive_measures": [{"action": "...", "priority": "P0|P1|P2"}]}'
    )
    user = f"Root cause:\n{json.dumps(root_cause)}\n\nEvidence:\n{context}"
    return _extract_json(_call_llm(system, user))


def quality_reviewer_agent(report: dict) -> dict:
    system = (
        "You are the Quality Reviewer Agent. Score this draft postmortem for "
        "completeness and internal consistency (do the timeline, root cause, and "
        "impact agree?). "
        'Respond ONLY with JSON: {"completeness_score": 0.0, '
        '"consistency_score": 0.0, "flags": ["..."]}'
    )
    return _extract_json(_call_llm(system, json.dumps(report)))


def run_pipeline(incident_id: str, title: str) -> dict:
    timeline = timeline_builder_agent(incident_id)
    root_cause = root_cause_agent(incident_id, timeline)
    impact = impact_analyzer_agent(incident_id)
    remediation = remediation_agent(incident_id, root_cause)

    draft = {
        "title": title,
        "timeline": timeline.get("timeline", []),
        "root_cause": root_cause,
        "impact": impact,
        "resolution": remediation.get("resolution", ""),
        "preventive_measures": remediation.get("preventive_measures", []),
    }

    review = quality_reviewer_agent(draft)
    draft["quality_review"] = review
    return draft
