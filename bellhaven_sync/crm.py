# Copyright (c) 2026 Logan Walker. All rights reserved. See NOTICE.
"""Thin client for the CRM sandbox API (stdlib only)."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config


class CRMError(RuntimeError):
    pass


class CRMClient:
    def __init__(self, api_base: str = config.API_BASE, token: str = config.TOKEN):
        if not token:
            raise CRMError("BELLHAVEN_TOKEN is not set. Put it in .env (see .env.example).")
        self.api_base = api_base
        self.token = token

    def _request(self, method: str, path: str, params=None, body=None):
        url = self.api_base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")
                if e.code >= 500 and attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise CRMError(f"{method} {path} -> HTTP {e.code}: {detail}") from e
            except urllib.error.URLError as e:
                if attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise CRMError(f"{method} {path} failed: {e}") from e

    @staticmethod
    def _unwrap(resp):
        if isinstance(resp, dict) and "account_id" not in resp and isinstance(resp.get("data"), dict):
            return resp["data"]
        return resp

    def _list(self, path, **params):
        out, page = [], 1
        while True:
            resp = self._request("GET", path, {**params, "page": page, "page_size": 100})
            items = resp.get("data", []) if isinstance(resp, dict) else resp
            out.extend(items)
            total = resp.get("total") if isinstance(resp, dict) else None
            if not items or (total is not None and len(out) >= total) or len(items) < 100:
                return out
            page += 1

    def list_accounts(self, **filters):
        return self._list("/accounts", **filters)

    def list_contacts(self, **filters):
        return self._list("/contacts", **filters)

    def get_account(self, account_id):
        return self._unwrap(self._request("GET", f"/accounts/{account_id}"))

    def update_account(self, account_id, fields):
        return self._unwrap(self._request("PATCH", f"/accounts/{account_id}", body=fields))

    def create_account(self, fields):
        return self._unwrap(self._request("POST", "/accounts", body=fields))
