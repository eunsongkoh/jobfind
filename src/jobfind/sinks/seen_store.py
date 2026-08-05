import datetime

import gspread

_HEADER = ["source", "track", "job_id", "first_seen"]


class SeenStore:
    """Dedup state backed by a hidden 'SeenJobs' worksheet tab in the same
    spreadsheet — no git commits needed, and it works identically for every
    person who clones this repo since state lives in their own sheet.

    Keyed on the granular `Job.source` string (e.g. "ats:greenhouse:stripe",
    "jobspy:linkedin"), not the coarse source module name, so adding a new target
    company or tracker later only bootstraps that one source, not its whole family.
    """

    def __init__(self, worksheet: gspread.Worksheet):
        self.worksheet = worksheet
        self._rows: list[list[str]] = []
        self._seen: set[tuple[str, str]] = set()
        self._sources_with_rows: set[str] = set()
        self._pending: list[list[str]] = []

    def load(self) -> None:
        values = self.worksheet.get_all_values()
        if not values:
            self.worksheet.update([_HEADER], "A1")
            self._rows = []
            return
        self._rows = values[1:] if values[0] == _HEADER else values
        for row in self._rows:
            if len(row) < 3:
                continue
            source, job_id = row[0], row[2]
            self._seen.add((source, job_id))
            self._sources_with_rows.add(source)

    def is_first_run(self, source: str) -> bool:
        return source not in self._sources_with_rows

    def is_new(self, source: str, job_id: str) -> bool:
        return (source, job_id) not in self._seen

    def mark_seen(self, source: str, track: str, job_id: str) -> None:
        if (source, job_id) in self._seen:
            return
        self._seen.add((source, job_id))
        self._sources_with_rows.add(source)
        self._pending.append(
            [source, track, job_id, datetime.datetime.now(datetime.timezone.utc).isoformat()]
        )

    def flush(self) -> None:
        if not self._pending:
            return
        self.worksheet.append_rows(self._pending, value_input_option="RAW")
        self._rows.extend(self._pending)
        self._pending = []

    def prune(self, retention_days: int) -> None:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=retention_days)
        kept = []
        for row in self._rows:
            if len(row) < 4:
                continue
            try:
                first_seen = datetime.datetime.fromisoformat(row[3])
            except ValueError:
                kept.append(row)
                continue
            if first_seen >= cutoff:
                kept.append(row)

        if len(kept) == len(self._rows):
            return

        self._rows = kept
        self.worksheet.clear()
        self.worksheet.update([_HEADER] + kept, "A1")
