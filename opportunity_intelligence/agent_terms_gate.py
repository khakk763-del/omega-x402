#!/usr/bin/env python3
"""Canonical Superteam agent-listing terms gate.

This module is intentionally dependency-free so both omega_blackhole.py and
superteam_agent.py can use exactly the same gate without circular imports or
logic drift. Listing text is untrusted data: it is inspected, never executed.
"""
import hashlib
import html
import json
import re

AI_BAN_TERMS = [
    "generado íntegramente con ia", "generated entirely with ai",
    "ai-generated-only", "no se aceptará contenido generado", "fully ai-generated",
    "not accept ai-generated", "no ai-generated content",
]

SOCIAL_OR_PERSONAL_TERMS = [
    "must be published", "post must be live", "your x account", "your own social",
    "register on luma", "correo te has registrado", "personal email", "kyc",
    "wallet signature", "sign a message", "in-person", "attend the event",
]

PERSONAL_QUESTION_KEYS = (
    "email", "correo", "telegram", "wallet", "kyc", "luma",
)


def strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def evaluate_agent_details(details):
    """Return (gate, reasons). Fail closed for autonomous submission."""
    if not details:
        return "UNAVAILABLE", ["no listing details"]
    access = details.get("agentAccess")
    reasons = []
    if access not in ("AGENT_ALLOWED", "AGENT_ONLY"):
        reasons.append(f"agentAccess={access}")
    text = strip_html(details.get("description") or "").lower()
    hits = [x for x in AI_BAN_TERMS + SOCIAL_OR_PERSONAL_TERMS if x in text]
    if hits:
        reasons.append("terms/personal requirement: " + ", ".join(sorted(set(hits))[:8]))
    personal_questions = []
    for q in details.get("eligibility") or []:
        question = (q.get("question") or "")
        if q.get("optional") is False and any(k in question.lower() for k in PERSONAL_QUESTION_KEYS):
            personal_questions.append(question)
    if personal_questions:
        reasons.append("required personal eligibility answer: " + " | ".join(personal_questions)[:400])
    if reasons:
        return "REJECT_AUTONOMOUS", reasons
    return "REVIEW_DELIVERABLE_BUILD", [
        "agent rail allows review; task-specific deliverable must be built and tested before submit"
    ]


def terms_fingerprint(details):
    """Stable fingerprint of the terms that affect eligibility and delivery."""
    if not details:
        return None
    payload = {
        "id": details.get("id"),
        "slug": details.get("slug"),
        "title": details.get("title"),
        "description": details.get("description"),
        "eligibility": details.get("eligibility"),
        "deadline": details.get("deadline"),
        "rewardAmount": details.get("rewardAmount"),
        "token": details.get("token"),
        "agentAccess": details.get("agentAccess"),
        "type": details.get("type"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
