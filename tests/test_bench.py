"""Regression tests for the metric and leaderboard fixes.

Run with: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bench  # noqa: E402


def _issue_row(
    run_id: str,
    *,
    wall: float,
    work: float,
    quality: float,
    eligible: bool,
    compactions: int = 0,
) -> dict:
    return {
        "run_id": run_id,
        "model": "test/model",
        "provider": "test",
        "wall_s": wall,
        "wall_ex_self_test_s": work,
        "quality": quality,
        "eligible": eligible,
        "compactions": compactions,
    }


class LeaderboardTests(unittest.TestCase):
    def test_eligible_baseline_and_speed_cap(self) -> None:
        rows = [
            _issue_row("fast-failed", wall=10, work=10, quality=0.0, eligible=False),
            _issue_row("eligible-a", wall=100, work=50, quality=1.0, eligible=True),
            _issue_row("eligible-b", wall=200, work=200, quality=1.0, eligible=True),
        ]
        entries = bench.leaderboard_entries(rows)
        by_id = {entry["row"]["run_id"]: entry for entry in entries}

        # The failed 10s run must not set the baseline for eligible scoring.
        self.assertAlmostEqual(by_id["eligible-a"]["score_total"], 100.0, places=6)
        self.assertAlmostEqual(by_id["eligible-b"]["score_total"], 92.5, places=6)
        self.assertAlmostEqual(by_id["fast-failed"]["speed_total"], 1.0, places=6)
        self.assertAlmostEqual(by_id["fast-failed"]["score_total"], 15.0, places=6)

        # Work basis has its own eligible baseline (50s) and cap.
        self.assertAlmostEqual(by_id["eligible-a"]["score_work"], 100.0, places=6)
        self.assertAlmostEqual(by_id["eligible-b"]["score_work"], 88.75, places=6)
        self.assertAlmostEqual(by_id["fast-failed"]["speed_work"], 1.0, places=6)

        self.assertEqual(entries[0]["row"]["run_id"], "eligible-a")


class SessionMetricTests(unittest.TestCase):
    def _write_db(self, path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.execute(
            "create table session_message ("
            "type text, seq integer, time_created integer, data text)"
        )
        rows = [
            ("user", 1, 0, {"time": {"created": 0}}),
            (
                "assistant",
                2,
                1000,
                {
                    "time": {"created": 1000, "streamed": 2000, "completed": 2100},
                    "tokens": {
                        "input": 10,
                        "output": 100,
                        "reasoning": 0,
                        "cache": {"read": 0, "write": 0},
                    },
                    "content": [
                        {
                            "type": "reasoning",
                            "time": {"created": 1200, "completed": 1500},
                        },
                        {
                            "type": "tool",
                            "name": "shell",
                            "state": {
                                "input": {"command": "npx playwright test"},
                                "content": [{"type": "text", "text": "1 failed"}],
                                "metadata": {"exit": 0},
                            },
                            "time": {"created": 1500, "ran": 1600, "completed": 2000},
                        },
                    ],
                },
            ),
            (
                "assistant",
                3,
                3000,
                {
                    "time": {"created": 3000, "streamed": 4000, "completed": 4100},
                    "tokens": {
                        "input": 0,
                        "output": 50,
                        "reasoning": 0,
                        "cache": {"read": 0, "write": 0},
                    },
                    "content": [
                        {
                            "type": "reasoning",
                            "time": {"created": 3200, "completed": 3500},
                        },
                        {
                            "type": "tool",
                            "name": "shell",
                            "state": {"input": {"command": "npm run build"}},
                            "time": {"created": 3500, "ran": 3600, "completed": 3900},
                        },
                    ],
                },
            ),
            (
                "assistant",
                4,
                5000,
                {
                    "time": {"created": 5000, "streamed": 6000, "completed": 6100},
                    "finish": "error",
                    "error": {
                        "type": "provider.invalid-output",
                        "message": "stream ended without finish_reason",
                    },
                    "tokens": {},
                    "content": [
                        {
                            "type": "reasoning",
                            "time": {"created": 5100, "completed": 6000},
                        }
                    ],
                },
            ),
        ]
        for message_type, seq, created, data in rows:
            connection.execute(
                "insert into session_message values (?, ?, ?, ?)",
                (message_type, seq, created, json.dumps(data)),
            )
        connection.commit()
        connection.close()

    def test_retry_is_excluded_from_generation_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "opencode-data"
            data_dir.mkdir()
            self._write_db(data_dir / "opencode.db")
            metrics = bench.read_session_metrics(data_dir, 10.0)

        self.assertTrue(metrics["available"])
        self.assertEqual(metrics["assistant_steps"], 2)
        self.assertEqual(metrics["retries"], 1)
        self.assertAlmostEqual(metrics["retry_s"], 1.1, places=6)
        self.assertAlmostEqual(metrics["think_s"], 0.6, places=6)
        self.assertAlmostEqual(metrics["output_s"], 1.0, places=6)
        self.assertAlmostEqual(metrics["self_test_s"], 0.4, places=6)
        self.assertEqual(metrics["self_test_runs"], 1)
        self.assertEqual(metrics["self_test_failures"], 1)
        self.assertAlmostEqual(metrics["build_tool_s"], 0.3, places=6)

    def test_agent_usage_median_tok_s(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "agent.log"
            events = [
                {"type": "step_start", "timestamp": 0, "part": {"messageID": "a"}},
                {
                    "type": "step_finish",
                    "timestamp": 1000,
                    "part": {
                        "messageID": "a",
                        "tokens": {"output": 100, "input": 0, "cache": {}},
                    },
                },
                {"type": "step_start", "timestamp": 2000, "part": {"messageID": "b"}},
                {
                    "type": "step_finish",
                    "timestamp": 3000,
                    "part": {
                        "messageID": "b",
                        "tokens": {"output": 50, "input": 0, "cache": {}},
                    },
                },
            ]
            log.write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
            usage = bench.read_agent_usage(log)

        self.assertEqual(usage["steps"], 2)
        self.assertAlmostEqual(usage["gen_tokens_per_s_median"], 75.0, places=6)


if __name__ == "__main__":
    unittest.main()
