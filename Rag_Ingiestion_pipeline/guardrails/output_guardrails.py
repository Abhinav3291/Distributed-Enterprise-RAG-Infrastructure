import re
from typing import List, Dict, Tuple


class OutputGuardrails:

    def __init__(self, groundedness_threshold: float = 0.25):
        self.groundedness_threshold = groundedness_threshold

        # Regex patterns for PII and sensitive data masking
        self.pii_patterns = {
            "EMAIL": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            "PHONE": r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
            "CREDIT_CARD": r"\b(?:\d[ -]*?){13,16}\b",
            "API_KEY": r"\b(?:sk-[a-zA-Z0-9]{32,}|AKIA[0-9A-Z]{16})\b",
            "SSN_AADHAAR": r"\b\d{3}-\d{2}-\d{4}\b|\b\d{4}\s?\d{4}\s?\d{4}\b",
        }

        # Blocked refusal or leak signatures
        self.unwanted_signatures = [
            "as an ai language model",
            "as an ai",
            "my system instructions",
            "ignore previous instructions",
        ]

    def mask_pii(self, text: str) -> str:
        """Redacts sensitive PII patterns from the LLM answer."""
        sanitized_text = text
        for pii_type, pattern in self.pii_patterns.items():
            sanitized_text = re.sub(
                pattern, f"[REDACTED_{pii_type}]", sanitized_text
            )
        return sanitized_text

    def check_groundedness(self, answer: str, context_chunks: List[Dict]) -> bool:
        """
        Validates whether key terms in the answer are anchored in the retrieved context.
        Prevents hallucinated content when low context relevance is present.
        """
        if not context_chunks:
            return False

        # Extract unique words (4+ characters) from retrieved chunks
        context_text = " ".join([c.get("text", "") for c in context_chunks]).lower()
        context_words = set(re.findall(r"\b[a-z0-9]{4,}\b", context_text))

        if not context_words:
            return False

        # Extract unique words from generated answer
        answer_words = set(re.findall(r"\b[a-z0-9]{4,}\b", answer.lower()))
        if not answer_words:
            return True

        # Calculate lexical overlap ratio
        overlap = answer_words.intersection(context_words)
        overlap_ratio = len(overlap) / len(answer_words)

        return overlap_ratio >= self.groundedness_threshold

    def validate_and_sanitize(
        self, raw_answer: str, context_chunks: List[Dict]
    ) -> Tuple[str, bool, str]:
        """
        Full Output Guardrail Pipeline:
        1. Check for AI refusal/leak signatures
        2. Verify lexical groundedness against context
        3. Mask PII and sensitive data
        """
        if not raw_answer or not raw_answer.strip():
            return (
                "Unable to generate a valid response from the knowledge base.",
                False,
                "EMPTY_RESPONSE",
            )

        lower_answer = raw_answer.lower()

        # Rule 1: Signature Check
        for sig in self.unwanted_signatures:
            if sig in lower_answer:
                return (
                    "Response blocked due to meta-prompting leak or system violation.",
                    False,
                    "META_LEAK_BLOCKED",
                )

        # Rule 2: Groundedness / Hallucination Check
        is_grounded = self.check_groundedness(raw_answer, context_chunks)
        if not is_grounded:
            return (
                "The system generated a response that could not be confidently verified against the retrieved source documents.",
                False,
                "HALLUCINATION_RISK",
            )

        # Rule 3: PII Masking
        sanitized_answer = self.mask_pii(raw_answer)

        return sanitized_answer, True, "PASSED"