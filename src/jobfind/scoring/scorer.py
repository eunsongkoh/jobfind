import json
import logging
import re

from pydantic import ValidationError

from ..config import Profile
from ..models import Job, ScoredJob
from .models import CandidateProfile, JobPosting, ScoreResponse
from .provider import LLMProvider

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a job recommendation engine. Estimate how likely the candidate would reasonably want to apply to this job, based on both their qualifications and preferences — not whether they are perfectly qualified.

Recommendation mode "personalized": prioritize precision over recall. Prefer jobs matching the candidate's stated preferences; penalize mismatched location, negative keywords, and conflicting notes.
Recommendation mode "broad": prioritize recall over precision. Recommend jobs the candidate could reasonably want even if imperfect; weigh transferable skills; don't heavily penalize missing technologies or imperfect preference alignment.

Consider: target roles, technical and transferable skills, location, remote preference, graduation date/experience level, positive and negative keywords, additional notes. Do not require every listed skill; treat equivalent technologies as transferable. If information is missing, reduce confidence rather than assuming a poor match. Never invent facts.

Scoring guide: 90-100 excellent, 80-89 strong, 70-79 worth recommending, 60-69 possible, 40-59 weak, 0-39 poor.

Return ONLY valid JSON matching this schema: {"score": integer, "confidence": integer, "reason": string}. The reason must be one sentence under 30 words citing the strongest matching factors."""

_RESPONSE_FORMAT = ScoreResponse.model_json_schema()

_USER_PROMPT_TEMPLATE = """Evaluate the following candidate against the job posting.

CANDIDATE
Recommendation Mode: {profile.recommendation_mode}
Target Roles: {profile.role_target}
Skills: {profile.skills}
Preferred Locations: {profile.locations_preferred}
Remote Accepted: {profile.locations_acceptable_remote}
Graduation Date: {profile.graduation_date}
Positive Keywords: {profile.keywords_positive}
Negative Keywords: {profile.keywords_negative}
Additional Notes: {profile.notes}

JOB POSTING
Title: {job.title}
Company: {job.company}
Location: {job.location}
Track: {job.track}
Description:
{job.description}
"""

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_SCORE_FALLBACK_RE = re.compile(r'"?score"?\s*[:=]\s*(\d{1,3})', re.IGNORECASE)


def _build_user_prompt(job: Job, profile: Profile) -> str:
    candidate = CandidateProfile.from_profile(profile)
    candidate = candidate.model_copy(
        update={
            "graduation_date": candidate.graduation_date or "not specified",
            "keywords_positive": candidate.keywords_positive or "none",
            "keywords_negative": candidate.keywords_negative or "none",
            "notes": candidate.notes or "none",
        }
    )
    posting = JobPosting.from_job(job)
    posting = posting.model_copy(update={"description": posting.description or "not available"})

    return _USER_PROMPT_TEMPLATE.format(profile=candidate, job=posting)


def _parse_response(text: str) -> tuple[int, int, str]:
    try:
        parsed = ScoreResponse.model_validate_json(text)
        return parsed.score, parsed.confidence, parsed.reason
    except (ValidationError, ValueError):
        pass

    # if pydantic validation fails
    match = _JSON_OBJECT_RE.search(text)
    if match:
        try:
            data = json.loads(match.group(0))
            score = int(data["score"])
            confidence = int(data.get("confidence", 0))
            reason = str(data.get("reason", ""))
            return max(0, min(100, score)), max(0, min(100, confidence)), reason
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            pass

    fallback = _SCORE_FALLBACK_RE.search(text)
    if fallback:
        return max(0, min(100, int(fallback.group(1)))), 0, "parsed_from_fallback"

    return 0, 0, "parse_error"


def score_job(job: Job, profile: Profile, provider: LLMProvider, *, max_tokens: int = 200) -> ScoredJob:
    """Never raises — a failed provider call or an unparseable/malformed
    response both score the job as 0 rather than propagating, so one bad
    response can't take down the whole batch (pipeline.py scores every
    to-score job in a single comprehension; one uncaught exception there
    would discard every already-scored job alongside it)."""
    try:
        response = provider.complete(
            _SYSTEM_PROMPT,
            _build_user_prompt(job, profile),
            temperature=0.0,
            max_tokens=max_tokens,
            response_format=_RESPONSE_FORMAT,
        )
        score, confidence, rationale = _parse_response(response)
    except Exception:
        logger.exception("scoring failed for job %s, scoring as 0", job.id)
        return ScoredJob(job=job, score=0, confidence=0, rationale="provider_error")

    return ScoredJob(job=job, score=score, confidence=confidence, rationale=rationale)
