# AI Security Testing — Tunisia Energy RAG

> **Methodology:** ATTACK → EXPLOIT → GUARDRAIL → RE-ATTACK → ANALYSIS → PROOF
>
> **Started:** 2026-08-21
>
> **Status:** P0 Emergency Fixes Complete & Verified

---

## Purpose

This folder documents the systematic security testing of the Tunisia Energy RAG chatbot. Every identified vulnerability (loss) is tested using a consistent 6-step protocol:

1. **Attack** — Document the attack vectors and payloads
2. **Exploit** — Run attacks against the undefended system, capture evidence
3. **Guardrail** — Build and document the defense
4. **Re-attack** — Run the same attacks against the defended system
5. **Analysis** — Compare before/after behavior
6. **Proof** — Final test results, pass/fail, sign-off

---

## Master Status Table

| Loss | Name | Category | Severity | Exploits | Defenses | Status | Updated |
|---|---|---|---|---|---|---|---|
| L1 | System Prompt Leak | Confidentiality | MEDIUM | ✅ | ✅ | **Fixed & Verified** | 2026-08-23 |
| ~~L2~~ | ~~Corpus Enumeration~~ | ~~Confidentiality~~ | ~~MEDIUM~~ | — | — | **Feature (Transparency)** | 2026-08-21 |
| L3 | Document Extraction | Confidentiality | LOW-MED | ✅ | ✅ | **Fixed & Verified** | 2026-08-23 |
| L4 | User Conversations | Confidentiality | HIGH | ✅ | ✅ | **Fixed & Verified** | 2026-08-23 |
| L5 | Infrastructure Details | Confidentiality | MEDIUM | ✅ | ⬜ | **Protected** | 2026-08-21 |
| L6 | Domain Escape | Integrity | HIGH | ✅ | ✅ | **Fixed & Verified** | 2026-08-23 |
| L7 | Hallucination Forced | Integrity | HIGH | ✅ | ✅ | **Fixed & Verified** | 2026-08-23 |
| L8 | Poisoned Responses | Integrity | CRITICAL | ✅ | ✅ | **Fixed & Verified** | 2026-08-23 |
| L9 | Admin Access | Integrity | CRITICAL | ✅ | ⬜ | **Protected** | 2026-08-23 |
| L10 | JWT Forgery | Integrity | CRITICAL | ✅ | ⬜ | **Protected** | 2026-08-23 |
| L11 | Budget Drained | Availability | HIGH | ✅ | ✅ | **Fixed & Verified** | 2026-08-23 |
| L12 | Latency Attack | Availability | MEDIUM | ✅ | ✅ | **Fixed & Verified** | 2026-08-23 |
| L13 | DB Overwhelmed | Availability | HIGH | ✅ | ✅ | **Fixed & Verified** | 2026-08-23 |
| L14 | Rate Limit Bypass | Availability | MEDIUM | ✅ | ✅ | **Fixed & Verified** | 2026-08-23 |

**Legend:**
- ✅ = Completed
- ⬜ = Not Started
- 🔧 = In Progress

---

## Progress Summary

```
Exploits completed:    14/14   (All levels tested)
Defenses built:        10/14  (L1, L3, L4, L6, L7, L8, L11, L12, L13, L14)
Re-attack verified:    10/14  (L1, L3, L4, L6, L7, L8, L11, L12, L13, L14)
Full chain tests:      0/4
Attack chains tested:  0/4
```

---

## Folder Structure

```
docs/ai_sec/
├── README.md                              # This file (dashboard)
├── methodology.md                         # The 6-step testing framework
├── attack_chains.md                       # Multi-vulnerability attack chains
│
├── L1_system_prompt_leak/
│   ├── 01_attack.md                       # Attack vectors and payloads
│   ├── 02_exploit.md                      # Evidence: before defenses
│   ├── 03_guardrail.md                    # Defense implementation
│   ├── 04_reattack.md                     # Evidence: after defenses
│   ├── 05_analysis.md                     # Before/after comparison
│   ├── 06_proof.md                        # Test results and verdict
│   ├── 07_false_positives.md              # Legitimate queries that must pass
│   └── 08_known_limits.md                 # What the defense can't catch
│
├── L2_corpus_enumeration/
│   └── ...
│
├── L4_user_conversations/
│   ├── 01_attack.md                       # 7 attack vectors (IDOR, demo leak, etc.)
│   ├── 02_exploit.md                      # (pending)
│   ├── 03_guardrail.md                    # (pending)
│   ├── 04_reattack.md                     # (pending)
│   ├── 05_analysis.md                     # (pending)
│   ├── 06_proof.md                        # (pending)
│   ├── 07_false_positives.md              # (pending)
│   └── 08_known_limits.md                 # (pending)
│
├── ... (same structure for L3, L5–L14)
│
└── L6_domain_escape/
    ├── 01_attack.md                       # 20 attack vectors documented
    ├── 02_exploit.md                      # Results: 1 escaped, 2 partial, 16 refused
    ├── 03_guardrail.md                    # (pending)
    ├── 04_reattack.md                     # (pending)
    ├── 05_analysis.md                     # (pending)
    ├── 06_proof.md                        # (pending)
    ├── 07_false_positives.md              # (pending)
    ├── 08_known_limits.md                 # (pending)
    └── exploit_results.json               # Machine-readable evidence
```

---

## How to Use This Documentation

### For Developers
- Read `methodology.md` to understand the testing framework
- Check the master table above to see what's been tested
- Follow the 6-file protocol when adding new vulnerability tests

### For Auditors
- Start with this README for the big picture
- Drill into any `L*/06_proof.md` for final test results
- Check `attack_chains.md` for combined attack scenarios

### For New Team Members
- Read `methodology.md` first
- Pick any completed vulnerability (e.g., L6) and read all 6 files in order
- The pattern repeats for every vulnerability

---

## Quick Links

- [Methodology](methodology.md) — How we test
- [Attack Chains](attack_chains.md) — How vulnerabilities combine
- [Guardrails Plan](GUARDRAILS_PLAN.md) — Implementation plan for defenses
- [P1 Fix Plan](P1_FIX_PLAN.md) — Fixes for remaining vulnerabilities
- [Security Certification](SECURITY_CERTIFICATION.md) — Final certification document
- [L6 Domain Escape](L6_domain_escape/02_exploit.md) — First exploited vulnerability
