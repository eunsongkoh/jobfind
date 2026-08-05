import gspread

from ..config import SheetsConfig


class SheetsClient:
    """Thin auth wrapper — opens the spreadsheet once and hands out worksheets to
    whoever needs them (SeenStore and SheetsWriter), independent of what produced
    the data written to either tab."""

    def __init__(self, config: SheetsConfig, credentials_json: dict):
        gc = gspread.service_account_from_dict(credentials_json)
        self.spreadsheet = gc.open_by_key(config.sheet_id)
        self.config = config

    def jobs_worksheet(self) -> gspread.Worksheet:
        return self._get_or_create(self.config.jobs_worksheet)

    def seen_worksheet(self) -> gspread.Worksheet:
        return self._get_or_create(self.config.seen_worksheet)

    def _get_or_create(self, title: str) -> gspread.Worksheet:
        try:
            return self.spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            return self.spreadsheet.add_worksheet(title=title, rows=1000, cols=10)
