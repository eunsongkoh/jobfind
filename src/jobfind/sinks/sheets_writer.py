import gspread

from ..models import ScoredJob

_HEADER = ["title", "company", "location", "link", "date_detected", "score"]


class SheetsWriter:
    """Writes matched jobs to the 'Jobs' worksheet tab. Takes only plain ScoredJob
    objects — no knowledge of which source or LLM provider produced them."""

    def __init__(self, worksheet: gspread.Worksheet):
        self.worksheet = worksheet

    def ensure_header(self) -> None:
        values = self.worksheet.get_all_values()
        if not values:
            self.worksheet.update([_HEADER], "A1")

    def append_rows(self, scored_jobs: list[ScoredJob]) -> None:
        if not scored_jobs:
            return
        rows = [
            [sj.job.title, sj.job.company, sj.job.location, sj.job.job_url, sj.job.date_detected, sj.score]
            for sj in scored_jobs
        ]
        self.worksheet.append_rows(rows, value_input_option="RAW")
