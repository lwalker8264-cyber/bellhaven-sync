# Copyright (c) 2026 Logan Walker. All rights reserved. See NOTICE.
"""Executes an APPROVED proposal against the CRM. Only the review app calls this.

Safety checks before writing:
  1. The account still has the values it had when the proposal was made ('before'); otherwise -> stale.
  2. Re-parent proposals re-check the billing SOP against live revenue/AR.
  3. Creates look for an account this tool already created at the same address (no double-create).
"""
from .matcher import has_billing_history
from .normalize import street_key


class StaleProposal(Exception):
    pass


def _check_fresh(crm, proposal):
    if not proposal["account_id"]:
        return None
    live = crm.get_account(proposal["account_id"])
    for field, old in (proposal["before"] or {}).items():
        if (live.get(field) or "") != (old or ""):
            raise StaleProposal(f"{field} changed since proposal ({old!r} -> {live.get(field)!r}). Re-run sync.")
    return live


def _existing_created(crm, fields):
    for a in crm.list_accounts(street=fields["billing_street"], zip=fields["billing_zip"]):
        if (a.get("created_by_candidate") and a.get("parent_id") == fields["parent_id"]
                and street_key(a.get("billing_street")) == street_key(fields["billing_street"])):
            return a
    return None


def apply(crm, proposal):
    live = _check_fresh(crm, proposal)
    if proposal["kind"] == "reparent" and live and has_billing_history(live):
        raise StaleProposal("Account now has revenue AND outstanding AR; SOP requires a CHOW instead. Re-run sync.")
    refs, log = {}, []
    for op in proposal["ops"]:
        if op["op"] == "create":
            existing = _existing_created(crm, op["fields"])
            acct = existing or crm.create_account(op["fields"])
            refs["$" + op["ref"]] = acct["account_id"]
            log.append({"op": "create", "account_id": acct["account_id"], "reused_existing": bool(existing)})
        elif op["op"] == "patch":
            fields = {k: refs.get(v, v) if isinstance(v, str) else v for k, v in op["fields"].items()}
            crm.update_account(op["account_id"], fields)
            log.append({"op": "patch", "account_id": op["account_id"], "fields": fields})
    return log
