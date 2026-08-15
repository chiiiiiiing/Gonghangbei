from __future__ import annotations

import subprocess
import sys
import unittest
from unittest.mock import patch

import scripts.collect_live_cninfo_lithium_signals as collector
import scripts.run_daily_lithium_v6 as daily
import scripts.verify_lithium_v6_system as verify_system


class LithiumV6PipelineTests(unittest.TestCase):
    def test_collector_loads_v3_rulebook_and_discovery_contexts(self) -> None:
        rulebook = collector.load_v3_rulebook()
        self.assertGreaterEqual(len(rulebook), 1)
        self.assertIn("conditions", rulebook[0])
        contexts = collector.discovery_records()
        self.assertGreaterEqual(len(contexts), 100)

    def test_collector_no_eligible_branch_returns_zero_without_api(self) -> None:
        with patch.object(collector, "build_stock_map", return_value={}), patch.object(
            collector, "collect_candidates", return_value=[]
        ), patch.object(collector, "append_run_log") as append_run_log, patch.object(
            sys, "argv",
            ["collect", "--end", "2026-08-15", "--days", "7"],
        ):
            self.assertEqual(collector.main(), 0)
            append_run_log.assert_called_once()
            self.assertEqual(
                append_run_log.call_args.args[0]["notes"],
                "no_eligible_post_freeze_documents",
            )

    def test_daily_pipeline_can_skip_live_collector_and_run_update_and_monitor(self) -> None:
        fake_update = subprocess.CompletedProcess(
            ["update"], 0, stdout="{}", stderr=""
        )
        fake_monitor = subprocess.CompletedProcess(
            ["monitor"], 1, stdout="{}", stderr=""
        )
        with patch.object(
            daily, "run", side_effect=[fake_update, fake_monitor]
        ), patch.object(
            sys, "argv",
            ["daily", "--end", "2026-08-14", "--skip-live-collector"],
        ):
            self.assertEqual(daily.main(), 0)

    def test_system_verifier_returns_zero_with_current_frozen_state(self) -> None:
        self.assertEqual(verify_system.main(), 0)


if __name__ == "__main__":
    unittest.main()
