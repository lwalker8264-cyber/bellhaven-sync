# Copyright (c) 2026 Logan Walker. All rights reserved. See NOTICE.
"""Normalization so that cosmetic differences ("Blvd" vs "Boulevard") don't look like real changes."""
import re

from . import config

STREET_WORDS = {
    "street": "st", "avenue": "ave", "av": "ave", "road": "rd", "boulevard": "blvd", "drive": "dr",
    "lane": "ln", "pike": "pike", "pk": "pike", "court": "ct", "place": "pl", "parkway": "pkwy",
    "highway": "hwy", "circle": "cir", "terrace": "ter",
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northwest": "nw", "northeast": "ne", "southwest": "sw", "southeast": "se",
}
NAME_WORDS = {"rehab": "rehabilitation", "centre": "center", "ctr": "center", "&": "and"}


def _tokens(text: str):
    text = (text or "").lower().replace("&", " & ")
    return re.findall(r"[a-z0-9&]+", text)


def street_key(street: str) -> str:
    return " ".join(STREET_WORDS.get(t, t) for t in _tokens(street))


def name_key(name: str) -> str:
    toks = [NAME_WORDS.get(t, t) for t in _tokens(name) if t != "the"]
    joined = " ".join(toks)
    return joined.replace("health care", "healthcare")


def name_similarity(a: str, b: str) -> float:
    ta, tb = set(name_key(a).split()), set(name_key(b).split())
    ignore = {"of", "at", "and", "bellhaven"}
    ta, tb = ta - ignore, tb - ignore
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def phone_key(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")[-10:]


def person_key(name: str) -> str:
    return " ".join(_tokens(name))


def care_types(offerings):
    """Website offerings -> list of CRM care_type values (first = primary)."""
    out = []
    for o in offerings or []:
        mapped = config.CARE_TYPE_MAP.get(o.strip().lower())
        if mapped and mapped not in out:
            out.append(mapped)
    return out


def is_parent_account(acct) -> bool:
    return "(parent account)" in (acct.get("name") or "").lower()
