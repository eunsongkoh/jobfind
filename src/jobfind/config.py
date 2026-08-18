from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class GithubTrackerEntry(BaseModel):
    name: str
    url: str


class TrackDefinition(BaseModel):
    jobspy_job_type: str
    keywords_include: list[str] = Field(default_factory=list)
    keywords_exclude: list[str] = Field(default_factory=list)
    github_trackers: list[GithubTrackerEntry] = Field(default_factory=list)


class TracksConfig(BaseModel):
    active: list[str]
    definitions: dict[str, TrackDefinition]


class JobspyConfig(BaseModel):
    enabled_sites: list[str]
    results_wanted: int = 20
    hours_old: int = 3
    country_indeed: str = "USA"


class TargetCompany(BaseModel):
    name: str
    ats: str
    identifier: str


class DedupConfig(BaseModel):
    retention_days: int = 120


class ScoringConfig(BaseModel):
    provider: str = "google"
    model: str
    score_threshold: int = 70
    # None = provider-specific default (e.g. GoogleAIProvider's own default
    # base_url) — only set this to override that default.
    api_base: str | None = None
    max_tokens: int = 200
    # Optional secondary provider, used only once the primary's own retries
    # are exhausted (e.g. a Gemini rate limit). Leave fallback_provider unset
    # to keep today's Gemini-only behavior.
    fallback_provider: str | None = None
    fallback_model: str | None = None
    fallback_api_base: str | None = None
    fallback_api_key: str | None = None


class SheetsConfig(BaseModel):
    sheet_id: str
    jobs_worksheet: str = "Jobs"
    seen_worksheet: str = "SeenJobs"
    rejected_worksheet: str = "RejectedJobs"


class AppConfig(BaseModel):
    tracks: TracksConfig
    locations: list[str] = Field(default_factory=list)
    active_sources: list[str]
    jobspy: JobspyConfig
    target_companies: list[TargetCompany] = Field(default_factory=list)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    scoring: ScoringConfig
    sheets: SheetsConfig


class Profile(BaseModel):
    # "personalized" = precise, selective recommendations weighted toward stated
    # preferences. "broad" = high-recall, favors transferable-skill/potential fit
    # over exact preference alignment. See scoring/scorer.py's system prompt for
    # how each mode changes the LLM's scoring behavior.
    recommendation_mode: Literal["personalized", "broad"] = "personalized"
    role_target: list[str] = Field(default_factory=list)
    graduation_date: str | None = None
    skills: list[str] = Field(default_factory=list)
    keywords_positive: list[str] = Field(default_factory=list)
    keywords_negative: list[str] = Field(default_factory=list)
    locations_preferred: list[str] = Field(default_factory=list)
    locations_acceptable_remote: bool = True
    # False = the candidate is not a US citizen. Only relevant when config.locations
    # targets the US — gates whether description_fails() checks US-specific
    # sponsorship/clearance language (see filters.py).
    us_citizen: bool = True
    notes: str | None = None


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    path = Path(path)
    with path.open() as f:
        raw = yaml.safe_load(f)
    return AppConfig.model_validate(raw)


def load_profile(path: str | Path = "profile.yaml") -> Profile:
    path = Path(path)
    with path.open() as f:
        raw = yaml.safe_load(f)
    return Profile.model_validate(raw)
