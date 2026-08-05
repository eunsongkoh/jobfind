import gspread

from ..models import ScoredJob

_HEADER = ["title", "company", "location", "link", "date_detected", "score", "rationale", "description", "date_posted"]

_DESCRIPTION_LIMIT = 3000


def _truncate(text: str | None, limit: int = _DESCRIPTION_LIMIT) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


class SheetsWriter:
    """Writes matched jobs to the 'Jobs' worksheet tab. Takes only plain ScoredJob
    objects — no knowledge of which source or LLM provider produced them."""

    def __init__(self, worksheet: gspread.Worksheet):
        self.worksheet = worksheet

    def ensure_header(self) -> None:
        values = self.worksheet.get_all_values()
        if not values or values[0] != _HEADER:
            self.worksheet.update([_HEADER], "A1")

    def append_rows(self, scored_jobs: list[ScoredJob]) -> None:
        if not scored_jobs:
            return
        rows = [
            [
                sj.job.title,
                sj.job.company,
                sj.job.location,
                sj.job.job_url,
                sj.job.date_detected,
                sj.score,
                sj.rationale,
                _truncate(sj.job.description),
                sj.job.date_posted or "",
            ]
            for sj in scored_jobs
        ]
        # USER_ENTERED (not RAW) so the link column is parsed the way Sheets parses a
        # human-typed URL, which makes it clickable instead of inert plain text.
        self.worksheet.append_rows(rows, value_input_option="USER_ENTERED")
