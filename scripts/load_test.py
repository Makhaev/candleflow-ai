"""Read-path load test: concurrent users hit the main screens and we report latency percentiles.

Usage: python -m scripts.load_test --base http://127.0.0.1:8000 --users 50 --seconds 20
"""

import argparse
import asyncio
import statistics
import time
from collections import defaultdict

import httpx

ENDPOINTS = (
    "/api/batches?limit=20",
    "/api/jobs",
    "/api/stock/SOY",
    "/api/jobs/4",
    "/api/jobs/4/cost",
    "/api/materials",
)


async def sign_in(base: str, login: str) -> httpx.Cookies:
    async with httpx.AsyncClient(base_url=base, timeout=30) as client:
        response = await client.post("/api/login", json={"login": login, "password": "demo-candleflow"})
        response.raise_for_status()
        return client.cookies


async def user(base: str, cookies: httpx.Cookies, deadline: float, timings: dict, errors: dict) -> None:
    """One virtual user: walks the main screens in a loop, reusing a signed-in session."""
    async with httpx.AsyncClient(base_url=base, timeout=30, cookies=cookies) as client:
        index = 0
        while time.perf_counter() < deadline:
            path = ENDPOINTS[index % len(ENDPOINTS)]
            index += 1
            started = time.perf_counter()
            try:
                response = await client.get(path)
                ok = response.is_success
            except httpx.HTTPError:
                ok = False
            timings[path].append(time.perf_counter() - started)
            if not ok:
                errors[path] += 1


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * share))] * 1000


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--users", type=int, default=50)
    parser.add_argument("--seconds", type=float, default=20)
    args = parser.parse_args()
    timings: dict[str, list[float]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    # One session per role, as the login endpoint itself is rate limited per account and IP.
    sessions = [await sign_in(args.base, login) for login in ("manager", "technologist", "warehouse")]
    deadline = time.perf_counter() + args.seconds
    await asyncio.gather(
        *(user(args.base, sessions[i % 3], deadline, timings, errors) for i in range(args.users))
    )
    total = sum(len(values) for values in timings.values())
    print(
        f"{args.users} users, {args.seconds:.0f} s: {total / args.seconds:.0f} req/s, errors {sum(errors.values())}"
    )
    print(f"{'endpoint':28} {'count':>6} {'p50 ms':>8} {'p95 ms':>8} {'p99 ms':>8}")
    for path in ENDPOINTS:
        values = timings[path]
        print(
            f"{path:28} {len(values):6} {statistics.median(values) * 1000:8.1f} "
            f"{percentile(values, 0.95):8.1f} {percentile(values, 0.99):8.1f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
