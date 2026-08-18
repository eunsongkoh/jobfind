import re

from .config import TrackDefinition
from .models import Job, ScoredJob

# Common non-US markers so a broad "United States" entry in config.locations acts as
# a permissive default (jobs already scoped to the US by jobspy's own location/country
# params and by the US-focused tracker repos) rather than requiring exact city matches.
_NON_US_MARKERS = (
    "canada",
    "india",
    "united kingdom",
    " uk",
    "germany",
    "france",
    "singapore",
    "australia",
    "mexico",
    "brazil",
)
_US_WILDCARDS = ("united states", "usa", "us")

_SPONSORSHIP_KEYWORDS_EXCLUDE = (
    "will not sponsor",
    "does not sponsor",
    "no sponsorship",
    "unable to sponsor",
    "without sponsorship",
    "security clearance",
    "must obtain clearance",
)
_STAFFING_KEYWORDS_EXCLUDE = (
    "consulting firm",
    "staffing agency",
    "staffing firm",
    "professional services firm",
)
_NOT_REMOTE = re.compile(r"(not|isn't|is not|no)\s+(a\s+)?(corporate,?\s*)?remote", re.I)
_ONSITE_REQUIRED = re.compile(
    r"(on-?site|in-office|in[\s-]person)\s+(requirement|required|only|\d+\s*days?\s*(a|per)\s*week)"
    r"|must (work|be) on-?site",
    re.I,
)
# Postings explicitly requiring at least this many years are excluded. Requires "years"
# to be followed by "experience" (with optional qualifiers) to avoid false positives
# like "our company has been operating for 40 years".
_MIN_YEARS_EXPERIENCE = 3
_EXPERIENCE_YEARS_RE = re.compile(
    r"(\d+)\+?\s*(?:-\s*\d+)?\+?\s*years?\s*(?:of\s+)?"
    r"(?:relevant\s+|professional\s+|industry\s+|working\s+)?experience",
    re.I,
)


def apply_filters(jobs: list[Job], track_def: TrackDefinition, locations: list[str]) -> list[Job]:
    """Shared keyword-include + location filter, applied identically regardless of
    which source produced the job. Silent drop — out of scope for prefiltering, which
    is a separate, stricter pass that still records what it excludes (see
    `prefilter_job`)."""
    include = [k.lower() for k in track_def.keywords_include]
    allowed_locations = [loc.lower() for loc in locations]

    return [
        job
        for job in jobs
        if _include_match(job.title.lower(), include) and _location_matches(job.location, allowed_locations)
    ]


def _include_match(title_lower: str, include: list[str]) -> bool:
    if include and not any(k in title_lower for k in include):
        return False
    return True


def _location_matches(job_location: str, allowed_locations: list[str]) -> bool:
    if not allowed_locations:
        return True

    loc = (job_location or "").lower()

    if "remote" in loc and "remote" in allowed_locations:
        return True

    specific = [a for a in allowed_locations if a not in _US_WILDCARDS]
    if any(a in loc for a in specific):
        return True

    if any(a in allowed_locations for a in _US_WILDCARDS):
        return not any(marker in loc for marker in _NON_US_MARKERS)

    return False


def targets_us_location(locations: list[str]) -> bool:
    return any(loc.lower() in _US_WILDCARDS for loc in locations)


def description_fails(description: str | None, *, check_sponsorship: bool) -> str | None:
    """pre-LLM description checks. Returns the matched reason, or None if the
    description doesn't trip any hard-exclude rule. Sponsorship/clearance language is
    only checked when `check_sponsorship` is True (candidate isn't a US citizen and the
    search targets a US location) — irrelevant otherwise."""
    if not description:
        return None
    desc_lower = description.lower()

    if check_sponsorship:
        matched = next((kw for kw in _SPONSORSHIP_KEYWORDS_EXCLUDE if kw in desc_lower), None)
        if matched:
            return matched

    matched = next((kw for kw in _STAFFING_KEYWORDS_EXCLUDE if kw in desc_lower), None)
    if matched:
        return matched

    if _NOT_REMOTE.search(description):
        return "not remote"
    if _ONSITE_REQUIRED.search(description):
        return "onsite required"

    years_match = _EXPERIENCE_YEARS_RE.search(description)
    if years_match and int(years_match.group(1)) >= _MIN_YEARS_EXPERIENCE:
        return f"requires {years_match.group(1)}+ years experience"

    return None


def prefilter_job(job: Job, track_def: TrackDefinition | None, *, check_sponsorship: bool) -> ScoredJob | None:
    """Hard-excludes a job by title keyword or description pattern before it reaches
    the LLM — cheap, deterministic, saves a scoring call. Matches are still recorded
    (not dropped) with a rationale naming what matched, mirroring score_job()'s own
    ScoredJob-returning convention."""
    if track_def is not None:
        title_lower = job.title.lower()
        for kw in track_def.keywords_exclude:
            if kw.lower() in title_lower:
                return ScoredJob(job=job, score=0, confidence=0, rationale=f"prefiltered: title keyword '{kw}'")

    reason = description_fails(job.description, check_sponsorship=check_sponsorship)
    if reason:
        return ScoredJob(job=job, score=0, confidence=0, rationale=f"prefiltered: {reason}")

    return None
