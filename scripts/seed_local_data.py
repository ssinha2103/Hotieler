#!/usr/bin/env python3
"""Populate a running local Hotieler instance through its public HTTP API.

This script deliberately does not import application internals or write repositories
directly. A normal server start is empty; running ``./run.sh seed`` exercises the same
owner and property endpoints that any API client would use.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _post(base_url: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - caller owns base URL
            body = json.load(response)
    except HTTPError as error:
        details = error.read().decode(errors="replace")
        raise RuntimeError(f"POST {path} failed with HTTP {error.code}: {details}") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach Hotieler at {base_url}: {error.reason}") from error

    if not isinstance(body, dict):
        raise RuntimeError(f"POST {path} returned an unexpected response shape.")
    return body


def _property_payloads() -> list[dict[str, Any]]:
    return [
        {
            "name": "Northstar Bengaluru",
            "city": "Bengaluru",
            "locality": "Indiranagar",
            "address": "100 Main Road, Indiranagar",
            "star_rating": "4.5",
            "amenities": ["wifi", "pool", "gym"],
            "room_types": [
                {
                    "name": "Deluxe King",
                    "total_units": 3,
                    "guests_per_unit": 2,
                    "nightly_rate": {"amount": "4500.00", "currency": "INR"},
                    "amenities": ["air conditioning", "workspace"],
                },
                {
                    "name": "Family Suite",
                    "total_units": 2,
                    "guests_per_unit": 4,
                    "nightly_rate": {"amount": "7800.00", "currency": "INR"},
                    "amenities": ["air conditioning", "living room"],
                },
            ],
        },
        {
            "name": "Northstar Goa",
            "city": "Goa",
            "locality": "Candolim",
            "address": "22 Beach Road, Candolim",
            "star_rating": "4.0",
            "amenities": ["wifi", "pool", "breakfast"],
            "room_types": [
                {
                    "name": "Garden Room",
                    "total_units": 4,
                    "guests_per_unit": 2,
                    "nightly_rate": {"amount": "5200.00", "currency": "INR"},
                    "amenities": ["air conditioning", "patio"],
                },
                {
                    "name": "Private Villa",
                    "total_units": 1,
                    "guests_per_unit": 4,
                    "nightly_rate": {"amount": "12500.00", "currency": "INR"},
                    "amenities": ["private pool", "kitchen"],
                },
            ],
        },
    ]


def seed(base_url: str) -> dict[str, Any]:
    suffix = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    owner = _post(
        base_url,
        "/api/v1/owners",
        {
            "name": "Northstar Hospitality",
            "contact_email": f"local-seed-{suffix}@northstar.example",
        },
    )
    owner_id = owner["id"]
    properties = [
        _post(base_url, f"/api/v1/owners/{owner_id}/properties", payload)
        for payload in _property_payloads()
    ]

    check_in = date.today() + timedelta(days=30)
    check_out = check_in + timedelta(days=3)
    searches = []
    for property_ in properties:
        query = urlencode(
            {
                "city": property_["city"],
                "check_in": check_in.isoformat(),
                "check_out": check_out.isoformat(),
                "guest_count": 2,
            }
        )
        searches.append(f"{base_url.rstrip('/')}/api/v1/properties/search?{query}")

    return {
        "owner": owner,
        "properties": properties,
        "sample_searches": searches,
        "note": "Data exists only in the current API process; restart to clear it.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    try:
        result = seed(args.base_url)
    except (KeyError, RuntimeError) as error:
        print(f"Local seed failed: {error}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
