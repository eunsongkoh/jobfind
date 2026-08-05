import datetime
import html
import logging
import re

import requests

from ..config import TargetCompany
from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


def _clean_html(raw: str | None) -> str | None:
    """Greenhouse's `content` field is HTML (with entities double-encoded in
    practice), unlike Lever/Ashby which expose a ready-made plain-text field."""
    if not raw:
        return None
    text = _TAG_RE.sub(" ", html.unescape(raw))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _epoch_ms_to_iso(ms: int | None) -> str | None:
    if not ms:
        return None
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc).isoformat()


class AtsSource(BaseSource):
    """Direct polling of Greenhouse/Lever/Ashby public job-board JSON APIs for a
    configurable target-company list.

    Each company's board returns its full open-role list (not scoped to a track),
    so results are narrowed by the pipeline's shared filters.py after fetch — the
    same as every other source.
    """

    name = "ats"

    def fetch(self) -> list[Job]:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        jobs: list[Job] = []

        for company in self.app_config.target_companies:
            try:
                raw_jobs = self._fetch_company(company)
            except Exception:
                logger.exception("ATS fetch failed for '%s' (%s), skipping", company.name, company.ats)
                continue

            for raw in raw_jobs:
                jobs.append(
                    Job(
                        id=f"ats:{company.ats}:{company.identifier}:{raw['id']}",
                        source=f"ats:{company.ats}:{company.identifier}",
                        track=self.track,
                        title=raw["title"],
                        company=company.name,
                        location=raw["location"],
                        job_url=raw["job_url"],
                        date_posted=raw.get("date_posted"),
                        date_detected=now,
                        description=raw.get("description"),
                    )
                )
        return jobs

    def _fetch_company(self, company: TargetCompany) -> list[dict]:
        if company.ats == "greenhouse":
            return self._fetch_greenhouse(company.identifier)
        if company.ats == "lever":
            return self._fetch_lever(company.identifier)
        if company.ats == "ashby":
            return self._fetch_ashby(company.identifier)
        raise ValueError(f"unknown ats '{company.ats}' for company '{company.name}'")

    @staticmethod
    def _fetch_greenhouse(identifier: str) -> list[dict]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{identifier}/jobs?content=true"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "id": str(job["id"]),
                "title": job["title"],
                "location": (job.get("location") or {}).get("name", ""),
                "job_url": job["absolute_url"],
                "date_posted": job.get("updated_at"),
                "description": _clean_html(job.get("content")),
            }
            for job in data.get("jobs", [])
        ]

    @staticmethod
    def _fetch_lever(identifier: str) -> list[dict]:
        url = f"https://api.lever.co/v0/postings/{identifier}?mode=json"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "id": job["id"],
                "title": job["text"],
                "location": (job.get("categories") or {}).get("location", ""),
                "job_url": job.get("hostedUrl") or job.get("applyUrl"),
                "date_posted": _epoch_ms_to_iso(job.get("createdAt")),
                "description": job.get("descriptionPlain") or None,
            }
            for job in data
        ]

    @staticmethod
    def _fetch_ashby(identifier: str) -> list[dict]:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{identifier}"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "id": job.get("id") or job["jobUrl"],
                "title": job["title"],
                "location": job.get("location", ""),
                "job_url": job["jobUrl"],
                "date_posted": job.get("publishedAt"),
                "description": job.get("descriptionPlain") or None,
            }
            for job in data.get("jobs", [])
        ]
