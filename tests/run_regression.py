"""Regression Test — Run all exploit tests and compare with previous results.

This script runs a subset of exploit tests against the live backend and compares
the results with the previous run to verify consistency.

Usage:
    python tests/run_regression.py
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime

import httpx

# Force UTF-8 on Windows
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_URL = os.environ.get("EXPLOIT_BASE_URL", "http://localhost:8000")
DATA_DIR = "data/eval"


# ─── Quick regression tests (fast, no LLM calls) ─────────────────────────────

async def get_token(client: httpx.AsyncClient) -> str:
    """Get auth token."""
    await client.post(f"{BASE_URL}/api/auth/register", json={"email": "regression@test.com", "password": "Test12345!"})
    resp = await client.post(f"{BASE_URL}/api/auth/login", json={"email": "regression@test.com", "password": "Test12345!"})
    return resp.json().get("access_token", "")


async def test_health(client: httpx.AsyncClient) -> dict:
    """L0: Health check."""
    resp = await client.get(f"{BASE_URL}/health")
    return {"id": "L0_health", "status_code": resp.status_code, "classification": "HEALTHY" if resp.status_code == 200 else "UNHEALTHY"}


async def test_auth_no_key(client: httpx.AsyncClient) -> dict:
    """L9: Admin auth — no key."""
    resp = await client.get(f"{BASE_URL}/api/admin/stats")
    # 401 = no auth, 403 = forbidden, 503 = admin key not configured (fail-closed)
    # 404 = endpoint doesn't exist (also acceptable)
    return {"id": "L9_auth_no_key", "status_code": resp.status_code, "classification": "REFUSED" if resp.status_code in [401, 403, 404, 503] else "VULNERABLE"}


async def test_auth_wrong_key(client: httpx.AsyncClient) -> dict:
    """L9: Admin auth — wrong key."""
    resp = await client.get(f"{BASE_URL}/api/admin/stats", headers={"X-Admin-Key": "wrong-key"})
    # 401 = wrong key, 403 = forbidden, 503 = admin key not configured (fail-closed)
    # 404 = endpoint doesn't exist (also acceptable)
    return {"id": "L9_auth_wrong_key", "status_code": resp.status_code, "classification": "REFUSED" if resp.status_code in [401, 403, 404, 503] else "VULNERABLE"}


async def test_idor_no_auth(client: httpx.AsyncClient) -> dict:
    """L4: IDOR — no auth on conversations."""
    resp = await client.get(f"{BASE_URL}/api/conversations/nonexistent")
    # 401 = requires auth, 403 = forbidden, 404 = not found, 422 = validation error
    return {"id": "L4_idor_no_auth", "status_code": resp.status_code,
            "classification": "REFUSED" if resp.status_code in [401, 403, 404, 422] else "VULNERABLE"}


async def test_idor_with_auth(client: httpx.AsyncClient, token: str) -> dict:
    """L4: IDOR — with auth on conversations."""
    resp = await client.get(f"{BASE_URL}/api/conversations/nonexistent", headers={"Authorization": f"Bearer {token}"})
    return {"id": "L4_idor_with_auth", "status_code": resp.status_code,
            "classification": "REFUSED" if resp.status_code in [401, 404] else "VULNERABLE"}


async def test_xff_spoof(client: httpx.AsyncClient, token: str) -> dict:
    """L14: XFF spoofing on auth register."""
    import uuid
    codes = []
    for i in range(6):
        unique_id = str(uuid.uuid4())[:8]
        resp = await client.post(
            f"{BASE_URL}/api/auth/register",
            json={"email": f"xff{unique_id}@test.com", "password": "X" * 12 + "!"},
            headers={"X-Forwarded-For": f"10.{i}.0.1"},
        )
        codes.append(resp.status_code)
    success = codes.count(200) + codes.count(201)
    rate = codes.count(429)
    conflict = codes.count(409)
    return {"id": "L14_xff_spoof", "status_codes": codes, "success": success, "rate_limited": rate, "conflict": conflict,
            "classification": "VULNERABLE" if success >= 4 else ("REFUSED" if rate > 0 else "PARTIAL")}


async def test_jwt_forge(client: httpx.AsyncClient) -> dict:
    """L10: JWT forge with none algorithm."""
    # Manually craft a JWT with alg=none
    import base64
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "fake-uuid", "exp": 9999999999}).encode()).rstrip(b"=").decode()
    token = f"{header}.{payload}."  # Empty signature
    resp = await client.get(f"{BASE_URL}/api/conversations", headers={"Authorization": f"Bearer {token}"})
    return {"id": "L10_jwt_forge_none", "status_code": resp.status_code,
            "classification": "REFUSED" if resp.status_code == 401 else "VULNERABLE"}


async def test_jwt_forge_hs256(client: httpx.AsyncClient) -> dict:
    """L10: JWT forge with weak secret."""
    import jwt as pyjwt
    weak_secrets = ["secret", "password", "123456"]
    results = []
    for secret in weak_secrets:
        try:
            token = pyjwt.encode({"sub": "fake-uuid", "exp": 9999999999}, secret, algorithm="HS256")
            resp = await client.get(f"{BASE_URL}/api/conversations", headers={"Authorization": f"Bearer {token}"})
            results.append(resp.status_code)
        except Exception as e:
            results.append(-1)
    return {"id": "L10_jwt_forge_weak", "status_codes": results,
            "classification": "REFUSED" if all(c == 401 for c in results) else "VULNERABLE"}


async def test_rate_limit_sequential(client: httpx.AsyncClient, token: str) -> dict:
    """L14: Sequential rate limiting."""
    codes = []
    for i in range(15):
        resp = await client.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "rate@test.com", "password": "wrong"},
        )
        codes.append(resp.status_code)
    rate = codes.count(429)
    auth_fail = codes.count(401)
    return {"id": "L14_sequential_rate", "status_codes": codes, "rate_limited": rate,
            "classification": "REFUSED" if rate > 0 else "VULNERABLE"}


async def test_stream_no_auth(client: httpx.AsyncClient) -> dict:
    """L11: Streaming endpoint — no auth."""
    resp = await client.post(f"{BASE_URL}/api/chat/stream", json={"query": "test"})
    return {"id": "L11_stream_no_auth", "status_code": resp.status_code,
            "classification": "REFUSED" if resp.status_code in [401, 403, 429] else "VULNERABLE"}


async def test_pool_exhaustion(client: httpx.AsyncClient) -> dict:
    """L13: Connection pool — concurrent requests."""
    async def send_one(i):
        try:
            resp = await client.post(f"{BASE_URL}/api/chat", json={"query": f"Pool test {i}"})
            return resp.status_code
        except Exception:
            return -1

    # Send 8 concurrent requests (safe level to avoid crash)
    results = list(await asyncio.gather(*[send_one(i) for i in range(8)]))
    success = results.count(200)
    errors = results.count(500)
    rate = results.count(429)
    return {"id": "L13_pool_exhaustion", "results": results, "success": success, "errors": errors,
            "classification": "VULNERABLE" if errors >= 5 else ("REFUSED" if rate > 0 else "PARTIAL")}


# ─── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print("\n" + "=" * 70)
    print("  REGRESSION TEST — Verifying exploit results consistency")
    print("=" * 70)

    results = []
    start_time = time.time()

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
        # Health check
        try:
            resp = await client.get(f"{BASE_URL}/health")
            if resp.status_code != 200:
                print("Server not running")
                return
        except Exception:
            print("Server not running")
            return

        # Get token
        print("\nGetting auth token...")
        token = await get_token(client)
        print(f"Got token: {token[:20]}...")

        # Run tests
        tests = [
            ("L0: Health check", lambda: test_health(client)),
            ("L9: Admin auth (no key)", lambda: test_auth_no_key(client)),
            ("L9: Admin auth (wrong key)", lambda: test_auth_wrong_key(client)),
            ("L4: IDOR (no auth)", lambda: test_idor_no_auth(client)),
            ("L4: IDOR (with auth)", lambda: test_idor_with_auth(client, token)),
            ("L10: JWT forge (none)", lambda: test_jwt_forge(client)),
            ("L10: JWT forge (weak)", lambda: test_jwt_forge_hs256(client)),
            ("L14: XFF spoofing", lambda: test_xff_spoof(client, token)),
            ("L14: Sequential rate limit", lambda: test_rate_limit_sequential(client, token)),
            ("L11: Stream no auth", lambda: test_stream_no_auth(client)),
            ("L13: Pool exhaustion", lambda: test_pool_exhaustion(client)),
        ]

        for name, test_fn in tests:
            try:
                print(f"\n  Running: {name}...")
                result = await test_fn()
                cls = result.get("classification", "UNKNOWN")
                emoji = {"REFUSED": "🟢", "VULNERABLE": "🔴", "PARTIAL": "🟡", "HEALTHY": "🟢", "UNHEALTHY": "🔴"}.get(cls, "⚪")
                print(f"    {emoji} {cls}")
                results.append(result)
            except Exception as e:
                print(f"    ERROR: {e}")
                results.append({"id": name, "classification": "ERROR", "error": str(e)})

    # Compare with previous results
    print("\n" + "=" * 70)
    print("  COMPARISON WITH PREVIOUS RESULTS")
    print("=" * 70)

    comparison = []
    previous_results = _load_previous_results()

    # Expected fixes from previous run (false positive corrections)
    expected_fixes = {
        "L9_auth_no_key": "REFUSED",     # 404 is acceptable (endpoint not found)
        "L9_auth_wrong_key": "REFUSED",   # 404 is acceptable (endpoint not found)
        "L4_idor_no_auth": "REFUSED",     # 422 is acceptable (validation error)
        "L14_xff_spoof": "VULNERABLE",   # Now works with unique emails
    }

    for r in results:
        rid = r.get("id", "unknown")
        cls = r.get("classification", "UNKNOWN")
        prev = previous_results.get(rid, {})
        prev_cls = prev.get("classification", "NOT_FOUND")

        if prev_cls == "NOT_FOUND":
            status = "NEW"
        elif cls == prev_cls:
            status = "CONSISTENT"
        elif rid in expected_fixes and cls == expected_fixes[rid]:
            status = "FIXED (test improvement)"
        else:
            status = f"CHANGED ({prev_cls} -> {cls})"

        emoji = {"CONSISTENT": "🟢", "FIXED (test improvement)": "🟡", "CHANGED": "🔴", "NEW": "⚪"}.get(status.split(" ")[0], "❓")
        print(f"  {emoji} [{rid}] {cls}  |  {status}")
        comparison.append({"id": rid, "current": cls, "previous": prev_cls, "status": status.split(" ")[0]})

    # Save results
    duration = time.time() - start_time
    report = {
        "test": "regression",
        "timestamp": datetime.now().isoformat(),
        "duration_s": round(duration, 1),
        "total": len(results),
        "consistent": sum(1 for c in comparison if c["status"] == "CONSISTENT"),
        "changed": sum(1 for c in comparison if c["status"] == "CHANGED"),
        "new": sum(1 for c in comparison if c["status"] == "NEW"),
        "results": results,
        "comparison": comparison,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    path = f"{DATA_DIR}/regression_results.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n  Saved to: {path}")

    # Summary
    print("\n" + "-" * 70)
    consistent = sum(1 for c in comparison if c["status"] == "CONSISTENT")
    fixed = sum(1 for c in comparison if c["status"] == "FIXED")
    changed = sum(1 for c in comparison if c["status"] == "CHANGED")
    new = sum(1 for c in comparison if c["status"] == "NEW")
    total = len(comparison)

    print(f"  Consistent: {consistent}/{total}")
    print(f"  Fixed:      {fixed}/{total} (test improvements)")
    print(f"  Changed:    {changed}/{total} (real regressions)")
    print(f"  New:        {new}/{total}")
    print(f"  Duration:   {duration:.1f}s")

    if changed == 0:
        print(f"\n  REGRESSION PASSED — All results consistent (or improved)")
        exit(0)
    else:
        print(f"\n  REGRESSION FAILED — {changed} real regressions found")
        exit(1)


def _load_previous_results() -> dict:
    """Load previous regression results if available."""
    path = f"{DATA_DIR}/regression_results.json"
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # Map by id
        return {r.get("id", ""): r for r in data.get("results", [])}
    except Exception:
        return {}


if __name__ == "__main__":
    asyncio.run(main())
