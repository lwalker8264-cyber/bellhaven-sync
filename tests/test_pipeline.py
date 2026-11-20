# Copyright (c) 2026 Logan Walker. All rights reserved. See NOTICE.
"""End-to-end test against a fake CRM + fake website built from a snapshot of the real sandbox.
Run:  python -m unittest discover tests -v"""
import csv
import itertools
import tempfile
import unittest
from pathlib import Path

from bellhaven_sync.__main__ import sync
from bellhaven_sync.apply import apply
from bellhaven_sync.scraper import scrape
from bellhaven_sync.store import Store

FIX = Path(__file__).parent / "fixtures"
BELLHAVEN = "0015QAPLGS3FVYEEEM"


def _tsv(name):
    with open(FIX / name, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


class FakeCRM:
    _ids = itertools.count(1)

    def __init__(self):
        names = {r["account_id"]: r["name"] for r in _tsv("accounts.tsv")}
        self.accounts = {}
        for r in _tsv("accounts.tsv"):
            r.update(lifetime_revenue=float(r["lifetime_revenue"]), outstanding_ar=float(r["outstanding_ar"]),
                     parent_name=names.get(r["parent_id"], ""), chow_current_account="", duplicate_of_account="",
                     note="", created_by_candidate=False)
            self.accounts[r["account_id"]] = r
        self.contacts = [dict(c, is_active=True) for c in _tsv("contacts.tsv")]
        self.writes = []

    def list_accounts(self, **f):
        out = [dict(a) for a in self.accounts.values()]
        if f.get("street"):
            out = [a for a in out if a["billing_street"] == f["street"]]
        return out

    def list_contacts(self):
        return self.contacts

    def get_account(self, aid):
        return dict(self.accounts[aid])

    def update_account(self, aid, fields):
        self.writes.append(("patch", aid, fields))
        self.accounts[aid].update(fields)
        return self.accounts[aid]

    def create_account(self, fields):
        aid = f"NEW{next(self._ids):04d}"
        acct = {"account_id": aid, "lifetime_revenue": 0, "outstanding_ar": 0, "chow_current_account": "",
                "duplicate_of_account": "", "created_by_candidate": True, **fields}
        self.accounts[aid] = acct
        self.writes.append(("create", aid, fields))
        return acct


def fake_site():
    """Mimics the real site: 34 communities over 3 directory pages, Findlay linked ONLY from the home page."""
    locs = _tsv("locations.tsv")
    directory = [l for l in locs if l["slug"] != "bellhaven-meadows-of-findlay"]
    pages = {"/": '<a href="/communities">dir</a><a href="/communities/bellhaven-meadows-of-findlay">new</a>',
             "/about": "<p>about</p>"}
    for i in range(3):
        chunk = directory[i * 16:(i + 1) * 16]
        nxt = f'<a href="/communities?page={i + 2}">Next</a>' if i < 2 else ""
        body = "".join(f'<a href="/communities/{l["slug"]}">{l["name"]}</a>' for l in chunk) + nxt
        pages["/communities" if i == 0 else f"/communities?page={i + 1}"] = body
    for l in locs:
        badges = "".join(f'<span class="badge">{c}</span>' for c in l["care"].split("|"))
        pages[f"/communities/{l['slug']}"] = (
            f'<div class="wrap"><h1>{l["name"]}</h1><dl class="detail"><dt>Address</dt><dd>{l["street"]}<br>'
            f'{l["city"]}, {l["state"]} {l["zip"]}</dd><dt>Care Offerings</dt><dd>{badges}</dd>'
            f'<dt>Administrator</dt><dd>{l["admin"]}</dd><dt>Phone</dt><dd>{l["phone"]}</dd></dl></div>')
    return lambda: scrape("https://x", fetch=lambda url: pages[url.replace("https://x", "")])


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "state.db")
        self.crm = FakeCRM()
        self.site = fake_site()

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def run_sync(self):
        return sync(crm=self.crm, scrape_fn=self.site, store=self.store)

    def by_account(self, aid):
        return [p for p in self.store.list() if p["account_id"] == aid]

    def test_scraper_finds_all_35_including_homepage_only_findlay(self):
        locs = self.site()
        self.assertEqual(len(locs), 35)
        f = next(l for l in locs if l["slug"] == "bellhaven-meadows-of-findlay")
        self.assertEqual((f["street"], f["zip"], f["care_offerings"]),
                         ("1800 N Blanchard St", "45840", ["Assisted Living", "Memory Support"]))

    def test_sync_never_writes(self):
        self.run_sync()
        self.assertEqual(self.crm.writes, [])

    def test_expected_classifications(self):
        self.run_sync()
        kinds = {}
        for p in self.store.list():
            kinds.setdefault(p["kind"], []).append(p["title"])
        for k, v in sorted(kinds.items()):
            print(f"\n{k} ({len(v)})"); [print("   ", t) for t in v]
        # CHOW: Tiffin & Marietta have revenue AND AR -> preserve; Sandusky sold -> link to Millstone account
        chow = {p["account_id"] for p in self.store.list() if p["kind"] == "chow"}
        self.assertEqual(chow, {"001U6RW32TY0WSXZZB", "001A34WFSUYHCRBLFT", "001SXSF4ELF0Z2LGDM"})
        # direct re-parent: Lima (rev, no AR), Findlay (rev, no AR), Zanesville, Kettering survivor
        rep = {p["account_id"] for p in self.store.list() if p["kind"] == "reparent"}
        self.assertEqual(rep, {"001LGFPBJY4N9MB6KL", "001UKEFGADQ8YCZ4YM", "001H1JMVZWP46D5VUF",
                               "001WR41PYNWXCAE2X4"})  # Kettering: 3-way tie, Harborview copy kept
        # traps: Amberly Manor (CO) and Union Square (Juniper, other address) must NOT be touched
        self.assertEqual(self.by_account("0015D74ZLRY810RGY5"), [])
        self.assertEqual(self.by_account("001GNU41AVXZRLLJ9P"), [])
        new = sorted(p["location_slug"] for p in self.store.list() if p["kind"] == "new_account")
        self.assertEqual(new, ["amberly-manor", "bellhaven-at-union-square", "bellhaven-of-batavia",
                               "bellhaven-of-carlisle"])
        # Owosso: survivor is the copy whose contact is the website administrator
        dup = [p for p in self.store.list() if p["kind"] == "duplicate" and p["location_slug"] == "bellhaven-of-owosso"]
        self.assertEqual([p["account_id"] for p in dup], ["001QU150PM4Z15UA71"])
        # cosmetic-only differences produce nothing (Altoona Boulevard, Grove City Rehab, Arbors, Ashland)
        for aid in ("0011D2P3CWSHMF81C8", "001TZCSTBZFM6K5BGN", "0010KJFP601YNTZVDA", "001RJ0D7Z5MAZN5HX5"):
            self.assertEqual(self.by_account(aid), [], aid)

    def test_approve_all_then_rerun_is_idempotent(self):
        self.run_sync()
        for p in self.store.list("pending"):
            self.store.set_status(p["id"], "applied", result=apply(self.crm, p))
        a = self.crm.accounts
        # SOP: Tiffin old account untouched except chow pointer; new account under Bellhaven
        tiffin = a["001U6RW32TY0WSXZZB"]
        self.assertEqual(tiffin["parent_id"], "001FWSQ30SFW6S7604")
        self.assertEqual(a[tiffin["chow_current_account"]]["parent_id"], BELLHAVEN)
        self.assertEqual(a["001SXSF4ELF0Z2LGDM"]["chow_current_account"], "0017JP8Z1UQ763BVK3")
        self.assertEqual(a["001SXSF4ELF0Z2LGDM"]["parent_id"], BELLHAVEN)
        self.assertEqual(a["001LGFPBJY4N9MB6KL"]["parent_id"], BELLHAVEN)
        self.assertEqual(a["001JD2MWRA74LTSN24"]["duplicate_of_account"], "001UELXDAKFRKB8932")
        self.assertEqual(a["001JD2MWRA74LTSN24"]["status"], "Inactive")
        self.assertEqual(a["001CF3LDWVRGL09P4F"]["billing_zip"], "45662")
        # Kettering: Harborview copy kept, re-parented, flagged for review with a note
        k = a["001WR41PYNWXCAE2X4"]
        self.assertEqual((k["parent_id"], k["name"], k["status"]), (BELLHAVEN, "Bellhaven of Kettering", "Needs Review"))
        self.assertIn("Kept as the surviving record", k["note"])
        # Ashtabula: PO Box kept as billing address, flagged for review with a note
        ash = a["001NXP9X46CWEPSLSV"]
        self.assertEqual((ash["billing_street"], ash["status"]), ("PO Box 517", "Needs Review"))
        self.assertIn("3156 W Prospect Rd", ash["note"])
        # second run: nothing new
        second = self.run_sync()
        self.assertEqual(second["added"], 0, self.store.list("pending"))
        self.assertEqual(second["proposals_generated"], 0)

    def test_rejected_items_are_not_reproposed(self):
        self.run_sync()
        first = self.store.list("pending")
        for p in first:
            self.store.set_status(p["id"], "rejected")
        second = self.run_sync()
        self.assertEqual(second["added"], 0)
        self.assertEqual(second["already_known"], len(first))

    def test_reopen_rejected(self):
        self.run_sync()
        p = self.store.list("pending")[0]
        self.store.set_status(p["id"], "rejected", "demo")
        self.assertTrue(self.store.reopen(p["id"], "changed my mind"))
        self.assertEqual(self.store.get(p["id"])["status"], "pending")
        self.assertFalse(self.store.reopen(p["id"]))  # only rejected items can be reopened
        self.assertEqual(self.run_sync()["added"], 0)   # reopening doesn't create a duplicate proposal

    def test_stale_proposal_is_refused(self):
        self.run_sync()
        p = next(p for p in self.store.list() if p["kind"] == "fix")
        self.crm.accounts[p["account_id"]][next(iter(p["before"]))] = "edited by a rep meanwhile"
        from bellhaven_sync.apply import StaleProposal
        with self.assertRaises(StaleProposal):
            apply(self.crm, p)


if __name__ == "__main__":
    unittest.main()
