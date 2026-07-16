from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.content_quality import (
    MODEL_FINAL_CHECKS,
    QUALITY_CONTRACT_VERSION,
    REQUIRED_ITEM_DIMENSIONS,
    REQUIRED_FINAL_CHECKS,
    _section_form_signals,
    _validate_form_factor,
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
            "quality_contract_version": QUALITY_CONTRACT_VERSION,
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
        "The first condition applies during baseline monitoring. "
        "Its risk is missing drift. It affects operational readiness. "
        "Team A must validate it.\n\n"
        "The second condition applies during change preparation. "
        "Its limitation is incomplete readiness data. It affects release timing. "
        "Team B must prepare for it.\n\n"
        "## Operational actions\n\n"
        "| Owner | Action |\n"
        "|---|---|\n"
        "| Team A | Validate the first condition. |\n"
        "| Team B | Prepare for the second condition. |\n"
        "| Team C | Record the result. |\n\n"
        "## Items Requiring Further Verification\n\n"
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
            "visual_evidence_plan": {
                "status": "not_applicable",
                "rationale": "The fixture has no selected snapshots.",
                "items": [],
            },
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
                    "heading": "Items Requiring Further Verification",
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
    required_item_checks = []
    dimension_refs = {
        "E001": {
            "core_facts": "The first condition applies during baseline monitoring.",
            "conditions_exceptions": "The first condition applies during baseline monitoring.",
            "risks_limitations": "Its risk is missing drift.",
            "impact": "It affects operational readiness.",
            "actions_decisions": "Team A must validate it.",
        },
        "E002": {
            "core_facts": "The second condition applies during change preparation.",
            "conditions_exceptions": "The second condition applies during change preparation.",
            "risks_limitations": "Its limitation is incomplete readiness data.",
            "impact": "It affects release timing.",
            "actions_decisions": "Team B must prepare for it.",
        },
    }
    for item_id, refs in dimension_refs.items():
        required_item_checks.append(
            {
                "item_id": item_id,
                "section_id": "S02",
                "dimensions": {
                    name: {"status": "covered", "document_refs": [refs[name]]}
                    for name in REQUIRED_ITEM_DIMENSIONS
                },
            }
        )
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
            "required_item_checks": required_item_checks,
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

    def test_open_questions_uses_the_canonical_trust_heading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            noncanonical_text = minutes_text.replace(
                "## Items Requiring Further Verification",
                "## Open questions",
            )
            (job_dir / "minutes.md").write_text(noncanonical_text, encoding="utf-8")
            blueprint = json.loads(
                (job_dir / "document_blueprint.json").read_text(encoding="utf-8")
            )
            blueprint["sections"][-1]["heading"] = "Open questions"
            write_json(job_dir / "document_blueprint.json", blueprint)
            finalize_compact_review(job_dir)
            issues: list[str] = []

            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=noncanonical_text,
                issues=issues,
            )

        self.assertTrue(any("canonical open_questions heading" in issue for issue in issues))

    def test_official_verification_contract_requires_the_final_trust_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            write_json(
                job_dir / "official_sources.json",
                {
                    "schema_version": 1,
                    "status": "not_applicable",
                    "checked_at": "2026-07-16T12:00:00+09:00",
                    "policy": "official_only",
                    "appendix_heading": "External Evidence Check",
                    "reason": "No unresolved public claim remained after local cross-checking.",
                    "claims": [],
                    "privacy": {"raw_transcript_or_ocr_sent": False},
                },
            )
            issues: list[str] = []

            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=minutes_text,
                issues=issues,
            )

        self.assertTrue(
            any("requires exactly one external_evidence section" in issue for issue in issues)
        )

    def test_trust_sections_are_the_final_two_h2_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            reason = "No unresolved public claim remained after local cross-checking."
            trusted_text = (
                minutes_text.rstrip()
                + "\n\n## External Evidence Check\n\n"
                + reason
                + "\n"
            )
            (job_dir / "minutes.md").write_text(trusted_text, encoding="utf-8")
            blueprint = json.loads(
                (job_dir / "document_blueprint.json").read_text(encoding="utf-8")
            )
            blueprint["sections"].append(
                {
                    "id": "S05",
                    "heading": "External Evidence Check",
                    "role": "external_evidence",
                    "form_factor": "prose",
                    "applicability": "not_applicable",
                    "rationale": reason,
                    "primary_inventory_item_ids": [],
                }
            )
            write_json(job_dir / "document_blueprint.json", blueprint)
            write_json(
                job_dir / "official_sources.json",
                {
                    "schema_version": 1,
                    "status": "not_applicable",
                    "checked_at": "2026-07-16T12:00:00+09:00",
                    "policy": "official_only",
                    "appendix_heading": "External Evidence Check",
                    "reason": reason,
                    "claims": [],
                    "privacy": {"raw_transcript_or_ocr_sent": False},
                },
            )
            finalize_compact_review(job_dir)
            issues: list[str] = []

            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=trusted_text,
                issues=issues,
            )

        self.assertEqual(issues, [])

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

    def test_front_matter_accepts_bulleted_document_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            bulleted_text = minutes_text.replace(
                "Document type: Technical session analysis",
                "- Document type: Technical session analysis",
            )
            (job_dir / "minutes.md").write_text(bulleted_text, encoding="utf-8")
            finalize_compact_review(job_dir)
            issues: list[str] = []

            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=bulleted_text,
                issues=issues,
            )

        self.assertFalse(
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

    def test_compact_review_normalizes_common_model_schema_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            review = json.loads((job_dir / "content_quality_review.json").read_text())
            for item in review["required_item_checks"]:
                item["inventory_item_id"] = item.pop("item_id")
                item["primary_section_id"] = item.pop("section_id")
                item["checks"] = item.pop("dimensions")
            review["review_cycles"] = [
                {
                    "cycle": 1,
                    "status": "revised",
                    "findings": ["TARGETED_REPAIR in S02"],
                    "changes": [],
                },
                {
                    "cycle": 2,
                    "status": "passed",
                    "findings": [],
                    "changes": ["Expanded only S02 from the evidence inventory."],
                    "target_section_ids": ["S02"],
                },
            ]
            write_json(job_dir / "content_quality_review.json", review)

            finalized = finalize_compact_review(job_dir)
            issues: list[str] = []
            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=minutes_text,
                issues=issues,
            )

        first_item = finalized["required_item_checks"][0]
        self.assertIn("item_id", first_item)
        self.assertIn("section_id", first_item)
        self.assertIn("dimensions", first_item)
        self.assertNotIn("checks", first_item)
        self.assertEqual(
            finalized["review_cycles"][0]["target_section_ids"],
            ["S02"],
        )
        self.assertEqual(
            finalized["review_cycles"][0]["changes"],
            ["Expanded only S02 from the evidence inventory."],
        )
        self.assertEqual(finalized["review_cycles"][1]["changes"], [])
        self.assertFalse(any("required_item_checks" in issue for issue in issues))
        self.assertFalse(any("LOW_INFORMATION_DENSITY" in issue for issue in issues))

    def test_mixed_form_factor_counts_a_substantive_image(self) -> None:
        signals = _section_form_signals(
            "A prose explanation introduces the architecture.\n\n"
            "![Architecture](snapshots/snapshot_0001.jpg)"
        )
        issues: list[str] = []

        _validate_form_factor(
            "mixed",
            signals,
            label="document_blueprint.json sections[0]",
            issues=issues,
        )

        self.assertEqual(issues, [])

    def test_quality_contract_requires_substance_checks_for_every_required_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            review = json.loads((job_dir / "content_quality_review.json").read_text())
            review["required_item_checks"] = review["required_item_checks"][:1]
            write_json(job_dir / "content_quality_review.json", review)
            issues: list[str] = []

            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=minutes_text,
                issues=issues,
            )

        self.assertTrue(
            any("required_item_checks must exactly cover" in issue for issue in issues)
        )

    def test_substance_check_references_must_resolve_in_primary_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            review = json.loads((job_dir / "content_quality_review.json").read_text())
            review["required_item_checks"][0]["dimensions"]["risks_limitations"][
                "document_refs"
            ] = ["Record the result."]
            write_json(job_dir / "content_quality_review.json", review)
            issues: list[str] = []

            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=minutes_text,
                issues=issues,
            )

        self.assertTrue(
            any("was not found in the assigned minutes.md section" in issue for issue in issues)
        )

    def test_low_density_warning_requires_one_targeted_revision_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            write_json(
                job_dir / "speech_activity.json",
                {"audio_duration_seconds": 3_600},
            )
            finalized = finalize_compact_review(job_dir)
            issues: list[str] = []

            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=minutes_text,
                issues=issues,
            )
            self.assertTrue(finalized["document_signals"]["density_warning"])
            baseline = json.loads(
                (job_dir / "content_density_baseline.json").read_text()
            )
            self.assertEqual(
                baseline["length_policy"],
                "minimum_completeness_only_no_maximum",
            )
            self.assertEqual(baseline["target_section_ids"], ["S02"])
            self.assertTrue(
                any("LOW_INFORMATION_DENSITY" in issue for issue in issues)
            )

            expanded_text = minutes_text.replace(
                "Team B must prepare for it.",
                "Team B must prepare for it. The team must also record the baseline, "
                "compare the observed change with the release threshold, and retain the "
                "result so the operational decision remains auditable. "
                + (
                    "The evidence-backed implementation note preserves the observed "
                    "condition, exception, operational risk, downstream impact, owner, "
                    "verification method, and acceptance result without changing the claim. "
                    * 14
                ),
            )
            (job_dir / "minutes.md").write_text(expanded_text, encoding="utf-8")
            finalized = finalize_compact_review(job_dir)
            issues = []
            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=expanded_text,
                issues=issues,
            )

        self.assertFalse(any("LOW_INFORMATION_DENSITY" in issue for issue in issues))
        self.assertEqual(finalized["review_cycles"][0]["status"], "revised")
        self.assertEqual(
            finalized["review_cycles"][0]["target_section_ids"],
            ["S02"],
        )
        self.assertTrue(
            any(
                "information_chars" in change
                for change in finalized["review_cycles"][0]["changes"]
            )
        )

    def test_low_density_revision_rejects_noop_or_wrong_section_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            write_json(
                job_dir / "speech_activity.json",
                {"audio_duration_seconds": 3_600},
            )
            finalize_compact_review(job_dir)
            review = json.loads((job_dir / "content_quality_review.json").read_text())
            review["review_cycles"] = [
                {
                    "cycle": 1,
                    "status": "revised",
                    "findings": ["LOW_INFORMATION_DENSITY in S03"],
                    "changes": ["Changed only the action appendix."],
                    "target_section_ids": ["S03"],
                },
                {
                    "cycle": 2,
                    "status": "passed",
                    "findings": [],
                    "changes": [],
                },
            ]
            write_json(job_dir / "content_quality_review.json", review)
            finalize_compact_review(job_dir)
            issues: list[str] = []

            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=minutes_text,
                issues=issues,
            )

        self.assertTrue(
            any("target_section_ids must exactly match" in issue for issue in issues)
        )
        self.assertTrue(
            any("did not gain enough information" in issue for issue in issues)
        )

    def test_visual_plan_rejects_adjacent_full_width_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            snapshots = job_dir / "snapshots"
            snapshots.mkdir()
            paths = [f"snapshots/snapshot_{index:04}.jpg" for index in range(1, 4)]
            for relative in paths:
                (job_dir / relative).write_bytes(b"snapshot")
            visual_text = minutes_text.replace(
                "Its risk is missing drift. It affects operational readiness.",
                "Its risk is missing drift.\n\n"
                f"![Risk screen]({paths[0]})\n\n"
                f"![Impact screen]({paths[1]})\n\n"
                "It affects operational readiness.",
            ).replace(
                "| Team C | Record the result. |",
                "| Team C | Record the result. |\n\n"
                f"![Action screen]({paths[2]})\n\n"
                "Record the verified result after the image.",
            )
            (job_dir / "minutes.md").write_text(visual_text, encoding="utf-8")
            blueprint = json.loads(
                (job_dir / "document_blueprint.json").read_text(encoding="utf-8")
            )
            blueprint["visual_evidence_plan"] = {
                "status": "embedded",
                "rationale": "Three distinct screens materially support the reader.",
                "items": [
                    {
                        "snapshot_path": paths[0],
                        "section_id": "S02",
                        "purpose": "Show the risk state.",
                        "reader_value": "Makes the operational risk legible.",
                    },
                    {
                        "snapshot_path": paths[1],
                        "section_id": "S02",
                        "purpose": "Show the impact state.",
                        "reader_value": "Distinguishes impact from risk.",
                    },
                    {
                        "snapshot_path": paths[2],
                        "section_id": "S03",
                        "purpose": "Show the action state.",
                        "reader_value": "Supports the execution checklist.",
                    },
                ],
            }
            write_json(job_dir / "document_blueprint.json", blueprint)
            finalize_compact_review(job_dir)
            issues: list[str] = []

            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=visual_text,
                issues=issues,
            )

        self.assertTrue(
            any("adjacent full-width Markdown images" in issue for issue in issues)
        )

    def test_visual_plan_accepts_three_core_images_spread_across_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            snapshots = job_dir / "snapshots"
            snapshots.mkdir()
            paths = [f"snapshots/snapshot_{index:04}.jpg" for index in range(1, 4)]
            for relative in paths:
                (job_dir / relative).write_bytes(b"snapshot")
            visual_text = minutes_text.replace(
                "- The recording remains the source of truth.",
                "- The recording remains the source of truth.\n\n"
                f"![Summary screen]({paths[0]})\n\n"
                "The summary image anchors the main distinction.",
            ).replace(
                "Team A must validate it.\n\n",
                "Team A must validate it.\n\n"
                f"![Condition screen]({paths[1]})\n\n",
            ).replace(
                "| Team C | Record the result. |",
                "| Team C | Record the result. |\n\n"
                f"![Action screen]({paths[2]})\n\n"
                "The action image supports the checklist without replacing it.",
            )
            (job_dir / "minutes.md").write_text(visual_text, encoding="utf-8")
            blueprint = json.loads(
                (job_dir / "document_blueprint.json").read_text(encoding="utf-8")
            )
            blueprint["visual_evidence_plan"] = {
                "status": "embedded",
                "rationale": "Three distinct screens support summary, condition, and action.",
                "items": [
                    {
                        "snapshot_path": paths[0],
                        "section_id": "S01",
                        "purpose": "Anchor the executive distinction.",
                        "reader_value": "Improves summary comprehension.",
                    },
                    {
                        "snapshot_path": paths[1],
                        "section_id": "S02",
                        "purpose": "Show the operating condition.",
                        "reader_value": "Supports the detailed analysis.",
                    },
                    {
                        "snapshot_path": paths[2],
                        "section_id": "S03",
                        "purpose": "Show the action state.",
                        "reader_value": "Supports checklist execution.",
                    },
                ],
            }
            write_json(job_dir / "document_blueprint.json", blueprint)
            finalize_compact_review(job_dir)
            issues: list[str] = []

            validate_content_quality_artifacts(
                job_dir,
                inventory_ids=inventory_ids,
                required_ids=required_ids,
                minutes_text=visual_text,
                issues=issues,
            )

        self.assertEqual(issues, [])

    def test_quality_contract_allows_only_one_targeted_revision(self) -> None:
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

        self.assertTrue(any("must not exceed 2" in issue for issue in issues))

    def test_schema_v2_review_remains_valid_for_existing_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            inventory_ids, required_ids, minutes_text = write_quality_artifacts(job_dir)
            review = json.loads((job_dir / "content_quality_review.json").read_text())
            manifest = json.loads((job_dir / "evidence_chunks.json").read_text())
            manifest.pop("quality_contract_version")
            write_json(job_dir / "evidence_chunks.json", manifest)
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
