from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.benchmark_ocr import build_benchmark_report


class BenchmarkOcrTests(unittest.TestCase):
    def test_report_is_hash_bound_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mov"
            video.write_bytes(b"video")
            snapshots = root / "job" / "snapshots"
            snapshots.mkdir(parents=True)
            (snapshots / "snapshot_0001_00-00-00.jpg").write_bytes(b"jpg")
            result = {
                "frame_interval_seconds": 5,
                "ffmpeg_threads": 2,
                "workers": 5,
                "tesseract_thread_limit": 1,
                "tesseract_nice": 0,
                "frame_extract_cpu_limit_percent": 0,
                "signature_cpu_limit_percent": 0,
                "tesseract_cpu_limit_percent": 0,
                "max_snapshot_gap_seconds": 120,
                "raw_frame_count": 2,
                "raw_frames_bytes": 22,
                "selection_reason_counts": {"selected": 1},
                "selection_result_sha256": "a" * 64,
                "metrics": {"phases": {"frame_extraction": {"wall_seconds": 1}}},
            }
            coverage = {
                "coverage_passed": True,
                "accounting_complete": True,
                "max_selected_snapshot_gap_seconds": 120,
                "raw_frames_manifest_sha256": "b" * 64,
            }

            with mock.patch("os.getpriority", return_value=10):
                report = build_benchmark_report(
                    video_path=video,
                    job_dir=root / "job",
                    result=result,
                    coverage=coverage,
                    wall_seconds=2.0,
                    cpu_seconds_used=5.0,
                )

            self.assertEqual(report["runtime"]["observed_process_nice"], 10)
            self.assertEqual(report["configuration"]["ffmpeg_threads"], 2)
            self.assertEqual(report["timing"]["cpu_to_wall_ratio"], 2.5)
            self.assertEqual(report["work"]["snapshots"]["count"], 1)
            self.assertEqual(len(report["work"]["snapshots"]["manifest_sha256"]), 64)
            self.assertNotIn('"frames": [', json.dumps(report))


if __name__ == "__main__":
    unittest.main()
