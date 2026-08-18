from jobfind.config import TrackDefinition
from jobfind.filters import apply_filters, description_fails, prefilter_job, targets_us_location
from jobfind.models import Job


def _job(title="Software Engineer New Grad", location="New York, NY", description=None, **overrides) -> Job:
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
        description=description,
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


def test_prefilter_job_title_exclude_match():
    track_def = _track_def(exclude=["senior", "staff"])
    job = _job(title="Senior Software Engineer")

    result = prefilter_job(job, track_def, check_sponsorship=False)

    assert result is not None
    assert result.score == 0
    assert "senior" in result.rationale


def test_prefilter_job_passes_clean_job():
    track_def = _track_def(exclude=["senior", "staff"])
    job = _job(title="Software Engineer New Grad", description="A great role.")

    result = prefilter_job(job, track_def, check_sponsorship=False)

    assert result is None


def test_prefilter_job_none_track_def_still_checks_description():
    job = _job(description="We are a staffing agency placing candidates.")

    result = prefilter_job(job, None, check_sponsorship=False)

    assert result is not None
    assert "staffing agency" in result.rationale


def test_description_fails_sponsorship_only_checked_when_flagged():
    description = "Note: we will not sponsor work visas for this role."

    assert description_fails(description, check_sponsorship=True) == "will not sponsor"
    assert description_fails(description, check_sponsorship=False) is None


def test_description_fails_staffing_keywords_unconditional():
    assert description_fails("We are a staffing agency.", check_sponsorship=False) == "staffing agency"


def test_description_fails_not_remote_regex():
    assert description_fails("This is not a remote position.", check_sponsorship=False) is not None
    assert description_fails("This is a fully remote position.", check_sponsorship=False) is None


def test_description_fails_onsite_required_regex():
    assert description_fails("Onsite required 3 days a week.", check_sponsorship=False) is not None


def test_description_fails_experience_years_threshold():
    assert description_fails("Requires 1-2 years of experience.", check_sponsorship=False) is None
    result = description_fails("Requires 5+ years of experience.", check_sponsorship=False)
    assert result == "requires 5+ years experience"


def test_description_fails_experience_years_ignores_unrelated_years():
    assert description_fails("Our company has been operating for 40 years.", check_sponsorship=False) is None


def test_targets_us_location():
    assert targets_us_location(["United States", "Canada"])
    assert not targets_us_location(["Canada", "Remote"])
