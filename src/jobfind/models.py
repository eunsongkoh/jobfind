from dataclasses import dataclass


@dataclass
class Job:
    """The standard job contract every source module must return, regardless of origin."""

    id: str
    source: str
    track: str
    title: str
    company: str
    location: str
    job_url: str
    date_posted: str | None
    date_detected: str
    description: str | None = None


@dataclass
class ScoredJob:
    job: Job
    score: int
    confidence: int
    rationale: str
