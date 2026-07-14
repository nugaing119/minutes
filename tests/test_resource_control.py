from __future__ import annotations

import fcntl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.resource_control import resource_policy_command, single_job_lock


class ResourceControlTests(unittest.TestCase):
    def test_resource_policy_wraps_the_whole_process_tree(self) -> None:
        locations = {
            "taskpolicy": "/usr/sbin/taskpolicy",
            "nice": "/usr/bin/nice",
        }
        with patch(
            "scripts.resource_control.shutil.which",
            side_effect=lambda name: locations.get(name),
        ):
            command = resource_policy_command(
                ["python", "script.py", "video.mov"],
                qos="utility",
                nice=10,
            )

        self.assertEqual(
            command,
            [
                "/usr/sbin/taskpolicy",
                "-c",
                "utility",
                "/usr/bin/nice",
                "-n",
                "10",
                "python",
                "script.py",
                "video.mov",
            ],
        )

    def test_single_job_lock_is_exclusive_and_released(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "jobs" / ".process.lock"
            with single_job_lock(lock_path):
                with lock_path.open("a+") as contender:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(
                            contender.fileno(),
                            fcntl.LOCK_EX | fcntl.LOCK_NB,
                        )

            with lock_path.open("a+") as contender:
                fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(contender.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    unittest.main()
