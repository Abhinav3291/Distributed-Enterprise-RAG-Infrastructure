import os
import torch
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
from transformers import pipeline


class InputGuardrails:

    def __init__(
        self,
        toxigen_model: str = "tomh/toxigen_roberta",
        toxicity_threshold: float = 0.5,
        pii_score_threshold: float = 0.6,
    ):
        """Initializes ToxiGen for toxicity detection and Presidio for PII masking."""
        # Detect GPU availability for lower latency
        device = 0 if torch.cuda.is_available() else -1

        # Truncation enabled to handle inputs larger than 512 tokens safely
        self.toxic_classifier = pipeline(
            "text-classification",
            model=toxigen_model,
            device=device,
            truncation=True,
            max_length=512,
        )
        self.threshold = toxicity_threshold
        self.pii_score_threshold = pii_score_threshold

        self.analyzer = AnalyzerEngine()
        self.anonymizer = AnonymizerEngine()

    def _check_toxicity(self, text: str) -> tuple[bool, str]:
        """Detects toxic content using the ToxiGen model.

        Returns: (is_toxic: bool, reason: str)
        """
        try:
            result = self.toxic_classifier(text)[0]
            score = float(result["score"])
            label = str(result["label"]).upper()

            # Check if predicted label indicates toxicity and exceeds threshold
            is_toxic = (label in ["LABEL_1", "TOXIC", "POSITIVE"]) and (
                score >= self.threshold
            )

            if is_toxic:
                return (
                    True,
                    f"Content flagged for toxicity (Confidence: {score:.2f}).",
                )

            return False, ""

        except Exception as e:
            # Log warning and fail open/safe depending on policy
            print(f"[Guardrail Warning] Toxicity check failed: {e}")
            return False, ""

    def _detect_PII(self, text: str) -> tuple[str, bool]:
        """Detects and masks PII using the Presidio analyzer with confidence thresholding.

        Returns: (sanitized_text: str, pii_found: bool)
        """
        try:
            analysis_results = self.analyzer.analyze(
                text=text,
                entities=[
                    "EMAIL_ADDRESS",
                    "PHONE_NUMBER",
                    "PERSON",
                    "CREDIT_CARD",
                    "US_SSN",
                    "IP_ADDRESS",
                ],
                score_threshold=self.pii_score_threshold,  # Filter out low-confidence false positives
                language="en",
            )

            # If no high-confidence PII is found, return original text
            if not analysis_results:
                return text, False

            # Anonymize text
            masked_text = self.anonymizer.anonymize(
                text=text,
                analyzer_results=analysis_results,
            )

            return masked_text.text, True

        except Exception as e:
            print(f"[Guardrail Warning] PII check failed: {e}")
            return text, False

    def validate_prompt(self, text: str) -> dict:
        """Validates text: Rejects if toxic, sanitizes if it contains PII.

        Returns: dict with status, reason, and sanitized_prompt.
        """
        try:
            is_toxic, toxicity_reason = self._check_toxicity(text)
            if is_toxic:
                return {
                    "status": "REJECTED",
                    "reason": toxicity_reason,
                    "sanitized_prompt": None,
                }

            sanitized_text, pii_found = self._detect_PII(text)

            return {
                "status": "ACCEPTED",
                "reason": (
                    "PII detected and redacted."
                    if pii_found
                    else "Clean prompt."
                ),
                "sanitized_prompt": sanitized_text,
            }

        except Exception as e:
            return {
                "status": "ERROR",
                "reason": str(e),
                "sanitized_prompt": None,
            }

    def validate_input(self, text: str) -> tuple[bool, str]:
        """Simplified interface for the API gateway.

        Returns: (is_safe: bool, message_or_prompt: str)
        """
        result = self.validate_prompt(text)
        status = result.get("status")

        if status in ["REJECTED", "ERROR"]:
            return False, result.get("reason", "Rejected by guardrails.")

        return True, result.get("sanitized_prompt", text)