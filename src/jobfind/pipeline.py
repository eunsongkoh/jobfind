import json
import logging
import os
import sys
from collections import defaultdict

from .config import AppConfig, load_config, load_profile
from .filters import apply_filters, prefilter_job, targets_us_location
from .models import Job, ScoredJob
from .scoring.provider import get_provider
from .scoring.scorer import score_job
from .sinks.seen_store import SeenStore
from .sinks.sheets_client import SheetsClient
from .sinks.sheets_writer import SheetsWriter
from .sources import SOURCE_REGISTRY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _fetch_all(config: AppConfig) -> list[Job]:
    jobs: list[Job] = []
    any_source_succeeded = False

    for track_name in config.tracks.active:
        track_def = config.tracks.definitions.get(track_name)
        if track_def is None:
            logger.warning("track '%s' is active but has no definition, skipping", track_name)
            continue

        track_jobs: list[Job] = []
        for source_name in config.active_sources:
            source_cls = SOURCE_REGISTRY.get(source_name)
            if source_cls is None:
                logger.warning("unknown source '%s' in active_sources, skipping", source_name)
                continue
            try:
                source = source_cls(track_name, track_def, config)
                fetched = source.fetch()
                any_source_succeeded = True
            except Exception:
                logger.exception(
                    "source '%s' failed entirely for track '%s', skipping", source_name, track_name
                )
                continue
            logger.info("track=%s source=%s fetched=%d", track_name, source_name, len(fetched))
            track_jobs.extend(fetched)

        filtered = apply_filters(track_jobs, track_def, config.locations)
        logger.info("track=%s fetched_total=%d filtered=%d", track_name, len(track_jobs), len(filtered))
        jobs.extend(filtered)

    if not any_source_succeeded and config.active_sources:
        raise RuntimeError("every discovery source failed")

    return jobs


def _dedup(jobs: list[Job], seen_store: SeenStore) -> list[Job]:
    by_source: dict[str, list[Job]] = defaultdict(list)
    for job in jobs:
        by_source[job.source].append(job)

    new_jobs: list[Job] = []
    for source, source_jobs in by_source.items():
        bootstrap = seen_store.is_first_run(source)
        for job in source_jobs:
            if not seen_store.is_new(source, job.id):
                continue
            seen_store.mark_seen(source, job.track, job.id)
            if not bootstrap:
                new_jobs.append(job)
        if bootstrap:
            logger.info("source=%s first run — seeded %d jobs without scoring", source, len(source_jobs))

    return new_jobs


def run() -> None:
    config = load_config()
    profile = load_profile()

    credentials_json = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    sheets_client = SheetsClient(config.sheets, credentials_json)

    seen_store = SeenStore(sheets_client.seen_worksheet())
    seen_store.load()

    writer = SheetsWriter(sheets_client.jobs_worksheet())
    writer.ensure_header()

    rejected_writer = SheetsWriter(sheets_client.rejected_worksheet())
    rejected_writer.ensure_header()

    jobs = _fetch_all(config)
    new_jobs = _dedup(jobs, seen_store)
    logger.info("new_jobs=%d", len(new_jobs))

    check_sponsorship = not profile.us_citizen and targets_us_location(config.locations)

    to_score: list[Job] = []
    prefiltered: list[ScoredJob] = []
    for job in new_jobs:
        track_def = config.tracks.definitions.get(job.track)
        rejected = prefilter_job(job, track_def, check_sponsorship=check_sponsorship)
        if rejected is not None:
            prefiltered.append(rejected)
        else:
            to_score.append(job)
    logger.info("prefiltered=%d to_score=%d", len(prefiltered), len(to_score))

    provider = get_provider(config.scoring)
    scored = prefiltered + [
        score_job(job, profile, provider, max_tokens=config.scoring.max_tokens) for job in to_score
    ]
    for sj in scored:
        seen_store.record_score(sj.job.source, sj.job.id, score=sj.score, confidence=sj.confidence, rationale=sj.rationale)

    matches = [sj for sj in scored if sj.score >= config.scoring.score_threshold]
    rejected = [sj for sj in scored if sj.score < config.scoring.score_threshold]
    logger.info("scored=%d matches=%d rejected=%d", len(scored), len(matches), len(rejected))

    writer.append_rows(matches)
    rejected_writer.append_rows(rejected)

    seen_store.flush()
    seen_store.prune(config.dedup.retention_days)


if __name__ == "__main__":
    try:
        run()
    except Exception:
        logger.exception("pipeline run failed")
        sys.exit(1)
