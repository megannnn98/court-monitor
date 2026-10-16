from __future__ import annotations

import asyncio

import httpx

from ingestion.concurrency import FetchLimits, RequestGates, build_http_client


def test_global_and_per_domain_limits_gate_real_client_requests() -> None:
    active = peak = 0
    by_host: dict[str, int] = {}
    host_peaks: dict[str, int] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        host = request.url.host
        active += 1
        by_host[host] = by_host.get(host, 0) + 1
        peak = max(peak, active)
        host_peaks[host] = max(host_peaks.get(host, 0), by_host[host])
        try:
            await asyncio.sleep(0.01)
            return httpx.Response(200, request=request)
        finally:
            active -= 1
            by_host[host] -= 1

    async def run() -> None:
        async with build_http_client(
            FetchLimits(max_concurrency=2, per_domain_concurrency=1),
            transport=httpx.MockTransport(handler),
        ) as client:
            await asyncio.gather(
                *(
                    client.get(url)
                    for url in (
                        "https://a.test/1",
                        "https://a.test/2",
                        "https://b.test/1",
                        "https://b.test/2",
                    )
                )
            )

    asyncio.run(run())

    assert peak == 2
    assert host_peaks == {"a.test": 1, "b.test": 1}


def test_a_domain_cooldown_never_outlasts_the_backoff_ceiling() -> None:
    gates = RequestGates(FetchLimits(max_delay_seconds=2.0))

    gates.note_cooldown("sudrf.test", 3600)

    assert 0 < gates.cooldown_seconds("sudrf.test") <= 2.0
