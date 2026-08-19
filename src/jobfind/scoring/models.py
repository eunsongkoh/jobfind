import re

from pydantic import BaseModel, ConfigDict, Field

from ..config import Profile
from ..models import Job

_INLINE_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _normalize_description(text: str) -> str:
    """Collapses scraped-HTML whitespace padding (repeated spaces/tabs, runs
    of blank lines) so the prompt's char budget goes toward real content
    instead of formatting noise — this doesn't change what the description
    says, only how many tokens it costs to say it."""
    text = _INLINE_WHITESPACE_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


class CandidateProfile(BaseModel):
    """Prompt-ready view of a candidate Profile — one field per variable the
    user-prompt template interpolates."""

    recommendation_mode: str
    role_target: str
    skills: str
    locations_preferred: str
    locations_acceptable_remote: bool
    graduation_date: str | None = None
    keywords_positive: str | None = None
    keywords_negative: str | None = None
    notes: str | None = None

    @classmethod
    def from_profile(cls, profile: Profile) -> "CandidateProfile":
        return cls(
            recommendation_mode=profile.recommendation_mode,
            role_target=", ".join(profile.role_target) or "unspecified",
            skills=", ".join(profile.skills) or "unspecified",
            locations_preferred=", ".join(profile.locations_preferred) or "unspecified",
            locations_acceptable_remote=profile.locations_acceptable_remote,
            graduation_date=profile.graduation_date,
            keywords_positive=", ".join(profile.keywords_positive) or None,
            keywords_negative=", ".join(profile.keywords_negative) or None,
            notes=profile.notes,
        )


class JobPosting(BaseModel):
    """Prompt-ready view of a Job — one field per variable the user-prompt
    template interpolates."""

    title: str
    company: str
    location: str
    track: str
    description: str | None = None

    @classmethod
    def from_job(cls, job: Job, *, max_description_chars: int = 2000) -> "JobPosting":
        description = _normalize_description(job.description)[:max_description_chars] if job.description else None
        return cls(
            title=job.title,
            company=job.company,
            location=job.location,
            track=job.track,
            description=description,
        )


class ScoreResponse(BaseModel):
    # No docstring here on purpose — pydantic promotes a class docstring to the
    # schema's top-level "description", which gets sent to the model as part of
    # response_format; keep this class's own doc out of that payload.
    # extra="forbid" makes model_json_schema() emit additionalProperties: false,
    # required for provider strict-schema/structured-output modes.
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=100, description="Overall recommendation strength from 0 (poor) to 100 (excellent)")
    confidence: int = Field(
        ge=0,
        le=100,
        description="Confidence in this recommendation given how much candidate/job information was available — not a measure of fit",
    )
    reason: str = Field(description="One concise sentence citing the strongest factors behind the recommendation")
