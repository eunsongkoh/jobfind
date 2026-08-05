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
- **Scoring** (`src/jobfind/scoring/`): a thin provider wrapper calls an LLM
  (OpenRouter by default) to score each new posting against `profile.yaml`.
  Only postings above the configured threshold get written.
- **Output** (`src/jobfind/sinks/sheets_writer.py`): matches are appended to a
  `Jobs` tab — title, company, location, link (clickable), date detected,
  description, date posted, score, rationale.
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
   the `Jobs`/`SeenJobs` tabs yourself — the pipeline creates them on first run.)
6. Copy the Sheet ID out of its URL: `https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`.

### 2. Local config

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

### 3. Configuring tracks (new-grad / internship / mid-level)

`config.yaml`'s `tracks.active` list controls which job types are searched each
run — start with just `[new_grad]`, and add `internship` and/or `mid_level`
later by adding the name to that list. Each track's keywords, job type, and
GitHub tracker URLs live under `tracks.definitions`, so adding a track (or
tuning an existing one) is a config edit, not a code change.

### 4. GitHub Actions secrets

In the repo's **Settings → Secrets and variables → Actions**, add:

| Secret | Value |
|---|---|
| `OPENROUTER_API_KEY` | API key from [openrouter.ai/keys](https://openrouter.ai/keys) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full contents of the JSON key file from step 1 |
| `APP_CONFIG_YAML` | Full contents of your local `config.yaml` |
| `APP_PROFILE_YAML` | Full contents of your local `profile.yaml` |

The workflow writes the last two secrets back out to `config.yaml`/`profile.yaml`
on the runner before each run — that's how personal config gets to GitHub
Actions without ever being committed.

### 5. Enable the workflow

Either wait for the hourly cron or trigger
**Actions → Discover jobs → Run workflow** manually to test end-to-end.

### 6. First run — expect to run it twice

The first time the pipeline runs against a fresh Sheet, every source treats
everything it fetches as a baseline rather than "new" — it seeds the `SeenJobs`
tab but deliberately writes **zero rows** to `Jobs`. Otherwise the very first
run would dump a company's entire current job board, or a tracker repo's whole
README, into your sheet as if it all just went live.

So: trigger it manually (or wait for the next hourly run), confirm `SeenJobs`
populated with rows and `Jobs` stayed empty, then trigger it a second time.
Only the second run compares against that baseline and writes anything that's
genuinely new and above `score_threshold` to `Jobs`. After that, the hourly
cron behaves normally — this manual double-run is a one-time thing for a new
Sheet (or if you ever add a new tracker/target company, only *that* source
re-bootstraps, not the whole sheet).

## Known limitation: LinkedIn rate limits

GitHub-hosted runners share Azure IP ranges reused across countless workflows.
JobSpy's own guidance is that LinkedIn rate-limits around page 10 of results per
IP — likely to trigger faster from a shared cloud IP than a residential one,
especially at hourly frequency. Mitigations already in place: each site is
scraped independently (one blocked site doesn't affect the others),
`results_wanted` is conservative by default, and `jobspy.enabled_sites` in
config.yaml lets you drop a persistently-blocked site with a one-line edit.
GitHub tracker repos and direct ATS polling aren't IP-reputation-sensitive the
same way, so they're the dependable backbone — JobSpy is a bonus source, not
the only feed. If LinkedIn blocking becomes a persistent problem, the next step
would be a self-hosted runner or a proxy, not built in by default.

## Adding a new discovery source

Implement `BaseSource.fetch()` in one new file under `src/jobfind/sources/`,
returning `list[Job]`, add one entry to `SOURCE_REGISTRY` in
`src/jobfind/sources/__init__.py`, and add its name to `active_sources` in
`config.yaml`. Nothing else needs to change.

## Swapping LLM providers

All scoring calls go through `src/jobfind/scoring/provider.py`. To use a
provider other than OpenRouter, add a new class implementing `LLMProvider`'s
`complete()` method in that file and one branch in `get_provider()`. No other
file references OpenRouter directly.

## Running tests

```bash
pip install -r requirements.txt
PYTHONPATH=src pytest
```
