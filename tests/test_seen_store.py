import datetime

from jobfind.sinks.seen_store import SeenStore, _HEADER


class FakeWorksheet:
    def __init__(self, rows=None):
        self.rows = rows or []  # data rows only, header not included
        self.has_header = bool(rows)
        self.append_calls = []
        self.clear_calls = 0

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

    def clear(self):
        self.clear_calls += 1
        self.has_header = False
        self.rows = []


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

    assert worksheet.clear_calls == 1
    assert len(worksheet.rows) == 1
    assert worksheet.rows[0][2] == "recent-job"

    # A pruned row only needs to be forgotten on the *next* run's load() —
    # a fresh store reading the now-pruned worksheet should treat it as new again.
    reloaded = SeenStore(worksheet)
    reloaded.load()
    assert reloaded.is_new("jobspy:linkedin", "old-job") is True
    assert reloaded.is_new("jobspy:linkedin", "recent-job") is False
