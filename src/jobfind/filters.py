from .config import TrackDefinition
from .models import Job

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


def apply_filters(jobs: list[Job], track_def: TrackDefinition, locations: list[str]) -> list[Job]:
    """Shared keyword include/exclude + location filter, applied identically
    regardless of which source produced the job."""
    include = [k.lower() for k in track_def.keywords_include]
    exclude = [k.lower() for k in track_def.keywords_exclude]
    allowed_locations = [loc.lower() for loc in locations]

    return [
        job
        for job in jobs
        if _keyword_match(job.title.lower(), include, exclude)
        and _location_matches(job.location, allowed_locations)
    ]


def _keyword_match(title_lower: str, include: list[str], exclude: list[str]) -> bool:
    if include and not any(k in title_lower for k in include):
        return False
    if exclude and any(k in title_lower for k in exclude):
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
