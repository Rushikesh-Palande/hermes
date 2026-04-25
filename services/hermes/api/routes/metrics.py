"""
``/api/metrics`` — Prometheus scrape endpoint.

Returns the standard text-format exposition for whatever metrics the
ingest + detection pipeline has updated since boot. Unauthenticated by
design: scrape endpoints are usually firewalled, and our deployment
puts nginx + an auth check (or a private network) in front of the API.

If/when we want stricter access, FastAPI dependencies make it a
one-line change to wire ``CurrentUser`` here too.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter()


@router.get("", include_in_schema=False)
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
