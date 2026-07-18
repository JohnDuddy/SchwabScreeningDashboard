import unittest

from runtime_state import make_scan_state, reset_scan_state


class RuntimeStateTests(unittest.TestCase):
    def test_make_scan_state_includes_common_lifecycle_fields(self):
        state = make_scan_state(include_summary=True, extra={"enabled": True})
        self.assertFalse(state["running"])
        self.assertIsNone(state["started"])
        self.assertEqual(state["progress"], 0)
        self.assertIsNone(state["results"])
        self.assertIsNone(state["summary"])
        self.assertTrue(state["enabled"])

    def test_reset_scan_state_clears_transient_fields_and_applies_overrides(self):
        state = make_scan_state(extra={"regime": {"old": True}})
        state.update({"results": [{"ticker": "AAPL"}], "error": "boom", "progress": 7})
        reset_scan_state(state, regime=None)
        self.assertTrue(state["running"])
        self.assertIsNone(state["completed"])
        self.assertEqual(state["progress"], 0)
        self.assertEqual(state["current"], "")
        self.assertIsNone(state["results"])
        self.assertIsNone(state["error"])
        self.assertIsNone(state["regime"])


if __name__ == "__main__":
    unittest.main()
