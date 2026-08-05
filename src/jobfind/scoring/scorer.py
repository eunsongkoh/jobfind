import json
import logging
import re

from pydantic import ValidationError

from ..config import Profile
from ..models import Job, ScoredJob
from .models import CandidateProfile, JobPosting, ScoreResponse
from .provider import LLMProvider

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a job recommendation engine.

Your task is to determine how worthwhile it is to recommend a job posting to a candidate.

Your goal is NOT to determine whether the candidate is perfectly qualified.

Instead, estimate the likelihood that the candidate would reasonably want to apply for this job based on both their qualifications and preferences.

Recommendation Mode:

If the recommendation mode is "personalized":
- Prioritize precision over recall.
- Prefer jobs that closely align with the candidate's stated preferences.
- Penalize mismatched locations, negative keywords, and conflicting notes.

If the recommendation mode is "broad":
- Prioritize recall over precision.
- Recommend jobs that the candidate could reasonably be interested in even if they are not perfect matches.
- Consider transferable skills.
- Do not heavily penalize missing technologies or imperfect preference alignment.

When evaluating, consider:

- Target roles
- Technical skills
- Transferable skills
- Location
- Remote preference
- Graduation date or experience level
- Positive keywords
- Negative keywords
- Additional notes

Do not require every listed skill.

Treat equivalent technologies as transferable when appropriate.

If information is missing, reduce confidence rather than assuming a poor match.

Never invent facts.

Scoring Guide:

90-100
Excellent recommendation.

80-89
Strong recommendation.

70-79
Worth recommending.

60-69
Possible recommendation.

40-59
Weak recommendation.

0-39
Poor recommendation.

Return ONLY valid JSON.

Schema:

{
  "score": integer,
  "confidence": integer,
  "reason": string
}

The reason must be one sentence under 30 words and mention the strongest matching factors."""

_RESPONSE_FORMAT = ScoreResponse.model_json_schema()

_USER_PROMPT_TEMPLATE = """
Evaluate the following candidate against the job posting.

CANDIDATE

Recommendation Mode:
{profile.recommendation_mode}

Target Roles:
{profile.role_target}

Skills:
{profile.skills}

Preferred Locations:
{profile.locations_preferred}

Remote Accepted:
{profile.locations_acceptable_remote}

Graduation Date:
{profile.graduation_date}

Positive Keywords:
{profile.keywords_positive}

Negative Keywords:
{profile.keywords_negative}

Additional Notes:
{profile.notes}

----------------------------------------

JOB POSTING

Title:
{job.title}

Company:
{job.company}

Location:
{job.location}

Track:
{job.track}

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
    try:
        response = provider.complete(
            _SYSTEM_PROMPT,
            _build_user_prompt(job, profile),
            temperature=0.0,
            max_tokens=max_tokens,
            response_format=_RESPONSE_FORMAT,
        )
    except Exception:
        logger.exception("scoring call failed for job %s, scoring as 0", job.id)
        return ScoredJob(job=job, score=0, confidence=0, rationale="provider_error")

    score, confidence, rationale = _parse_response(response)
    return ScoredJob(job=job, score=score, confidence=confidence, rationale=rationale)
