import datetime

from jobfind.sinks.seen_store import SeenStore, _HEADER


class FakeWorksheet:
    def __init__(self, rows=None):
        self.rows = rows or []  # data rows only, header not included
        self.has_header = bool(rows)
        self.append_calls = []
        self.delete_calls = []

    def get_all_values(self):
        if not self.has_header:
            return []
        return [_HEADER] + self.rows

    def update(self, values, range_name=None):
        self.has_header = True
        self.rows = values[1:]

    def append_rows(self, rows, value_input_option="RAW"):
        self.append_calls.append(rows)
        self.rows.extend(rows)

    def delete_rows(self, start_index, end_index=None):
        end_index = end_index or start_index
        self.delete_calls.append((start_index, end_index))
        # 1-indexed sheet rows, row 1 is the header, so data starts at row 2.
        del self.rows[start_index - 2 : end_index - 1]


def test_first_run_is_true_when_worksheet_empty():
    store = SeenStore(FakeWorksheet())
    store.load()

    assert store.is_first_run("ats:greenhouse:stripe") is True
    assert store.is_new("ats:greenhouse:stripe", "123") is True


def test_first_run_is_false_once_source_has_rows_but_true_for_new_source():
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    worksheet = FakeWorksheet(rows=[["ats:greenhouse:stripe", "new_grad", "123", now]])
    store = SeenStore(worksheet)
    store.load()

    assert store.is_first_run("ats:greenhouse:stripe") is False
    assert store.is_first_run("ats:lever:ramp") is True


def test_mark_seen_then_is_new_returns_false():
    store = SeenStore(FakeWorksheet())
    store.load()

    assert store.is_new("jobspy:linkedin", "abc") is True
    store.mark_seen("jobspy:linkedin", "new_grad", "abc")
    assert store.is_new("jobspy:linkedin", "abc") is False


def test_record_score_fills_in_pending_row_for_rejected_and_accepted_jobs():
    worksheet = FakeWorksheet()
    store = SeenStore(worksheet)
    store.load()

    store.mark_seen("jobspy:linkedin", "new_grad", "rejected-job")
    store.mark_seen("jobspy:linkedin", "new_grad", "accepted-job")
    store.record_score("jobspy:linkedin", "rejected-job", score=20, confidence=90, rationale="Not a fit")
    store.record_score("jobspy:linkedin", "accepted-job", score=85, confidence=70, rationale="Strong match")
    store.flush()

    (rows,) = worksheet.append_calls
    as_dict = {row[2]: dict(zip(_HEADER, row)) for row in rows}
    assert as_dict["rejected-job"]["score"] == "20"
    assert as_dict["rejected-job"]["confidence"] == "90"
    assert as_dict["rejected-job"]["rationale"] == "Not a fit"
    assert as_dict["accepted-job"]["score"] == "85"


def test_record_score_is_noop_for_unmarked_job():
    store = SeenStore(FakeWorksheet())
    store.load()

    # Bootstrap-seeded jobs never get scored — record_score should not raise
    # or fabricate a row if mark_seen was never called for this key.
    store.record_score("jobspy:linkedin", "never-marked", score=50, confidence=50, rationale="n/a")

    assert store._pending == []


def test_bootstrap_job_row_has_empty_score_columns():
    worksheet = FakeWorksheet()
    store = SeenStore(worksheet)
    store.load()

    store.mark_seen("jobspy:linkedin", "new_grad", "bootstrap-job")
    store.flush()

    (rows,) = worksheet.append_calls
    as_dict = dict(zip(_HEADER, rows[0]))
    assert as_dict["score"] == ""
    assert as_dict["confidence"] == ""
    assert as_dict["rationale"] == ""


def test_flush_appends_only_pending_rows():
    worksheet = FakeWorksheet()
    store = SeenStore(worksheet)
    store.load()

    store.mark_seen("jobspy:linkedin", "new_grad", "abc")
    store.mark_seen("jobspy:linkedin", "new_grad", "def")
    store.flush()

    assert len(worksheet.append_calls) == 1
    assert len(worksheet.append_calls[0]) == 2

    # flushing again with nothing pending should not append again
    store.flush()
    assert len(worksheet.append_calls) == 1


def test_prune_drops_rows_older_than_retention():
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=200)).isoformat()
    recent = datetime.datetime.now(datetime.timezone.utc).isoformat()
    worksheet = FakeWorksheet(
        rows=[
            ["jobspy:linkedin", "new_grad", "old-job", old],
            ["jobspy:linkedin", "new_grad", "recent-job", recent],
        ]
    )
    store = SeenStore(worksheet)
    store.load()

    store.prune(retention_days=120)

    # Only the stale row was deleted in place — no clear()/full rewrite of the tab.
    assert worksheet.delete_calls == [(2, 2)]
    assert len(worksheet.rows) == 1
    assert worksheet.rows[0][2] == "recent-job"

    # A pruned row only needs to be forgotten on the *next* run's load() —
    # a fresh store reading the now-pruned worksheet should treat it as new again.
    reloaded = SeenStore(worksheet)
    reloaded.load()
    assert reloaded.is_new("jobspy:linkedin", "old-job") is True
    assert reloaded.is_new("jobspy:linkedin", "recent-job") is False


def test_prune_does_nothing_when_nothing_stale():
    recent = datetime.datetime.now(datetime.timezone.utc).isoformat()
    worksheet = FakeWorksheet(rows=[["jobspy:linkedin", "new_grad", "recent-job", recent]])
    store = SeenStore(worksheet)
    store.load()

    store.prune(retention_days=120)

    assert worksheet.delete_calls == []
    assert len(worksheet.rows) == 1
