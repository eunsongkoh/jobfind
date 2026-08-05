from jobfind.config import TrackDefinition
from jobfind.filters import apply_filters
from jobfind.models import Job


def _job(title="Software Engineer New Grad", location="New York, NY", **overrides) -> Job:
    defaults = dict(
        id="1",
        source="test",
        track="new_grad",
        title=title,
        company="Acme",
        location=location,
        job_url="https://example.com/1",
        date_posted=None,
        date_detected="2026-08-04T00:00:00+00:00",
    )
    defaults.update(overrides)
    return Job(**defaults)


def _track_def(include=None, exclude=None) -> TrackDefinition:
    return TrackDefinition(
        jobspy_job_type="fulltime",
        keywords_include=include or [],
        keywords_exclude=exclude or [],
    )


def test_include_keyword_required():
    track_def = _track_def(include=["new grad"])
    jobs = [_job(title="Software Engineer New Grad"), _job(title="Senior Software Engineer")]

    result = apply_filters(jobs, track_def, [])

    assert len(result) == 1
    assert result[0].title == "Software Engineer New Grad"


def test_exclude_keyword_filters_out():
    track_def = _track_def(exclude=["senior", "staff"])
    jobs = [_job(title="Software Engineer New Grad"), _job(title="Senior Software Engineer")]

    result = apply_filters(jobs, track_def, [])

    assert len(result) == 1
    assert result[0].title == "Software Engineer New Grad"


def test_us_wildcard_allows_us_city_but_blocks_other_country():
    track_def = _track_def()
    jobs = [
        _job(location="Austin, TX"),
        _job(location="Toronto, Canada"),
    ]

    result = apply_filters(jobs, track_def, ["United States"])

    assert len(result) == 1
    assert result[0].location == "Austin, TX"


def test_remote_matches_remote_allowance():
    track_def = _track_def()
    jobs = [_job(location="Remote")]

    result = apply_filters(jobs, track_def, ["Remote"])

    assert len(result) == 1


def test_no_locations_configured_passes_everything():
    track_def = _track_def()
    jobs = [_job(location="Toronto, Canada")]

    result = apply_filters(jobs, track_def, [])

    assert len(result) == 1
