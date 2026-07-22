# Job Watcher

Checks company job boards (Greenhouse, Lever, Ashby, SmartRecruiters,
Recruitee, Workable) for openings matching your keywords, and adds new
matches straight into your Google Sheets application tracker as
"wishlist" entries — automatically, on a schedule.

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

If a company's careers page doesn't match any of these patterns, it's
likely on Workday or a custom-built system — this tool doesn't support
those (see the "Limitations" section below).

## Limitations

- Only covers the six ATS platforms above — not LinkedIn, Indeed, or
  Workday (Workday doesn't expose a stable public API the same way).
- You maintain the company list yourself — it won't discover new
  companies on its own.
- Occasionally a company reposts the same role with a new internal ID,
  which can show up as a near-duplicate. Harmless, just delete the
  extra row if it bothers you.
- Respect each platform's terms of use — this only calls the same
  public endpoints their own careers pages use, at a light, scheduled
  pace (once a day), not aggressive scraping.
