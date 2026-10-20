# Wayback WordPress Recovery

Recover your old WordPress blog from Internet Archive snapshots.

## Setup

Requires Python 3.9+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

- `SITE_URL`: **required**, the original website URL (e.g. `https://example.com`), not a Wayback URL.
- `ARCHIVE_FROM_YEAR` and `ARCHIVE_TO_YEAR`: the years to search, inclusive.
- `ARCHIVE_BASE_URL`: archive host (defaults to Internet Archive Wayback Machine).
- `MAX_CAPTURES_PER_POST`: number of known snapshots to try for each candidate.
- `EXCLUDE_PATHS`: comma-separated static-page slugs to skip.

## Run

```bash
python recover.py --limit 5   # test first
python recover.py             # full run
python recover.py --refresh   # ignore checkpoint and retry
```

Results in `recovered/`:

- `wordpress-import.xml`: draft-only WordPress import file.
- `posts.json`: extracted content and metadata.
- `recovery-report.csv`: results for each candidate URL.
- `checkpoint.json`: saved after every URL so reruns skip previous results.

Import WXR using **WordPress > Tools > Import > WordPress**. All posts are drafts. Check author mapping, dates, content, URLs and image availability before publishing. Images are **not downloaded**. Archive navigation may yield false candidates, and HTML structures differ by theme. Some posts may be unavailable or incompletely extracted. Missing dates are placeholders and must be corrected manually.

Only restore sites and content you have permission to reproduce. Be considerate of archive rate limits.
