# jobfind

Automated job-discovery pipeline: finds new-grad software engineering postings
(and, optionally, internships / mid-level roles) within hours of going live,
scores them against a personal profile with an LLM, and writes only the good
matches to a Google Sheet. Runs hourly on GitHub Actions — no laptop required.

**Scope**: discovery and scoring only. There is no resume content or
application-answering logic here — that lives in a separate, private repo.

## How it works

- **Discovery** (`src/jobfind/sources/`): each source is a standalone module
  behind a common interface — `python-jobspy` (LinkedIn/Indeed/ZipRecruiter/Google),
  GitHub tracker repos (SimplifyJobs/vanshb03 new-grad & internship lists), and
  direct polling of Greenhouse/Lever/Ashby for a configurable company list.
- **Dedup**: seen-job state lives in a hidden `SeenJobs` tab in your own Google
  Sheet — not in git. Nothing is scored/written until it's been seen fresh.
  Every scored job's `score`/`confidence`/`rationale` is also logged here for
  audit purposes (bootstrap-seeded jobs are the exception — those are never
  scored, so those columns stay empty).
- **Scoring** (`src/jobfind/scoring/`): a thin provider wrapper calls an LLM
  (Google AI Studio's Gemini API, via structured JSON-schema output) to score
  each new posting against `profile.yaml` — as a *recommendation*, not a
  qualification check. The model returns a `score` (0-100 recommendation strength), a
  `confidence` (0-100 — lowered when candidate/job info is missing, rather
  than penalizing the score itself), and a one-sentence `reason`.
  `profile.yaml`'s `recommendation_mode` (`personalized` or `broad`) controls
  how selective it is.
- **Output** (`src/jobfind/sinks/sheets_writer.py`): postings at or above
  `score_threshold` go to a `Jobs` tab; everything scored below it goes to a
  `RejectedJobs` tab instead of being discarded, so you can see why something
  wasn't recommended. Both tabs share the same columns — title, company,
  location, link (clickable), date detected, description, date posted, score,
  confidence, rationale.
- **Scheduling**: `.github/workflows/discover.yml` runs the pipeline hourly via
  `cron`, plus `workflow_dispatch` for manual test runs.

Everything tunable — active tracks, keywords, locations, score threshold, active
sources, target-company list — lives in one file: `config.yaml`.

## Setup

### 1. Google Sheet + service account

1. In the [Google Cloud Console](https://console.cloud.google.com/), create or
   select a project, then enable the **Google Sheets API** and **Google Drive API**.
2. Go to **IAM & Admin → Service Accounts → Create Service Account** (any name,
   e.g. `jobfind-bot`).
3. Open the service account → **Keys → Add Key → Create new key → JSON**, and
   download it.
4. Open the downloaded file and copy the `client_email` value.
5. Create the Google Sheet you want results written to, click **Share**, paste
   that `client_email`, and grant **Editor** access. (You don't need to create
   the `Jobs`/`SeenJobs`/`RejectedJobs` tabs yourself — the pipeline creates
   them on first run.)
6. Copy the Sheet ID out of its URL: `https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`.

### 2. Google AI Studio API key

1. Go to [aistudio.google.com/projects](https://aistudio.google.com/projects)
   and create (or select) a project.
2. Click **Create API key** and attach it to that project.
3. Copy the key — this is `GOOGLE_AI_API_KEY`, exported as an env var when
   running the pipeline locally, and set as a GitHub Actions secret (step 5)
   for the scheduled workflow.

Read the data-sharing note in the next step before you use this key with
anything but throwaway test data.

### 3. Local config

```bash
cp config.example.yaml config.yaml
cp profile.example.yaml profile.yaml
```

Fill in your real values — target companies, locations, keywords, the Sheet ID
from step 1, and your scoring preferences in `profile.yaml`. **Both files are
gitignored and must never be committed** — that's what keeps this repo safe to
make public later, and it's the same thing anyone else cloning this repo does
for their own instance.

`profile.yaml` is scoring input only (role, seniority, skills, locations) — no
contact info, no resume content.

> ⚠️ **Free-tier data sharing**: Google AI Studio's *free* Gemini API tier
> uses your prompts and outputs (including everything in `profile.yaml` —
> `notes` especially, since that's freeform text) to improve Google's
> products, per Gemini API's terms. It is **not** private the way a paid tier
> is. Keep `notes`/keywords to job-search preferences only, and see "Gemini
> free-tier data sharing" under Known limitations below for the full note
> before putting anything sensitive in `profile.yaml`.

`profile.yaml`'s `recommendation_mode` controls how selective scoring is:
`personalized` (default) weighs your stated preferences (locations, remote,
positive/negative keywords, notes) heavily and is more selective; `broad`
favors recall and transferable-skill/potential fit even when preferences
aren't a close match.

### 4. Configuring tracks (new-grad / internship / mid-level)

`config.yaml`'s `tracks.active` list controls which job types are searched each
run — start with just `[new_grad]`, and add `internship` and/or `mid_level`
later by adding the name to that list. Each track's keywords, job type, and
GitHub tracker URLs live under `tracks.definitions`, so adding a track (or
tuning an existing one) is a config edit, not a code change.

### 5. GitHub Actions secrets

In the repo's **Settings → Secrets and variables → Actions**, add:

| Secret | Value |
|---|---|
| `GOOGLE_AI_API_KEY` | API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full contents of the JSON key file from step 1 |
| `APP_CONFIG_YAML` | Full contents of your local `config.yaml` |
| `APP_PROFILE_YAML` | Full contents of your local `profile.yaml` |

The workflow writes the last two secrets back out to `config.yaml`/`profile.yaml`
on the runner before each run — that's how personal config gets to GitHub
Actions without ever being committed.

### 6. Enable the workflow

Either wait for the hourly cron or trigger
**Actions → Discover jobs → Run workflow** manually to test end-to-end.

The cron schedule in `.github/workflows/discover.yml` runs at minute 17 of
every hour (`17 * * * *`) rather than minute 0 — GitHub's scheduler is
best-effort, and the top of the hour is high-traffic across everyone's
scheduled workflows, so runs there are more likely to be delayed or skipped.
Change the `17` to whatever minute you like (or a different cron expression
entirely) if you want a different offset — it just needs to avoid `0`.

### 7. First run — expect to run it twice

- On a fresh Sheet, the first run treats everything each source fetches as a
  baseline, not "new" — it seeds `SeenJobs` but writes **zero rows** to `Jobs`
  or `RejectedJobs`. (Otherwise you'd get a company's entire current job
  board, or a tracker repo's whole README, dumped in as if it all just went
  live.)
- Trigger it once, confirm `SeenJobs` now has rows while `Jobs`/`RejectedJobs`
  are still empty, then trigger it again — only the second run compares
  against that baseline and writes anything genuinely new.
- After that, the hourly cron behaves normally. This double-run is a
  one-time thing per Sheet — adding a new tracker or target company later
  only re-bootstraps *that* source, not the whole Sheet.
- Heads up: the same one-time re-bootstrap happens automatically whenever
  `SeenJobs`'s column layout changes in code — a header mismatch on load
  resets that run's in-memory history (old rows aren't deleted, just no
  longer read), so expect one bootstrap-only run any time you pull a change
  that touches `SeenJobs`'s schema against an existing Sheet.

## Known limitations

**LinkedIn rate limits**
- GitHub-hosted runners share IPs across countless workflows, so LinkedIn
  blocks them faster than a residential IP would, especially at hourly
  frequency.
- Already mitigated: each site scrapes independently (one blocked site
  doesn't affect the others), `results_wanted` is conservative by default,
  and `jobspy.enabled_sites` in `config.yaml` lets you drop a
  persistently-blocked site with a one-line edit.
- LinkedIn is a bonus source, not the backbone — GitHub trackers and direct
  ATS polling aren't IP-sensitive the same way, so they keep working even if
  LinkedIn is fully blocked.
- For scraper-specific limits/behavior, see the
  [JobSpy docs](https://github.com/speedyapply/JobSpy) directly rather than
  duplicating them here.

**Gemini free-tier data sharing**

The *free* Gemini API tier (what `GOOGLE_AI_API_KEY` from
[aistudio.google.com](https://aistudio.google.com/apikey) gets you) is
governed by the [Gemini API additional terms of service](https://ai.google.dev/gemini-api/terms) —
under those terms, Google **may use prompts, outputs, and related data from
the free tier to improve their products** (including human review), unlike a
paid-billing tier. Every scoring call sends the full rendered prompt —
`profile.yaml`'s role/skills/locations/keywords/**notes**, plus the fetched
job's title/company/location/**full description** — to that free endpoint.
Don't put anything in `profile.yaml` (or expect anything in a scraped job
description) that you wouldn't want reviewed by Google. If that's not
acceptable, the only fix is moving to a paid-billing Gemini tier — data
sharing is a tier property, not something `GoogleAIProvider` can opt out of
via a request parameter.

**Gemini free-tier rate limits**

Free-tier limits are per-model and evaluated across three dimensions
(requests/minute, tokens/minute, requests/day) — hitting any one triggers an
HTTP 429 (`rationale: provider_error`, or a printed HTTP error from
`scripts/smoke_test_scoring.py`), even if you're within the other two.

`gemini-3.1-flash-lite` (current default) gets **1,000 requests/day** on the
free tier — check current RPM/TPM alongside it, and any other model you're
considering, at
[ai.google.dev/gemini-api/docs/rate-limits](https://ai.google.dev/gemini-api/docs/rate-limits)
(also viewable live in [AI Studio](https://aistudio.google.com/rate-limit))
before switching `scoring.model`. Confirm the model name itself is still
current at
[ai.google.dev/gemini-api/docs/models](https://ai.google.dev/gemini-api/docs/models)
(a 404 means it's wrong or retired). An empty response (`rationale:
parse_error`) usually means the interaction was cut off by
`scoring.max_tokens` before producing output, or blocked — raise
`scoring.max_tokens` if you see this.

`gemini-3.1-flash-lite` supports `thinking_level` values `minimal`/`low`/
`medium`/`high` and defaults to `minimal` — which is also what
`GoogleAIProvider` explicitly requests, since this is a single-step scoring
call that doesn't benefit from extended reasoning. If you switch to a
different model, double-check it supports `minimal` (some models default to
thinking *on* and don't allow disabling it) before assuming this still
applies.

## Adding a new discovery source

Implement `BaseSource.fetch()` in one new file under `src/jobfind/sources/`,
returning `list[Job]`, add one entry to `SOURCE_REGISTRY` in
`src/jobfind/sources/__init__.py`, and add its name to `active_sources` in
`config.yaml`. Nothing else needs to change.

## Swapping LLM providers

All scoring calls go through `src/jobfind/scoring/provider.py`. Google AI
Studio's Gemini API (`scoring.provider: google`) is the only built-in
provider — `GoogleAIProvider` wraps the `google-genai` SDK's Interactions API
(`client.interactions.create()`), passing `ScoreResponse.model_json_schema()`
straight through as `response_format` (the Interactions API accepts standard
JSON Schema natively, no translation needed) and reading the result back via
`interaction.output_text`.

To add another provider, add a class implementing `LLMProvider`'s
`complete()` method in `provider.py` and one branch in `get_provider()`. No
other file references a specific provider directly.

## Running tests

```bash
pip install -r requirements.txt
PYTHONPATH=src pytest
```

## Contributing

Found a bug or want a feature added? Open a GitHub Issue describing it, or a
PR if you've already got a fix — both get reviewed.
