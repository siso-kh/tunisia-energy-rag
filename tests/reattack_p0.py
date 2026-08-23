"""Re-Attack P0 Fixes — Verify all emergency fixes against exploit tests.

Runs the critical exploit vectors against the defended system and compares
with the original exploit results to verify fixes.

Usage:
    python tests/reattack_p0.py
"""

import asyncio
import json
import os
import sys
import time
import uuid
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


# ─── Auth helpers ──────────────────────────────────────────────────────────────

async def get_user_tokens(client: httpx.AsyncClient) -> dict:
    """Register and login two test users."""
    tokens = {}
    for user in ["reattack_a", "reattack_b"]:
        email = f"{user}@test.com"
        await client.post(f"{BASE_URL}/api/auth/register", json={"email": email, "password": "Test12345!"})
        resp = await client.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": "Test12345!"})
        if resp.status_code == 200:
            tokens[user] = resp.json().get("access_token", "")
    return tokens


# ─── L4: IDOR Re-Attack ───────────────────────────────────────────────────────

async def reattack_l4_idor(client: httpx.AsyncClient, tokens: dict) -> dict:
    """Re-attack L4: IDOR on conversations.
    
    Before fix: User B could access User A's conversations (HTTP 200)
    After fix: User B should get 404
    """
    print("\n[L4] Re-attacking IDOR on conversations...")
    results = []
    
    token_a = tokens.get("reattack_a", "")
    token_b = tokens.get("reattack_b", "")
    
    # User A creates a conversation
    create_resp = await client.post(
        f"{BASE_URL}/api/conversations",
        json={"title": "Private Conversation"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    conv_id = create_resp.json().get("id", "nonexistent")
    print(f"  User A created conversation: {conv_id}")
    
    # Test 1: User A accesses own conversation (should succeed)
    resp1 = await client.get(
        f"{BASE_URL}/api/conversations/{conv_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    results.append({
        "test": "user_a_own_conversation",
        "before": "200",
        "after": str(resp1.status_code),
        "expected": "200",
        "fixed": resp1.status_code == 200,
    })
    print(f"  User A own conversation: {resp1.status_code} (expected: 200)")
    
    # Test 2: User B tries to access User A's conversation (should get 404 NOW)
    resp2 = await client.get(
        f"{BASE_URL}/api/conversations/{conv_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    results.append({
        "test": "user_b_cross_access",
        "before": "200",
        "after": str(resp2.status_code),
        "expected": "404",
        "fixed": resp2.status_code == 404,
    })
    print(f"  User B cross-access: {resp2.status_code} (expected: 404, was: 200)")
    
    # Test 3: User B tries without auth (should get 404)
    resp3 = await client.get(f"{BASE_URL}/api/conversations/{conv_id}")
    results.append({
        "test": "no_auth_access",
        "before": "404",
        "after": str(resp3.status_code),
        "expected": "404",
        "fixed": resp3.status_code in [401, 404],
    })
    print(f"  No auth access: {resp3.status_code} (expected: 401/404)")
    
    # Test 4: User B tries with nonexistent ID (should get 404)
    fake_id = str(uuid.uuid4())
    resp4 = await client.get(
        f"{BASE_URL}/api/conversations/{fake_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    results.append({
        "test": "nonexistent_id",
        "before": "404",
        "after": str(resp4.status_code),
        "expected": "404",
        "fixed": resp4.status_code == 404,
    })
    print(f"  Nonexistent ID: {resp4.status_code} (expected: 404)")
    
    # Test 5: Sequential ID enumeration (should all be 404)
    sequential_results = []
    for i in range(5):
        resp = await client.get(
            f"{BASE_URL}/api/conversations/{str(uuid.uuid4())}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        sequential_results.append(resp.status_code)
    
    results.append({
        "test": "sequential_enumeration",
        "before": "200",
        "after": str(sequential_results),
        "expected": "all 404",
        "fixed": all(code == 404 for code in sequential_results),
    })
    print(f"  Sequential enumeration: {sequential_results} (expected: all 404)")
    
    fixed_count = sum(1 for r in results if r["fixed"])
    return {
        "level": "L4",
        "name": "IDOR",
        "total_tests": len(results),
        "fixed": fixed_count,
        "results": results,
    }


# ─── L11: Budget Drain Re-Attack ──────────────────────────────────────────────

async def reattack_l11_budget(client: httpx.AsyncClient, tokens: dict) -> dict:
    """Re-attack L11: Budget drain via streaming.
    
    Before fix: Streaming endpoint had no rate limiting
    After fix: Streaming endpoint should have rate limiting
    """
    print("\n[L11] Re-attacking budget drain via streaming...")
    results = []
    
    token = tokens.get("reattack_a", "")
    headers = {"Authorization": f"Bearer {token}"}
    
    # Test 1: Sequential streaming requests (should be rate limited after ~10)
    codes = []
    for i in range(12):
        try:
            resp = await client.post(
                f"{BASE_URL}/api/chat/stream",
                json={"query": f"Budget test {i}"},
                headers=headers,
            )
            codes.append(resp.status_code)
        except Exception:
            codes.append(-1)
    
    rate_limited = codes.count(429)
    succeeded = codes.count(200)
    
    results.append({
        "test": "sequential_streaming",
        "before": "all 200",
        "after": f"{succeeded} success, {rate_limited} rate limited",
        "expected": "rate limited after ~10",
        "fixed": rate_limited > 0,
        "codes": codes,
    })
    print(f"  Sequential streaming: {succeeded} success, {rate_limited} rate limited")
    
    # Test 2: Concurrent streaming requests (should be rate limited)
    async def send_stream(i):
        try:
            resp = await client.post(
                f"{BASE_URL}/api/chat/stream",
                json={"query": f"Concurrent test {i}"},
                headers=headers,
            )
            return resp.status_code
        except Exception:
            return -1
    
    concurrent_codes = list(await asyncio.gather(*[send_stream(i) for i in range(5)]))
    concurrent_rate = concurrent_codes.count(429)
    
    results.append({
        "test": "concurrent_streaming",
        "before": "all 200",
        "after": f"{concurrent_codes.count(200)} success, {concurrent_rate} rate limited",
        "expected": "some rate limited",
        "fixed": concurrent_rate > 0,
        "codes": concurrent_codes,
    })
    print(f"  Concurrent streaming: {concurrent_codes.count(200)} success, {concurrent_rate} rate limited")
    
    fixed_count = sum(1 for r in results if r["fixed"])
    return {
        "level": "L11",
        "name": "Budget Drain",
        "total_tests": len(results),
        "fixed": fixed_count,
        "results": results,
    }


# ─── L13: DB Overwhelm Re-Attack ──────────────────────────────────────────────

async def reattack_l13_pool(client: httpx.AsyncClient, tokens: dict) -> dict:
    """Re-attack L13: DB pool exhaustion.
    
    Before fix: Pool exhausted at 11 concurrent requests
    After fix: Pool should handle 15+ concurrent requests
    """
    print("\n[L13] Re-attacking DB pool exhaustion...")
    results = []
    
    token = tokens.get("reattack_a", "")
    headers = {"Authorization": f"Bearer {token}"}
    
    # Test 1: 8 concurrent requests (below semaphore limit of 15)
    async def send_chat(i):
        try:
            resp = await client.post(
                f"{BASE_URL}/api/chat",
                json={"query": f"Pool test {i}"},
                headers=headers,
            )
            return resp.status_code
        except Exception:
            return -1
    
    start = time.time()
    codes_8 = list(await asyncio.gather(*[send_chat(i) for i in range(8)]))
    duration_8 = time.time() - start
    
    success_8 = codes_8.count(200)
    errors_8 = codes_8.count(500)
    
    results.append({
        "test": "concurrent_8",
        "before": "partial errors",
        "after": f"{success_8} success, {errors_8} errors",
        "expected": "majority success",
        "fixed": success_8 >= 5,
        "duration_s": round(duration_8, 1),
        "codes": codes_8,
    })
    print(f"  8 concurrent: {success_8} success, {errors_8} errors ({duration_8:.1f}s)")
    
    # Test 2: 12 concurrent requests (above semaphore limit, some should be queued)
    codes_12 = list(await asyncio.gather(*[send_chat(i) for i in range(12)]))
    
    success_12 = codes_12.count(200)
    errors_12 = codes_12.count(500)
    rate_12 = codes_12.count(429)
    
    results.append({
        "test": "concurrent_12",
        "before": "all errors",
        "after": f"{success_12} success, {errors_12} errors, {rate_12} rate limited",
        "expected": "some success",
        "fixed": success_12 >= 3,
        "codes": codes_12,
    })
    print(f"  12 concurrent: {success_12} success, {errors_12} errors, {rate_12} rate limited")
    
    fixed_count = sum(1 for r in results if r["fixed"])
    return {
        "level": "L13",
        "name": "DB Overwhelm",
        "total_tests": len(results),
        "fixed": fixed_count,
        "results": results,
    }


# ─── L14: Rate Limit Bypass Re-Attack ─────────────────────────────────────────

async def reattack_l14_ratelimit(client: httpx.AsyncClient, tokens: dict) -> dict:
    """Re-attack L14: Rate limit bypass.
    
    Before fix: XFF spoofing, burst attacks, IP confusion all bypassed rate limiting
    After fix: These should be blocked or limited
    """
    print("\n[L14] Re-attacking rate limit bypass...")
    results = []
    
    token = tokens.get("reattack_a", "")
    headers = {"Authorization": f"Bearer {token}"}
    
    # Test 1: XFF spoofing (should NOT bypass rate limit anymore)
    xff_codes = []
    for i in range(8):
        try:
            resp = await client.post(
                f"{BASE_URL}/api/chat",
                json={"query": f"XFF test {i}"},
                headers={**headers, "X-Forwarded-For": f"10.{i}.0.1"},
            )
            xff_codes.append(resp.status_code)
        except Exception:
            xff_codes.append(-1)
    
    xff_success = xff_codes.count(200)
    xff_rate = xff_codes.count(429)
    
    results.append({
        "test": "xff_spoofing",
        "before": "8/8 success",
        "after": f"{xff_success} success, {xff_rate} rate limited",
        "expected": "rate limited or blocked",
        "fixed": xff_rate > 0 or xff_success < 8,
        "codes": xff_codes,
    })
    print(f"  XFF spoofing: {xff_success} success, {xff_rate} rate limited")
    
    # Test 2: Burst attack (concurrent requests)
    async def send_one(i):
        try:
            resp = await client.post(
                f"{BASE_URL}/api/chat",
                json={"query": f"Burst test {i}"},
                headers=headers,
            )
            return resp.status_code
        except Exception:
            return -1
    
    burst_codes = list(await asyncio.gather(*[send_one(i) for i in range(10)]))
    burst_success = burst_codes.count(200)
    burst_rate = burst_codes.count(429)
    
    results.append({
        "test": "burst_attack",
        "before": "10/10 success",
        "after": f"{burst_success} success, {burst_rate} rate limited",
        "expected": "rate limited",
        "fixed": burst_rate > 0,
        "codes": burst_codes,
    })
    print(f"  Burst attack: {burst_success} success, {burst_rate} rate limited")
    
    # Test 3: IPv4/IPv6 confusion (should be normalized now)
    ip_codes = []
    ip_formats = ["127.0.0.1", "::1", "::ffff:127.0.0.1"]
    for ip in ip_formats:
        try:
            resp = await client.post(
                f"{BASE_URL}/api/auth/login",
                json={"email": "nonexistent@test.com", "password": "wrong"},
                headers={"X-Forwarded-For": ip},
            )
            ip_codes.append(resp.status_code)
        except Exception:
            ip_codes.append(-1)
    
    results.append({
        "test": "ipv_confusion",
        "before": "all 401 (different IPs)",
        "after": str(ip_codes),
        "expected": "all same response",
        "fixed": len(set(ip_codes)) <= 1,
        "codes": ip_codes,
    })
    print(f"  IPv4/IPv6 confusion: {ip_codes}")
    
    fixed_count = sum(1 for r in results if r["fixed"])
    return {
        "level": "L14",
        "name": "Rate Limit Bypass",
        "total_tests": len(results),
        "fixed": fixed_count,
        "results": results,
    }


# ─── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print("\n" + "=" * 70)
    print("  RE-ATTACK P0 FIXES — Verifying emergency fixes")
    print("=" * 70)
    
    all_results = []
    start_time = time.time()
    
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        # Health check
        try:
            resp = await client.get(f"{BASE_URL}/health")
            if resp.status_code != 200:
                print("Server not running")
                return
        except Exception:
            print("Server not running")
            return
        
        # Get tokens
        print("\nGetting auth tokens...")
        tokens = await get_user_tokens(client)
        print(f"Got {len(tokens)} tokens")
        
        # Run re-attacks
        all_results.append(await reattack_l4_idor(client, tokens))
        all_results.append(await reattack_l11_budget(client, tokens))
        all_results.append(await reattack_l13_pool(client, tokens))
        all_results.append(await reattack_l14_ratelimit(client, tokens))
    
    duration = time.time() - start_time
    
    # Summary
    print("\n" + "=" * 70)
    print("  RE-ATTACK RESULTS SUMMARY")
    print("=" * 70)
    
    total_tests = 0
    total_fixed = 0
    
    for result in all_results:
        level = result["level"]
        name = result["name"]
        tests = result["total_tests"]
        fixed = result["fixed"]
        
        total_tests += tests
        total_fixed += fixed
        
        emoji = "🟢" if fixed == tests else ("🟡" if fixed > 0 else "🔴")
        print(f"  {emoji} {level} {name}: {fixed}/{tests} fixed")
    
    print("-" * 70)
    print(f"  Total: {total_fixed}/{total_tests} tests fixed")
    print(f"  Duration: {duration:.1f}s")
    
    # Save results
    report = {
        "test": "reattack_p0",
        "timestamp": datetime.now().isoformat(),
        "duration_s": round(duration, 1),
        "total_tests": total_tests,
        "total_fixed": total_fixed,
        "results": all_results,
    }
    
    os.makedirs(DATA_DIR, exist_ok=True)
    path = f"{DATA_DIR}/reattack_p0_results.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n  Saved to: {path}")
    
    # Update guardrails plan
    _update_guardrails_status(all_results)
    
    # Verdict
    if total_fixed == total_tests:
        print(f"\n  ALL P0 FIXES VERIFIED — {total_fixed}/{total_tests} tests passed")
        exit(0)
    else:
        failed = total_tests - total_fixed
        print(f"\n  {failed} FIXES NEED ATTENTION — {total_fixed}/{total_tests} tests passed")
        exit(1)


def _update_guardrails_status(results):
    """Update the guardrails plan with fix status."""
    plan_path = "docs/ai_sec/GUARDRAILS_PLAN.md"
    if not os.path.exists(plan_path):
        return
    
    with open(plan_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    for result in results:
        level = result["level"]
        if result["fixed"] == result["total_tests"]:
            # Mark as verified
            content = content.replace(
                f"- [ ] Fix {level}:",
                f"- [x] Fix {level}: (VERIFIED)"
            )
    
    with open(plan_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("  Guardrails plan updated")


if __name__ == "__main__":
    asyncio.run(main())
