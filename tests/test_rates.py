from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from app.server import _RATE_LIMITS, app
from scripts.augment_rates_policy_sources import _index_existing_by_url
from scripts.fetch_rates_structured_data import _mlf_amount, _mlf_rate, parse_pboc_afre_stock_text
from scripts.review_rates_annotations import evaluate_review, prepare_review
from src.ai.gateway import AISettings
from src.rates.engine import (
    _daily_context,
    _structured_context,
    _structured_status,
    append_review,
    load_backtest,
    load_forecast,
    load_status,
)
from src.rates.factors import factor_scores, ground_predicates, independent_event_key, merge_llm_predicates
from src.rates.llm import extract_with_llm
from src.rates.modeling import (
    apply_rule_prior,
    blend_with_market_baseline,
    labels_for_market,
    paired_block_bootstrap,
)
from src.rates.rules import RULES, activate_rules
from src.rates.schema import (
    FLAT_THRESHOLD_BP,
    PREDICATES,
    STRUCTURED_FIELDS,
    direction_label,
    effective_trade_date,
    validate_structured_row,
)


class RatesResearchTests(unittest.TestCase):
    def test_direction_threshold_is_configurable_and_symmetric(self) -> None:
        self.assertEqual(FLAT_THRESHOLD_BP, 2.0)
        self.assertEqual(direction_label(2.01), "up")
        self.assertEqual(direction_label(-2.01), "down")
        self.assertEqual(direction_label(2.0), "flat")
        self.assertEqual(direction_label(-2.0), "flat")

    def test_after_close_and_non_trading_day_map_to_next_market_date(self) -> None:
        dates = ["2026-08-28", "2026-08-31", "2026-09-01"]
        self.assertEqual(effective_trade_date("2026-08-28 17:30:00", dates), "2026-08-28")
        self.assertEqual(effective_trade_date("2026-08-28 17:30:01", dates), "2026-08-31")
        self.assertEqual(effective_trade_date("2026-08-29 09:00:00", dates), "2026-08-31")

    def test_predicate_schema_is_complete_and_evidence_is_grounded(self) -> None:
        document = {
            "title": "公开市场操作",
            "content": "中国人民银行开展逆回购操作，向市场投放流动性，保持流动性合理充裕。",
        }
        rows = ground_predicates(document)
        self.assertEqual({row["predicate_name"] for row in rows}, set(PREDICATES))
        active = [row for row in rows if row["value"]]
        self.assertIn("liquidity_supply_increases", {row["predicate_name"] for row in active})
        full_text = f"{document['title']}。{document['content']}"
        self.assertTrue(all(row["evidence_text"] in full_text for row in active))
        self.assertLess(factor_scores(rows)["liquidity"], 0)

    def test_routine_operation_without_direction_is_not_called_liquidity_increase(self) -> None:
        rows = ground_predicates({
            "title": "公开市场操作",
            "content": "中国人民银行以固定利率、数量招标方式开展了7天期逆回购操作。",
            "source_name": "中国人民银行",
        })
        liquidity = next(row for row in rows if row["predicate_name"] == "liquidity_supply_increases")
        self.assertFalse(liquidity["value"])

    def test_grounded_high_confidence_llm_can_extend_dictionary_recall(self) -> None:
        document = {
            "title": "流动性安排", "content": "银行体系流动性供给较前期扩容。",
            "source_name": "中国人民银行",
        }
        deterministic = ground_predicates(document)
        evidence = "银行体系流动性供给较前期扩容。"
        llm_rows = [{
            "predicate_name": name,
            "value": name == "liquidity_supply_increases",
            "evidence_text": evidence if name == "liquidity_supply_increases" else "",
            "confidence": 0.9,
            "intensity": 0.8,
        } for name in PREDICATES]
        merged = merge_llm_predicates(deterministic, llm_rows, f"{document['title']}。{document['content']}")
        liquidity = next(row for row in merged if row["predicate_name"] == "liquidity_supply_increases")
        self.assertTrue(liquidity["value"])
        self.assertEqual(liquidity["consensus"], "llm_only_grounded")
        self.assertEqual(liquidity["source"], "llm")
        self.assertEqual(liquidity["yield_direction"], -1)
        self.assertLess(factor_scores(merged)["liquidity"], 0)

    def test_rules_do_not_fire_without_evidence(self) -> None:
        rows = ground_predicates({"title": "无关文本", "content": "今天公布一份普通说明。"})
        self.assertEqual(activate_rules(rows), [])

    def test_llm_object_map_is_normalized_and_regrounded(self) -> None:
        settings = AISettings(
            mode="api", base_url="https://example.test", api_key="test", chat_model="test-model",
            embedding_model="", timeout_seconds=1, json_mode="object",
        )
        predicate_map = {name: False for name in PREDICATES}
        predicate_map["liquidity_supply_increases"] = True
        raw = {
            "summary": "流动性投放",
            "events": [{
                "subject": "中国人民银行", "action": "开展逆回购操作", "object": "市场",
                "policy_direction": "宽松", "intensity": "中等", "horizon": "短期",
                "transmission_channel": "流动性投放",
                "evidence_text": "中国人民银行开展逆回购操作，向市场投放流动性。",
                "confidence": True,
            }],
            "predicates": predicate_map,
        }
        document = {
            "title": "逆回购", "content": "中国人民银行开展逆回购操作，向市场投放流动性。",
            "source_name": "中国人民银行",
        }
        with patch("src.rates.llm.AISettings.from_environment", return_value=settings), patch(
            "src.rates.llm.OpenAICompatibleGateway.chat_json", return_value=(raw, {"request_id": "REQ-1"})
        ):
            result = extract_with_llm(document)
        self.assertTrue(result["used"])
        self.assertEqual(len(result["predicates"]), len(PREDICATES))
        active = next(row for row in result["predicates"] if row["predicate_name"] == "liquidity_supply_increases")
        self.assertIn(active["evidence_text"], document["content"])
        self.assertEqual(result["events"][0]["transmission_channel"], "liquidity")
        self.assertEqual(result["events"][0]["policy_direction"], -1)

    def test_llm_true_without_exact_evidence_cannot_enter_factor(self) -> None:
        deterministic = ground_predicates({
            "title": "逆回购",
            "content": "中国人民银行开展逆回购操作，向市场投放流动性。",
            "source_name": "中国人民银行",
        })
        llm_rows = [
            {"predicate_name": name, "value": name == "liquidity_supply_increases", "evidence_text": "", "confidence": 0.9}
            for name in PREDICATES
        ]
        merged = merge_llm_predicates(deterministic, llm_rows, "逆回购。中国人民银行开展逆回购操作，向市场投放流动性。")
        liquidity = next(row for row in merged if row["predicate_name"] == "liquidity_supply_increases")
        self.assertFalse(liquidity["value"])
        self.assertEqual(liquidity["consensus"], "agreed_false")
        self.assertEqual(factor_scores(merged)["liquidity"], 0.0)

    def test_labels_only_use_strictly_future_fifth_market_row(self) -> None:
        rows = [
            {"trade_date": f"2026-01-{index + 1:02d}", "cgb_10y_yield": str(1.0 + index * 0.01), "dr007_proxy": "1.5"}
            for index in range(8)
        ]
        labels = labels_for_market(rows)
        self.assertEqual(labels[0], "up")
        self.assertIsNone(labels[-1])
        self.assertEqual(sum(label is None for label in labels), 5)

    def test_labels_support_audit_only_horizon_and_threshold_variants(self) -> None:
        rows = [
            {"trade_date": f"2026-02-{index + 1:02d}", "cgb_10y_yield": str(1.0 + index * 0.01), "dr007_proxy": "1.5"}
            for index in range(12)
        ]
        one_day = labels_for_market(rows, horizon_trading_days=1, threshold_bp=0.5)
        ten_day = labels_for_market(rows, horizon_trading_days=10, threshold_bp=2.0)
        self.assertEqual(one_day[0], "up")
        self.assertEqual(sum(label is None for label in one_day), 1)
        self.assertEqual(ten_day[0], "up")
        self.assertEqual(sum(label is None for label in ten_day), 10)
        with self.assertRaises(ValueError):
            labels_for_market(rows, horizon_trading_days=0)

    def test_independent_event_key_collapses_repeated_calendar_notice(self) -> None:
        base = {
            "subject": "中国人民银行", "action": "投放", "object": "银行间流动性",
            "policy_direction": -1, "transmission_channel": "liquidity",
        }
        first = {**base, "evidence_text": "2026年8月1日开展逆回购投放100亿元。"}
        repeated = {**base, "evidence_text": "2026年8月2日开展逆回购投放100亿元。"}
        changed = {**base, "evidence_text": "2026年8月2日开展逆回购投放200亿元。"}
        self.assertEqual(independent_event_key(first), independent_event_key(repeated))
        self.assertNotEqual(independent_event_key(first), independent_event_key(changed))

    def test_daily_context_gates_raw_llm_events_with_accepted_predicates(self) -> None:
        document = {
            "doc_id": "D-LLM-GATE", "source_sha256": "a" * 64,
            "title": "逆回购投放",
            "content": "中国人民银行开展逆回购操作，向市场投放流动性100亿元。",
            "source_name": "中国人民银行", "source_url": "https://www.pbc.gov.cn/",
            "publish_time": "2026-01-02 09:00:00",
        }
        evidence = "中国人民银行开展逆回购操作，向市场投放流动性100亿元。"
        annotation = {
            "used": True,
            "events": [{
                "event_id": "raw-llm-event", "subject": "中国人民银行", "action": "投放",
                "object": "流动性", "policy_direction": -1, "intensity": 0.8,
                "horizon": "短期", "transmission_channel": "monetary_policy",
                "evidence_text": evidence, "confidence": 0.9,
            }],
            "predicates": [{
                "predicate_name": "liquidity_supply_increases", "value": True,
                "evidence_text": evidence, "confidence": 0.9,
            }],
            "metadata": {"request_id": "REQ-GATE"},
        }
        market = [{"trade_date": "2026-01-02"}, {"trade_date": "2026-01-05"}]
        with patch("src.rates.engine._llm_annotation_cache", return_value={
            (document["doc_id"], document["source_sha256"]): annotation,
        }), patch("src.rates.engine.MINIMUM_INDEPENDENT_EVENTS", 1):
            factors, _pressure, audit, daily = _daily_context(market, [document], [])
        self.assertLess(factors["2026-01-02"]["liquidity"], 0)
        self.assertEqual(daily[0]["liquidity_independent_event_count"], 1)
        self.assertEqual(daily[0]["monetary_policy_independent_event_count"], 0)
        self.assertEqual({row["transmission_channel"] for row in audit[0]["events"]}, {"liquidity"})
        self.assertEqual(audit[0]["llm_events"][0]["transmission_channel"], "monetary_policy")

    def test_text_signal_really_decays_instead_of_cancelling_its_decay_weight(self) -> None:
        document = {
            "doc_id": "D-DECAY", "source_sha256": "c" * 64,
            "title": "流动性投放", "content": "中国人民银行向市场投放流动性。",
            "source_name": "中国人民银行", "source_url": "https://www.pbc.gov.cn/",
            "publish_time": "2026-01-02 09:00:00",
        }
        market = [
            {"trade_date": day} for day in
            ("2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08")
        ]
        with patch("src.rates.engine._llm_annotation_cache", return_value={}), patch(
            "src.rates.engine.MINIMUM_INDEPENDENT_EVENTS", 1
        ):
            factors, _pressure, _audit, _daily = _daily_context(market, [document], [])
        magnitudes = [abs(factors[row["trade_date"]]["liquidity"]) for row in market]
        self.assertTrue(all(left > right for left, right in zip(magnitudes, magnitudes[1:])))

    def test_text_overlay_and_rule_prior_have_bounded_nonzero_effects(self) -> None:
        baseline = {"down": 0.40, "flat": 0.40, "up": 0.20}
        text_model = {"down": 0.20, "flat": 0.30, "up": 0.50}
        blended = blend_with_market_baseline(baseline, text_model)
        self.assertAlmostEqual(sum(blended.values()), 1.0, places=7)
        self.assertGreater(blended["up"], baseline["up"])
        self.assertLess(blended["down"], baseline["down"])
        ruled = apply_rule_prior(blended, pressure=1.0)
        self.assertAlmostEqual(sum(ruled.values()), 1.0, places=7)
        self.assertGreater(ruled["up"], blended["up"])
        self.assertLess(ruled["down"], blended["down"])

    def test_text_before_market_sample_is_not_replayed_on_first_trade_date(self) -> None:
        document = {
            "doc_id": "D-PRE-SAMPLE", "source_sha256": "b" * 64,
            "title": "逆回购投放",
            "content": "中国人民银行开展逆回购操作，向市场投放流动性200亿元。",
            "source_name": "中国人民银行", "source_url": "https://www.pbc.gov.cn/",
            "publish_time": "2019-12-31 09:00:00",
        }
        market = [{"trade_date": "2020-01-02"}, {"trade_date": "2020-01-03"}]
        with patch("src.rates.engine._llm_annotation_cache", return_value={}):
            factors, _pressure, audit, daily = _daily_context(market, [document], [])
        self.assertIsNone(audit[0]["effective_trade_date"])
        self.assertFalse(audit[0]["model_eligible"])
        self.assertEqual(audit[0]["exclusion_reason"], "published_before_market_sample")
        self.assertEqual(daily[0]["supporting_document_count"], 0)
        self.assertEqual(daily[0]["independent_event_count"], 0)
        self.assertEqual(factors["2020-01-02"]["liquidity"], 0.0)

    def test_structured_observation_requires_public_release_contract(self) -> None:
        row = dict.fromkeys(STRUCTURED_FIELDS, "")
        row.update({
            "observation_date": "2020-01-31", "release_time": "2020-02-15 23:59:59",
            "period_start": "2020-01-01", "period_end": "2020-01-31",
            "indicator": "cpi_yoy", "value": "5.4", "unit": "%",
            "source_name": "国家统计局", "source_url": "https://www.stats.gov.cn/",
            "source_sha256": "a" * 64, "vintage": "first_publication",
        })
        validate_structured_row(row)
        row["observation_date"] = "1900-01-01"
        with self.assertRaises(ValueError):
            validate_structured_row(row)

    def test_structured_value_is_invisible_before_public_release(self) -> None:
        market = [{"trade_date": "2020-02-14"}, {"trade_date": "2020-02-17"}]
        structured = [{"release_time": "2020-02-15 10:00:00", "indicator": "cpi_yoy", "value": "5.4"}]
        context, coverage = _structured_context(market, structured)
        self.assertNotIn("cpi_yoy", context["2020-02-14"])
        self.assertEqual(context["2020-02-17"]["cpi_yoy"], 5.4)
        self.assertEqual(coverage["cpi_yoy"], 1)

    def test_structured_context_keeps_latest_period_not_latest_file(self) -> None:
        market = [{"trade_date": "2025-10-14"}, {"trade_date": "2025-11-04"}, {"trade_date": "2025-11-06"}]
        structured = [
            {"release_time": "2025-10-15 09:00:00", "observation_date": "2025-10-31", "indicator": "afre_government_bonds", "value": "100"},
            {"release_time": "2025-11-05 09:00:00", "observation_date": "2019-12-31", "indicator": "afre_government_bonds", "value": "999"},
        ]
        context, _coverage = _structured_context(market, structured)
        self.assertEqual(context["2025-11-04"]["afre_government_bonds"], 100.0)
        self.assertEqual(context["2025-11-06"]["afre_government_bonds"], 100.0)

    def test_retrospective_structured_reconstruction_is_audit_only(self) -> None:
        market = [{"trade_date": "2025-11-04"}, {"trade_date": "2025-11-06"}]
        structured = [{
            "release_time": "2025-11-05 23:59:59", "observation_date": "2019-12-31",
            "indicator": "afre_government_bonds", "value": "377273",
            "vintage": "official_pboc_afre_stock_reconstruction_2025",
        }]
        context, coverage = _structured_context(market, structured)
        counts, statuses, overall = _structured_status(structured)
        self.assertNotIn("afre_government_bonds", context["2025-11-06"])
        self.assertNotIn("afre_government_bonds", coverage)
        self.assertEqual(counts["afre_government_bonds"], 1)
        self.assertEqual(statuses["afre_government_bonds"], "audit_only")
        self.assertEqual(overall, "partial")

    def test_known_government_bond_issuance_expires_after_event_window(self) -> None:
        market = [
            {"trade_date": "2026-01-02"},
            {"trade_date": "2026-01-05"},
            {"trade_date": "2026-01-12"},
        ]
        structured = [{
            "release_time": "2026-01-01 09:00:00", "observation_date": "2026-01-06",
            "indicator": "government_bond_issuance", "value": "100", "vintage": "treasury",
        }]
        context, coverage = _structured_context(market, structured)
        self.assertEqual(context["2026-01-02"]["government_bond_issuance"], 100.0)
        self.assertEqual(context["2026-01-05"]["government_bond_issuance"], 100.0)
        self.assertEqual(context["2026-01-12"]["government_bond_issuance"], 0.0)
        self.assertEqual(coverage["government_bond_issuance"], 1)

    def test_official_mlf_and_afre_parsers_reject_ambiguous_values(self) -> None:
        notice = (
            "人民银行开展中期借贷便利操作共7330亿元，其中期限6个月3580亿元、"
            "1年期3750亿元，利率分别为2.85%、3.0%。"
        )
        self.assertEqual(_mlf_amount(notice), 7330.0)
        self.assertEqual(_mlf_rate(notice), 3.0)
        self.assertIsNone(_mlf_rate("开展9000亿元MLF操作，采用多重价位中标。"))
        pdf_layout = "\n".join(
            f"2017.{index + 1:02d} 1800000 1000000 1 2 3 4 5 {220000 + index} 7 8 9"
            for index in range(36)
        )
        parsed = parse_pboc_afre_stock_text(pdf_layout)
        self.assertEqual(len(parsed), 36)
        self.assertEqual(parsed[0], ("2017-01-31", 220000.0))

    def test_bootstrap_converts_twenty_days_to_four_non_overlapping_windows(self) -> None:
        timeline = [
            {"as_of": f"2026-01-{index + 1:02d}", "correct": index % 2 == 0}
            for index in range(10)
        ]
        baseline = {"timeline": timeline}
        enhanced = {"timeline": [{**row, "correct": True} for row in timeline]}
        result = paired_block_bootstrap(baseline, enhanced, iterations=20, block_days=20)
        self.assertEqual(result["block_days"], 20)
        self.assertEqual(result["block_observations"], 4)

    def test_official_sample_loads_and_backtest_has_no_lookahead(self) -> None:
        status = load_status()
        self.assertTrue(status["data_ready"], status["data_errors"])
        self.assertGreaterEqual(status["market_rows"], 2000)
        self.assertGreaterEqual(status["text_rows"], 350)
        self.assertGreaterEqual(status["structured_rows"], 500)
        self.assertTrue(status["structured_data_ready"], status["data_errors"])
        self.assertEqual(status["structured_indicator_status"]["mlf_rate"], "sufficient")
        self.assertEqual(status["structured_indicator_status"]["government_bond_issuance"], "sufficient")
        self.assertEqual(status["structured_indicator_status"]["afre_government_bonds"], "audit_only")
        self.assertEqual(status["structured_data_status"], "partial")
        self.assertGreaterEqual(len(RULES), 10)
        forecast = load_forecast()
        self.assertEqual(forecast["horizon_trading_days"], 5)
        self.assertAlmostEqual(sum(forecast["probabilities"].values()), 1.0, places=5)
        backtest = load_backtest()
        self.assertEqual({row["route"] for row in backtest["routes"]}, {"market_baseline", "text_only", "fusion", "fusion_rules"})
        self.assertIn("retrospective_holdout", backtest["periods"])
        self.assertIn("prospective_oos", backtest["periods"])
        self.assertIn("holdout_increment_bootstrap", backtest)
        diagnostics = backtest["enhancement_diagnostics"]
        self.assertTrue(diagnostics["text_overlay"]["effect_observed"])
        self.assertGreater(diagnostics["text_overlay"]["accuracy_difference_vs_market"], 0)
        self.assertGreater(diagnostics["text_overlay"]["macro_f1_difference_vs_market"], 0)
        self.assertTrue(diagnostics["rule_prior"]["effect_observed"])
        self.assertGreater(diagnostics["rule_prior"]["active_observations"], 0)
        self.assertGreater(diagnostics["rule_prior"]["mean_total_variation_probability_change"], 0)
        for route in backtest["routes"]:
            self.assertIn("macro_precision", route)
            self.assertIn("macro_recall", route)
            self.assertIn("macro_auc_ovr", route)
            self.assertEqual(route["training_policy"]["kind"], "purged_non_overlapping_rolling_window")
            self.assertEqual(route["training_policy"]["evaluation_stride_days"], 5)
            for row in route["timeline"]:
                self.assertLess(row["train_origin_end"], row["as_of"])
                self.assertLessEqual(row["label_known_through"], row["as_of"])
                self.assertLessEqual(row["train_observations"], 756)

    def test_rates_endpoints_and_single_text_marginal_output(self) -> None:
        client = app.test_client()
        for path in (
            "/api/rates/status", "/api/rates/forecast?horizon=5", "/api/rates/backtest",
            "/api/rates/evidence", "/api/rates/reviews", "/api/rates/demo-cases",
            "/api/rates/report",
        ):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
        invalid = client.get("/api/rates/forecast?horizon=10")
        self.assertEqual(invalid.status_code, 400)
        with patch.dict("os.environ", {"ALPHALENS_ALLOW_SERVER_LLM": "false"}), patch(
            "src.rates.engine.extract_with_llm"
        ) as llm_call:
            response = client.post("/api/rates/analyze", json={
                "title": "逆回购投放",
                "content": "中国人民银行开展逆回购操作，向市场投放流动性，保持流动性合理充裕。",
                "source_name": "中国人民银行",
                "source_url": "https://www.pbc.gov.cn/",
                "publish_time": "2026-09-04T09:30:00",
            })
        llm_call.assert_not_called()
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["analysis_type"], "incremental_single_text")
        self.assertFalse(payload["llm_analysis"]["used"])
        self.assertAlmostEqual(sum(payload["updated_forecast"].values()), 1.0, places=5)
        self.assertIn("边际影响", payload["interpretation"])
        self.assertIn("processed_at", payload)

    def test_new_text_after_latest_close_is_an_age_zero_next_trade_scenario(self) -> None:
        client = app.test_client()
        with patch.dict("os.environ", {"ALPHALENS_ALLOW_SERVER_LLM": "false"}), patch(
            "src.rates.engine.datetime"
        ) as mocked_datetime:
            mocked_datetime.now.return_value = __import__("datetime").datetime(2026, 9, 7, 10, 0, 0)
            mocked_datetime.fromisoformat.side_effect = __import__("datetime").datetime.fromisoformat
            response = client.post("/api/rates/analyze", json={
                "title": "经济与物价回升",
                "content": "经济运行回升，PMI扩张；CPI同比回升，物价上涨压力增加。",
                "source_name": "国家统计局", "source_url": "https://www.stats.gov.cn/",
                "publish_time": "2026-09-07T09:30:00",
            })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["marginal_model"]["active_in_latest_window"])
        self.assertEqual(payload["marginal_model"]["scenario_alignment"], "next_available_trade_date_scenario")
        self.assertTrue(any(abs(value) > 0 for value in payload["probability_delta"].values()))

        oversized = client.post("/api/rates/analyze", json={
            "title": "过长文本", "content": "政" * 120001,
            "source_name": "中国人民银行", "source_url": "https://www.pbc.gov.cn/",
            "publish_time": "2026-08-28T09:30:00",
        })
        self.assertEqual(oversized.status_code, 400)

    def test_historical_forecast_is_bounded_and_rate_limited_by_trusted_ip(self) -> None:
        client = app.test_client()
        self.assertEqual(
            client.get("/api/rates/forecast?as_of=not-a-date").status_code, 400
        )
        self.assertEqual(
            client.get("/api/rates/forecast?as_of=2000-01-01").status_code, 400
        )
        _RATE_LIMITS.clear()
        try:
            with patch("app.server.load_forecast", return_value={"status": "test"}) as forecast_call:
                for index in range(6):
                    response = client.get(
                        "/api/rates/forecast?as_of=2026-01-02",
                        environ_base={"REMOTE_ADDR": "127.0.0.1"},
                        headers={
                            "X-Real-IP": "203.0.113.7",
                            "X-Forwarded-For": f"198.51.100.{index}",
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                blocked = client.get(
                    "/api/rates/forecast?as_of=2026-01-02",
                    environ_base={"REMOTE_ADDR": "127.0.0.1"},
                    headers={
                        "X-Real-IP": "203.0.113.7",
                        "X-Forwarded-For": "198.51.100.250",
                    },
                )
                self.assertEqual(blocked.status_code, 429)
                self.assertEqual(forecast_call.call_count, 6)
        finally:
            _RATE_LIMITS.clear()

    def test_missing_market_snapshot_returns_evidence_insufficient(self) -> None:
        missing = Path("/path/that/does/not/exist.csv")
        with (
            patch("src.rates.engine.MARKET_PATH", missing),
            patch("src.rates.engine.TEXT_PATH", missing),
            patch("src.rates.engine.AUDIT_PATH", missing),
        ):
            status = load_status()
            forecast = load_forecast()
            backtest = load_backtest()
        self.assertFalse(status["data_ready"])
        self.assertEqual(forecast["status"], "research_evidence_insufficient")
        self.assertEqual(backtest["status"], "research_evidence_insufficient")

    def test_submission_exposes_only_rates_apis(self) -> None:
        routes = {
            rule.rule
            for rule in app.url_map.iter_rules()
            if rule.rule.startswith("/api/")
        }
        self.assertEqual(routes, {
            "/api/rates/status",
            "/api/rates/forecast",
            "/api/rates/backtest",
            "/api/rates/evidence",
            "/api/rates/reviews",
            "/api/rates/demo-cases",
            "/api/rates/report",
            "/api/rates/extract-file",
            "/api/rates/analyze",
            "/api/rates/review",
        })
        response = app.test_client().get("/api/unknown")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "接口不存在")
        asset_response = app.test_client().get("/vendor/lucide.min.js")
        try:
            self.assertEqual(asset_response.status_code, 200)
        finally:
            asset_response.close()

    def test_text_file_upload_extracts_content_without_analysis(self) -> None:
        client = app.test_client()
        response = client.post(
            "/api/rates/extract-file",
            data={"file": (BytesIO("货币政策保持稳健。".encode("utf-8")), "policy.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["content"], "货币政策保持稳健。")
        self.assertEqual(len(payload["sha256"]), 64)

    def test_review_is_append_only_and_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "reviews.jsonl"
            with patch("src.rates.engine.REVIEW_PATH", target):
                first = append_review({"document_id": "D1", "decision": "approved", "comment": "证据一致"})
                second = append_review({"document_id": "D1", "decision": "needs_revision", "comment": "补来源"})
            rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertNotEqual(first["review_id"], second["review_id"])
        with self.assertRaises(ValueError):
            append_review({"document_id": "D1", "decision": "unknown"})

    def test_blind_annotation_review_requires_independent_gold_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            review_dir = Path(directory)
            prepared = prepare_review(review_dir=review_dir)
            self.assertEqual(prepared["documents"], 60)
            self.assertEqual(prepared["predicate_labels_required"], 840)
            self.assertTrue(prepared["blind"])
            evaluated = evaluate_review(review_dir=review_dir)
            self.assertEqual(evaluated["status"], "awaiting_independent_human_labels")
            self.assertEqual(evaluated["labels_completed"], 0)
            self.assertEqual(evaluated["metrics"], {})

    def test_policy_augmentation_preserves_all_verified_mof_urls(self) -> None:
        rows = [
            {"source_url": "https://www.mof.gov.cn/seed.html", "source_name": "财政部"},
            {"source_url": "https://www.mof.gov.cn/crawled.html", "source_name": "财政部"},
            {"source_url": "https://www.pbc.gov.cn/policy.html", "source_name": "中国人民银行"},
        ]
        indexed = _index_existing_by_url(rows)
        self.assertEqual(set(indexed), {row["source_url"] for row in rows})
        self.assertEqual(indexed["https://www.mof.gov.cn/crawled.html"]["source_name"], "财政部")


if __name__ == "__main__":
    unittest.main()
