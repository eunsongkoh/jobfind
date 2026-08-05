import datetime

import gspread

_HEADER = ["source", "track", "job_id", "first_seen", "score", "confidence", "rationale"]


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
        self._pending_by_key: dict[tuple[str, str], list[str]] = {}

    def load(self) -> None:
        values = self.worksheet.get_all_values()
        if values and values[0] == _HEADER:
            self._rows = values[1:]
        else:
            self.worksheet.update([_HEADER], "A1")
            self._rows = []
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
        row = [source, track, job_id, datetime.datetime.now(datetime.timezone.utc).isoformat(), "", "", ""]
        self._pending.append(row)
        self._pending_by_key[(source, job_id)] = row

    def record_score(self, source: str, job_id: str, *, score: int, confidence: int, rationale: str) -> None:
        """Fills in the score/confidence/rationale columns of a row already
        written by mark_seen() for this run, once scoring has actually run —
        called for every scored job, not just ones that clear score_threshold,
        so SeenJobs shows why a job was rejected as well as accepted. A no-op
        if the row was already flushed to the sheet or never marked this run
        (e.g. a bootstrap-seeded job, which is never scored)."""
        row = self._pending_by_key.get((source, job_id))
        if row is None:
            return
        row[4] = str(score)
        row[5] = str(confidence)
        row[6] = rationale

    def flush(self) -> None:
        if not self._pending:
            return
        self.worksheet.append_rows(self._pending, value_input_option="RAW")
        self._rows.extend(self._pending)
        self._pending = []

    def prune(self, retention_days: int) -> None:
        """Deletes only the stale rows in place — never clears/rewrites the tab,
        so this can't wipe out data mid-flight (e.g. a row appended between when
        this run loaded and when it prunes)."""
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=retention_days)
        kept: list[list[str]] = []
        stale_sheet_rows: list[int] = []

        for i, row in enumerate(self._rows):
            sheet_row = i + 2  # 1-indexed, +1 for the header row
            if len(row) < 4:
                kept.append(row)
                continue
            try:
                first_seen = datetime.datetime.fromisoformat(row[3])
            except ValueError:
                kept.append(row)
                continue
            if first_seen >= cutoff:
                kept.append(row)
            else:
                stale_sheet_rows.append(sheet_row)

        if not stale_sheet_rows:
            return

        self._rows = kept
        # Delete bottom-to-top so earlier row indices stay valid as rows shift up.
        for sheet_row in sorted(stale_sheet_rows, reverse=True):
            self.worksheet.delete_rows(sheet_row)
