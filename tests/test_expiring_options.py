import unittest

from expiring_options.scanner import (
    ExpiringOptionFilters,
    build_candidates,
    filter_reason,
    option_midpoint_price,
    next_standard_expiration,
    rank_rows,
    select_strike_at_or_below,
    target_strike,
)


class ExpiringOptionsCalculationsTest(unittest.TestCase):
    def test_next_standard_expiration(self):
        from datetime import date
        self.assertEqual(next_standard_expiration(date(2026, 5, 28)).isoformat(), "2026-05-29")
        self.assertEqual(next_standard_expiration(date(2026, 5, 29)).isoformat(), "2026-05-29")

    def test_target_strike(self):
        self.assertEqual(target_strike(500, 5), 475)
        self.assertEqual(target_strike(500, 10), 450)
        self.assertEqual(target_strike(500, 15), 425)

    def test_select_strike_at_or_below(self):
        strike, approx = select_strike_at_or_below([470, 475, 480], 477)
        self.assertEqual(strike, 475)
        self.assertFalse(approx)

    def test_select_nearest_when_none_below(self):
        strike, approx = select_strike_at_or_below([100, 105], 95)
        self.assertEqual(strike, 100)
        self.assertTrue(approx)

    def test_midpoint_formula(self):
        self.assertEqual(option_midpoint_price(3.00, 4.00), 3.50)

    def test_build_candidates_metrics_and_ranking(self):
        symbols = [{"symbol": "MSFT", "company_name": "Microsoft"}]
        prices = {"MSFT": 500.0}
        chains = {"MSFT": [
            {"strike_price": 475.0, "expiration_date": "2026-05-28", "bid_price": 3.0, "ask_price": 4.0, "last_price": 3.4, "volume": 10, "open_interest": 100},
            {"strike_price": 450.0, "expiration_date": "2026-05-28", "bid_price": 1.0, "ask_price": 2.0, "last_price": 1.4, "volume": 10, "open_interest": 100},
            {"strike_price": 425.0, "expiration_date": "2026-05-28", "bid_price": 0.5, "ask_price": 1.0, "last_price": 0.7, "volume": 10, "open_interest": 100},
        ]}
        errors = []
        rows_by_level, _chain_rows, excluded = build_candidates(symbols, prices, chains, ExpiringOptionFilters(), "midpoint_premium_yield_on_strike", __import__("datetime").date(2026, 5, 28), errors)
        row = rows_by_level["5"][0]
        self.assertEqual(excluded, 0)
        self.assertAlmostEqual(row["option_midpoint_price"], 3.5)
        self.assertAlmostEqual(row["bid_ask_spread"], 1.0)
        self.assertAlmostEqual(row["midpoint_premium_yield_on_strike"], 3.5 / 475 * 100)
        self.assertAlmostEqual(row["midpoint_premium_percent_of_selected_strike"], 3.5 / 475 * 100)
        self.assertAlmostEqual(row["midpoint_premium_yield_on_underlying"], 3.5 / 500 * 100)
        self.assertAlmostEqual(row["breakeven_price"], 471.5)
        self.assertAlmostEqual(row["breakeven_discount"], (500 - 471.5) / 500 * 100)

    def test_filter_zero_bid(self):
        row = {"bid_price": 0, "ask_price": 0.2, "option_midpoint_price": 0.1, "open_interest": 1, "volume": 1, "distance_below_current_stock_price": 5}
        self.assertEqual(filter_reason(row, ExpiringOptionFilters()), "zero bid")

    def test_rank_rows(self):
        rows = [{"midpoint_premium_yield_on_strike": 0.5}, {"midpoint_premium_yield_on_strike": 1.5}]
        ranked = rank_rows(rows, "midpoint_premium_yield_on_strike")
        self.assertEqual(ranked[0]["rank"], 1)
        self.assertEqual(ranked[0]["midpoint_premium_yield_on_strike"], 1.5)


if __name__ == "__main__":
    unittest.main()
