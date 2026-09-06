"""
Unit tests for the dependency-free helper functions in
place_confirmation_calls.py: phone masking, E.164 validation, region
inference, allowlist matching, idempotency-key stability, error-body
sanitization, and terminal-result resolution.

These exercise pure logic only — no network calls, no CALL-E API key
required. Run with:

    python -m unittest scripts/test_place_confirmation_calls.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from place_confirmation_calls import (
    CALLE_BASE_URL, OFFICIAL_HOST, Appointment, _infer_region, _mask,
    _sanitize_output_text, _stable_idempotency_key, normalize_phone_for_match,
    resolve_result, validate_e164,
)


class TestMask(unittest.TestCase):
    def test_masks_middle_digits(self):
        self.assertEqual(_mask("+14155550101"), "+1415•••••01")

    def test_short_number_fully_masked(self):
        self.assertEqual(_mask("123"), "•••")

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(_mask("  +14155550101  "), "+1415•••••01")


class TestValidateE164(unittest.TestCase):
    def test_accepts_valid_ascii_e164(self):
        ok, _ = validate_e164("+14155550101")
        self.assertTrue(ok)

    def test_rejects_missing_plus(self):
        ok, reason = validate_e164("14155550101")
        self.assertFalse(ok)
        self.assertIn("E.164", reason)

    def test_rejects_non_ascii_digits(self):
        # Arabic-Indic digit variant of "1" smuggled into the number.
        ok, reason = validate_e164("+١4155550101")
        self.assertFalse(ok)
        self.assertIn("non-ASCII", reason)

    def test_rejects_letters(self):
        ok, _ = validate_e164("+1415555ABCD")
        self.assertFalse(ok)

    def test_rejects_too_short(self):
        ok, _ = validate_e164("+123")
        self.assertFalse(ok)

    def test_rejects_leading_zero_country_code(self):
        ok, _ = validate_e164("+0145550101")
        self.assertFalse(ok)


class TestInferRegion(unittest.TestCase):
    def test_infers_us(self):
        self.assertEqual(_infer_region("+14155550101"), "US")

    def test_infers_uk(self):
        self.assertEqual(_infer_region("+442079460101"), "GB")

    def test_prefers_longer_country_code_match(self):
        # +971 (UAE) must not be misread as +9 or +97 matching something else.
        self.assertEqual(_infer_region("+971501234567"), "AE")

    def test_unknown_code_returns_none(self):
        self.assertIsNone(_infer_region("+9999999999"))


class TestAllowlistMatching(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(normalize_phone_for_match("+14155550101"), "+14155550101")

    def test_no_partial_match_semantics(self):
        # normalize_phone_for_match must not strip/alter digits in a way
        # that would let a near-miss number match — exact string only.
        a = normalize_phone_for_match("+14155550101")
        b = normalize_phone_for_match("+1415555010")  # one digit short
        self.assertNotEqual(a, b)


class TestStableIdempotencyKey(unittest.TestCase):
    def _appt(self, **overrides):
        base = dict(
            recipient_name="Alex Rivera", phone="+14155550101",
            appointment_time="2026-09-08T15:00:00-04:00",
            context="annual checkup", business_name="Sunrise Clinic",
        )
        base.update(overrides)
        return Appointment(**base)

    def test_same_appointment_same_key_across_calls(self):
        a1 = self._appt()
        a2 = self._appt()
        self.assertEqual(_stable_idempotency_key(a1), _stable_idempotency_key(a2))

    def test_key_is_not_random_each_time(self):
        a = self._appt()
        self.assertEqual(_stable_idempotency_key(a), _stable_idempotency_key(a))

    def test_different_appointment_different_key(self):
        a1 = self._appt()
        a2 = self._appt(appointment_time="2026-09-09T15:00:00-04:00")
        self.assertNotEqual(_stable_idempotency_key(a1), _stable_idempotency_key(a2))


class TestBaseUrlIsHardcoded(unittest.TestCase):
    """The base URL is a module constant with no override mechanism —
    these tests just confirm it's what it should be and hasn't
    regressed back to something environment/flag-configurable."""

    def test_base_url_is_official_https_host(self):
        self.assertEqual(CALLE_BASE_URL, f"https://{OFFICIAL_HOST}")
        self.assertTrue(CALLE_BASE_URL.startswith("https://"))

    def test_official_host_constant_matches(self):
        self.assertEqual(OFFICIAL_HOST, "api.heycall-e.com")


class TestSanitizeOutputText(unittest.TestCase):
    """Sanitization is applied uniformly to ALL provider-supplied text
    (error bodies, notes, requested_new_time) — not just HTTP errors."""

    def test_redacts_api_key(self):
        text = _sanitize_output_text("auth failed for key sk_live_abc123", api_key="sk_live_abc123")
        self.assertNotIn("sk_live_abc123", text)
        self.assertIn("REDACTED_API_KEY", text)

    def test_redacts_raw_phone_number(self):
        text = _sanitize_output_text("could not reach +14155550101", api_key="")
        self.assertNotIn("+14155550101", text)
        self.assertIn("REDACTED_PHONE", text)

    def test_truncates_long_text(self):
        text = _sanitize_output_text("x" * 1000, api_key="")
        self.assertLessEqual(len(text), 500)

    def test_strips_control_characters(self):
        # Embedded ANSI/control characters (e.g. from a manipulated
        # voice-call transcript) must never reach a terminal or a
        # results file unsanitized.
        text = _sanitize_output_text("confirmed\x1b[31mFAKE ALERT\x1b[0m\x07", api_key="")
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x07", text)

    def test_applies_to_notes_and_requested_new_time_fields(self):
        # Simulates what run() does with structured_result fields —
        # both must go through sanitization, not just error paths.
        structured = {
            "requested_new_time": "2026-09-09T11:30:00+00:00\x1b[2Jinjected",
            "notes": "caller mentioned +14155550101 as a callback number",
        }
        safe_time = _sanitize_output_text(str(structured["requested_new_time"]))
        safe_notes = _sanitize_output_text(str(structured["notes"]))
        self.assertNotIn("\x1b", safe_time)
        self.assertNotIn("+14155550101", safe_notes)


class TestResolveResult(unittest.TestCase):
    def test_timed_out_call_is_pending(self):
        status, structured = resolve_result({"_timed_out": True, "status": "in_progress"})
        self.assertEqual(status, "pending")
        self.assertEqual(structured, {})

    def test_failed_status_overrides_any_structured_result(self):
        call = {
            "status": "failed",
            "structured_result": {"status": "confirmed"},
        }
        status, _ = resolve_result(call)
        self.assertEqual(status, "failed")

    def test_recognized_structured_status_passes_through(self):
        call = {
            "status": "completed",
            "structured_result": {"status": "needs_reschedule"},
        }
        status, structured = resolve_result(call)
        self.assertEqual(status, "needs_reschedule")
        self.assertEqual(structured["status"], "needs_reschedule")

    def test_unrecognized_structured_status_is_unclear(self):
        call = {
            "status": "completed",
            "structured_result": {"status": "maybe_confirmed_not_sure"},
        }
        status, _ = resolve_result(call)
        self.assertEqual(status, "unclear")

    def test_missing_structured_result_is_unclear(self):
        call = {"status": "completed"}
        status, _ = resolve_result(call)
        self.assertEqual(status, "unclear")


if __name__ == "__main__":
    unittest.main()
