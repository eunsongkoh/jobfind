from jobfind.models import Job, ScoredJob
from jobfind.sinks.sheets_writer import _HEADER, SheetsWriter


class FakeSpreadsheet:
    def __init__(self):
        self.batch_update_calls = []

    def batch_update(self, body):
        self.batch_update_calls.append(body)


class FakeWorksheet:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.has_header = bool(rows)
        self.append_calls = []
        self.update_calls = []
        self.id = 0
        self.spreadsheet = FakeSpreadsheet()

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
        start = len(self.rows) + 2  # +1 for header row, +1 for 1-indexing
        self.rows.extend(rows)
        end = start + len(rows) - 1
        return {"updates": {"updatedRange": f"Jobs!A{start}:J{end}"}}


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


def test_append_rows_formats_date_detected_human_readable():
    worksheet = FakeWorksheet()
    writer = SheetsWriter(worksheet)
    scored = _scored_job(job_overrides={"date_detected": "2026-08-18T16:50:30.425485+00:00"})

    writer.append_rows([scored])

    (rows, _), = worksheet.append_calls
    as_dict = dict(zip(_HEADER, rows[0]))
    assert as_dict["date_detected"] == "2026-08-18 09:50 AM PDT"


def test_append_rows_keeps_new_rows_compact():
    worksheet = FakeWorksheet()
    writer = SheetsWriter(worksheet)

    writer.append_rows([_scored_job(), _scored_job()])

    (body,) = worksheet.spreadsheet.batch_update_calls
    requests = body["requests"]
    assert len(requests) == 2
    repeat_cell, update_dims = requests

    assert repeat_cell["repeatCell"]["cell"]["userEnteredFormat"]["wrapStrategy"] == "CLIP"
    row_range = update_dims["updateDimensionProperties"]["range"]
    assert row_range["dimension"] == "ROWS"
    assert row_range["endIndex"] - row_range["startIndex"] == 2
    assert update_dims["updateDimensionProperties"]["properties"]["pixelSize"] == 21


def test_append_rows_does_not_touch_prior_rows_range():
    worksheet = FakeWorksheet(rows=[["existing"] * len(_HEADER)])
    writer = SheetsWriter(worksheet)

    writer.append_rows([_scored_job()])

    (body,) = worksheet.spreadsheet.batch_update_calls
    row_range = body["requests"][1]["updateDimensionProperties"]["range"]
    # header (row 1) + 1 existing row (row 2) -> new row starts at index 2 (0-indexed)
    assert row_range["startIndex"] == 2
    assert row_range["endIndex"] == 3
