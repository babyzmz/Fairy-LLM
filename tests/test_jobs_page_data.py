from __future__ import annotations

import unittest

from app.ui.pages.jobs_page import build_generation_lineage, build_health_check_overview, build_health_check_rows


class JobsPageDataTest(unittest.TestCase):
    def test_build_health_check_rows_handles_missing_fields(self) -> None:
        rows = build_health_check_rows({})
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]["label"], "Processed ratio")
        self.assertEqual(rows[0]["value"], "0.00%")
        self.assertFalse(rows[0]["passed"])
        self.assertFalse(rows[-1]["passed"])

    def test_build_health_check_overview_reads_reasons_and_warnings(self) -> None:
        overview = build_health_check_overview(
            {
                "passed": False,
                "processed_ratio": 0.95,
                "failed_ratio": 0.03,
                "collection_readable": True,
                "vector_dim_match": False,
                "sample_lookup_passed": True,
                "query_smoke_test_passed": False,
                "warnings": ["sample drift"],
                "reasons": ["failed_ratio_above_threshold", "vector_dim_mismatch"],
            }
        )
        self.assertFalse(overview["overall_passed"])
        self.assertEqual(overview["warnings_count"], 1)
        self.assertEqual(overview["reasons_count"], 2)
        self.assertEqual(overview["failed_count"], 4)

    def test_build_generation_lineage_marks_active_and_previous(self) -> None:
        lineage = build_generation_lineage(
            [
                {
                    "job_id": "job_old",
                    "target_fingerprint": "fp_old",
                    "target_provider": "hash",
                    "target_model": "old",
                    "backend_type": "sqlite",
                    "promoted_at": "2026-03-16T10:00:00+00:00",
                    "is_active": False,
                    "metadata": {},
                },
                {
                    "job_id": "job_new",
                    "target_fingerprint": "fp_new",
                    "target_provider": "hash",
                    "target_model": "new",
                    "backend_type": "sqlite",
                    "promoted_at": "2026-03-17T10:00:00+00:00",
                    "is_active": True,
                    "metadata": {"parent_job_id": "job_old", "retry_mode": "failed_chunks_only"},
                },
            ]
        )
        self.assertEqual(lineage[0]["job_id"], "job_new")
        self.assertEqual(lineage[0]["generation_role"], "current")
        self.assertEqual(lineage[0]["parent_job_id"], "job_old")
        self.assertEqual(lineage[1]["generation_role"], "previous")


if __name__ == "__main__":
    unittest.main()
