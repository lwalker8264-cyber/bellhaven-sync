# Copyright (c) 2026 Logan Walker. All rights reserved. See NOTICE.
"""Usage:
    python -m bellhaven_sync sync      # scrape + match + queue proposals (never writes to the CRM)
    python -m bellhaven_sync review    # start the local review app at http://127.0.0.1:8000
    python -m bellhaven_sync status    # print queue counts
"""
import json
import sys

from . import config
from .store import Store


def sync(crm=None, scrape_fn=None, store=None):
    from .crm import CRMClient
    from .matcher import build_proposals
    from .scraper import scrape

    crm = crm or CRMClient()
    store = store or Store(config.DB_PATH)
    run_id = store.start_run()
    locations = (scrape_fn or scrape)()
    accounts = crm.list_accounts()
    contacts = crm.list_contacts()
    proposals, report = build_proposals(locations, accounts, contacts)
    result = store.add_proposals(proposals, run_id)
    summary = {"locations_scraped": len(locations), "crm_accounts": len(accounts),
               "confirmed_no_change": len(report["confirmed"]), "proposals_generated": len(proposals), **result}
    store.finish_run(run_id, summary)
    out = config.ROOT / "data" / "last_scrape.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(locations, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return summary


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "help"
    if cmd == "sync":
        sync()
    elif cmd == "review":
        from .app import serve
        serve()
    elif cmd == "status":
        print(json.dumps(Store(config.DB_PATH).counts(), indent=2))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
