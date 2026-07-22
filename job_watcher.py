#!/usr/bin/env python3
"""
Job Watcher — pulls open roles from company ATS boards (Greenhouse, Lever,
Ashby, SmartRecruiters, Recruitee, Workable), filters by keyword, and adds
any new matches to the Google Sheets application tracker as "wishlist" rows.

Run manually:
    SHEETS_URL="https://script.google.com/macros/s/.../exec" python job_watcher.py

Normally this runs on a schedule via GitHub Actions (see .github/workflows).
"""

import os
import sys
import json
import time
import datetime
import urllib.request
import urllib.error

try:
    import yaml
except ImportError:
    sys.exit("Missing dependency 'pyyaml'. Run: pip install -r requirements.txt")


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")
TIMEOUT = 20
USER_AGENT = "job-watcher/1.0 (personal job search tool)"


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        # text/plain avoids a CORS-style preflight; Apps Script parses the
        # body as JSON regardless of the declared content type.
        headers={"Content-Type": "text/plain;charset=utf-8", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---- ATS fetchers -----------------------------------------------------
# Each returns a list of dicts: {raw_id, title, location, url}

def fetch_greenhouse(token):
    data = http_get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
    out = []
    for j in data.get("jobs", []):
        out.append({
            "raw_id": str(j.get("id")),
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
        })
    return out


def fetch_lever(token):
    data = http_get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    out = []
    for j in data:
        out.append({
            "raw_id": str(j.get("id")),
            "title": j.get("text", ""),
            "location": ((j.get("categories") or {}).get("location") or ""),
            "url": j.get("hostedUrl", ""),
        })
    return out


def fetch_ashby(token):
    data = http_get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    out = []
    for j in data.get("jobs", []):
        out.append({
            "raw_id": str(j.get("id")),
            "title": j.get("title", ""),
            "location": j.get("location", ""),
            "url": j.get("jobUrl", ""),
        })
    return out


def fetch_smartrecruiters(token):
    data = http_get_json(f"https://api.smartrecruiters.com/v1/companies/{token}/postings")
    out = []
    for j in data.get("content", []):
        out.append({
            "raw_id": str(j.get("id")),
            "title": j.get("name", ""),
            "location": ((j.get("location") or {}).get("city") or ""),
            "url": f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}",
        })
    return out


def fetch_recruitee(token):
    data = http_get_json(f"https://{token}.recruitee.com/api/offers/")
    out = []
    for j in data.get("offers", []):
        out.append({
            "raw_id": str(j.get("id")),
            "title": j.get("title", ""),
            "location": j.get("location", ""),
            "url": j.get("careers_url", ""),
        })
    return out


def fetch_workable(token):
    data = http_get_json(f"https://apply.workable.com/api/v1/widget/accounts/{token}")
    out = []
    for j in data.get("jobs", []):
        out.append({
            "raw_id": str(j.get("shortcode") or j.get("id")),
            "title": j.get("title", ""),
            "location": j.get("location", ""),
            "url": j.get("url", ""),
        })
    return out


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "recruitee": fetch_recruitee,
    "workable": fetch_workable,
}


# ---- Sheets tracker integration ---------------------------------------

def get_existing_ids(sheets_url):
    try:
        data = http_get_json(sheets_url + "?action=list")
    except Exception as e:
        print(f"WARNING: could not read existing tracker rows ({e}); "
              f"proceeding without dedupe against the sheet.")
        return set()
    if not data.get("ok"):
        print(f"WARNING: tracker returned an error: {data.get('error')}")
        return set()
    return {row.get("id") for row in data.get("applications", [])}


def add_to_tracker(sheets_url, job):
    payload = {"action": "upsert", "app": job}
    result = http_post_json(sheets_url, payload)
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "unknown error"))


# ---- Notifications (optional) -----------------------------------------

def notify_discord(webhook_url, new_jobs):
    if not webhook_url or not new_jobs:
        return
    lines = [f"**{j['company']}** — {j['role']} ({j['location'] or 'n/a'})\n{j['link']}"
             for j in new_jobs]
    content = f"🔔 {len(new_jobs)} new job(s) found:\n\n" + "\n\n".join(lines)
    # Discord has a 2000 char limit per message; trim if needed.
    if len(content) > 1900:
        content = content[:1900] + "\n… (truncated)"
    payload = {"content": content}
    try:
        http_post_json(webhook_url, payload)
    except Exception as e:
        print(f"WARNING: failed to send Discord notification: {e}")


# ---- Main ---------------------------------------------------------------

def matches_keywords(title, keywords):
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in keywords)


def main():
    sheets_url = os.environ.get("SHEETS_URL", "").strip()
    if not sheets_url:
        sys.exit("Missing SHEETS_URL environment variable (your Apps Script Web App URL).")

    discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    companies = config.get("companies", [])
    keywords = config.get("keywords", [])

    if not keywords:
        print("WARNING: no keywords configured — every open role will match.")

    print(f"Loading existing tracker entries from Sheets...")
    existing_ids = get_existing_ids(sheets_url)
    print(f"Found {len(existing_ids)} existing entries.")

    today = datetime.date.today().isoformat()
    new_jobs = []

    for company in companies:
        name = company.get("name", "Unknown")
        ats = company.get("ats", "").lower()
        token = company.get("token", "")

        fetcher = FETCHERS.get(ats)
        if not fetcher:
            print(f"SKIP {name}: unknown ATS type '{ats}'")
            continue

        try:
            jobs = fetcher(token)
        except urllib.error.HTTPError as e:
            print(f"SKIP {name}: HTTP {e.code} — check the token/slug is correct")
            continue
        except Exception as e:
            print(f"SKIP {name}: {e}")
            continue

        matched = [j for j in jobs if matches_keywords(j["title"], keywords)]
        print(f"{name} ({ats}): {len(jobs)} open roles, {len(matched)} match keywords")

        for job in matched:
            stable_id = f"{ats}:{token}:{job['raw_id']}"
            if stable_id in existing_ids:
                continue  # already in the tracker — don't touch it, in case
                          # the user already moved its status

            entry = {
                "id": stable_id,
                "company": name,
                "role": job["title"],
                "status": "wishlist",
                "dateApplied": "",
                "link": job["url"],
                "notes": f"Auto-discovered via {ats} on {today}"
                         + (f" — {job['location']}" if job["location"] else ""),
            }
            try:
                add_to_tracker(sheets_url, entry)
                new_jobs.append(entry)
                print(f"  + added: {entry['role']}")
            except Exception as e:
                print(f"  ! failed to add '{entry['role']}': {e}")

        time.sleep(0.5)  # be polite to the APIs

    print(f"\nDone. {len(new_jobs)} new job(s) added to the tracker.")
    if new_jobs:
        notify_discord(discord_webhook, new_jobs)


if __name__ == "__main__":
    main()
