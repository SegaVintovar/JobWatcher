# Job Watcher

Checks company job boards (Greenhouse, Lever, Ashby, SmartRecruiters,
Recruitee, Workable, and Workday) for openings matching your keywords,
and adds new matches straight into your Google Sheets application
tracker as "wishlist" entries — automatically, on a schedule.

It never touches a row that's already in the sheet, so if you've moved
something to "Applied" or "Interview," this won't overwrite it.

## Setup (one-time)

1. **Create a GitHub repo** and push these files to it (or upload
   them via the GitHub web UI: Add file → Upload files).

2. **Add repo secrets** (Settings → Secrets and variables → Actions →
   New repository secret):
   - `SHEETS_URL` — your Apps Script Web App URL from the job tracker
     setup (ends in `/exec`). Required.
   - `DISCORD_WEBHOOK_URL` — optional. If set, you'll get a Discord
     message whenever new jobs are found. Leave it out if you don't
     want this.
   - `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` — required only if you keep
     `adzuna.enabled: true` in config.yaml (see below). Free to get at
     https://developer.adzuna.com — sign up, create an app, copy both
     values.

3. **Edit `config.yaml`**:
   - Fill in real companies and their ATS token (the slug from their
     careers page URL — see the comments in the file for examples per
     platform).
   - Adjust the `keywords` list to match what you're looking for.
   - Any company with a wrong/unverified token just gets skipped with
     a warning in the logs — it won't break the rest of the run.

4. **Commit and push.** The workflow runs automatically every day at
   07:00 UTC. You can also trigger it manually any time from the
   repo's **Actions** tab → "Job Watcher" → "Run workflow".

## Testing it locally (optional)

```bash
pip install -r requirements.txt
export SHEETS_URL="https://script.google.com/macros/s/XXXX/exec"
python job_watcher.py
```

Check the terminal output — it prints how many roles it found per
company, how many matched your keywords, and what got added.

## Finding a company's ATS token

Open their careers page and look at the URL:

| ATS             | URL pattern                              | Token                |
|-----------------|-------------------------------------------|-----------------------|
| Greenhouse      | `boards.greenhouse.io/{token}`            | `{token}`             |
| Lever           | `jobs.lever.co/{token}`                   | `{token}`             |
| Ashby           | `jobs.ashbyhq.com/{token}`                | `{token}`             |
| SmartRecruiters | `careers.smartrecruiters.com/{Token}`     | `{Token}` (case-sensitive) |
| Recruitee       | `{token}.recruitee.com`                   | `{token}`             |
| Workable        | `apply.workable.com/{token}`              | `{token}`             |
| Workday         | `{tenant}.{wd_server}.myworkdayjobs.com/{site}` | needs all three fields — see `config.yaml` comments |

If a company's careers page doesn't match any of these patterns, it's
likely on a custom-built or heavily locked-down system this tool
doesn't support.

## Finding new companies automatically (Adzuna)

Everything above requires you to already know which company to watch.
Adzuna works differently: it's a job aggregator, so a keyword + country
search surfaces postings from companies you've never added to
`config.yaml` — that's the "what else is out there" layer.

In `config.yaml`:
```yaml
adzuna:
  enabled: true
  country: nl
  queries:
    - "junior software engineer"
    - "ai engineer"
```
Each line under `queries` is a separate search and costs one API call
per run — keep the list short and specific rather than broad and long,
both for your free-tier quota and to keep results relevant.

New Adzuna finds land in the same Wishlist column, tagged with the
actual hiring company (not "Adzuna") and a note saying how they were
found.

**Worth knowing:**
- Free tier has a request-per-day limit — check current limits at
  developer.adzuna.com if you add a lot of queries
- Adzuna's own coverage isn't complete — it won't catch everything, and
  can lag behind a company's own careers page
- If a job shows up both directly (via your company list) *and* via
  Adzuna, you may occasionally get two rows for the same role, since
  they're tracked under different IDs. Harmless — just delete the extra
  one if you notice it.

## A note on Workday

Workday support works differently from the rest: there's no official
public API, so this calls the same internal JSON endpoint Workday's own
careers pages use to render themselves in your browser. It works, but:

- It's not a documented, stable contract — Workday changing something
  internally could break it without notice. If a Workday company starts
  failing, that's the likely cause, not a bug in the config.
- Workday runs bot protection (Akamai). Running this more than once a
  day, or against many Workday tenants at once, raises the chance of
  getting temporarily blocked. The script paces requests and caps how
  many jobs it pages through per company to stay reasonably polite.
- Field detail is thinner than the other platforms (e.g. posting dates
  come through as relative text, not exact timestamps).

## Limitations

- Covers Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Workable,
  and Workday — not LinkedIn or Indeed.
- You maintain the company list yourself — it won't discover new
  companies on its own.
- Occasionally a company reposts the same role with a new internal ID,
  which can show up as a near-duplicate. Harmless, just delete the
  extra row if it bothers you.
- Respect each platform's terms of use — this only calls the same
  public (or, for Workday, effectively public-facing) endpoints their
  own careers pages use, at a light, scheduled pace, not aggressive
  scraping.
