from jobfind.models import Job, ScoredJob
from jobfind.sinks.sheets_writer import _HEADER, SheetsWriter


class FakeWorksheet:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.has_header = bool(rows)
        self.append_calls = []
        self.update_calls = []

    def get_all_values(self):
        if not self.has_header:
            return []
        return [_HEADER] + self.rows

    def update(self, values, range_name=None):
        self.update_calls.append((values, range_name))
        self.has_header = True
        self.rows = values[1:]

    def append_rows(self, rows, value_input_option="RAW"):
        self.append_calls.append((rows, value_input_option))
        self.rows.extend(rows)


def _scored_job(job_overrides=None, score=88, confidence=80, rationale="Strong match") -> ScoredJob:
    job_defaults = dict(
        id="1",
        source="test",
        track="new_grad",
        title="Machine Learning Engineer",
        company="Acme",
        location="Remote",
        job_url="https://example.com/job/1",
        date_posted="2026-08-01T00:00:00+00:00",
        date_detected="2026-08-04T00:00:00+00:00",
        description="A great role doing great things.",
    )
    job_defaults.update(job_overrides or {})
    return ScoredJob(job=Job(**job_defaults), score=score, confidence=confidence, rationale=rationale)


def test_ensure_header_writes_when_sheet_blank():
    worksheet = FakeWorksheet()
    writer = SheetsWriter(worksheet)

    writer.ensure_header()

    assert worksheet.update_calls[0][0] == [_HEADER]


def test_ensure_header_skips_when_already_correct():
    worksheet = FakeWorksheet(rows=[["x"] * len(_HEADER)])
    worksheet.rows = []
    worksheet.has_header = True
    writer = SheetsWriter(worksheet)

    writer.ensure_header()

    assert worksheet.update_calls == []


def test_append_rows_includes_link_rationale_description_date_posted():
    worksheet = FakeWorksheet()
    writer = SheetsWriter(worksheet)

    writer.append_rows([_scored_job()])

    (rows, value_input_option), = worksheet.append_calls
    assert value_input_option == "USER_ENTERED"
    row = rows[0]
    as_dict = dict(zip(_HEADER, row))
    assert as_dict["link"] == "https://example.com/job/1"
    assert as_dict["rationale"] == "Strong match"
    assert as_dict["description"] == "A great role doing great things."
    assert as_dict["date_posted"] == "2026-08-01T00:00:00+00:00"
    assert as_dict["score"] == 88
    assert as_dict["confidence"] == 80


def test_append_rows_handles_missing_description_and_date_posted():
    worksheet = FakeWorksheet()
    writer = SheetsWriter(worksheet)
    scored = _scored_job(job_overrides={"description": None, "date_posted": None})

    writer.append_rows([scored])

    (rows, _), = worksheet.append_calls
    as_dict = dict(zip(_HEADER, rows[0]))
    assert as_dict["description"] == ""
    assert as_dict["date_posted"] == ""


def test_append_rows_truncates_long_description():
    worksheet = FakeWorksheet()
    writer = SheetsWriter(worksheet)
    scored = _scored_job(job_overrides={"description": "x" * 5000})

    writer.append_rows([scored])

    (rows, _), = worksheet.append_calls
    as_dict = dict(zip(_HEADER, rows[0]))
    assert len(as_dict["description"]) == 3001  # 3000 chars + truncation marker
    assert as_dict["description"].endswith("…")
