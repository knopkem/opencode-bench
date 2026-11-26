"""Regression tests for the metric and leaderboard fixes.

Run with: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class CommunitySubmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixtures = ROOT / "tests" / "fixtures"
        self.example = json.loads(
            (ROOT / "submissions" / "examples" / "issue-1.json").read_text()
        )

    def test_example_validates(self) -> None:
        self.assertEqual(bench.validate_submission(self.example), [])

    def test_schema_is_valid_json(self) -> None:
        schema = json.loads((ROOT / "submissions" / "schema.json").read_text())
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["properties"]["schema"]["const"], bench.SUBMISSION_SCHEMA)

    def test_extract_block(self) -> None:
        issue = json.loads((self.fixtures / "issues.json").read_text())[0]
        data, errors = bench.extract_result_block(issue["body"])
        self.assertEqual(errors, [])
        self.assertEqual(data["run_id"], self.example["run_id"])

    def test_effort_lines_show_ttft_per_step(self) -> None:
        meta = {
            "metrics_source": "db+log",
            "think_s": 0.0,
            "ttft_s": 830.0,
            "ttft_share": 0.51,
            "steps": 51,
        }
        text = "\n".join(bench.render_effort_lines(meta))
        self.assertIn("TTFT (wait for first token)", text)
        self.assertIn("00:00:16 per step", text)
        self.assertIn("51% of wall", text)

    def test_extract_block_after_other_fences(self) -> None:
        block = bench._submission_block(self.example)
        body = "Intro\n\n```bash\necho hi\n```\n\n## Result\n\n" + block
        data, errors = bench.extract_result_block(body)
        self.assertEqual(errors, [])
        self.assertEqual(data["run_id"], self.example["run_id"])

    def test_update_preserves_prose_around_result_block(self) -> None:
        block = bench._submission_block(self.example)
        body = (
            "## Details\n\nSome notes.\n\n"
            + block
            + "\n## Reproduce\n\nRun it.\n"
        )
        updated = bench._replace_result_block(body, block)
        self.assertTrue(updated.startswith("## Details\n\nSome notes."))
        self.assertIn("```\n\n## Reproduce\n\nRun it.", updated)
        self.assertEqual(updated.count('"schema": "opencode-bench/result@1"'), 1)

    def test_update_keeps_other_fenced_blocks(self) -> None:
        block = bench._submission_block(self.example)
        body = "Intro\n\n```bash\necho hi\n```\n\n" + block
        updated = bench._replace_result_block(body, block)
        self.assertIn("```bash\necho hi\n```", updated)

    def test_update_collapses_duplicate_result_blocks(self) -> None:
        block = bench._submission_block(self.example)
        body = "Intro\n\n" + block + "\nMiddle\n\n" + block + "\nEnd\n"
        updated = bench._replace_result_block(body, block)
        self.assertEqual(updated.count('"schema": "opencode-bench/result@1"'), 1)
        self.assertIn("Intro", updated)
        self.assertIn("End", updated)

    def test_find_submission_issue_by_run_id(self) -> None:
        issue = json.loads((self.fixtures / "issues.json").read_text())[0]
        other = json.loads(json.dumps(issue))
        other["number"] = 2
        other["body"] = other["body"].replace(
            self.example["run_id"], "20260101T000000Z-other-model-r1"
        )
        found = bench.find_submission_issue([other, issue], self.example["run_id"])
        self.assertEqual(found["number"], issue["number"])
        self.assertIsNone(bench.find_submission_issue([other], self.example["run_id"]))

    def test_find_submission_issue_prefers_visible_newest(self) -> None:
        issue = json.loads((self.fixtures / "issues.json").read_text())[0]
        hidden = json.loads(json.dumps(issue))
        hidden["number"] = 99
        hidden["labels"].append({"name": "hidden"})
        newer = json.loads(json.dumps(issue))
        newer["number"] = 10
        found = bench.find_submission_issue([issue, hidden, newer], self.example["run_id"])
        self.assertEqual(found["number"], 10)

    def test_find_submission_issue_prefers_verified(self) -> None:
        issue = json.loads((self.fixtures / "issues.json").read_text())[0]
        verified = json.loads(json.dumps(issue))
        verified["number"] = 3
        verified["labels"].append({"name": "verified"})
        newer = json.loads(json.dumps(issue))
        newer["number"] = 10
        newer["createdAt"] = "2026-11-05T10:00:00Z"
        found = bench.find_submission_issue([newer, verified], self.example["run_id"])
        self.assertEqual(found["number"], 3)

    def test_bad_counts_rejected(self) -> None:
        bad = json.loads(json.dumps(self.example))
        bad["run"]["counts"]["P0"]["pass"] = 4
        bad["run"]["counts"]["P1"]["pass"] = 9
        bad["run"]["counts"]["P2"]["pass"] = 6
        errors = bench.validate_submission(bad)
        self.assertTrue(any("quality" in error or "eligible" in error for error in errors))

        bad["run"]["self_test_s"] = 100
        bad["run"]["wall_ex_self_test_s"] = 10
        errors = bench.validate_submission(bad)
        self.assertTrue(any("wall_ex_self_test_s" in error for error in errors))

    def test_hidden_submission_is_not_rendered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "site"
            errors = Path(temp) / "errors.json"
            args = argparse.Namespace(
                issues=str(self.fixtures / "issues-hidden.json"),
                output=str(out),
                errors=str(errors),
                include_hidden=False,
            )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(bench.cmd_site(args), 0)
            results = json.loads((out / "data" / "results.json").read_text())
            self.assertEqual(results, [])
            self.assertTrue((out / "index.html").exists())

    def test_verified_submission_is_rendered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "site"
            args = argparse.Namespace(
                issues=str(self.fixtures / "issues-verified.json"),
                output=str(out),
                errors=None,
                include_hidden=False,
            )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(bench.cmd_site(args), 0)
            results = json.loads((out / "data" / "results.json").read_text())
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["evidence"]["level"], "verified")
            self.assertEqual(results[0]["source"], "community")
            html = (out / "index.html").read_text()
            self.assertIn("TTFT (wait for first token)", html)
            self.assertIn("TTFT / step", html)

    def test_duplicate_prefers_verified(self) -> None:
        issue = json.loads((self.fixtures / "issues.json").read_text())[0]
        newer = json.loads(json.dumps(issue))
        newer["number"] = 10
        newer["createdAt"] = "2026-11-05T10:00:00Z"
        newer["url"] = "https://github.com/knopkem/opencode-bench/issues/10"
        verified = json.loads(json.dumps(issue))
        verified["number"] = 11
        verified["createdAt"] = "2026-11-04T10:00:00Z"
        verified["url"] = "https://github.com/knopkem/opencode-bench/issues/11"
        verified["labels"].append({"name": "verified"})
        with tempfile.TemporaryDirectory() as temp:
            issues_path = Path(temp) / "issues.json"
            issues_path.write_text(json.dumps([newer, verified]))
            out = Path(temp) / "site"
            args = argparse.Namespace(
                issues=str(issues_path),
                output=str(out),
                errors=None,
                include_hidden=False,
            )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(bench.cmd_site(args), 0)
            results = json.loads((out / "data" / "results.json").read_text())
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["issue"], 11)
            self.assertEqual(results[0]["evidence"]["level"], "verified")

    def test_invalid_submission_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "site"
            errors_path = Path(temp) / "errors.json"
            args = argparse.Namespace(
                issues=str(self.fixtures / "issues-invalid.json"),
                output=str(out),
                errors=str(errors_path),
                include_hidden=False,
            )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(bench.cmd_site(args), 0)
            errors = json.loads(errors_path.read_text())
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0]["issue"], 2)
            self.assertTrue(errors[0]["errors"])

    def test_row_conversion_keeps_metrics(self) -> None:
        normalized = bench.normalize_submission(self.example)
        row = bench.submission_to_row(normalized)
        self.assertEqual(row["run_id"], self.example["run_id"])
        self.assertEqual(row["quality"], 1.0)
        self.assertTrue(row["eligible"])
        self.assertEqual(row["wall_s"], 1136)
        self.assertEqual(row["self_test_runs"], 28)
        self.assertEqual(row["source"], "community")


class AgentNetworkTests(unittest.TestCase):
    def test_custom_provider_base_url_and_extra_auth_host_are_allowed(self) -> None:
        info = {
            "providers": {
                "custom": {
                    "settings": {"baseURL": "https://api.example.com/v1"},
                    "models": {"model": {}},
                }
            }
        }
        with mock.patch.object(bench, "load_config_info", return_value=info):
            allowlist = bench.resolve_agent_allowlist(
                "custom/model", Path("/config"), ["https://login.example.com/oauth"]
            )

        self.assertEqual(allowlist["api.example.com"], [443])
        self.assertEqual(allowlist["login.example.com"], [443])
        self.assertEqual(allowlist["models.dev"], [443])
        self.assertNotIn("github.com", allowlist)

    def test_builtin_provider_uses_model_metadata_and_sdk_default(self) -> None:
        with (
            mock.patch.object(bench, "load_config_info", return_value={}),
            mock.patch.object(
                bench,
                "load_models_dev",
                return_value={"anthropic": {"npm": "@ai-sdk/anthropic"}},
            ),
        ):
            allowlist = bench.resolve_agent_allowlist("anthropic/claude", Path("/config"), [])
        self.assertEqual(allowlist["api.anthropic.com"], [443])

    def test_unknown_provider_fails_closed_with_actionable_option(self) -> None:
        with (
            mock.patch.object(bench, "load_config_info", return_value={}),
            mock.patch.object(bench, "load_models_dev", return_value={}),
            self.assertRaisesRegex(SystemExit, "--allow-host"),
        ):
            bench.resolve_agent_allowlist("unknown/model", Path("/config"), [])

    def test_endpoint_parser_supports_local_ports_and_rejects_credentials(self) -> None:
        self.assertEqual(bench.parse_endpoint("http://192.168.1.5:8000/v1"), ("192.168.1.5", 8000))
        self.assertEqual(bench.parse_endpoint("api.example.com"), ("api.example.com", 443))
        with self.assertRaisesRegex(ValueError, "credentials"):
            bench.parse_endpoint("https://user:secret@api.example.com")

    def test_agent_dry_run_uses_internal_network_and_preserves_auth_forwarding(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("work", "data", "run", "config"):
                (root / name).mkdir()
            args = argparse.Namespace(
                engine="docker",
                agent_image="agent:test",
                dry_run=True,
                variant=None,
                agent=None,
                pure=False,
                timeout=100,
                idle_timeout=50,
            )
            output = io.StringIO()
            with (
                mock.patch.object(bench.subprocess, "Popen") as popen,
                contextlib.redirect_stdout(output),
            ):
                result = bench.start_agent(
                    "custom/model",
                    root / "config",
                    root / "work",
                    root / "data",
                    root / "run",
                    env_names={"API_KEY"},
                    allowlist={"api.example.com": [443]},
                    args=args,
                )
            popen.assert_not_called()

        command = output.getvalue()
        self.assertIn("network create --internal", command)
        self.assertIn("HTTPS_PROXY=http://egress-proxy:8080", command)
        self.assertIn("-e API_KEY", command)
        self.assertNotIn("API_KEY=", command)
        self.assertIn("OPENCODE_CONFIG_CONTENT=", command)
        self.assertFalse(result["integrity_violation"])

    def test_egress_audit_separates_integrity_from_other_denials(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "egress.log"
            events = [
                {"event": "allow", "host": "api.example.com", "port": 443},
                {"event": "deny", "host": "example.com", "port": 443},
                {"event": "deny", "host": "ghcr.io", "port": 443},
                {"event": "deny", "host": "raw.githubusercontent.com", "port": 443},
            ]
            log.write_text("\n".join(json.dumps(event) for event in events) + "\n")
            audit = bench.read_egress_audit(log)

        self.assertEqual(audit["network_denied"], 3)
        self.assertTrue(audit["integrity_violation"])
        self.assertEqual(len(audit["integrity_reasons"]), 2)
        self.assertNotIn("example.com", " ".join(audit["integrity_reasons"]))

    def test_integrity_violation_changes_eligibility_not_quality(self) -> None:
        tests = {
            f"[{tier}] test-{index}": True
            for tier, total in bench.EXPECTED.items()
            for index in range(total)
        }
        clean = bench.score_run(tests, {"build_ok": True}, goal="balanced")
        flagged = bench.score_run(
            tests,
            {"build_ok": True},
            goal="balanced",
            integrity_violation=True,
        )
        self.assertTrue(clean["eligible"])
        self.assertFalse(flagged["eligible"])
        self.assertEqual(flagged["quality"], clean["quality"])
        self.assertEqual(flagged["counts"], clean["counts"])

    def test_community_integrity_round_trip(self) -> None:
        example = json.loads(
            (ROOT / "submissions" / "examples" / "issue-1.json").read_text()
        )
        example["run"]["integrity_violation"] = True
        example["run"]["eligible"] = True
        self.assertTrue(any("eligible" in error for error in bench.validate_submission(example)))

        example["run"]["eligible"] = False
        self.assertEqual(bench.validate_submission(example), [])
        normalized = bench.normalize_submission(example)
        row = bench.submission_to_row(normalized)
        self.assertFalse(row["eligible"])
        self.assertTrue(row["integrity_violation"])
        self.assertEqual(row["quality"], 1.0)


class ImageMigrationTests(unittest.TestCase):
    def test_missing_public_image_is_pulled(self) -> None:
        with (
            mock.patch.object(bench, "image_exists", return_value=False),
            mock.patch.object(bench.subprocess, "run") as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            run.return_value = mock.Mock(returncode=0)
            self.assertTrue(bench.ensure_image("docker", bench.PUBLIC_AGENT_IMAGE))

        run.assert_called_once_with(
            ["docker", "pull", bench.PUBLIC_AGENT_IMAGE], check=False
        )

    def test_missing_custom_image_is_not_pulled_automatically(self) -> None:
        with (
            mock.patch.object(bench, "image_exists", return_value=False),
            mock.patch.object(bench.subprocess, "run") as run,
        ):
            self.assertFalse(bench.ensure_image("docker", "local-agent:test"))

        run.assert_not_called()

    def test_existing_image_is_not_pulled(self) -> None:
        with (
            mock.patch.object(bench, "image_exists", return_value=True),
            mock.patch.object(bench.subprocess, "run") as run,
        ):
            self.assertTrue(bench.ensure_image("docker", bench.PUBLIC_AGENT_IMAGE))

        run.assert_not_called()

    def test_default_images_are_public_ghcr_references(self) -> None:
        parser = bench.make_parser()
        args = parser.parse_args(["run", "--model", "provider/model"])
        old_agent_default = bench.DEFAULT_AGENT_IMAGE
        old_eval_default = bench.DEFAULT_EVAL_IMAGE
        bench.DEFAULT_AGENT_IMAGE = bench.PUBLIC_AGENT_IMAGE
        bench.DEFAULT_EVAL_IMAGE = bench.PUBLIC_EVAL_IMAGE
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                bench.resolve_image_args(parser, args)
        finally:
            bench.DEFAULT_AGENT_IMAGE = old_agent_default
            bench.DEFAULT_EVAL_IMAGE = old_eval_default

        self.assertEqual(args.agent_image, bench.PUBLIC_AGENT_IMAGE)
        self.assertEqual(args.eval_image, bench.PUBLIC_EVAL_IMAGE)

    def test_image_defaults_and_legacy_alias(self) -> None:
        parser = bench.make_parser()
        args = parser.parse_args(
            [
                "--image",
                "legacy-agent:test",
                "run",
                "--model",
                "provider/model",
                "--eval-image",
                "evaluator:test",
            ]
        )
        with contextlib.redirect_stderr(io.StringIO()):
            bench.resolve_image_args(parser, args)

        self.assertEqual(args.agent_image, "legacy-agent:test")
        self.assertEqual(args.eval_image, "evaluator:test")
        self.assertEqual(args.image, "legacy-agent:test")

    def test_subcommand_image_options_and_conflict(self) -> None:
        parser = bench.make_parser()
        args = parser.parse_args(
            [
                "run",
                "--agent-image",
                "agent:test",
                "--eval-image",
                "eval:test",
                "--model",
                "provider/model",
            ]
        )
        with contextlib.redirect_stderr(io.StringIO()):
            bench.resolve_image_args(parser, args)
        self.assertEqual(args.agent_image, "agent:test")
        self.assertEqual(args.eval_image, "eval:test")

        conflicting = parser.parse_args(
            [
                "--image",
                "legacy:test",
                "--agent-image",
                "agent:test",
                "run",
                "--model",
                "provider/model",
            ]
        )
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                bench.resolve_image_args(parser, conflicting)

    def test_count_suite_tiers_from_private_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tests_dir = Path(temp)
            (tests_dir / "suite.spec.ts").write_text(
                'test("[P0] one", async () => {});\n'
                'test.describe("[P1] group", () => {\n'
                '  test("[P1] two", async () => {});\n'
                '  test("[P1] three", async () => {});\n'
                '});\n',
                encoding="utf-8",
            )
            self.assertEqual(
                bench.count_suite_tiers(tests_dir), {"P0": 1, "P1": 2, "P2": 0}
            )

    def test_run_tests_uses_evaluator_image_without_host_test_mount(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workdir = root / "workdir"
            run_dir = root / "run"
            (workdir / "dist").mkdir(parents=True)
            (workdir / "dist" / "index.html").write_text("ok", encoding="utf-8")
            run_dir.mkdir()

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = bench.run_tests(
                    run_dir,
                    workdir,
                    engine="docker",
                    evaluator_image="evaluator:test",
                    dry_run=True,
                )

            self.assertTrue(result["test_ok"])
            command = stdout.getvalue()
            self.assertIn("evaluator:test", command)
            self.assertIn(f"--config {bench.EVALUATOR_CONFIG}", command)
            self.assertNotIn(":/opt/opencode-bench/tests:ro", command)


if __name__ == "__main__":
    unittest.main()
