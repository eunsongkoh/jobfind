import datetime
import logging
import re

import requests

from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r'href="([^"]+)"')
_MD_LINK_RE = re.compile(r"\]\((https?://[^\s)]+)\)")
_CLOSED_MARKER = "\U0001f512"  # 🔒


def _extract_url(text: str) -> str | None:
    match = _HREF_RE.search(text)
    if match:
        return match.group(1)
    match = _MD_LINK_RE.search(text)
    if match:
        return match.group(1)
    return None


def _clean_text(cell: str) -> str:
    text = _TAG_RE.sub("", cell)
    text = text.replace("**", "").replace("&nbsp;", " ")
    text = re.sub(r"[\U0001f300-\U0001faff]", "", text)  # strip emoji markers (🔥, 🇺🇸, etc)
    return re.sub(r"\s+", " ", text).strip()


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return all(set(cell) <= {"-", ":", " "} for cell in cells)


def _is_header_row(cells: list[str]) -> bool:
    return len(cells) > 1 and _clean_text(cells[0]).lower() in {"company", ""} and _clean_text(cells[1]).lower() == "role"


class GithubTrackerSource(BaseSource):
    """Diffs the SimplifyJobs/vanshb03-style tracker READMEs against previously-seen
    state (handled by the pipeline's dedup step, not here).

    These READMEs are markdown tables with raw HTML embedded in cells (<a>, <strong>,
    <details>), not clean markdown — parsing is line-based with tag-stripping rather
    than a strict markdown-table parser.
    """

    name = "github_trackers"

    def fetch(self) -> list[Job]:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        jobs: list[Job] = []

        for tracker in self.track_def.github_trackers:
            try:
                resp = requests.get(tracker.url, timeout=30)
                resp.raise_for_status()
            except Exception:
                logger.exception("failed to fetch tracker '%s', skipping", tracker.name)
                continue

            for line in resp.text.splitlines():
                job = self._parse_row(line, tracker.name, tracker.url, now)
                if job is not None:
                    jobs.append(job)

        return jobs

    def _parse_row(self, line: str, tracker_name: str, tracker_url: str, now: str) -> Job | None:
        line = line.strip()
        if not line.startswith("|") or _CLOSED_MARKER in line:
            return None

        cells = _split_row(line)
        if len(cells) < 3 or _is_separator_row(cells) or _is_header_row(cells):
            return None

        company = _clean_text(cells[0])
        title = _clean_text(cells[1])
        location = _clean_text(cells[2])
        if not company or not title:
            return None

        job_url = _extract_url(cells[3]) if len(cells) > 3 else None
        if job_url is None:
            job_url = _extract_url(line)

        date_posted = _clean_text(cells[4]) if len(cells) > 4 else None
        job_id = job_url or f"{tracker_name}:{company}:{title}:{location}"

        return Job(
            id=job_id,
            source=f"github:{tracker_name}",
            track=self.track,
            title=title,
            company=company,
            location=location,
            job_url=job_url or tracker_url,
            date_posted=date_posted or None,
            date_detected=now,
            description=None,
        )
