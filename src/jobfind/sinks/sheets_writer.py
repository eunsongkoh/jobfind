import gspread

from ..models import ScoredJob

_HEADER = [
    "title",
    "company",
    "location",
    "link",
    "date_detected",
    "description",
    "date_posted",
    "score",
    "confidence",
    "rationale",
]

_DESCRIPTION_LIMIT = 3000


def _truncate(text: str | None, limit: int = _DESCRIPTION_LIMIT) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


class SheetsWriter:
    """Writes scored jobs to a worksheet tab. Takes only plain ScoredJob objects —
    no knowledge of which source or LLM provider produced them — so the same
    writer works for both the 'Jobs' (accepted) and 'RejectedJobs' tabs; the
    caller decides which ScoredJobs go where and which worksheet to point it at."""

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
                _truncate(sj.job.description),
                sj.job.date_posted or "",
                sj.score,
                sj.confidence,
                sj.rationale,
            ]
            for sj in scored_jobs
        ]
        # USER_ENTERED (not RAW) so the link column is parsed the way Sheets parses a
        # human-typed URL, which makes it clickable instead of inert plain text.
        self.worksheet.append_rows(rows, value_input_option="USER_ENTERED")
