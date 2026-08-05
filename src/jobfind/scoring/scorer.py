import json
import logging
import re

from ..config import Profile
from ..models import Job, ScoredJob
from .provider import LLMProvider

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a job-relevance scorer. Given a candidate profile and a single job "
    "posting, respond with ONLY a JSON object of the form "
    '{"score": <integer 0-100>, "reason": "<one sentence>"}. '
    "No markdown, no code fences, no extra text."
)

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_SCORE_FALLBACK_RE = re.compile(r'"?score"?\s*[:=]\s*(\d{1,3})', re.IGNORECASE)


def _build_user_prompt(job: Job, profile: Profile) -> str:
    profile_lines = [
        f"Role target: {', '.join(profile.role_target) or 'unspecified'}",
        f"Skills: {', '.join(profile.skills) or 'unspecified'}",
        f"Preferred locations: {', '.join(profile.locations_preferred) or 'unspecified'}",
        f"Remote acceptable: {profile.locations_acceptable_remote}",
    ]
    if profile.graduation_date:
        profile_lines.append(f"Graduation date: {profile.graduation_date}")
    if profile.keywords_positive:
        profile_lines.append(f"Positive signals: {', '.join(profile.keywords_positive)}")
    if profile.keywords_negative:
        profile_lines.append(f"Negative signals: {', '.join(profile.keywords_negative)}")
    if profile.notes:
        profile_lines.append(f"Notes: {profile.notes}")

    job_lines = [
        f"Title: {job.title}",
        f"Company: {job.company}",
        f"Location: {job.location}",
        f"Track: {job.track}",
    ]
    if job.description:
        job_lines.append(f"Description: {job.description[:2000]}")

    return (
        "CANDIDATE PROFILE:\n"
        + "\n".join(profile_lines)
        + "\n\nJOB POSTING:\n"
        + "\n".join(job_lines)
    )


def _parse_response(text: str) -> tuple[int, str]:
    match = _JSON_OBJECT_RE.search(text)
    if match:
        try:
            data = json.loads(match.group(0))
            score = int(data["score"])
            reason = str(data.get("reason", ""))
            return max(0, min(100, score)), reason
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            pass

    fallback = _SCORE_FALLBACK_RE.search(text)
    if fallback:
        return max(0, min(100, int(fallback.group(1)))), "parsed_from_fallback"

    return 0, "parse_error"


def score_job(job: Job, profile: Profile, provider: LLMProvider, *, max_tokens: int = 200) -> ScoredJob:
    try:
        response = provider.complete(
            _SYSTEM_PROMPT,
            _build_user_prompt(job, profile),
            temperature=0.0,
            max_tokens=max_tokens,
        )
    except Exception:
        logger.exception("scoring call failed for job %s, scoring as 0", job.id)
        return ScoredJob(job=job, score=0, rationale="provider_error")

    score, rationale = _parse_response(response)
    return ScoredJob(job=job, score=score, rationale=rationale)
