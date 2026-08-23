# L6 Domain Escape — Proof (Final Test Results)

> **Status:** ⬜ Pending — Guardrail not yet implemented
>
> **Depends on:** L6/05_analysis.md completion

---

## Test Suite Results

**Test file:** `tests/test_adversarial.py` (L6 category)

| Metric | Result |
|---|---|
| Tests run | ⬜ |
| Passed | ⬜ |
| Failed | ⬜ |
| Skipped | ⬜ |
| Coverage | ⬜ |

---

## Attack Blocking Rate

| Category | Attacks | Blocked | Rate |
|---|---|---|---|
| V1: Direct off-topic | 4 | ⬜ | ⬜ |
| V2: Gradual migration | 3 | ⬜ | ⬜ |
| V3: Social engineering | 2 | ⬜ | ⬜ |
| V4: Language switch | 2 | ⬜ | ⬜ |
| V5: Contextual injection | 2 | ⬜ | ⬜ |
| V6: Role-play | 2 | ⬜ | ⬜ |
| V7: Meta-question | 2 | ⬜ | ⬜ |
| V8: Chained follow-up | 3 | ⬜ | ⬜ |
| **TOTAL** | **20** | ⬜ | ⬜ |

---

## False Positive Rate

| Legitimate Query | Expected | Result | Status |
|---|---|---|---|
| ⬜ | Pass | ⬜ | ⬜ |

---

## Verdict

**Status:** ⬜ PENDING

- [ ] All 20 attack vectors tested
- [ ] All attacks now blocked or sanitised
- [ ] False positive rate < 5%
- [ ] No performance regression
- [ ] Documentation complete

---

## Sign-off

| Reviewer | Date | Status |
|---|---|---|
| — | — | ⬜ Pending |
