from jobfind.models import Job
from jobfind.scoring.models import JobPosting


def _job(description: str | None) -> Job:
    return Job(
        id="1",
        source="test",
        track="new_grad",
        title="Software Engineer",
        company="Acme",
        location="Remote",
        job_url="https://example.com/1",
        date_posted=None,
        date_detected="2026-08-04T00:00:00+00:00",
        description=description,
    )


def test_from_job_collapses_repeated_inline_whitespace():
    posting = JobPosting.from_job(_job("Build   things\twith\t  us"))

    assert posting.description == "Build things with us"


def test_from_job_collapses_runs_of_blank_lines():
    posting = JobPosting.from_job(_job("Role summary\n\n\n\n\nRequirements"))

    assert posting.description == "Role summary\n\nRequirements"


def test_from_job_strips_leading_and_trailing_whitespace():
    posting = JobPosting.from_job(_job("  \n  Great role  \n  "))

    assert posting.description == "Great role"


def test_from_job_truncates_after_normalizing():
    posting = JobPosting.from_job(_job("a" * 10 + "   " + "b" * 10), max_description_chars=15)

    assert posting.description == "a" * 10 + " " + "b" * 4


def test_from_job_handles_missing_description():
    posting = JobPosting.from_job(_job(None))

    assert posting.description is None
