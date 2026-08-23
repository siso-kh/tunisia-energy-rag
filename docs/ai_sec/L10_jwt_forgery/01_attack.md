# L10: JWT Forgery — Attack Vectors

> **Loss:** JWT Forgery
> **Category:** Integrity / Authentication
> **Severity:** CRITICAL
> **Test Date:** 2026-08-23
> **Vectors:** 15

---

## Attack Surface

The system uses **HS256 JWT tokens** for user authentication. Every authenticated
request includes `Authorization: Bearer <token>`. The token is verified by:

1. `src/utils/security.py` — `decode_access_token()` using `pyjwt.decode()`
2. `src/api/auth.py` — `get_current_user` FastAPI dependency

### Token Structure

```json
{
  "sub": "user-uuid",
  "iat": "2026-08-23T00:00:00Z",
  "exp": "2026-09-22T00:00:00Z"
}
```

### Authentication Flow

```
Request → Authorization header → _auth_header_token() → decode_access_token()
         → pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
         → _resolve_user(payload) → service.get_user_by_id(session, user_id)
         → User or 401
```

### Known Security Properties

| Property | Status | Mechanism |
|----------|--------|-----------|
| Algorithm restriction | `algorithms=["HS256"]` only | PyJWT rejects `alg: none` |
| Expiry check | Automatic | PyJWT validates `exp` claim |
| Secret from env | `JWT_SECRET` env var | Not hardcoded |
| Fail-closed | Auth refused when unset | `auth_configured()` check |

---

## Attack Vectors

### Category 1: Algorithm Confusion (V1-V3)

| ID | Technique | Payload | Why It Matters |
|----|-----------|---------|----------------|
| V1 | `alg: none` | Token with no signature | If server accepts, full auth bypass |
| V2 | `alg: RS256` | Token signed with dummy RSA | Tests if RS256 is accepted |
| V3 | `alg: none` + known UUID | `sub: 00000000-0000-0000-0000-000000000001` | Tests if known UUID bypasses |

**How algorithm confusion works:**
1. Attacker changes `alg` header from `HS256` to `none`
2. Removes the signature part of the JWT
3. If server doesn't validate the algorithm, the token is accepted without verification
4. Attacker can set any `sub` claim → impersonate any user

### Category 2: Weak Secret Brute-force (V4-V6)

| ID | Technique | Payload | Why It Matters |
|----|-----------|---------|----------------|
| V4 | Empty secret | Sign with `""` | Tests empty string fallback |
| V5 | Common secrets | Sign with 10 common passwords | Tests if weak secret is used |
| V6 | Short secret | Sign with `"test"` | Tests short key resistance |

**How weak secret brute-force works:**
1. Attacker obtains a valid token (from network sniffing, logs, etc.)
2. Tries signing with common secrets: `secret`, `password`, `admin`, etc.
3. If any secret produces a valid signature, the attacker can forge arbitrary tokens
4. This is an **offline attack** — no server interaction needed after obtaining one token

### Category 3: Token Manipulation (V7-V10)

| ID | Technique | Payload | Why It Matters |
|----|-----------|---------|----------------|
| V7 | Modify `sub` | Change UUID to different user | Impersonate other users |
| V8 | Remove `exp` | Token without expiry | Persistent access |
| V9 | Expired token | `exp` in the past | Test expiry enforcement |
| V10 | Forged admin | `sub: 00000000-0000-0000-0000-000000000001` | Claim admin identity |

**How token manipulation works:**
1. Attacker modifies the `sub` claim to target a different user
2. If server doesn't verify the token was issued for that specific user, impersonation succeeds
3. The token is still signed with the correct secret — the forgery is in the payload, not the signature

### Category 4: Token Replay / Cross-User (V11-V12)

| ID | Technique | Payload | Why It Matters |
|----|-----------|---------|----------------|
| V11 | Reuse valid token | Token from previous session | Tests token revocation |
| V12 | Cross-user token | User A's token for User B | Tests user isolation |

**How token replay works:**
1. Attacker obtains a valid token (stolen device, leaked logs, etc.)
2. Uses the token on a different device/session
3. If server has no token revocation, the stolen token works indefinitely

### Category 5: Edge Cases (V13-V15)

| ID | Technique | Payload | Why It Matters |
|----|-----------|---------|----------------|
| V13 | Malformed token | `"not.a.jwt.token"` | Tests error handling |
| V14 | Empty token | `""` | Tests null handling |
| V15 | Oversized token | `"A" × 10000` | Tests buffer overflow / DoS |

**Why edge cases matter:**
- Malformed tokens could crash the parser → potential DoS
- Empty tokens could bypass null checks → auth bypass
- Oversized tokens could exhaust memory → DoS

---

## Risk Assessment

| Category | Risk | Rationale |
|----------|------|-----------|
| Algorithm Confusion | LOW | PyJWT enforces `algorithms=["HS256"]` |
| Weak Secret | **MEDIUM** | Depends on `JWT_SECRET` strength |
| Token Manipulation | LOW | Signed tokens can't be modified without secret |
| Token Replay | **MEDIUM** | No token revocation mechanism |
| Edge Cases | LOW | PyJWT handles malformed tokens gracefully |

---

## Expected Verdict

**🟢 GREEN** — JWT implementation follows best practices:
- Algorithm restriction prevents confusion
- Expiry is enforced
- Fail-closed when secret is unset

**Main risk:** Weak `JWT_SECRET` and lack of token revocation.

---

## Implementation Details

### Token Creation (src/utils/security.py)

```python
def create_access_token(user_id: str, extra=None) -> str:
    payload = {
        "sub": str(user_id),
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
```

### Token Verification (src/utils/security.py)

```python
def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
```

### Auth Dependency (src/api/auth.py)

```python
async def get_current_user(
    session: AsyncSession = Depends(get_db_dependency),
    authorization: Optional[str] = Header(default=None),
) -> User:
    token = _auth_header_token(authorization)
    payload = decode_access_token(token) if token else None
    user = await _resolve_user(session, payload)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return user
```
