from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.content_audit import validate_content_artifacts
from scripts.content_quality import TRUST_APPENDIX_DISCLOSURES
from scripts.utils import write_json


def write_valid_artifacts(job_dir: Path) -> None:
    (job_dir / "source.mov").write_bytes(b"video")
    frames_dir = job_dir / "frames"
    snapshots_dir = job_dir / "snapshots"
    frames_dir.mkdir()
    snapshots_dir.mkdir()
    raw_frame = frames_dir / "frame_000001.jpg"
    snapshot = snapshots_dir / "snapshot_0001_00-01-10.jpg"
    raw_frame.write_bytes(b"frame-evidence")
    snapshot.write_bytes(b"frame-evidence")
    evidence_hash = hashlib.sha256(b"frame-evidence").hexdigest()
    write_json(
        job_dir / "transcript.json",
        {
            "segments": [
                {"start": 50.0, "end": 130.0, "text": "recorded evidence"}
            ]
        },
    )
    write_json(
        job_dir / "screen_text.json",
        {
            "frames": [
                {
                    "timestamp_seconds": 70,
                    "timestamp": "00:01:10",
                    "snapshot": str(snapshot),
                    "text": "Version 8.0",
                }
            ]
        },
    )
    write_json(
        job_dir / "evidence_coverage.json",
        {
            "schema_version": 1,
            "status": "completed",
            "coverage_passed": True,
            "accounting_complete": True,
            "raw_frame_count": 1,
            "selected_snapshot_count": 1,
            "accounted_frame_count": 1,
            "max_snapshot_gap_limit_seconds": 120,
            "max_selected_snapshot_gap_seconds": 0,
            "reason_counts": {"selected": 1},
            "frames": [
                {
                    "evidence_id": "frame-000001",
                    "source_frame": raw_frame.name,
                    "timestamp_seconds": 70,
                    "raw_frame": "frames/frame_000001.jpg",
                    "raw_frame_sha256": evidence_hash,
                    "selected": True,
                    "reason": "selected",
                    "snapshot_evidence_id": "snapshot-0001",
                    "snapshot": "snapshots/snapshot_0001_00-01-10.jpg",
                    "snapshot_sha256": evidence_hash,
                }
            ],
        },
    )
    (job_dir / "minutes.md").write_text(
        "# Version support\n\n"
        "Version 8.0 support ends in April 2027.\n\n"
        "The slide and speech differ on availability, so the current policy "
        "requires official confirmation.\n\n"
        "## External Evidence Check\n\n"
        "- Checked: 2026-07-13\n"
        f"- Policy: {TRUST_APPENDIX_DISCLOSURES['en'][0]}\n"
        f"- Privacy: {TRUST_APPENDIX_DISCLOSURES['en'][1]}\n\n"
        "### Evidence conflicting with the video\n\n"
        "The current official schedule differs from the recorded statement. "
        "[Official schedule](https://docs.example.com/version-support)\n",
        encoding="utf-8",
    )
    write_json(
        job_dir / "content_inventory.json",
        {
            "schema_version": 1,
            "items": [
                {
                    "id": "E001",
                    "time_range": "00:01:00-00:02:00",
                    "category": "policy",
                    "statement": "Version 8.0 support ends in April 2027.",
                    "importance": "required",
                    "qualifier": "recording_claim",
                    "source_refs": [
                        "STT:00:01:00-00:02:00",
                        "OCR:00:01:10",
                        "Snapshot:snapshot-0001@00:01:10",
                    ],
                    "official_verification": "required",
                },
                {
                    "id": "E002",
                    "time_range": "00:02:00-00:02:10",
                    "category": "filler",
                    "statement": "The presenter repeated the transition.",
                    "importance": "optional",
                    "qualifier": "repetition",
                    "source_refs": ["STT:00:02:00"],
                    "official_verification": "not_applicable",
                },
            ],
            "conflicts": [
                {
                    "id": "C001",
                    "description": "The slide and speech differ on availability.",
                    "source_refs": ["STT:00:01:00", "OCR:00:01:10"],
                }
            ],
        },
    )
    write_json(
        job_dir / "content_audit.json",
        {
            "schema_version": 1,
            "status": "passed",
            "covered_item_ids": ["E001"],
            "missing_item_ids": [],
            "qualifier_changes": [],
            "silent_conflicts": [],
            "documented_conflict_ids": ["C001"],
            "recording_fidelity": {
                "preserved_item_ids": ["E001"],
                "rewritten_by_external_source_item_ids": [],
            },
            "coverage": [
                {
                    "item_id": "E001",
                    "document_refs": [
                        "Version 8.0 support ends in April 2027."
                    ],
                }
            ],
            "conflict_coverage": [
                {
                    "conflict_id": "C001",
                    "document_refs": [
                        "The slide and speech differ on availability"
                    ],
                }
            ],
            "intentional_omissions": [
                {"item_id": "E002", "reason": "Exact repetition without new meaning"}
            ],
        },
    )
    write_json(
        job_dir / "official_sources.json",
        {
            "schema_version": 1,
            "status": "completed",
            "checked_at": "2026-07-13T22:00:00+09:00",
            "policy": "official_only",
            "appendix_heading": "External Evidence Check",
            "privacy": {"raw_transcript_or_ocr_sent": False},
            "claims": [
                {
                    "inventory_item_ids": ["E001"],
                    "status": "contradicted",
                    "purpose": "source_conflict_resolution",
                    "appendix_category": "video_conflict",
                    "appendix_category_heading": "Evidence conflicting with the video",
                    "recording_content_preserved": True,
                    "current_official_finding": "The current schedule differs.",
                    "document_treatment": "Kept the video statement and documented the conflict.",
                    "recording_document_refs": [
                        "Version 8.0 support ends in April 2027."
                    ],
                    "appendix_document_refs": [
                        "The current official schedule differs from the recorded statement."
                    ],
                    "sources": [
                        {
                            "title": "Official version support schedule",
                            "url": "https://docs.example.com/version-support",
                            "publisher": "Example Vendor",
                            "source_type": "official",
                            "published_or_updated": "2026-07-01",
                        }
                    ],
                }
            ],
        },
    )


class ContentAuditTests(unittest.TestCase):
    def test_strict_audit_accepts_complete_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_valid_artifacts(job_dir)

            result = validate_content_artifacts(
                job_dir,
                audit_mode="strict",
                official_source_verification="required",
            )

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["required_items"], 1)
        self.assertEqual(result["official_source_status"], "completed")

    def test_not_applicable_official_check_still_requires_a_visible_final_appendix(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_valid_artifacts(job_dir)
            inventory_path = job_dir / "content_inventory.json"
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            inventory["items"][0]["official_verification"] = "not_applicable"
            write_json(inventory_path, inventory)
            reason = "No unresolved public claim remained after local cross-checking."
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
            minutes_path = job_dir / "minutes.md"
            minutes_path.write_text(
                minutes_path.read_text(encoding="utf-8").split(
                    "## External Evidence Check",
                    1,
                )[0].rstrip()
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "missing the official-evidence appendix"):
                validate_content_artifacts(
                    job_dir,
                    audit_mode="strict",
                    official_source_verification="auto",
                )

    def test_not_applicable_official_check_is_disclosed_in_the_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_valid_artifacts(job_dir)
            inventory_path = job_dir / "content_inventory.json"
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            inventory["items"][0]["official_verification"] = "not_applicable"
            write_json(inventory_path, inventory)
            reason = "No unresolved public claim remained after local cross-checking."
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
            minutes_path = job_dir / "minutes.md"
            body = minutes_path.read_text(encoding="utf-8").split(
                "## External Evidence Check",
                1,
            )[0].rstrip()
            minutes_path.write_text(
                body
                + "\n\n## External Evidence Check\n\n"
                + "- Checked: 2026-07-16\n"
                + f"- Result: {reason}\n"
                + f"- Policy: {TRUST_APPENDIX_DISCLOSURES['en'][0]}\n"
                + f"- Privacy: {TRUST_APPENDIX_DISCLOSURES['en'][1]}\n",
                encoding="utf-8",
            )

            result = validate_content_artifacts(
                job_dir,
                audit_mode="strict",
                official_source_verification="auto",
            )

        self.assertEqual(result["official_source_status"], "not_applicable")

    def test_official_check_timestamp_cannot_be_in_the_future(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_valid_artifacts(job_dir)
            official_path = job_dir / "official_sources.json"
            official = json.loads(official_path.read_text(encoding="utf-8"))
            official["checked_at"] = "2999-07-16T14:30:00+09:00"
            write_json(official_path, official)
            minutes_path = job_dir / "minutes.md"
            minutes_path.write_text(
                minutes_path.read_text(encoding="utf-8").replace(
                    "Checked: 2026-07-13",
                    "Checked: 2999-07-16",
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "checked_at cannot be in the future"):
                validate_content_artifacts(
                    job_dir,
                    audit_mode="strict",
                    official_source_verification="required",
                )

    def test_strict_audit_fails_when_artifacts_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "missing content_inventory.json"):
                validate_content_artifacts(
                    Path(temp_dir),
                    audit_mode="strict",
                    official_source_verification="required",
                )

    def test_strict_audit_rejects_tampered_raw_frame_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_valid_artifacts(job_dir)
            (job_dir / "frames" / "frame_000001.jpg").write_bytes(b"tampered")

            with self.assertRaisesRegex(ValueError, "raw frame hash does not match"):
                validate_content_artifacts(
                    job_dir,
                    audit_mode="strict",
                    official_source_verification="required",
                )

    def test_strict_audit_rejects_snapshot_gap_over_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_valid_artifacts(job_dir)
            coverage_path = job_dir / "evidence_coverage.json"
            coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
            coverage["max_selected_snapshot_gap_seconds"] = 121
            write_json(coverage_path, coverage)

            with self.assertRaisesRegex(ValueError, "maximum snapshot gap"):
                validate_content_artifacts(
                    job_dir,
                    audit_mode="strict",
                    official_source_verification="required",
                )

    def test_inventory_timestamp_ref_must_resolve_to_collected_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_valid_artifacts(job_dir)
            inventory_path = job_dir / "content_inventory.json"
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            inventory["items"][0]["source_refs"][0] = (
                "STT:00:09:00-00:09:10"
            )
            write_json(inventory_path, inventory)

            with self.assertRaisesRegex(ValueError, "evidence ref does not resolve"):
                validate_content_artifacts(
                    job_dir,
                    audit_mode="strict",
                    official_source_verification="required",
                )

    def test_visual_only_inventory_item_requires_snapshot_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_valid_artifacts(job_dir)
            inventory_path = job_dir / "content_inventory.json"
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            inventory["items"][0]["source_refs"] = ["OCR:00:01:10"]
            write_json(inventory_path, inventory)

            with self.assertRaisesRegex(ValueError, "requires a Snapshot ref"):
                validate_content_artifacts(
                    job_dir,
                    audit_mode="strict",
                    official_source_verification="required",
                )

    def test_required_item_cannot_be_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_valid_artifacts(job_dir)
            audit_path = job_dir / "content_audit.json"
            write_json(
                audit_path,
                {
                    "schema_version": 1,
                    "status": "passed",
                    "covered_item_ids": [],
                    "missing_item_ids": [],
                    "qualifier_changes": [],
                    "silent_conflicts": [],
                    "documented_conflict_ids": ["C001"],
                    "recording_fidelity": {
                        "preserved_item_ids": [],
                        "rewritten_by_external_source_item_ids": [],
                    },
                    "coverage": [],
                    "conflict_coverage": [
                        {
                            "conflict_id": "C001",
                            "document_refs": [
                                "The slide and speech differ on availability"
                            ],
                        }
                    ],
                    "intentional_omissions": [
                        {"item_id": "E001", "reason": "Document was too long"}
                    ],
                },
            )

            with self.assertRaisesRegex(ValueError, "required item cannot"):
                validate_content_artifacts(
                    job_dir,
                    audit_mode="strict",
                    official_source_verification="required",
                )

    def test_undocumented_source_conflict_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_valid_artifacts(job_dir)
            audit = {
                "schema_version": 1,
                "status": "passed",
                "covered_item_ids": ["E001"],
                "missing_item_ids": [],
                "qualifier_changes": [],
                "silent_conflicts": [],
                "documented_conflict_ids": [],
                "recording_fidelity": {
                    "preserved_item_ids": ["E001"],
                    "rewritten_by_external_source_item_ids": [],
                },
                "coverage": [
                    {
                        "item_id": "E001",
                        "document_refs": [
                            "Version 8.0 support ends in April 2027."
                        ],
                    }
                ],
                "conflict_coverage": [],
                "intentional_omissions": [
                    {"item_id": "E002", "reason": "Exact repetition"}
                ],
            }
            write_json(job_dir / "content_audit.json", audit)

            with self.assertRaisesRegex(ValueError, "conflicts are not documented"):
                validate_content_artifacts(
                    job_dir,
                    audit_mode="strict",
                    official_source_verification="required",
                )

    def test_claimed_coverage_must_exist_in_final_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_valid_artifacts(job_dir)
            audit_path = job_dir / "content_audit.json"
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            audit["coverage"][0]["document_refs"] = ["Text missing from final document"]
            write_json(audit_path, audit)

            with self.assertRaisesRegex(ValueError, "was not found in minutes.md"):
                validate_content_artifacts(
                    job_dir,
                    audit_mode="strict",
                    official_source_verification="required",
                )

    def test_raw_evidence_must_not_be_sent_for_official_search(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_valid_artifacts(job_dir)
            official_path = job_dir / "official_sources.json"
            official = json.loads(official_path.read_text(encoding="utf-8"))
            official["privacy"]["raw_transcript_or_ocr_sent"] = True
            write_json(official_path, official)

            with self.assertRaisesRegex(ValueError, "raw_transcript_or_ocr_sent=false"):
                validate_content_artifacts(
                    job_dir,
                    audit_mode="strict",
                    official_source_verification="required",
                )

    def test_conflicting_official_source_must_be_cited_in_final_appendix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_valid_artifacts(job_dir)
            minutes_path = job_dir / "minutes.md"
            minutes_path.write_text(
                minutes_path.read_text(encoding="utf-8").replace(
                    "[Official schedule](https://docs.example.com/version-support)",
                    "Official schedule",
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "no official source URL cited"):
                validate_content_artifacts(
                    job_dir,
                    audit_mode="strict",
                    official_source_verification="required",
                )

    def test_official_evidence_appendix_must_be_the_final_h2(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_valid_artifacts(job_dir)
            minutes_path = job_dir / "minutes.md"
            minutes_path.write_text(
                minutes_path.read_text(encoding="utf-8")
                + "\n## Later Section\n\nThis must not follow the appendix.\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "must be the final H2"):
                validate_content_artifacts(
                    job_dir,
                    audit_mode="strict",
                    official_source_verification="required",
                )

    def test_external_source_cannot_replace_recording_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_valid_artifacts(job_dir)
            official_path = job_dir / "official_sources.json"
            official = json.loads(official_path.read_text(encoding="utf-8"))
            official["claims"][0]["recording_content_preserved"] = False
            write_json(official_path, official)

            with self.assertRaisesRegex(ValueError, "recording_content_preserved"):
                validate_content_artifacts(
                    job_dir,
                    audit_mode="strict",
                    official_source_verification="required",
                )

    def test_auto_mode_cites_transcription_disambiguation_in_appendix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_valid_artifacts(job_dir)
            official_path = job_dir / "official_sources.json"
            official = json.loads(official_path.read_text(encoding="utf-8"))
            minutes_path = job_dir / "minutes.md"
            minutes_path.write_text(
                minutes_path.read_text(encoding="utf-8")
                .replace(
                    "### Evidence conflicting with the video",
                    "### Transcription/OCR Supporting Evidence",
                )
                .replace(
                    "The current official schedule differs from the recorded statement.",
                    "The official source confirms the ambiguous product spelling.",
                ),
                encoding="utf-8",
            )
            official["claims"][0].update(
                {
                    "status": "verified",
                    "purpose": "transcription_disambiguation",
                    "appendix_category": "transcription_or_ocr_support",
                    "appendix_category_heading": "Transcription/OCR Supporting Evidence",
                    "current_official_finding": "The product spelling was confirmed.",
                    "document_treatment": "Used only to confirm the ambiguous spelling.",
                    "appendix_document_refs": [
                        "The official source confirms the ambiguous product spelling."
                    ],
                }
            )
            write_json(official_path, official)

            result = validate_content_artifacts(
                job_dir,
                audit_mode="strict",
                official_source_verification="auto",
            )

        self.assertEqual(result["status"], "passed")


if __name__ == "__main__":
    unittest.main()
