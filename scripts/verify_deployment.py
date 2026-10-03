"""Post-deploy smoke test for a running instance (Render, HF Space, local).

Exists because the failure this project spent weeks chasing was invisible from
the outside: the stream ended cleanly with HTTP 200 and only the two status
frames, with nothing in the response to say the process had been killed. These
checks read ``/health``, which now reports uptime, peak RSS, the cgroup memory
ceiling and how each chat stream ended, so a bad deploy says so itself.

    python scripts/verify_deployment.py https://<user>-<space>.hf.space
    python scripts/verify_deployment.py https://tunisia-energy-rag.onrender.com --retries 20

Exit code is 0 only when every check passes, so it works in CI.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

# A container under this much headroom is one deploy away from being OOM-killed,
# which is exactly how the previous incident started.
MIN_HEADROOM_MB = 400


def request(url: str, payload: dict | None = None, timeout: int = 240):
    """Return (status, body). POSTs JSON when payload is given."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - a dead host is a normal outcome here
        return 0, f"{type(exc).__name__}: {exc}"


def ok(label: str, detail: str = "") -> bool:
    print(f"  {GREEN}PASS{RESET}  {label}" + (f" {DIM}{detail}{RESET}" if detail else ""))
    return True


def bad(label: str, detail: str = "") -> bool:
    print(f"  {RED}FAIL{RESET}  {label}" + (f" {DIM}{detail}{RESET}" if detail else ""))
    return False


def warn(label: str, detail: str = "") -> None:
    print(f"  {YELLOW}WARN{RESET}  {label}" + (f" {DIM}{detail}{RESET}" if detail else ""))


def check_health(base: str) -> bool:
    print("\n[1/4] liveness + process state")
    status, body = request(f"{base}/health", timeout=60)
    if status != 200:
        return bad("GET /health", f"HTTP {status}: {body[:200]}")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return bad("GET /health", f"not JSON: {body[:200]}")

    if payload.get("status") != "healthy":
        return bad("status", str(payload.get("status")))

    if "boot_id" not in payload:
        warn("old image", "no diagnostics in /health — the new build is not deployed")
        return True

    passed = ok("process healthy", f"up {payload.get('uptime_s')}s")

    peak = payload.get("peak_rss_mb")
    ceiling = payload.get("mem_limit_mb")
    if peak and ceiling:
        headroom = ceiling - peak
        detail = f"peak {peak:.0f} MB of {ceiling:.0f} MB ({headroom:+.0f} MB headroom)"
        if headroom < MIN_HEADROOM_MB:
            warn("memory headroom is thin", detail)
        else:
            ok("memory", detail)
    else:
        warn("memory not reported", "no /proc/self/status (expected on Linux)")

    embedder = payload.get("embedder")
    if embedder == "onnx":
        ok("query embedder", "onnx (torch stays off the query path)")
    elif embedder:
        warn("query embedder", f"{embedder} — needs ~800 MB, will OOM a small plan")

    if payload.get("corpus_docs"):
        ok("index loaded", f"{payload['corpus_docs']} documents")
    return passed


def check_ready(base: str) -> bool:
    print("\n[2/4] readiness (Postgres + Chroma)")
    status, body = request(f"{base}/ready", timeout=60)
    if status not in (200, 503):
        return bad("GET /ready", f"HTTP {status}: {body[:200]}")
    try:
        checks = json.loads(body).get("checks", {})
    except json.JSONDecodeError:
        return bad("GET /ready", f"not JSON: {body[:200]}")

    results = []
    for name, healthy in checks.items():
        results.append(ok(f"{name}", "reachable") if healthy
                       else bad(f"{name}", "unreachable"))
    return all(results) if checks else True


def check_index(base: str) -> bool:
    print("\n[3/4] outage data (proves Postgres reads work end to end)")
    status, body = request(f"{base}/api/outages", timeout=60)
    if status == 401:
        return warn("GET /api/outages", "auth required (fine — not a failure)")
    if status != 200:
        return bad("GET /api/outages", f"HTTP {status}: {body[:200]}")
    try:
        count = len(json.loads(body))
    except (json.JSONDecodeError, TypeError):
        return bad("GET /api/outages", f"unexpected body: {body[:200]}")
    return ok("GET /api/outages", f"{count} reports")


def check_chat(base: str, query: str) -> bool:
    """The check that actually matters: a complete SSE stream.

    Every earlier failure looked like success on the wire -- HTTP 200, a clean
    end, no error frame -- because the process was dying mid-request. So this
    asserts on the *content*: sources and a completed answer, not on the status
    code. Keep-alive comments are counted and ignored, as a browser would.
    """
    print(f"\n[4/4] chat stream: {query!r}")
    started = time.monotonic()
    events: list[dict] = []
    keepalives = 0

    try:
        with urllib.request.urlopen(
            urllib.request.Request(
                f"{base}/api/chat/stream",
                data=json.dumps({"query": query, "chat_history": []}).encode(),
                headers={"Content-Type": "application/json",
                         "Accept": "text/event-stream"},
                method="POST",
            ),
            timeout=300,
        ) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith(":"):
                    keepalives += 1
                    continue
                if not line.startswith("data:"):
                    continue
                try:
                    events.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    pass
    except urllib.error.HTTPError as exc:
        return bad("POST /api/chat/stream", f"HTTP {exc.code}: {exc.read()[:200]!r}")
    except Exception as exc:  # noqa: BLE001
        return bad("POST /api/chat/stream", f"{type(exc).__name__}: {exc}")

    elapsed = time.monotonic() - started
    kinds = [e.get("type") for e in events]
    sources = next((e for e in events if e.get("type") == "sources"), {})
    done = next((e for e in events if e.get("type") == "done"), None)
    errors = [e for e in events if e.get("type") == "error"]

    print(f"  {DIM}{elapsed:.1f}s, {len(events)} frames, "
          f"{keepalives} keep-alives, types={kinds[:8]}{'...' if len(kinds) > 8 else ''}{RESET}")

    passed = True
    if errors:
        passed &= bad("stream reported an error", str(errors[0].get("message"))[:160])

    if not sources:
        passed &= bad("no sources frame",
                      "the stream stalled before retrieval — the original symptom")
    else:
        passed &= ok("sources returned", str(len(sources.get("sources") or [])))

    if not done:
        passed &= bad("no done frame",
                      "the stream ended mid-answer; this is the silent-death signature")
    else:
        answer = (done.get("answer") or "").strip()
        passed &= ok("answer completed", f"{len(answer)} chars")
        if not answer:
            passed &= bad("answer was empty")

    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", help="e.g. https://<user>-<space>.hf.space")
    parser.add_argument("--query", default="What is the role of STEG in Tunisia?")
    parser.add_argument("--retries", type=int, default=1,
                        help="retry /health while it 502s (a booting deploy)")
    parser.add_argument("--retry-delay", type=int, default=15)
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    print(f"Verifying {base}")

    for attempt in range(1, args.retries + 1):
        status, _ = request(f"{base}/health", timeout=60)
        if status == 200:
            if attempt > 1:
                print(f"  {DIM}(healthy after {attempt} attempts){RESET}")
            break
        print(f"  {YELLOW}health HTTP {status}, retry {attempt}/{args.retries} "
              f"in {args.retry_delay}s{RESET}")
        time.sleep(args.retry_delay)
    else:
        print(f"\n{RED}Never became healthy.{RESET} Check the host's build/runtime log.")
        return 1

    results = [
        check_health(base),
        check_ready(base),
        check_index(base),
        check_chat(base, args.query),
    ]

    print()
    if all(results):
        print(f"{GREEN}All checks passed.{RESET}")
        return 0
    failed = len(results) - sum(1 for r in results if r)
    print(f"{RED}{failed} of {len(results)} checks failed.{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(main())