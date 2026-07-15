from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.content_quality import (
    MODEL_FINAL_CHECKS,
    REQUIRED_FINAL_CHECKS,
    finalize_compact_review,
    validate_content_quality_artifacts,
)
from scripts.utils import write_json


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_quality_artifacts(job_dir: Path) -> tuple[set[str], set[str], str]:
    chunks_dir = job_dir / "evidence_chunks"
    chunks_dir.mkdir()
    manifest_chunks = []
    for index, text in enumerate(("first evidence\n", "second evidence\n"), start=1):
        path = chunks_dir / f"part-{index:04}.md"
        path.write_text(text, encoding="utf-8")
        manifest_chunks.append(
            {
                "index": index,
                "path": str(path),
                "start_line": index,
                "end_line": index,
                "bytes": len(text.encode("utf-8")),
                "sha256": sha256_bytes(text.encode("utf-8")),
            }
        )
    write_json(
        job_dir / "evidence_chunks.json",
        {
            "schema_version": 1,
            "source_path": str(job_dir / "codex_minutes_input.md"),
            "source_sha256": "a" * 64,
            "source_bytes": 30,
            "total_lines": 2,
            "lines_per_chunk": 1,
            "chunk_count": 2,
            "chunks": manifest_chunks,
        },
    )
    inventory = {
        "schema_version": 1,
        "items": [
            {
                "id": "E001",
                "importance": "required",
                "category": "baseline_monitoring",
            },
            {"id": "E002", "importance": "required", "category": "action"},
        ],
        "conflicts": [],
    }
    write_json(job_dir / "content_inventory.json", inventory)
    write_json(
        job_dir / "evidence_ledger.json",
        {
            "schema_version": 1,
            "status": "completed",
            "chunk_count": 2,
            "chunks": [
                {
                    "index": 1,
                    "source_sha256": manifest_chunks[0]["sha256"],
                    "classification": "material",
                    "rationale": "Contains the first product condition.",
                    "material_topics": ["first condition"],
                    "inventory_item_ids": ["E001"],
                },
                {
                    "index": 2,
                    "source_sha256": manifest_chunks[1]["sha256"],
                    "classification": "material",
                    "rationale": "Contains the second product condition.",
                    "material_topics": ["second condition"],
                    "inventory_item_ids": ["E002"],
                },
            ],
        },
    )
    front_matter = [
        {"key": "source", "label": "Source", "value": "Sample session"},
        {
            "key": "recording_datetime",
            "label": "Recording time",
            "value": "2026-07-15 10:00",
        },
        {"key": "duration", "label": "Duration", "value": "10 minutes"},
        {"key": "source_language", "label": "Source language", "value": "English"},
        {"key": "output_language", "label": "Output language", "value": "English"},
        {
            "key": "evidence_basis",
            "label": "Evidence basis",
            "value": "Timestamped STT and OCR",
        },
        {
            "key": "external_evidence_policy",
            "label": "External evidence policy",
            "value": "Recording first",
        },
    ]
    minutes_text = (
        "# Detailed report\n\n"
        "Document type: Technical session analysis\n\n"
        + "\n".join(f"- {item['label']}: {item['value']}" for item in front_matter)
        + "\n\n## Key messages\n\n"
        "- The first condition matters.\n"
        "- The second condition changes operations.\n"
        "- The recording remains the source of truth.\n\n"
        "## Topic analysis\n\n"
        "Both product conditions are preserved and explained together.\n\n"
        "## Operational actions\n\n"
        "| Owner | Action |\n"
        "|---|---|\n"
        "| Team A | Validate the first condition. |\n"
        "| Team B | Prepare for the second condition. |\n"
        "| Team C | Record the result. |\n\n"
        "## Open questions\n\n"
        "No unresolved questions remain.\n"
    )
    (job_dir / "minutes.md").write_text(minutes_text, encoding="utf-8")
    write_json(
        job_dir / "document_blueprint.json",
        {
            "schema_version": 1,
            "status": "completed",
            "document_archetype": "technical_session_analysis",
            "document_type": "Technical session analysis",
            "reader_goal": "Understand the conditions and act on them.",
            "front_matter": front_matter,
            "sections": [
                {
                    "id": "S01",
                    "heading": "Key messages",
                    "role": "executive_synthesis",
                    "form_factor": "grouped_bullets",
                    "applicability": "required",
                    "primary_inventory_item_ids": [],
                },
                {
                    "id": "S02",
                    "heading": "Topic analysis",
                    "role": "topic_analysis",
                    "form_factor": "prose",
                    "applicability": "required",
                    "primary_inventory_item_ids": ["E001", "E002"],
                },
                {
                    "id": "S03",
                    "heading": "Operational actions",
                    "role": "operational_actions",
                    "form_factor": "checklist",
                    "applicability": "required",
                    "primary_inventory_item_ids": [],
                },
                {
                    "id": "S04",
                    "heading": "Open questions",
                    "role": "open_questions",
                    "form_factor": "prose",
                    "applicability": "not_applicable",
                    "rationale": "No unresolved questions remain.",
                    "primary_inventory_item_ids": [],
                },
            ],
        },
    )
    final_checks = {
        name: {"status": "passed", "finding": f"{name} checked"}
        for name in MODEL_FINAL_CHECKS
    }
    write_json(
        job_dir / "content_quality_review.json",
        {
            "schema_version": 3,
            "status": "passed",
            "review_cycles": [
                {
                    "cycle": 1,
                    "status": "passed",
                    "findings": [],
                    "changes": [],
                }
            ],
            "final_checks": final_checks,
        },
    )
    finalize_compact_review(job_dir)
    return {"E001", "E002"}, {"E001", "E002"}, minutes_text


class ContentQualityTests(unittest.TestCase):
    def test_complete_ledger_and_adversarial_review_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            issues: list[str] = []

            result = validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=minutes_text,
                issues=issues,
            )

        self.assertEqual(issues, [])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["chunk_count"], 2)
        self.assertEqual(result["ledger_inventory_items"], 2)

    def test_material_chunk_without_inventory_mapping_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            ledger = json.loads((job_dir / "evidence_ledger.json").read_text())
            ledger["chunks"][1]["inventory_item_ids"] = []
            write_json(job_dir / "evidence_ledger.json", ledger)
            issues: list[str] = []

            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=minutes_text,
                issues=issues,
            )

        self.assertTrue(any("material chunk requires inventory_item_ids" in x for x in issues))
        self.assertTrue(any("required inventory items are not mapped" in x for x in issues))

    def test_reader_facing_body_rejects_raw_evidence_noise(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            noisy_text = minutes_text.replace(
                "- The first condition matters.",
                "- The first condition matters. STT:00:00:00-00:00:10 "
                "OCR:00:00:05 Snapshot:snapshot-0001@00:00:05",
            )
            (job_dir / "minutes.md").write_text(noisy_text, encoding="utf-8")
            issues: list[str] = []

            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=noisy_text,
                issues=issues,
            )

        self.assertTrue(any("too many raw STT/OCR/Snapshot" in x for x in issues))

    def test_front_matter_requires_explicit_document_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            missing_type_text = minutes_text.replace(
                "Document type: Technical session analysis\n\n",
                "",
            )
            (job_dir / "minutes.md").write_text(missing_type_text, encoding="utf-8")
            issues: list[str] = []

            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=missing_type_text,
                issues=issues,
            )

        self.assertTrue(
            any("must contain the blueprint document type line" in x for x in issues)
        )

    def test_blueprint_rejects_more_than_six_topic_analysis_h2_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            topic_sections = "\n\n".join(
                f"## Topic analysis {index}\n\nDetailed topic {index} explanation."
                for index in range(1, 8)
            )
            fragmented_text = minutes_text.replace(
                "## Topic analysis\n\n"
                "Both product conditions are preserved and explained together.",
                topic_sections,
            )
            (job_dir / "minutes.md").write_text(fragmented_text, encoding="utf-8")
            blueprint = json.loads(
                (job_dir / "document_blueprint.json").read_text(encoding="utf-8")
            )
            topic_template = blueprint["sections"][1]
            fragmented_sections = []
            for index in range(1, 8):
                section = {
                    **topic_template,
                    "id": f"S{index + 1:02}",
                    "heading": f"Topic analysis {index}",
                    "primary_inventory_item_ids": ["E001", "E002"]
                    if index == 1
                    else [],
                }
                fragmented_sections.append(section)
            blueprint["sections"] = [
                blueprint["sections"][0],
                *fragmented_sections,
                {
                    **blueprint["sections"][2],
                    "id": "S09",
                },
                {
                    **blueprint["sections"][3],
                    "id": "S10",
                },
            ]
            write_json(job_dir / "document_blueprint.json", blueprint)
            issues: list[str] = []

            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=fragmented_text,
                issues=issues,
            )

        self.assertTrue(any("more than six topic_analysis H2" in x for x in issues))

    def test_missing_document_blueprint_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            (job_dir / "document_blueprint.json").unlink()
            issues: list[str] = []

            result = validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=minutes_text,
                issues=issues,
            )

        self.assertEqual(result["status"], "failed")
        self.assertIn("missing document_blueprint.json", issues)

    def test_stale_quality_review_hash_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            review = json.loads((job_dir / "content_quality_review.json").read_text())
            review["bindings"]["minutes_sha256"] = "0" * 64
            write_json(job_dir / "content_quality_review.json", review)
            issues: list[str] = []

            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=minutes_text,
                issues=issues,
            )

        self.assertIn("content_quality_review.json minutes_sha256 does not match", issues)

    def test_compact_review_bindings_and_signals_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_quality_artifacts(job_dir)
            review = json.loads((job_dir / "content_quality_review.json").read_text())
            review["bindings"] = {"minutes_sha256": "model-supplied-wrong-value"}
            review["document_signals"] = {"minutes_bytes": -1}
            write_json(job_dir / "content_quality_review.json", review)

            finalized = finalize_compact_review(job_dir)
            expected_minutes_sha256 = sha256_bytes(
                (job_dir / "minutes.md").read_bytes()
            )

        self.assertEqual(
            finalized["bindings"]["minutes_sha256"],
            expected_minutes_sha256,
        )
        self.assertEqual(finalized["bindings"]["reviewed_chunk_indexes"], [1, 2])
        self.assertGreater(finalized["document_signals"]["minutes_bytes"], 0)
        self.assertEqual(finalized["document_signals"]["inventory_item_count"], 2)

    def test_compact_review_third_cycle_requires_blocking_defect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            review = json.loads((job_dir / "content_quality_review.json").read_text())
            review["review_cycles"] = [
                {"cycle": 1, "status": "revised", "findings": ["a"], "changes": ["a"]},
                {"cycle": 2, "status": "revised", "findings": ["b"], "changes": ["b"]},
                {"cycle": 3, "status": "passed", "findings": [], "changes": []},
            ]
            write_json(job_dir / "content_quality_review.json", review)
            issues: list[str] = []

            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=minutes_text,
                issues=issues,
            )

        self.assertTrue(any("third cycle requires" in issue for issue in issues))

    def test_schema_v2_review_remains_valid_for_existing_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            review = json.loads((job_dir / "content_quality_review.json").read_text())
            bindings = review.pop("bindings")
            review.update(bindings)
            review["schema_version"] = 2
            review["final_checks"] = {
                name: {"status": "passed", "finding": f"{name} checked"}
                for name in REQUIRED_FINAL_CHECKS
            }
            write_json(job_dir / "content_quality_review.json", review)
            issues: list[str] = []

            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=minutes_text,
                issues=issues,
            )

        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
