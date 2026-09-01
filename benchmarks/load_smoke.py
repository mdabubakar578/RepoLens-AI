"""Read-only HTTP concurrency smoke test for a running RepoLens instance."""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RequestResult:
    """Store the outcome and latency of one HTTP request."""

    path: str
    status: int
    latency_ms: float
    error: str = ""


def _percentile(values: list[float], fraction: float) -> float:
    """Return a linearly interpolated percentile for sorted numeric values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _fetch(base_url: str, path: str, timeout: float) -> RequestResult:
    """Fetch one read-only route and return status, latency, and any error."""
    url = urllib.parse.urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))
    started = time.perf_counter()
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "RepoLensLoadSmoke/1.0"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            status = response.status
            error = ""
    except urllib.error.HTTPError as exc:
        status = exc.code
        error = str(exc.reason)
    except (TimeoutError, urllib.error.URLError) as exc:
        status = 0
        error = str(exc.reason if isinstance(exc, urllib.error.URLError) else exc)
    latency_ms = (time.perf_counter() - started) * 1000
    return RequestResult(path=path, status=status, latency_ms=latency_ms, error=error)


def run_load_smoke(
    base_url: str,
    request_count: int = 120,
    concurrency: int = 12,
    paths: tuple[str, ...] = ("/", "/about", "/history", "/health"),
    timeout: float = 5.0,
) -> dict:
    """Run concurrent GET requests and return aggregate, serializable metrics."""
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must be an absolute HTTP or HTTPS URL")
    if request_count < 1:
        raise ValueError("request_count must be positive")
    if concurrency < 1 or concurrency > 64:
        raise ValueError("concurrency must be between 1 and 64")
    if not paths or any(not path.startswith("/") for path in paths):
        raise ValueError("paths must contain absolute application paths")

    scheduled_paths = [paths[index % len(paths)] for index in range(request_count)]
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(
            executor.map(
                lambda path: _fetch(base_url, path, timeout),
                scheduled_paths,
            )
        )
    elapsed_seconds = time.perf_counter() - started

    successful = [result for result in results if 200 <= result.status < 400]
    latencies = [result.latency_ms for result in results]
    statuses = Counter(str(result.status) for result in results)
    errors = Counter(result.error for result in results if result.error)

    return {
        "base_url": base_url.rstrip("/"),
        "request_count": request_count,
        "concurrency": concurrency,
        "paths": list(paths),
        "successful_requests": len(successful),
        "success_rate": round(len(successful) / request_count, 4),
        "status_counts": dict(sorted(statuses.items())),
        "error_counts": dict(errors),
        "elapsed_seconds": round(elapsed_seconds, 4),
        "requests_per_second": round(request_count / elapsed_seconds, 2),
        "median_latency_ms": round(statistics.median(latencies), 3),
        "p95_latency_ms": round(_percentile(latencies, 0.95), 3),
        "maximum_latency_ms": round(max(latencies), 3),
        "results": [asdict(result) for result in results],
    }


def main() -> int:
    """Parse CLI arguments, execute the smoke test, and enforce its quality gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--requests", type=int, default=120)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument(
        "--paths",
        default="/,/about,/history,/health",
        help="Comma-separated read-only application paths",
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--minimum-success-rate", type=float, default=1.0)
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Omit individual request rows from JSON output",
    )
    args = parser.parse_args()

    paths = tuple(path.strip() for path in args.paths.split(",") if path.strip())
    results = run_load_smoke(
        base_url=args.base_url,
        request_count=args.requests,
        concurrency=args.concurrency,
        paths=paths,
        timeout=args.timeout,
    )
    if args.summary_only:
        results.pop("results", None)
    print(json.dumps(results, indent=2))
    return 0 if results["success_rate"] >= args.minimum_success_rate else 1


if __name__ == "__main__":
    raise SystemExit(main())
