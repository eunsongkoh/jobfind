from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from gspread.utils import a1_range_to_grid_range

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
_FIXED_ROW_HEIGHT_PX = 21  # Sheets' default single-line row height

_PACIFIC = ZoneInfo("America/Los_Angeles")


def _truncate(text: str | None, limit: int = _DESCRIPTION_LIMIT) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _format_detected(iso_string: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_string).astimezone(_PACIFIC)
    except ValueError:
        return iso_string
    return dt.strftime("%Y-%m-%d %I:%M %p %Z")


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
                _format_detected(sj.job.date_detected),
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
        res = self.worksheet.append_rows(rows, value_input_option="USER_ENTERED")
        self._keep_new_rows_compact(res["updates"]["updatedRange"])

    def _keep_new_rows_compact(self, updated_range: str) -> None:
        """Runs right after append_rows(). Scoped to exactly the row/column range that
        call just wrote (from the API's own response), so pre-existing rows — including
        any a user has manually resized — are never touched.

        wrapStrategy=CLIP stops Sheets from treating embedded newlines in the
        description/rationale columns as needing a taller row (it also stops the height
        from being forced open again on a later edit/reflow, unlike leaving WRAP in
        place). The explicit pixelSize pins the exact height rather than relying on
        whatever height CLIP's auto-sizing happens to settle on."""
        grid_range = a1_range_to_grid_range(updated_range.split("!")[-1], self.worksheet.id)
        row_range = {
            "sheetId": self.worksheet.id,
            "dimension": "ROWS",
            "startIndex": grid_range["startRowIndex"],
            "endIndex": grid_range["endRowIndex"],
        }
        self.worksheet.spreadsheet.batch_update(
            {
                "requests": [
                    {
                        "repeatCell": {
                            "range": grid_range,
                            "cell": {"userEnteredFormat": {"wrapStrategy": "CLIP"}},
                            "fields": "userEnteredFormat.wrapStrategy",
                        }
                    },
                    {
                        "updateDimensionProperties": {
                            "range": row_range,
                            "properties": {"pixelSize": _FIXED_ROW_HEIGHT_PX},
                            "fields": "pixelSize",
                        }
                    },
                ]
            }
        )
