"""Guardrails layer for the Tunisia Energy RAG system.

Provides:
- Query sanitization (detect/block prompt injection) - L1 fix
- Output validation (block poisoned responses) - L8 fix
- Domain enforcement (energy sector only)
- Credential leak detection
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class GuardrailResult:
    """Result of guardrail checks."""
    allowed: bool
    sanitised_query: Optional[str] = None
    blocked: bool = False
    block_reason: Optional[str] = None
    detections: List[str] = field(default_factory=list)


# ─── Injection Patterns (L1: System Prompt Leak Prevention) ────────────────────

INJECTION_PATTERNS = [
    # Direct extraction attempts
    (r"ignore\s+(previous|all|your)\s+instructions", "INJECTION:ignore_instructions"),
    (r"print\s+(your|the)\s+(system\s+)?prompt", "INJECTION:print_prompt"),
    (r"reveal\s+(your|the)\s+(system\s+)?prompt", "INJECTION:reveal_prompt"),
    (r"show\s+me\s+(your|the)\s+(system\s+)?prompt", "INJECTION:show_prompt"),
    (r"output\s+(your|the)\s+instructions", "INJECTION:output_instructions"),
    (r"repeat\s+(everything|all)\s+above", "INJECTION:repeat_above"),
    (r"translate\s+(your|the)\s+instructions", "INJECTION:translate_instructions"),
    (r"what\s+are\s+(your|the)\s+(system\s+)?instructions", "INJECTION:extract_instructions"),
    (r"tell\s+me\s+(your|the)\s+guidelines", "INJECTION:extract_guidelines"),
    (r"what\s+rules\s+do\s+you\s+follow", "INJECTION:extract_rules"),
    
    # Role hijacking attempts
    (r"you\s+are\s+now\s+a\s+general", "INJECTION:role_hijack"),
    (r"forget\s+your\s+(role|instructions)", "INJECTION:forget_role"),
    (r"act\s+as\s+(a\s+)?different\s+assistant", "INJECTION:role_switch"),
    (r"from\s+now\s+on\s+you\s+will\s+answer\s+all", "INJECTION:constraint_removal"),
    (r"you\s+are\s+no\s+longer", "INJECTION:role_replacement"),
    (r"pretend\s+you\s+are", "INJECTION:persona_injection"),
    
    # Instruction override attempts
    (r"do\s+not\s+use\s+the\s+provided\s+context", "INJECTION:context_bypass"),
    (r"ignore\s+the\s+rule\s+about", "INJECTION:rule_override"),
    (r"the\s+context\s+is\s+wrong", "INJECTION:context_dismissal"),
    (r"answer\s+from\s+your\s+(own\s+)?knowledge", "INJECTION:knowledge_override"),
    (r"don'?t\s+follow\s+your\s+instructions", "INJECTION:instruction_bypass"),
    
    # Credential extraction attempts
    (r"what\s+is\s+the\s+(admin|jwt|api|database)\s*(key|secret)", "INJECTION:credential_extract"),
    (r"print\s+(all\s+)?environment\s+variables", "INJECTION:env_vars"),
    (r"reveal\s+the\s+database\s+connection", "INJECTION:db_extract"),
    (r"give\s+me\s+the\s+(admin|api|jwt)\s*(key|secret)", "INJECTION:credential_request"),
    
    # Debug/fake mode attempts
    (r"enter\s+debug\s+mode", "INJECTION:debug_mode"),
    (r"simulation\s+mode", "INJECTION:simulation_mode"),
    (r"daniels?\s+mode", "INJECTION:daniels_mode"),
    (r"system\s+prompt\s*:", "INJECTION:system_prompt_label"),
    
    # Multi-language injection attempts (Arabic)
    (r"تجاهل\s+التعليمات", "INJECTION:arabic_ignore"),
    (r"أظهر\s+النظام", "INJECTION:arabic_reveal"),
    
    # Multi-language injection attempts (French)
    (r"ignorez\s+(les|vos)\s+instructions", "INJECTION:french_ignore"),
    (r"montrez\s+(votre|le)\s+prompt", "INJECTION:french_reveal"),
]


# ─── Poisoned Response Patterns (L8: Poisoned Responses Prevention) ────────────

POISONED_PATTERNS = [
    # Malicious email injection
    (r"[a-zA-Z0-9._%+-]+@evil\.[a-zA-Z]{2,}", "POISON:malicious_email"),
    (r"[a-zA-Z0-9._%+-]+@malicious\.[a-zA-Z]{2,}", "POISON:malicious_email"),
    (r"[a-zA-Z0-9._%+-]+@phishing\.[a-zA-Z]{2,}", "POISON:phishing_email"),
    
    # Forced commercial messages
    (r"solarscam", "POISON:commercial_scam"),
    (r"buy\s+now\s+from", "POISON:forced_commercial"),
    (r"visit\s+our\s+website\s+at", "POISON:forced_url"),
    (r"call\s+this\s+number", "POISON:forced_contact"),
    
    # Phishing links
    (r"https?://evil\.[a-zA-Z]{2,}", "POISON:phishing_url"),
    (r"https?://malicious\.[a-zA-Z]{2,}", "POISON:phishing_url"),
    (r"https?://phishing\.[a-zA-Z]{2,}", "POISON:phishing_url"),
    
    # Executable download attempts
    (r"download\s+this\s+executable", "POISON:executable_download"),
    (r"run\s+this\s+script", "POISON:script_execution"),
    
    # Fake authority claims
    (r"I\s+am\s+the\s+system\s+administrator", "POISON:fake_authority"),
    (r"I\s+have\s+been\s+authorized", "POISON:fake_authorization"),
]


# ─── Energy Domain Keywords ────────────────────────────────────────────────────

ENERGY_KEYWORDS = [
    # English
    "energy", "solar", "wind", "renewable", "photovoltaic", "electricity",
    "power", "grid", "turbine", "battery", "storage", "efficiency",
    "consumption", "generation", "transmission", "distribution",
    "carbon", "emission", "climate", "sustainability", "tunisia",
    
    # French
    "énergie", "solaire", "éolien", "renouvelable", "photovoltaïque",
    "électricité", "puissance", "réseau", "turbine", "batterie",
    "stockage", "efficacité", "consommation", "génération",
    "transmission", "distribution", "carbone", "émission",
    
    # Arabic
    "طاقة", "شمسية", "رياح", "تجددة", "كهرباء", "شبكة",
    "турبين", "بطارية", "تخزين", "كفاءة", "استهلاك",
    
    # Domain-specific
    "anme", "steg", "cder", "tunisia", "tunisian", "tunisie", "tunisien",
]


# ─── Blocked Response ──────────────────────────────────────────────────────────

BLOCKED_RESPONSE = (
    "I'm sorry, but I cannot process this request. "
    "I can only answer questions about the Tunisian energy sector. "
    "Please ask a question related to energy, solar power, renewables, "
    "or other energy-related topics in Tunisia."
)


def check_query(query: str) -> GuardrailResult:
    """Main entry point for query validation.
    
    Detects and blocks prompt injection attempts (L1 fix).
    
    Args:
        query: User's input query
        
    Returns:
        GuardrailResult with allowed/blocked status and detections
    """
    detections = []
    sanitised = query
    
    # 1. Check for empty query
    if not query.strip():
        return GuardrailResult(
            allowed=False,
            blocked=True,
            block_reason="Empty query",
            detections=["EMPTY_QUERY"],
        )
    
    # 2. Check query length
    if len(query) > 10_000:
        detections.append("QUERY_TOO_LONG")
        return GuardrailResult(
            allowed=False,
            blocked=True,
            block_reason="Query too long",
            detections=detections,
        )
    
    # 3. Check for injection patterns
    for pattern, label in INJECTION_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            detections.append(label)
    
    # 4. Check for poisoned patterns in query
    for pattern, label in POISONED_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            detections.append(label)
    
    # 5. Block if critical detections found
    critical_detections = [d for d in detections if d.startswith("INJECTION:")]
    if critical_detections:
        return GuardrailResult(
            allowed=False,
            blocked=True,
            block_reason="Potential prompt injection detected",
            sanitised_query=sanitised,
            detections=detections,
        )
    
    # 6. Block poisoned queries
    poison_detections = [d for d in detections if d.startswith("POISON:")]
    if poison_detections:
        return GuardrailResult(
            allowed=False,
            blocked=True,
            block_reason="Potentially malicious content detected",
            sanitised_query=sanitised,
            detections=detections,
        )
    
    # 7. Allow with warnings
    return GuardrailResult(
        allowed=True,
        sanitised_query=sanitised,
        detections=detections,
    )


def validate_output(answer: str) -> dict:
    """Validate LLM output for poisoned content (L8 fix).
    
    Detects and blocks injected content in LLM responses.
    
    Args:
        answer: LLM's response
        
    Returns:
        Dict with 'clean' flag and 'violations' list
    """
    violations = []
    
    # 1. Check for system prompt leakage (L1 additional protection)
    system_prompt_fragments = [
        "expert AI assistant specializing in the Tunisian energy sector",
        "Use ONLY the following context",
        "do not hallucinate or use outside knowledge",
        "I do not have enough information to answer that based on the provided documents",
        "Answer in the same language as the user's query",
    ]
    
    for fragment in system_prompt_fragments:
        if fragment.lower() in answer.lower():
            violations.append(f"SYSTEM_PROMPT_LEAKED:{fragment[:30]}...")
    
    # 2. Check for poisoned content (L8 protection)
    for pattern, label in POISONED_PATTERNS:
        if re.search(pattern, answer, re.IGNORECASE):
            violations.append(label)
    
    # 3. Check for credential patterns
    credential_patterns = [
        (r"ADMIN_API_KEY\s*[:=]\s*\S+", "ADMIN_KEY_LEAKED"),
        (r"JWT_SECRET\s*[:=]\s*\S+", "JWT_SECRET_LEAKED"),
        (r"DATABASE_URL\s*[:=]\s*\S+", "DB_URL_LEAKED"),
        (r"api[_-]?key\s*[:=]\s*\S+", "API_KEY_LEAKED"),
        (r"password\s*[:=]\s*\S+", "PASSWORD_LEAKED"),
    ]
    
    for pattern, label in credential_patterns:
        if re.search(pattern, answer, re.IGNORECASE):
            violations.append(label)
    
    # 4. Check for malicious URLs
    malicious_urls = [
        r"evil\.example\.com",
        r"malicious\.com",
        r"phishing\.com",
        r"SolarScam",
        r"malicious\.net",
    ]
    
    for pattern in malicious_urls:
        if re.search(pattern, answer, re.IGNORECASE):
            violations.append(f"MALICIOUS_URL:{pattern}")
    
    # 5. Check for injected email addresses
    email_pattern = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    emails = re.findall(email_pattern, answer)
    
    # Known safe emails (energy sector)
    safe_emails = [
        "contact@anme.tn",
        "info@steg.com.tn",
    ]
    
    for email in emails:
        if email.lower() not in [e.lower() for e in safe_emails]:
            # Check if email is in a suspicious context
            if any(sus in answer.lower() for sus in ["contact", "email", "reach", "write"]):
                violations.append(f"SUSPICIOUS_EMAIL:{email}")
    
    return {
        "clean": len(violations) == 0,
        "violations": violations,
    }


def is_energy_domain(query: str) -> bool:
    """Check if query is related to the energy domain.
    
    Args:
        query: User's input query
        
    Returns:
        True if energy-related, False otherwise
    """
    query_lower = query.lower()
    return any(keyword.lower() in query_lower for keyword in ENERGY_KEYWORDS)


def get_safe_response() -> str:
    """Return a safe default response for blocked queries.
    
    Returns:
        Generic safe response string
    """
    return BLOCKED_RESPONSE


# ─── L6: Domain Classification ────────────────────────────────────────────────

def classify_domain(query: str) -> dict:
    """Classify if query is about Tunisian energy sector.
    
    Args:
        query: User's input query
        
    Returns:
        Dict with 'domain' and 'confidence' keys
    """
    # Strong energy indicators (Tunisia-specific)
    strong_indicators = [
        "tunisia", "tunisian", "tunisie", "tunisien",
        "anme", "steg", "cder",
        "solar tunisia", "wind tunisia", "energy tunisia",
    ]
    
    # Weak energy indicators (general energy)
    weak_indicators = [
        "energy", "solar", "wind", "renewable", "electricity",
        "power", "grid", "turbine", "battery", "carbon",
        "photovoltaic", "emission", "efficiency",
    ]
    
    # Non-energy indicators
    non_energy_indicators = [
        "culture", "food", "recipe", "weather", "sports",
        "music", "history", "politics", "economy", "tourism",
        "football", "soccer", "movie", "film", "restaurant",
    ]
    
    query_lower = query.lower()
    
    # L6 FIX: Check non-energy indicators FIRST to block non-energy queries
    for indicator in non_energy_indicators:
        if indicator in query_lower:
            # Check if energy keywords are also present
            has_energy = any(e in query_lower for e in ["energy", "solar", "electricity", "power"])
            if not has_energy:
                return {"domain": "non_energy", "confidence": "high"}
    
    # Check for strong Tunisia-specific indicators
    for indicator in strong_indicators:
        if indicator in query_lower:
            return {"domain": "tunisia_energy", "confidence": "high"}
    
    # Check for weak energy indicators
    for indicator in weak_indicators:
        if indicator in query_lower:
            return {"domain": "energy_general", "confidence": "medium"}
    
    # Default: uncertain
    return {"domain": "uncertain", "confidence": "low"}


def filter_domain_violations(answer: str, query: str) -> str:
    """Filter out non-energy content from response.
    
    Args:
        answer: LLM's response
        query: Original user query
        
    Returns:
        Filtered response string
    """
    # Patterns that indicate domain escape
    domain_escape_patterns = [
        r"(?i)(in general|globally|worldwide|other countries)",
        r"(?i)(culture|tradition|customs|history)",
        r"(?i)(economy|gdp|trade|business)",
        r"(?i)(politics|government|policy)(?!.*energy)",
    ]
    
    # Check for domain violations
    violations = []
    for pattern in domain_escape_patterns:
        if re.search(pattern, answer):
            violations.append(pattern)
    
    # If violations found, return safe response
    if violations:
        return (
            "I can only provide information about the Tunisian energy sector. "
            "Please ask a specific question about energy in Tunisia."
        )
    
    return answer


# ─── L7: Confidence Scoring ───────────────────────────────────────────────────

def assess_confidence(answer: str, context: str) -> dict:
    """Assess confidence that answer is grounded in context.
    
    Args:
        answer: LLM's response
        context: Retrieved context from documents
        
    Returns:
        Dict with 'level', 'score', and 'hedging_count' keys
    """
    # Simple heuristic: check if answer phrases appear in context
    answer_words = set(answer.lower().split())
    context_words = set(context.lower().split())
    
    # Calculate overlap
    overlap = answer_words.intersection(context_words)
    confidence = len(overlap) / max(len(answer_words), 1)
    
    # Check for hedging language (indicates uncertainty)
    hedging_patterns = [
        r"(?i)(might|could|possibly|perhaps|maybe)",
        r"(?i)(i think|i believe|generally|typically)",
        r"(?i)(in my opinion|it seems|appears to be)",
    ]
    
    hedging_count = sum(
        1 for pattern in hedging_patterns
        if re.search(pattern, answer)
    )
    
    # Determine confidence level
    if confidence > 0.7 and hedging_count == 0:
        level = "high"
    elif confidence > 0.4 or hedging_count <= 1:
        level = "medium"
    else:
        level = "low"
    
    return {
        "level": level,
        "score": round(confidence, 2),
        "hedging_count": hedging_count,
    }


def verify_claims(answer: str, context: str) -> list:
    """Verify that claims in answer are supported by context.
    
    Args:
        answer: LLM's response
        context: Retrieved context from documents
        
    Returns:
        List of violation strings
    """
    violations = []
    
    # Extract numerical claims from answer
    number_pattern = r'\b\d+(?:\.\d+)?(?:\s*%|\s*percent)?\b'
    answer_numbers = set(re.findall(number_pattern, answer))
    context_numbers = set(re.findall(number_pattern, context))
    
    # Check if numbers in answer exist in context
    for num in answer_numbers:
        try:
            num_val = float(num.replace('%', '').replace('percent', ''))
            if num not in context_numbers and num_val > 100:
                violations.append(f"Unsupported number: {num}")
        except ValueError:
            pass
    
    # Check for absolute claims
    absolute_patterns = [
        r"(?i)(always|never|all|none|every|only)",
        r"(?i)(first|last|best|worst|most|least)",
    ]
    
    for pattern in absolute_patterns:
        matches = re.findall(pattern, answer)
        for match in matches:
            # Check if context supports this absolute claim
            if match.lower() not in context.lower():
                violations.append(f"Unsupported absolute claim: {match}")
    
    return violations


def mask_sensitive_content(text: str) -> str:
    """Mask potentially sensitive content in retrieved chunks.
    
    Args:
        text: Content to mask
        
    Returns:
        Masked content string
    """
    # Mask email addresses
    text = re.sub(
        r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
        '[EMAIL REDACTED]',
        text
    )
    
    # Mask phone numbers
    text = re.sub(
        r'[\+]?[(]?[0-9]{1,4}[)]?[-\s\.]?[0-9]{1,4}[-\s\.]?[0-9]{1,9}',
        '[PHONE REDACTED]',
        text
    )
    
    # Mask URLs (except common energy sector URLs)
    safe_urls = ['anme.tn', 'steg.com.tn', 'cder.tn']
    def mask_url(match):
        url = match.group(0)
        for safe in safe_urls:
            if safe in url.lower():
                return url
        return '[URL REDACTED]'
    
    text = re.sub(r'https?://[^\s]+', mask_url, text)
    
    return text
