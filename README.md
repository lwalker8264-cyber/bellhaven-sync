# bellhaven-sync

Keeps the CRM's Bellhaven Senior Living ownership picture in line with Bellhaven's public website.
It scrapes every community, matches each one to a CRM account, proposes fixes with evidence,
and writes to the CRM **only** when a reviewer approves a proposal in the local review app.

> © 2026 Logan Walker. All rights reserved. Submitted for hiring evaluation only. See [NOTICE](NOTICE).

Pure Python standard library (3.10+). Nothing to `pip install`.

## Run it

```bash
cp .env.example .env              # then paste your CRM token into .env (git-ignored)
python -m bellhaven_sync sync     # scrape + match + queue proposals (read-only against the CRM)
python -m bellhaven_sync review   # open http://127.0.0.1:8000 and approve / reject
python -m bellhaven_sync status   # queue counts
python -m unittest discover tests # end-to-end tests on a snapshot of the sandbox data
```

## How it works

| Step | File | What it does |
|---|---|---|
| Scrape | `scraper.py` | Crawls the home page, About page and every directory page, and collects every `/communities/<slug>` link it sees anywhere. (Bellhaven Meadows of Findlay is announced on the home page but missing from the directory.) Parses name, street, city, state, zip, care offerings, administrator and phone. |
| Normalize | `normalize.py` | "Blvd" = "Boulevard", "Rehab and" = "Rehabilitation &", "Health Care Center" = "Healthcare Centre", so cosmetic differences never become proposals. |
| Match | `matcher.py` | Scores each location against each account: same street address, same name in the same city, same phone, and whether the website's administrator is a CRM contact on the account. A match requires the address, or the exact name in the same city. Several matches = duplicates. |
| Decide | `store.py` | SQLite log of every proposal, keyed by a fingerprint of the change. Already-seen fingerprints (pending, applied or rejected) are never re-proposed. |
| Review | `app.py` | Each card shows the website listing beside the CRM account, the reason, the billing SOP check, and the exact API calls that will run. Approve is the only path that writes. |
| Apply | `apply.py` | Re-reads the account first and refuses if it changed since the proposal (stale). Re-checks the billing SOP on live revenue/AR. Won't create the same account twice. |

## Outcomes it handles

| Kind | Rule |
|---|---|
| Confident match | Nothing to change, so nothing is proposed. |
| Fix | Out-of-date name, street, city, zip or care type. |
| Re-parent | On the website but under another parent (or none). Moved directly when revenue history or outstanding AR is zero. |
| Change of ownership (SOP) | Needs a new parent and has revenue **and** AR > 0: the old account is left exactly as is, a new account is created under the correct parent, and the old account's `chow_current_account` points to it. If the current owner's account already exists (Bellhaven of Sandusky → Millstone Care of Sandusky), it links to that account instead of creating a third copy. |
| Duplicate | Several accounts at one location. The survivor is the one already under Bellhaven, then the one whose contact is the website's administrator, then matching phone, then billing history. Losers get `duplicate_of_account` and `Inactive`. |
| New account | On the website with no CRM account. Same-name accounts elsewhere (Amberly Manor in Colorado) and same-city accounts at other addresses (Union Square Senior Living) are shown as context but never merged. |
| Not on website | Under Bellhaven but not on the site. If another operator's account sits at the same address, the SOP decides how to link it. Otherwise it is set to `Needs Review` with a note; the tool doesn't guess a new owner. |
| Review flags | A tie between duplicate copies, or a PO Box billing street that differs from the website, gets `Needs Review` plus a note explaining why. The PO Box is kept as the billing address. |

## Scheduling

* `.github/workflows/daily-sync.yml` runs every day at 11:17 UTC. It restores and saves the decision log
  between runs, runs the tests, then syncs. The token is a repository secret.
* `schedule/crontab.txt` holds the cron entry (and the Windows Task Scheduler line) for running the sync on the same
  machine as the review app, so both share `data/state.db`. That is the setup used for this exercise.
  In production the decision log would be a shared database.

The sync step never writes to the CRM, so running it any number of times is safe.
