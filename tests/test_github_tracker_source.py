from pathlib import Path
from unittest.mock import patch

from jobfind.config import GithubTrackerEntry, TrackDefinition
from jobfind.sources.github_tracker_source import GithubTrackerSource

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self) -> None:
        pass


def _make_source(fixture_name: str, tracker_name: str = "test_tracker") -> GithubTrackerSource:
    track_def = TrackDefinition(
        jobspy_job_type="fulltime",
        github_trackers=[
            GithubTrackerEntry(name=tracker_name, url=f"https://example.com/{fixture_name}")
        ],
    )
    return GithubTrackerSource(track="new_grad", track_def=track_def, app_config=None)


def test_parses_simplify_style_table_and_skips_closed_role():
    source = _make_source("simplify_sample.md")
    content = (FIXTURES / "simplify_sample.md").read_text()

    with patch(
        "jobfind.sources.github_tracker_source.requests.get",
        return_value=FakeResponse(content),
    ):
        jobs = source.fetch()

    assert len(jobs) == 1
    job = jobs[0]
    assert job.company == "Acme"
    assert job.title == "Software Engineer, New Grad"
    assert job.location == "New York, NY"
    assert job.job_url == "https://apply.acme.com/123"
    assert job.source == "github:test_tracker"
    assert job.track == "new_grad"


def test_parses_vansh_style_table_and_skips_closed_role():
    source = _make_source("vansh_sample.md")
    content = (FIXTURES / "vansh_sample.md").read_text()

    with patch(
        "jobfind.sources.github_tracker_source.requests.get",
        return_value=FakeResponse(content),
    ):
        jobs = source.fetch()

    assert len(jobs) == 1
    job = jobs[0]
    assert job.company == "Beta Corp"
    assert job.job_url == "https://beta.com/jobs/42"
    assert job.date_posted == "Aug 01"


def test_skips_header_and_separator_rows():
    source = _make_source("simplify_sample.md")
    content = "\n".join(
        [
            "| Company | Role | Location | Application | Age |",
            "| ------- | ---- | -------- | ----------- | --- |",
        ]
    )

    with patch(
        "jobfind.sources.github_tracker_source.requests.get",
        return_value=FakeResponse(content),
    ):
        jobs = source.fetch()

    assert jobs == []
