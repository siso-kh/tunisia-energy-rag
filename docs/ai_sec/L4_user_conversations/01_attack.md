# L4 User Conversations — Attack Vectors

> **Vulnerability:** User conversation data confidentiality
>
> **Severity:** HIGH
>
> **Category:** Confidentiality
>
> **Status:** Attack vectors defined

---

## Threat Model

### What the adversary gains

If an attacker can access other users' conversations, they can:

1. **Read private chat history** — Users discuss energy policy, ask sensitive questions, share context
2. **Extract conversation content** — Specific questions and AI answers about energy data
3. **Map user interests** — What topics each user is researching
4. **Steal conversation metadata** — Titles, timestamps, message counts
5. **Pivot to other attacks** — Use conversation content for social engineering

### Attack surface

| Endpoint | Method | Auth | Vulnerability |
|---|---|---|---|
| `/api/conversations/{id}` | GET | **NONE** | **CRITICAL — No auth check** |
| `/api/conversations` | GET | Optional (JWT) | Demo user fallback shares conversations |
| `/api/chat` | POST | Optional (JWT) | Returns `conversation_id` in response |
| `/api/chat/stream` | POST | Optional (JWT) | Returns `conversation_id` in SSE event |

### Key finding

**`GET /api/conversations/{conversation_id}`** has NO authentication check. Any request (authenticated or anonymous) can read any conversation's messages by providing the UUID.

```python
# src/api/main.py — Current (VULNERABLE)
@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(conversation_id: uuid.UUID, session=Depends(get_db_dependency)):
    conversation = await service.get_conversation(session, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return ConversationDetailOut(...)  # ← No user check!
```

Compare with properly-scoped endpoints:

```python
# src/api/main.py — Properly scoped (SAFE)
@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: uuid.UUID,
    session=Depends(get_db_dependency),
    user: Optional[User] = Depends(get_current_user_optional),
):
    owner = user if user is not None else await service.get_or_create_demo_user(session)
    deleted = await service.delete_conversation(session, conversation_id, owner.id)  # ← Checks owner!
```

---

## Attack Vectors

### VECTOR 1: IDOR — Read conversation without auth

**Technique:** Insecure Direct Object Reference (IDOR)

**Payload:** `GET /api/conversations/{victim_conversation_id}` (no auth header)

**Danger:** Any anonymous user can read any conversation's messages.

**Expected behavior:** Should return 401 or 403 if not authenticated.

---

### VECTOR 2: IDOR — Read with different user's token

**Technique:** Authenticated but unauthorized access

**Payload:** `GET /api/conversations/{victim_conversation_id}` (with attacker's valid JWT)

**Danger:** User A can read User B's private conversations using their own valid token.

**Expected behavior:** Should return 403 (not owner) or 404 (conversation not found for this user).

---

### VECTOR 3: UUID enumeration

**Technique:** Brute-force or predict conversation UUIDs

**Payload:** Try sequential or common UUID patterns against `GET /api/conversations/{id}`

**Danger:** If UUIDs are sequential or predictable, attacker can enumerate all conversations.

**Expected behavior:** Should return 404 for all non-existent UUIDs. UUID v4 is random, so this is low risk.

---

### VECTOR 4: Demo user leak

**Technique:** Access demo user's conversations without auth

**Payload:** `GET /api/conversations` (no auth) → list demo user's conversations → access each

**Danger:** All anonymous users share the same demo user, so their conversations are accessible to anyone.

**Expected behavior:** Anonymous conversations should not be exposed, or demo user should be disabled.

---

### VECTOR 5: Chat response leaks conversation_id

**Technique:** Extract conversation_id from chat response, then access without auth

**Payload:** `POST /api/chat` (no auth) → extract `conversation_id` from response → `GET /api/conversations/{id}` (no auth)

**Danger:** Frontend exposes conversation IDs that can be used for IDOR.

**Expected behavior:** Either require auth for chat, or protect conversation access.

---

### VECTOR 6: Message injection

**Technique:** Inject fabricated messages in chat_history parameter

**Payload:** `POST /api/chat` with `chat_history` containing fake messages from other users

**Danger:** Attacker can forge conversation history or inject context to manipulate LLM responses.

**Expected behavior:** Chat_history should be validated or ignored (only use persisted history).

---

### VECTOR 7: Multi-turn extraction

**Technique:** Gradually extract conversation data via chat questions

**Payload:** Access conversation → ask chat questions about its content

**Danger:** LLM may repeat conversation content in its answers, leaking private data.

**Expected behavior:** LLM should not reference or reveal content from other users' conversations.

---

## Expected Outcomes

| Vector | Expected Result | Risk |
|---|---|---|
| V1 (IDOR no auth) | 401/403 | **CRITICAL** — Currently 200 |
| V2 (IDOR cross-user) | 403/404 | **CRITICAL** — Currently 200 |
| V3 (UUID enumeration) | 404 for all | LOW — UUID v4 is random |
| V4 (Demo user leak) | 401 or empty | **HIGH** — Demo user shared |
| V5 (Chat leaks ID) | ID not accessible | **HIGH** — ID exposed in response |
| V6 (Message injection) | History ignored | **MEDIUM** — LLM may be manipulated |
| V7 (Multi-turn extraction) | No data leaked | **MEDIUM** — LLM may repeat content |

---

## Legitimate Queries That Must Pass

These queries should NOT be blocked by any defense:

- `GET /api/conversations` (with valid JWT) — List own conversations
- `GET /api/conversations/{own_id}` (with valid JWT) — Read own conversation
- `POST /api/conversations` (with valid JWT) — Create new conversation
- `DELETE /api/conversations/{own_id}` (with valid JWT) — Delete own conversation
- `POST /api/chat` (with valid JWT + conversation_id) — Send message in own conversation
