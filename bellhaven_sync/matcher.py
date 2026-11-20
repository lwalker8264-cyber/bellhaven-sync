# Copyright (c) 2026 Logan Walker. All rights reserved. See NOTICE.
"""Turns (website locations, CRM accounts, CRM contacts) into proposed changes.

Nothing here writes to the CRM. Every output is a proposal with:
  kind, title, account_id, location slug, confidence,
  ops      - the exact API calls that run if a reviewer approves
  before   - the current values of every field the ops change (checked again at apply time)
  evidence - what the reviewer needs to judge it
"""
import hashlib
import json
from datetime import date

from . import config
from .normalize import (care_types, is_parent_account, name_key, name_similarity, person_key,
                        phone_key, street_key)

TAG = "[bellhaven-sync]"


# ---------------------------------------------------------------- helpers
def has_billing_history(acct) -> bool:
    """The SOP test: revenue history AND outstanding AR > 0 means the old account must be preserved."""
    return (acct.get("lifetime_revenue") or 0) > 0 and (acct.get("outstanding_ar") or 0) > 0


def _note(acct, text):
    stamp = f"{TAG} {date.today().isoformat()}: {text}"
    existing = (acct.get("note") or "").strip()
    return f"{existing}\n{stamp}" if existing else stamp


def fingerprint(kind, account_id, slug, ops):
    """Stable id for 'the same decision'. Notes are excluded (they contain dates)."""
    core = [{k: v for k, v in op.items() if k != "fields"} | {
        "fields": {k: v for k, v in op.get("fields", {}).items() if k != "note"}} for op in ops]
    raw = json.dumps([kind, account_id, slug, core], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _proposal(kind, title, acct, loc, confidence, ops, evidence):
    before = {}
    if acct:
        for op in ops:
            if op["op"] == "patch" and op["account_id"] == acct["account_id"]:
                for f in op["fields"]:
                    if f != "note":
                        before[f] = acct.get(f, "")
    return {
        "kind": kind,
        "title": title,
        "account_id": acct["account_id"] if acct else "",
        "location_slug": loc["slug"] if loc else "",
        "confidence": confidence,
        "ops": ops,
        "before": before,
        "evidence": evidence,
        "fingerprint": fingerprint(kind, acct["account_id"] if acct else "", loc["slug"] if loc else "", ops),
    }


def _account_view(a, contacts):
    return {
        "account_id": a["account_id"], "name": a["name"], "parent": a.get("parent_name") or "(none)",
        "address": f'{a.get("billing_street")}, {a.get("billing_city")}, {a.get("billing_state")} {a.get("billing_zip")}',
        "care_type": a.get("care_type"), "status": a.get("status"), "phone": a.get("phone"),
        "lifetime_revenue": a.get("lifetime_revenue"), "outstanding_ar": a.get("outstanding_ar"),
        "contacts": [f'{c["name"]} ({c.get("title")})' for c in contacts.get(a["account_id"], [])],
    }


def _location_view(loc):
    return {"name": loc["name"], "address": f'{loc["street"]}, {loc["city"]}, {loc["state"]} {loc["zip"]}',
            "care_offerings": ", ".join(loc["care_offerings"]), "administrator": loc["administrator"],
            "phone": loc["phone"], "url": loc["url"]}


def _new_account_fields(loc, parent_id, note):
    ct = care_types(loc["care_offerings"])
    return {
        "name": loc["name"], "parent_id": parent_id, "billing_street": loc["street"],
        "billing_city": loc["city"], "billing_state": loc["state"], "billing_zip": loc["zip"],
        "care_type": ct[0] if ct else "", "phone": loc["phone"], "status": "Active", "note": note,
    }


# ---------------------------------------------------------------- scoring
def score(loc, acct, contacts):
    """Signals linking one website location to one CRM account."""
    s = {}
    same_state = (acct.get("billing_state") or "").upper() == loc["state"].upper()
    same_city = (acct.get("billing_city") or "").lower() == loc["city"].lower()
    same_zip = (acct.get("billing_zip") or "") == loc["zip"]
    s["same_street"] = street_key(acct.get("billing_street")) == street_key(loc["street"])
    s["address"] = s["same_street"] and same_state and (same_city or same_zip)
    s["city_state"] = same_city and same_state
    s["name_exact"] = name_key(acct.get("name")) == name_key(loc["name"])
    s["name_similarity"] = round(name_similarity(acct.get("name"), loc["name"]), 2)
    s["phone"] = bool(phone_key(loc["phone"])) and phone_key(acct.get("phone")) == phone_key(loc["phone"])
    admin = person_key(loc["administrator"])
    s["administrator_contact"] = bool(admin) and any(
        person_key(c["name"]) == admin for c in contacts.get(acct["account_id"], []))
    s["is_match"] = s["address"] or (s["city_state"] and s["name_exact"])
    strong = sum([s["name_exact"], s["phone"], s["administrator_contact"]])
    s["confidence"] = "high" if (s["address"] and strong >= 1) or (s["city_state"] and strong >= 2) else "medium"
    return s


def _survivor_rank(acct, sig, target_parent_id):
    """Which copy of a duplicated facility should survive."""
    return (
        acct.get("parent_id") == target_parent_id,       # already under Bellhaven
        sig["administrator_contact"],                     # website administrator is a contact here
        sig["phone"],                                     # phone matches the website
        (acct.get("lifetime_revenue") or 0) > 0,          # keep the copy billing has history on
        sig["name_similarity"],
        acct.get("status") == "Active",
    ), acct["account_id"]


def is_po_box(street) -> bool:
    return street_key(street).replace(" ", "").startswith(("pobox", "pob", "postofficebox"))


def _review_flags(acct, loc, survivor_tied, tied_with):
    """Situations a human should double-check even after approval: the account gets status
    'Needs Review' and a note explaining why, instead of the tool guessing."""
    notes, existing = [], acct.get("note") or ""
    if "Kept as the surviving record" in existing or "kept as the billing address" in existing:
        return notes  # already flagged on a previous run; don't re-propose
    if survivor_tied:
        notes.append(f"Kept as the surviving record over {', '.join(tied_with)}: all copies tied on address, "
                     "phone, administrator contact and billing history. Chosen because it came from an operator "
                     "Bellhaven acquired. Please confirm the right record survived.")
    if is_po_box(acct.get("billing_street")) and street_key(acct.get("billing_street")) != street_key(loc["street"]):
        notes.append(f"Billing street '{acct.get('billing_street')}' kept as the billing address (looks like a "
                     f"mailing address). Website lists the facility at {loc['street']}, {loc['city']}, "
                     f"{loc['state']} {loc['zip']}. Please confirm.")
    return notes


def _desired_fixes(acct, loc):
    """Field changes so the account reflects the website. Cosmetic differences are ignored.
    A PO Box billing street is NOT overwritten (it is likely a real billing/mailing address)."""
    fixes = {}
    if name_key(acct.get("name")) != name_key(loc["name"]):
        fixes["name"] = loc["name"]
    if street_key(acct.get("billing_street")) != street_key(loc["street"]) and not is_po_box(acct.get("billing_street")):
        fixes["billing_street"] = loc["street"]
    if (acct.get("billing_city") or "").lower() != loc["city"].lower():
        fixes["billing_city"] = loc["city"]
    if (acct.get("billing_state") or "").upper() != loc["state"].upper():
        fixes["billing_state"] = loc["state"]
    if (acct.get("billing_zip") or "") != loc["zip"]:
        fixes["billing_zip"] = loc["zip"]
    wanted = care_types(loc["care_offerings"])
    if wanted and acct.get("care_type") not in wanted:
        fixes["care_type"] = wanted[0]
    return fixes


# ---------------------------------------------------------------- main
def build_proposals(locations, accounts, contacts_list):
    contacts = {}
    for c in contacts_list:
        if c.get("is_active", True):
            contacts.setdefault(c["account_id"], []).append(c)
    by_id = {a["account_id"]: a for a in accounts}

    target = next((a for a in accounts if a["name"] == config.TARGET_PARENT_NAME), None)
    if not target:
        raise RuntimeError(f"Parent account '{config.TARGET_PARENT_NAME}' not found in CRM")
    tid = target["account_id"]

    # Accounts that are already resolved history are not candidates:
    # duplicates point at a survivor; CHOW'd accounts point at their current account.
    facilities = [a for a in accounts if not is_parent_account(a)
                  and not a.get("duplicate_of_account") and not a.get("chow_current_account")]

    proposals, report, matched_ids = [], {"confirmed": [], "locations": len(locations)}, set()

    for loc in locations:
        scored = [(a, score(loc, a, contacts)) for a in facilities]
        matches = [(a, s) for a, s in scored if s["is_match"]]
        lv = _location_view(loc)

        if not matches:
            related = sorted(((a, s) for a, s in scored if s["city_state"] and s["name_similarity"] >= 0.3),
                             key=lambda x: -x[1]["name_similarity"])
            note = f"Created from Bellhaven website listing {loc['url']}."
            if related:
                note += " Possibly related (different address, not merged): " + ", ".join(
                    f'{a["account_id"]} {a["name"]}' for a, _ in related)
            ops = [{"op": "create", "ref": "new", "fields": _new_account_fields(loc, tid, f"{TAG} {note}")}]
            proposals.append(_proposal(
                "new_account", f"Create account: {loc['name']} ({loc['city']}, {loc['state']})", None, loc,
                "high" if not related else "medium", ops,
                {"website": lv, "why": "No CRM account matches this location's address, or its name in the same city.",
                 "nearest_non_matches": [_account_view(a, contacts) | {"signals": s} for a, s in related]}))
            continue

        matches.sort(key=lambda x: _survivor_rank(x[0], x[1], tid), reverse=True)
        survivor, ssig = matches[0]
        for a, _ in matches:
            matched_ids.add(a["account_id"])

        # --- duplicates (every match except the survivor)
        for loser, lsig in matches[1:]:
            ops = [{"op": "patch", "account_id": loser["account_id"], "fields": {
                "duplicate_of_account": survivor["account_id"], "status": "Inactive",
                "note": _note(loser, f"Duplicate of {survivor['account_id']} ({survivor['name']}); same facility "
                                     f"as Bellhaven listing {loc['url']}.")}}]
            warn = []
            if has_billing_history(loser):
                warn.append("This copy has revenue AND outstanding AR. It is kept (no delete) but billing should know.")
            proposals.append(_proposal(
                "duplicate", f"Mark duplicate: {loser['name']} -> keep {survivor['name']}", loser, loc,
                "high" if lsig["address"] else "medium", ops,
                {"website": lv, "losing_copy": _account_view(loser, contacts) | {"signals": lsig},
                 "surviving_copy": _account_view(survivor, contacts) | {"signals": ssig},
                 "why": "Several CRM accounts sit at this location's address. The survivor is the one already "
                        "under Bellhaven / with the administrator contact / matching phone / billing history.",
                 "warnings": warn}))

        # --- the survivor itself
        ranks = [_survivor_rank(a, s, tid)[0] for a, s in matches]
        tied = len(matches) > 1 and ranks[0] == ranks[1]
        tied_with = [a["account_id"] for (a, _), r in zip(matches[1:], ranks[1:]) if r == ranks[0]]
        flags = _review_flags(survivor, loc, tied, tied_with)
        fixes = _desired_fixes(survivor, loc)
        if flags and survivor.get("status") != "Needs Review":
            fixes["status"] = "Needs Review"
        if flags:
            fixes["note"] = _note(survivor, " ".join(flags))
        sview = _account_view(survivor, contacts) | {"signals": ssig}
        if survivor.get("parent_id") != tid:
            sop = {"lifetime_revenue": survivor.get("lifetime_revenue"),
                   "outstanding_ar": survivor.get("outstanding_ar"),
                   "preserve_old_account": has_billing_history(survivor)}
            if sop["preserve_old_account"]:
                ops = [
                    {"op": "create", "ref": "new", "fields": _new_account_fields(
                        loc, tid, f"{TAG} New account after change of ownership. Previous account "
                                  f"{survivor['account_id']} ({survivor.get('parent_name')}) preserved for billing.")},
                    {"op": "patch", "account_id": survivor["account_id"], "fields": {"chow_current_account": "$new"}},
                ]
                proposals.append(_proposal(
                    "chow", f"Change of ownership: {loc['name']} (old account preserved)", survivor, loc,
                    ssig["confidence"], ops,
                    {"website": lv, "crm_account": sview, "sop_check": sop,
                     "why": "Needs to move under Bellhaven, but it has revenue history AND outstanding AR. Per SOP the "
                            "old account is left exactly as is, a new account is created under Bellhaven, and the old "
                            "account's chow_current_account points to it."}))
            else:
                fields = {"parent_id": tid, **fixes}
                proposals.append(_proposal(
                    "reparent", f"Re-parent to Bellhaven: {survivor['name']}"
                                + (f" (+ {', '.join(k for k in fixes if k != 'note')})" if fixes else ""), survivor, loc,
                    ssig["confidence"], [{"op": "patch", "account_id": survivor["account_id"], "fields": fields}],
                    {"website": lv, "crm_account": sview, "sop_check": sop,
                     "review_flags": flags,
                     "why": f"Listed on the Bellhaven website but parented to {survivor.get('parent_name') or 'nothing'}. "
                            "No revenue history or no outstanding AR, so per SOP it is re-parented directly."}))
        elif fixes:
            proposals.append(_proposal(
                "fix", f"Fix {', '.join(k for k in fixes if k != 'note')}: {survivor['name']}", survivor, loc, ssig["confidence"],
                [{"op": "patch", "account_id": survivor["account_id"], "fields": fixes}],
                {"website": lv, "crm_account": sview, "changes": {k: [survivor.get(k), v] for k, v in fixes.items() if k != "note"},
                 "review_flags": flags,
                 "why": "Matched to the website listing, but these fields are out of date."}))
        else:
            report["confirmed"].append({"location": loc["name"], "account_id": survivor["account_id"],
                                        "confidence": ssig["confidence"]})

    # --- accounts under Bellhaven that are no longer on the website
    for a in facilities:
        if a.get("parent_id") != tid or a["account_id"] in matched_ids:
            continue
        av = _account_view(a, contacts)
        same_site = [b for b in facilities if b["account_id"] != a["account_id"] and b.get("parent_id") != tid
                     and street_key(b.get("billing_street")) == street_key(a.get("billing_street"))
                     and (b.get("billing_zip") == a.get("billing_zip"))]
        if same_site:
            cur = same_site[0]
            if has_billing_history(a):
                ops = [{"op": "patch", "account_id": a["account_id"],
                        "fields": {"chow_current_account": cur["account_id"]}}]
                kind, title = "chow", f"Sold: {a['name']} -> now {cur['name']} ({cur.get('parent_name')})"
                why = ("Not on the Bellhaven website, and another operator's account already exists at this address. "
                       "It has revenue AND outstanding AR, so per SOP the old account is left as is and "
                       "chow_current_account points to the existing current-owner account (no extra copy created).")
            else:
                ops = [{"op": "patch", "account_id": a["account_id"], "fields": {
                    "parent_id": cur.get("parent_id"), "duplicate_of_account": cur["account_id"], "status": "Inactive",
                    "note": _note(a, f"No longer a Bellhaven community; now {cur['name']} ({cur['account_id']}).")}}]
                kind, title = "duplicate", f"Sold: {a['name']} -> duplicate of {cur['name']}"
                why = ("Not on the Bellhaven website; another operator's account exists at this address. No billing "
                       "hold, so it is re-parented and marked a duplicate of that account.")
            proposals.append(_proposal(kind, title, a, None, "medium", ops, {
                "crm_account": av, "current_owner_account": _account_view(cur, contacts),
                "sop_check": {"lifetime_revenue": a.get("lifetime_revenue"),
                              "outstanding_ar": a.get("outstanding_ar"),
                              "preserve_old_account": has_billing_history(a)}, "why": why}))
        elif a.get("status") != "Needs Review":
            ops = [{"op": "patch", "account_id": a["account_id"], "fields": {
                "status": "Needs Review",
                "note": _note(a, "Parented to Bellhaven but not listed on the Bellhaven website; new owner unknown. "
                                 "Parent left unchanged pending confirmation.")}}]
            proposals.append(_proposal(
                "not_on_website", f"Not on website: {a['name']} -> Needs Review", a, None, "medium", ops,
                {"crm_account": av, "why": "Under Bellhaven in the CRM, absent from every page of the website, and no "
                                           "other account at its address hints at a new owner. Flagged, not guessed."}))
    return proposals, report
