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
import urllib.parse

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


def http_post_json_raw(url, payload, extra_headers=None):
    """Like http_post_json, but sends real application/json — needed for
    APIs (like Workday) that actually validate the content type, unlike
    Apps Script which we deliberately dodge preflight for elsewhere."""
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": USER_AGENT}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---- ATS fetchers -----------------------------------------------------
# Each returns a list of dicts: {raw_id, title, location, url}
# Each takes the full company config dict (not just the token), since
# Workday needs more than a single slug to build its URL.

def fetch_greenhouse(company):
    token = company["token"]
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


def fetch_lever(company):
    token = company["token"]
    # Lever runs two separate regional hosts. A company whose careers page
    # is on jobs.eu.lever.co (not jobs.lever.co) is EU-hosted, and 404s on
    # the global host. Try global first, then EU, so this works either way
    # without needing to know a company's region in advance.
    region = company.get("region", "auto")
    if region == "eu":
        hosts = ["api.eu.lever.co"]
    elif region == "global":
        hosts = ["api.lever.co"]
    else:
        hosts = ["api.lever.co", "api.eu.lever.co"]

    last_error = None
    for host in hosts:
        try:
            data = http_get_json(f"https://{host}/v0/postings/{token}?mode=json")
            out = []
            for j in data:
                out.append({
                    "raw_id": str(j.get("id")),
                    "title": j.get("text", ""),
                    "location": ((j.get("categories") or {}).get("location") or ""),
                    "url": j.get("hostedUrl", ""),
                })
            return out
        except urllib.error.HTTPError as e:
            if e.code == 404:
                last_error = e
                continue
            raise
    raise last_error


def fetch_ashby(company):
    token = company["token"]
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


def fetch_smartrecruiters(company):
    token = company["token"]
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


def fetch_recruitee(company):
    token = company["token"]
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


def fetch_workable(company):
    token = company["token"]
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


def fetch_workday(company):
    """
    Workday has no official public API for this. Every Workday careers
    site quietly calls its own internal JSON endpoint to render the page
    you see in the browser — that's what we call here. It's widely used
    for this purpose, but it's NOT a documented/stable contract like the
    other ATS platforms, so:
      - field shapes can vary slightly between Workday versions/tenants
      - Workday runs Akamai bot protection; occasional blocks/CAPTCHAs
        are possible, especially if run too frequently
      - if it breaks, it's Workday's internal API changing, not a bug
        in the logic below

    Config needs three fields instead of a single token:
      tenant:    subdomain before .wdN.myworkdayjobs.com
      wd_server: the "wdN" part, e.g. wd1, wd3, wd5 (check the real URL)
      site:      the path segment after the domain

    Example: https://priceline.wd1.myworkdayjobs.com/BookingHoldings
      tenant: priceline
      wd_server: wd1
      site: BookingHoldings
    """
    tenant = company.get("tenant")
    wd_server = company.get("wd_server", "wd1")
    site = company.get("site")
    if not tenant or not site:
        raise ValueError("Workday entries need 'tenant' and 'site' set in config.yaml")

    base = f"https://{tenant}.{wd_server}.myworkdayjobs.com"
    url = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    referer = f"{base}/en-US/{site}"

    out = []
    limit = 20
    offset = 0
    max_jobs = 300  # safety cap — don't hammer a huge tenant on every run
    while True:
        payload = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""}
        data = http_post_json_raw(url, payload, extra_headers={
            "Accept-Language": "en-US",
            "Referer": referer,
        })
        postings = data.get("jobPostings", [])
        if not postings:
            break
        for j in postings:
            path = j.get("externalPath", "")
            out.append({
                "raw_id": path or f"offset{offset}-{j.get('title', '')}",
                "title": j.get("title", ""),
                "location": j.get("locationsText", ""),
                "url": base + path,
            })
        offset += limit
        total = data.get("total", 0)
        if offset >= total or offset >= max_jobs:
            break
        time.sleep(0.4)  # be extra polite — Workday is more block-happy
    return out


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "recruitee": fetch_recruitee,
    "workable": fetch_workable,
    "workday": fetch_workday,
}


# ---- Adzuna discovery layer --------------------------------------------
# Unlike the fetchers above (one specific company each), Adzuna is a job
# aggregator: you give it a keyword + country, and it returns matching
# postings from many different companies — including ones you haven't
# added to config.yaml at all. This is what covers "other companies I
# don't know about yet."
#
# Needs a free API key from https://developer.adzuna.com — set
# ADZUNA_APP_ID and ADZUNA_APP_KEY as environment variables / repo secrets.

def fetch_adzuna(query, adzuna_config):
    app_id = os.environ.get("ADZUNA_APP_ID", "").strip()
    app_key = os.environ.get("ADZUNA_APP_KEY", "").strip()
    if not app_id or not app_key:
        raise RuntimeError("ADZUNA_APP_ID / ADZUNA_APP_KEY environment variables not set")

    country = adzuna_config.get("country", "nl")
    results_per_page = adzuna_config.get("results_per_page", 50)
    max_days_old = adzuna_config.get("max_days_old")

    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": query,
        "content-type": "application/json",
        "results_per_page": str(results_per_page),
    }
    if max_days_old:
        params["max_days_old"] = str(max_days_old)

    query_string = urllib.parse.urlencode(params)
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1?{query_string}"
    data = http_get_json(url)

    out = []
    for j in data.get("results", []):
        out.append({
            "raw_id": str(j.get("id")),
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("display_name", ""),
            "url": j.get("redirect_url", ""),
            "company": (j.get("company") or {}).get("display_name", "Unknown"),
        })
    return out


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
    if not keywords:
        return True
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in keywords)


def matches_exclude(title, exclude_keywords):
    if not exclude_keywords:
        return False
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in exclude_keywords)


def matches_location(location, locations):
    if not locations:
        return True  # location filtering disabled
    if not location:
        return False  # can't confirm a match with no location data
    location_lower = location.lower()
    return any(loc.lower() in location_lower for loc in locations)


def main():
    sheets_url = os.environ.get("SHEETS_URL", "").strip()
    if not sheets_url:
        sys.exit("Missing SHEETS_URL environment variable (your Apps Script Web App URL).")

    discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    companies = config.get("companies", [])
    keywords = config.get("keywords", [])
    exclude_keywords = config.get("exclude_keywords", [])
    locations = config.get("locations", [])

    if not keywords:
        print("WARNING: no keywords configured — every open role will match.")
    if locations:
        print(f"Location filter active: {', '.join(locations)}")
    if exclude_keywords:
        print(f"Excluding titles containing: {', '.join(exclude_keywords)}")

    print(f"Loading existing tracker entries from Sheets...")
    existing_ids = get_existing_ids(sheets_url)
    print(f"Found {len(existing_ids)} existing entries.")

    today = datetime.date.today().isoformat()
    new_jobs = []

    for company in companies:
        name = company.get("name", "Unknown")
        ats = company.get("ats", "").lower()
        # Workday entries key off tenant+site instead of a single token
        id_key = company.get("token") or f"{company.get('tenant', '')}-{company.get('site', '')}"

        fetcher = FETCHERS.get(ats)
        if not fetcher:
            print(f"SKIP {name}: unknown ATS type '{ats}'")
            continue

        try:
            jobs = fetcher(company)
        except urllib.error.HTTPError as e:
            print(f"SKIP {name}: HTTP {e.code} — check the token/tenant config is correct")
            continue
        except Exception as e:
            print(f"SKIP {name}: {e}")
            continue

        matched = [
            j for j in jobs
            if matches_keywords(j["title"], keywords)
            and not matches_exclude(j["title"], exclude_keywords)
            and matches_location(j["location"], locations)
        ]
        print(f"{name} ({ats}): {len(jobs)} open roles, {len(matched)} match after filters")

        for job in matched:
            stable_id = f"{ats}:{id_key}:{job['raw_id']}"
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

    # ---- Adzuna discovery pass: other companies not in the config above ----
    adzuna_config = config.get("adzuna", {})
    if adzuna_config.get("enabled"):
        queries = adzuna_config.get("queries", [])
        country = adzuna_config.get("country", "nl")
        for query in queries:
            try:
                jobs = fetch_adzuna(query, adzuna_config)
            except Exception as e:
                print(f"SKIP Adzuna query '{query}': {e}")
                continue

            matched = [
                j for j in jobs
                if matches_keywords(j["title"], keywords)
                and not matches_exclude(j["title"], exclude_keywords)
                and matches_location(j["location"], locations)
            ]
            print(f"Adzuna '{query}': {len(jobs)} results, {len(matched)} match after filters")

            for job in matched:
                stable_id = f"adzuna:{country}:{job['raw_id']}"
                if stable_id in existing_ids:
                    continue

                entry = {
                    "id": stable_id,
                    "company": job["company"],
                    "role": job["title"],
                    "status": "wishlist",
                    "dateApplied": "",
                    "link": job["url"],
                    "notes": f"Auto-discovered via Adzuna on {today}"
                             + (f" — {job['location']}" if job["location"] else ""),
                }
                try:
                    add_to_tracker(sheets_url, entry)
                    new_jobs.append(entry)
                    existing_ids.add(stable_id)
                    print(f"  + added: {entry['company']} — {entry['role']}")
                except Exception as e:
                    print(f"  ! failed to add '{entry['role']}': {e}")

            time.sleep(0.5)

    print(f"\nDone. {len(new_jobs)} new job(s) added to the tracker.")
    if new_jobs:
        notify_discord(discord_webhook, new_jobs)


if __name__ == "__main__":
    main()
