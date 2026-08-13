from __future__ import annotations

import calendar
import csv
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app.server import app
from src.ai.gateway import AIServiceError
from src.macro.ai_rules import validate_macro_ai_output
from src.macro.features import deduplicate_documents, near_duplicate_similarity
from src.macro.modeling import build_model_rows, evaluate_routes
from src.macro.predicates import ground_macro_predicates
from src.macro.rules import learn_historical_rules
from src.macro.schema import (
    MACRO_PREDICATES,
    TARGET_NAME,
    period_for_date,
    validate_target_row,
)


def official_periods(start_year: int, end_year: int) -> list[dict[str, str]]:
    periods = []
    for year in range(start_year, end_year + 1):
        periods.append(
            {
                "period_start": f"{year}-01-01",
                "period_end": f"{year}-02-{calendar.monthrange(year, 2)[1]:02d}",
                "period_kind": "jan_feb_combined",
            }
        )
        for month in range(3, 13):
            periods.append(
                {
                    "period_start": f"{year}-{month:02d}-01",
                    "period_end": f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}",
                    "period_kind": "month",
                }
            )
    return periods


def target_rows(start_year: int = 2015, end_year: int = 2024) -> list[dict[str, str]]:
    rows = []
    for index, period in enumerate(official_periods(start_year, end_year)):
        released = date.fromisoformat(period["period_end"]) + timedelta(days=18)
        rows.append(
            {
                **period,
                "target_name": TARGET_NAME,
                "target_value": f"{5.0 + 0.03 * index:.4f}",
                "release_date": released.isoformat(),
                "source_url": f"https://www.stats.gov.cn/macro/{period['period_end']}",
            }
        )
    return rows


class MacroResearchTests(unittest.TestCase):
    def test_january_and_february_share_one_official_period(self) -> None:
        january = period_for_date("2020-01-08")
        february = period_for_date("2020-02-29")
        self.assertEqual(january, february)
        self.assertEqual(january["period_kind"], "jan_feb_combined")
        self.assertEqual(january["period_end"], "2020-02-29")

    def test_target_release_must_follow_period_end(self) -> None:
        valid = target_rows(2020, 2020)[0]
        validate_target_row(valid)
        invalid = {**valid, "release_date": valid["period_end"]}
        with self.assertRaisesRegex(ValueError, "release_date"):
            validate_target_row(invalid)

    def test_document_dedup_uses_id_url_and_near_duplicate_text(self) -> None:
        base = {
            "doc_id": "D1",
            "source_type": "news",
            "title": "新能源设备订单增长",
            "content": "企业新能源设备订单明显增长，排产和交付同步提升。",
            "publish_time": "2024-03-10",
            "source_name": "媒体",
            "url": "https://example.com/a?utm_source=x",
        }
        same_url = {**base, "doc_id": "D2", "url": "https://example.com/a"}
        near = {
            **base,
            "doc_id": "D3",
            "url": "https://example.com/b",
            "content": base["content"] + " 责任编辑：测试。",
        }
        self.assertGreater(near_duplicate_similarity(base, near), 0.88)
        kept, dropped = deduplicate_documents([base, same_url, near])
        self.assertEqual([row["doc_id"] for row in kept], ["D1"])
        self.assertEqual(len(dropped), 2)

    def test_deterministic_macro_predicates_are_complete_and_grounded(self) -> None:
        document = {
            "doc_id": "D1",
            "source_type": "announcement",
            "title": "新能源电池生产线投产公告",
            "content": "公司新能源电池生产线正式投产，订单增长并提升排产。",
            "publish_time": "2020-02-10",
            "source_name": "公司公告",
            "url": "https://example.com/a",
        }
        rows = ground_macro_predicates(document)
        self.assertEqual({row["predicate_name"] for row in rows}, set(MACRO_PREDICATES))
        active = [row for row in rows if row["value"] == "true"]
        self.assertIn("capacity_enters_production", {row["predicate_name"] for row in active})
        for row in active:
            self.assertIn(row["evidence_text"], f"{document['title']}。{document['content']}")

    def test_macro_ai_output_requires_complete_grounded_schema(self) -> None:
        document = {
            "doc_id": "D1",
            "title": "生产线投产",
            "content": "新能源电池生产线正式投产。",
            "publish_time": "2020-03-10",
        }
        predicates = []
        for name in MACRO_PREDICATES:
            active = name == "capacity_enters_production"
            predicates.append(
                {
                    "name": name,
                    "value": "true" if active else "false",
                    "direction": 1 if active else 0,
                    "intensity": 0.8 if active else 0,
                    "confidence": 0.9,
                    "expected_lag_months": 1,
                    "evidence_text": "新能源电池生产线正式投产" if active else "",
                    "rationale": "原文投产证据" if active else "未出现",
                }
            )
        raw = {"summary": "投产证据", "predicates": predicates, "candidate_rules": []}
        result = validate_macro_ai_output(raw, document)
        self.assertEqual(len(result["predicates"]), 12)
        broken = {**raw, "predicates": predicates[:-1]}
        with self.assertRaisesRegex(AIServiceError, "完整 12"):
            validate_macro_ai_output(broken, document)

    def test_invalid_ai_candidates_are_dropped_without_losing_predicates(self) -> None:
        document = {
            "doc_id": "D1",
            "title": "生产线投产",
            "content": "新能源电池生产线正式投产。",
            "publish_time": "2020-03-10",
        }
        predicates = {}
        for name in MACRO_PREDICATES:
            active = name == "capacity_enters_production"
            predicates[name] = {
                "value": "true" if active else "false",
                "direction": 1 if active else 0,
                "intensity": 0.8 if active else 0,
                "confidence": 0.9,
                "expected_lag_months": 1,
                "evidence_text": "新能源电池生产线正式投产" if active else "",
                "rationale": "原文证据" if active else "未出现",
            }
        raw = {
            "summary": "投产",
            "predicates": predicates,
            "candidate_rules": [{"rule": "自然语言规则", "evidence": "投产", "reason": "测试"}],
        }
        result = validate_macro_ai_output(raw, document)
        self.assertEqual(result["validation"]["dropped_candidate_rule_count"], 1)
        self.assertTrue(result["validation"]["candidate_fallback_used"])
        self.assertEqual(result["candidate_rules"][0]["conditions"], ["capacity_enters_production"])

    def test_model_rows_never_use_unreleased_current_target(self) -> None:
        targets = target_rows(2019, 2020)
        features = [
            {
                **period,
                "route": "predicate_baseline",
                "feature_name": "coverage.document_count_log1p",
                "feature_value": "1.0",
                "document_count": "1",
            }
            for period in official_periods(2019, 2020)
        ]
        rows, _names = build_model_rows(targets, features, "predicate_baseline")
        by_period = {row["period_end"]: row for row in rows}
        march = by_period["2020-03-31"]
        current_target = float(next(row["target_value"] for row in targets if row["period_end"] == "2020-03-31"))
        self.assertNotEqual(march["latest_released"], current_target)
        latest_available = max(
            (row for row in targets if row["release_date"] <= "2020-03-31"),
            key=lambda row: row["release_date"],
        )
        self.assertEqual(march["latest_released"], float(latest_available["target_value"]))

    def test_historical_rules_ignore_validation_and_oos_targets(self) -> None:
        targets = target_rows()
        predicates = []
        for index, period in enumerate(official_periods(2015, 2021)):
            for document_index in range(2):
                predicates.append(
                    {
                        "doc_id": f"D{index:03d}-{document_index}",
                        **period,
                        "predicate_name": "order_demand_improves",
                        "value": "true",
                        "direction": "1",
                        "intensity": f"{0.5 + index / 300:.4f}",
                        "confidence": "0.9",
                        "expected_lag_months": "0",
                        "evidence_text": "订单增长",
                        "source": "deterministic",
                    }
                )
        original = learn_historical_rules(predicates, targets)
        changed = [
            {**row, "target_value": "999.0"}
            if row["period_end"] >= "2022-01-01"
            else row
            for row in targets
        ]
        self.assertEqual(original, learn_historical_rules(predicates, changed))

    def test_insufficient_coverage_selects_no_text_ridge(self) -> None:
        result = evaluate_routes([], [])
        self.assertFalse(result["data_sufficient"])
        self.assertEqual(result["selected_route"], "no_text_ridge")
        self.assertEqual(result["conclusion"], "文本预测增量不足")

    def test_macro_endpoint_is_read_only_and_truthful(self) -> None:
        target_path = Path(__file__).resolve().parents[1] / "data" / "sample" / "macro_targets.csv"
        before = target_path.read_bytes()
        response = app.test_client().get("/api/macro-nowcast")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["conclusion"], "文本预测增量不足")
        self.assertEqual(payload["selected_route"], "no_text_ridge")
        self.assertEqual(payload["provisional_rule_route"], "historical_rules")
        self.assertGreater(payload["data_sufficiency_checks"]["train_gap"], 0)
        self.assertEqual(before, target_path.read_bytes())
        self.assertEqual(payload["disclaimer"], "本报告仅供研究参考，不构成投资建议")

    def test_macro_csv_headers_are_locked(self) -> None:
        expected = {
            "macro_targets.csv": [
                "period_start", "period_end", "period_kind", "target_name", "target_value", "release_date", "source_url",
            ],
            "macro_predicates.csv": [
                "doc_id", "period_start", "period_end", "period_kind", "predicate_name", "value", "direction", "intensity", "confidence", "expected_lag_months", "evidence_text", "source",
            ],
            "macro_rules.csv": [
                "rule_id", "route", "condition", "direction", "expected_lag_months", "independent_document_count", "independent_period_count", "train_correlation", "rolling_mae_improvement", "stability", "score", "status",
            ],
        }
        root = Path(__file__).resolve().parents[1] / "data" / "sample"
        for filename, fields in expected.items():
            with (root / filename).open(encoding="utf-8", newline="") as handle:
                self.assertEqual(csv.DictReader(handle).fieldnames, fields)


if __name__ == "__main__":
    unittest.main()
