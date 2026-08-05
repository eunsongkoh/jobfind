import datetime
import logging

from ..models import Job
from .base import BaseSource

logger = logging.getLogger(__name__)


class JobSpySource(BaseSource):
    """LinkedIn/Indeed/ZipRecruiter/Google Jobs via python-jobspy.

    Each site is scraped in its own try/except so one rate-limited site (LinkedIn
    is the usual offender on shared runner IPs) never empties the other sites'
    results for this run.
    """

    name = "jobspy"

    def fetch(self) -> list[Job]:
        from jobspy import scrape_jobs

        cfg = self.app_config.jobspy
        search_term = " ".join(self.track_def.keywords_include[:2]) or "software engineer"
        location = self.app_config.locations[0] if self.app_config.locations else None
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        jobs: list[Job] = []
        for site in cfg.enabled_sites:
            try:
                df = scrape_jobs(
                    site_name=[site],
                    search_term=search_term,
                    location=location,
                    results_wanted=cfg.results_wanted,
                    hours_old=cfg.hours_old,
                    country_indeed=cfg.country_indeed,
                    job_type=self.track_def.jobspy_job_type,
                )
            except Exception:
                logger.exception("jobspy site '%s' failed, skipping", site)
                continue

            if df is None or df.empty:
                continue

            for _, row in df.iterrows():
                job_url = row.get("job_url")
                if not job_url:
                    continue
                date_posted = row.get("date_posted")
                jobs.append(
                    Job(
                        id=job_url,
                        source=f"jobspy:{site}",
                        track=self.track,
                        title=row.get("title") or "",
                        company=row.get("company") or "",
                        location=row.get("location") or "",
                        job_url=job_url,
                        date_posted=str(date_posted) if date_posted else None,
                        date_detected=now,
                        description=row.get("description"),
                    )
                )
        return jobs
