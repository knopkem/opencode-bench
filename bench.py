#!/usr/bin/env python3
"""opencode-bench: one-command build-and-play benchmark for OpenCode models.

Usage:
    ./bench.py models
    ./bench.py run --model provider/model
    ./bench.py report

There is no model list in this repo. `models` asks the local `opencode` CLI what
its current configuration exposes, and `run --model` passes that reference
straight through to `opencode run` inside the agent container. The agent
container gets the local OpenCode config mounted read-only, plus Chromium,
Playwright and the seed's public smoke tests.

The hidden Playwright suite is mounted only into the test container after the
agent is dead.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import html
import io
import json
import os
import re
import shlex
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
import urllib.request
import uuid
import webbrowser
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TASK_DIR = ROOT / "tasks" / "spriteforge-lite"
SEED_DIR = TASK_DIR / "seed"
TESTS_DIR = TASK_DIR / "tests"
PROMPT_PATH = ROOT / "PROMPT.md"
RUNS_DIR = ROOT / "runs"
RESULTS_PATH = ROOT / "results.csv"
REPORT_PATH = ROOT / "report.md"

DEFAULT_IMAGE = os.environ.get("OPENCODE_BENCH_IMAGE", "opencode-bench:latest")
DEFAULT_ENGINE = os.environ.get("OPENCODE_BENCH_ENGINE", "docker")
OPENCODE = os.environ.get("OPENCODE_BENCH_OPENCODE", "opencode")

EXPECTED = {"P0": 5, "P1": 9, "P2": 6}
WEIGHT = {"P0": 3, "P1": 2, "P2": 1}
REASONING_ORDER = {
    "xhigh": 6,
    "high": 5,
    "medium": 4,
    "low": 3,
    "minimal": 2,
    "none": 1,
    "off": 0,
}
LEADERBOARD_PATH = ROOT / "leaderboard.html"
LEADERBOARD_WEIGHTS = {"quality": 0.80, "wall": 0.15, "compactions": 0.05}
# Ineligible runs stay on the leaderboard but keep only this fraction of their
# score, so a low-quality or broken run drops below quality runs instead of
# outranking them on raw speed. Tune this to make the penalty larger or smaller.
LEADERBOARD_INELIGIBLE_FACTOR = 0.75
METRICS_VERSION = 3
#: Shell commands that belong to self-testing rather than building.
SELF_TEST_RE = re.compile(
    r"(?:playwright\s+test|test:e2e|npm\s+(?:run\s+)?test|vitest|npx\s+playwright)",
    re.IGNORECASE,
)
#: Build/typecheck commands, tracked separately from self-testing.
BUILD_TOOL_RE = re.compile(
    r"(?:npm\s+run\s+build|vite\s+build|(?:npx\s+)?tsc\b|npm\s+run\s+typecheck)",
    re.IGNORECASE,
)
#: Dev-server commands; useful context but not part of the build clock.
DEV_TOOL_RE = re.compile(
    r"(?:npm\s+run\s+dev|vite(?:\s+--host)?|npm\s+run\s+preview|vite\s+preview)",
    re.IGNORECASE,
)
#: Time and token fields derived from `agent.log` and `opencode-data/opencode.db`.
EFFORT_FIELDS = [
    "span_s",
    "agent_s",
    "ttft_s",
    "think_s",
    "output_s",
    "tool_s",
    "other_s",
    "tool_shell_s",
    "tool_write_s",
    "tool_read_s",
    "tool_other_s",
    "self_test_s",
    "self_test_runs",
    "self_test_failures",
    "build_tool_s",
    "dev_tool_s",
    "wall_ex_self_test_s",
    "self_test_share",
    "retries",
    "retry_s",
    "step_median_s",
    "step_p90_s",
    "step_max_s",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "compaction_tokens",
    "total_tokens",
    "max_request_tokens",
    "mean_request_tokens",
    "cost_usd",
    "think_share",
    "output_share",
    "tool_share",
    "ttft_share",
    "other_share",
    "gen_tokens_per_s",
    "gen_tokens_per_s_median",
    "tokens_per_step",
    "cache_hit_rate",
    "metrics_source",
    "metrics_version",
]
CSV_FIELDS = [
    "run_id",
    "timestamp",
    "model",
    "provider",
    "hardware",
    "rep",
    "goal",
    "variant",
    "thinking",
    "reasoning_effort",
    "build_ok",
    "typecheck_ok",
    "p0_pass",
    "p0_total",
    "p1_pass",
    "p1_total",
    "p2_pass",
    "p2_total",
    "quality",
    "major_issues",
    "minor_issues",
    "fatal",
    "eligible",
    "wall_s",
    "compactions",
    "watchdog",
    "completion",
    "outcome",
    "steps",
    *EFFORT_FIELDS,
    "files_changed",
    "agent_ok",
    "report",
]


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", value.lower()).strip("-")


def provider_of(model: str) -> str:
    return model.split("/", 1)[0] if "/" in model else model


def format_duration(seconds: float | str) -> str:
    total = max(0, int(round(float(seconds))))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_optional_duration(seconds: object) -> str:
    if seconds is None or seconds == "":
        return "—"
    try:
        return format_duration(float(seconds))
    except (TypeError, ValueError):
        return "—"


def format_tokens(value: object) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not number:
        return "0"
    for unit, size in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(number) >= size:
            text = f"{number / size:.1f}".rstrip("0").rstrip(".")
            return f"{text}{unit}"
    return f"{number:.0f}"


def format_percent(value: object) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "—"



def metric_value(row: dict, key: str) -> float | None:
    """Read an optional numeric metric from a CSV row or score metadata."""
    value = row.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def median_or_none(values: list[float]) -> float | None:
    return statistics.median(values) if values else None



def atomic_write_text(path: Path, text: str) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def results_lock():
    """Serialize results.csv and leaderboard updates across processes."""
    lock_path = RESULTS_PATH.with_suffix(".csv.lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_command(
    cmd: list[str],
    *,
    log_path: Path | None = None,
    dry_run: bool = False,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    printable = " ".join(shlex.quote(part) for part in cmd)
    if dry_run:
        print(f"+ {printable}")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as handle:
            handle.write(f"+ {printable}\n\n")
            handle.flush()
            return subprocess.run(
                cmd,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                check=False,
            )
    return subprocess.run(cmd, text=True, env=env, check=False)


def engine_available(engine: str, dry_run: bool = False) -> bool:
    if dry_run:
        return True
    try:
        proc = subprocess.run(
            [engine, "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def image_exists(engine: str, image: str) -> bool:
    proc = subprocess.run(
        [engine, "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode == 0


def config_env(config_dir: Path | None) -> dict[str, str]:
    env = os.environ.copy()
    if config_dir is not None:
        if config_dir.name == "opencode":
            env["XDG_CONFIG_HOME"] = str(config_dir.parent)
        else:
            for name in ("opencode.json", "opencode.jsonc"):
                candidate = config_dir / name
                if candidate.exists():
                    env["OPENCODE_CONFIG"] = str(candidate)
                    break
    return env


def list_models(provider: str | None = None, config_dir: Path | None = None) -> list[str]:
    try:
        proc = subprocess.run(
            [OPENCODE, "models"],
            capture_output=True,
            text=True,
            timeout=60,
            env=config_env(config_dir),
            check=False,
        )
    except FileNotFoundError:
        sys.exit(f"error: {OPENCODE} not found on PATH")
    except subprocess.TimeoutExpired:
        sys.exit("error: `opencode models` timed out")
    if proc.returncode != 0:
        sys.exit(f"error: `opencode models` failed:\n{proc.stdout}{proc.stderr}")

    models: list[str] = []
    for line in proc.stdout.splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
        if not re.match(r"^[^\s/]+/\S+$", line):
            continue
        if provider and not line.startswith(f"{provider}/"):
            continue
        if line not in models:
            models.append(line)
    return models


def resolve_config_dir(explicit: str | None, run_dir: Path) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if path.is_file():
            # XDG layout: <root>/opencode/opencode.jsonc
            target = run_dir / "opencode-config" / "opencode"
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target / path.name)
            return target
        if path.is_dir():
            return path
        sys.exit(f"error: OpenCode config not found: {path}")

    try:
        proc = subprocess.run(
            [OPENCODE, "debug", "paths"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        for line in proc.stdout.splitlines():
            match = re.match(r"^config\s+(.+)$", line.strip())
            if match:
                path = Path(match.group(1)).expanduser()
                if path.exists():
                    return path
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    fallback = Path.home() / ".config" / "opencode"
    if fallback.exists():
        return fallback
    sys.exit("error: could not find the OpenCode config directory; pass --config")


def load_model_entry(model: str) -> dict | None:
    try:
        proc = subprocess.run(
            [OPENCODE, "debug", "config", "--standalone"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        sources = json.loads(proc.stdout)
        info: dict = {}
        for source in sources:
            if isinstance(source.get("info"), dict):
                info.update(source["info"])
        providers = info.get("providers") or info.get("provider") or {}
        provider, _, model_id = model.partition("/")
        return (providers.get(provider) or {}).get("models", {}).get(model_id)
    except (json.JSONDecodeError, OSError, subprocess.TimeoutExpired):
        return None


def describe_variant(entry: dict, variant: str | None) -> tuple[str, str]:
    body = dict(entry.get("body") or {})
    settings = dict(entry.get("settings") or {})
    if variant:
        for candidate in entry.get("variants") or []:
            if candidate.get("id") == variant:
                body.update(candidate.get("body") or {})
                settings.update(candidate.get("settings") or {})
                break
    thinking = body.get("thinking")
    effort = settings.get("reasoningEffort") or body.get("reasoningEffort") or ""
    if thinking is True:
        return "on", str(effort)
    if thinking is False:
        return "off", str(effort)
    if effort == "none":
        return "off", "none"
    if effort:
        return "on", str(effort)
    return "unknown", ""


def model_thinking(model: str, variant: str | None) -> tuple[str, str]:
    entry = load_model_entry(model)
    return describe_variant(entry, variant) if entry else ("unknown", "")


def model_variants(model: str) -> list[tuple[str, str, str]]:
    entry = load_model_entry(model)
    if not entry:
        return []
    out: list[tuple[str, str, str]] = []
    for candidate in entry.get("variants") or []:
        variant_id = str(candidate.get("id", ""))
        if not variant_id:
            continue
        thinking, effort = describe_variant(entry, variant_id)
        out.append((variant_id, thinking, effort))
    return out


def variant_rank(variant_id: str, thinking: str, effort: str) -> int:
    """Rank a variant by reasoning effort; higher first."""
    key = (effort or variant_id or "").strip().lower()
    if key in REASONING_ORDER:
        return REASONING_ORDER[key]
    if thinking == "off":
        return 0
    if thinking == "on":
        return 2
    return -1


def ordered_variants(model: str) -> list[tuple[str, str, str]]:
    """Return variants from highest reasoning effort to lowest."""
    return sorted(
        model_variants(model),
        key=lambda item: (-variant_rank(item[0], item[1], item[2]), item[0]),
    )


def config_env_names(config_dir: Path) -> set[str]:
    names: set[str] = set()
    if config_dir.is_file():
        files = [config_dir]
    else:
        files = list(config_dir.glob("*.json")) + list(config_dir.glob("*.jsonc"))
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name in re.findall(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}", text):
            if name in os.environ:
                names.add(name)
    return names


def copy_seed(destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        SEED_DIR,
        destination,
        ignore=shutil.ignore_patterns("node_modules", ".git", "dist", "test-results"),
    )
    shutil.copy2(PROMPT_PATH, destination / "PROMPT.md")


def agent_command(args: argparse.Namespace) -> str:
    model_ref = '"$OPENCODE_BENCH_MODEL"'
    if args.variant:
        # OpenCode 2 encodes the variant in the model reference, not a flag.
        model_ref = '"${OPENCODE_BENCH_MODEL}#${OPENCODE_BENCH_VARIANT}"'
    parts = [
        f"exec opencode run --format json --auto --model {model_ref}",
    ]
    if args.agent:
        parts.append(f"--agent {shlex.quote(args.agent)}")
    if args.pure:
        parts.append("--pure")
    parts.append('"$(cat /work/PROMPT.md)"')
    return " ".join(parts)


def count_compactions(data_dir: Path) -> int:
    database = data_dir / "opencode.db"
    if not database.exists():
        return 0
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "select count(*) from session_message where type = 'compaction'"
            ).fetchone()
        finally:
            connection.close()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


#: Build artefacts and caches that never count as agent edits.
WORKDIR_ARTEFACTS = {
    "node_modules",
    ".git",
    "dist",
    "test-results",
    "playwright-report",
    "coverage",
    ".vite",
    ".cache",
}


def read_agent_usage(log_path: Path) -> dict:
    """Token usage and step timings from an `opencode run --format json` log."""
    usage = {
        "available": False,
        "steps": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "max_request_tokens": None,
        "mean_request_tokens": None,
        "cost_usd": 0.0,
        "step_median_s": None,
        "step_p90_s": None,
        "step_max_s": None,
        "gen_tokens_per_s_median": None,
        "self_test_runs": 0,
    }
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return usage

    starts: dict[str, int] = {}
    durations: list[float] = []
    step_rates: list[float] = []
    request_tokens: list[int] = []
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        part = event.get("part") or {}
        if event_type == "step_start":
            message_id = str(part.get("messageID") or part.get("id") or "")
            if message_id:
                starts[message_id] = int(event.get("timestamp") or 0)
        elif event_type == "tool_use":
            state = part.get("state") or {}
            command = str((state.get("input") or {}).get("command") or "")
            tool = str(part.get("tool") or "")
            lower_tool = tool.lower()
            if (tool == "shell" and SELF_TEST_RE.search(command)) or any(
                marker in lower_tool
                for marker in ("browser", "chrome", "playwright")
            ):
                usage["self_test_runs"] += 1
        elif event_type == "step_finish":
            usage["steps"] += 1
            message_id = str(part.get("messageID") or "")
            started = starts.pop(message_id, None)
            finished = int(event.get("timestamp") or 0)
            duration = None
            if started is not None and finished and finished >= started:
                duration = (finished - started) / 1000.0
                durations.append(duration)
            tokens = part.get("tokens") or {}
            cash = tokens.get("cache") or {}
            generated = int(tokens.get("output") or 0) + int(
                tokens.get("reasoning") or 0
            )
            if duration and duration > 0 and generated > 0:
                step_rates.append(generated / duration)
            usage["input_tokens"] += int(tokens.get("input") or 0)
            usage["output_tokens"] += int(tokens.get("output") or 0)
            usage["reasoning_tokens"] += int(tokens.get("reasoning") or 0)
            usage["cache_read_tokens"] += int(cash.get("read") or 0)
            usage["cache_write_tokens"] += int(cash.get("write") or 0)
            request_tokens.append(
                int(tokens.get("input") or 0)
                + int(cash.get("read") or 0)
                + int(cash.get("write") or 0)
            )
            try:
                usage["cost_usd"] += float(part.get("cost") or 0)
            except (TypeError, ValueError):
                pass

    usage["available"] = True
    if request_tokens:
        usage["max_request_tokens"] = max(request_tokens)
        usage["mean_request_tokens"] = sum(request_tokens) / len(request_tokens)
    if durations:
        usage["step_median_s"] = statistics.median(durations)
        usage["step_p90_s"] = percentile(durations, 0.9)
        usage["step_max_s"] = max(durations)
    if step_rates:
        usage["gen_tokens_per_s_median"] = statistics.median(step_rates)
    return usage


def count_agent_steps(log_path: Path) -> int:
    """Count assistant steps in an `opencode run --format json` log."""
    return int(read_agent_usage(log_path).get("steps") or 0)


def _json_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def read_session_metrics(data_dir: Path, wall_s: float) -> dict:
    """Time-split metrics from the OpenCode SQLite session database.

    The database records each assistant message with `created`, `streamed` and
    `completed` timestamps, plus per-content-block timings. Reasoning and tool
    blocks are children of the same message, so this separates thinking from
    tool execution without a model proxy.
    """
    result = {
        "available": False,
        "span_s": None,
        "agent_s": None,
        "ttft_s": None,
        "think_s": None,
        "output_s": None,
        "tool_s": None,
        "tool_shell_s": None,
        "tool_write_s": None,
        "tool_read_s": None,
        "tool_other_s": None,
        "self_test_s": 0.0,
        "self_test_runs": 0,
        "self_test_failures": 0,
        "build_tool_s": 0.0,
        "dev_tool_s": 0.0,
        "retries": 0,
        "retry_s": 0.0,
        "compaction_tokens": 0,
        "compaction_cost_usd": 0.0,
        "db_input_tokens": 0,
        "db_output_tokens": 0,
        "db_reasoning_tokens": 0,
        "db_cache_read_tokens": 0,
        "db_cache_write_tokens": 0,
        "assistant_steps": 0,
        "max_request_tokens": None,
        "mean_request_tokens": None,
    }
    database = data_dir / "opencode.db"
    if not database.exists():
        return result

    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    except sqlite3.Error:
        return result

    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "select name from sqlite_master where type = 'table'"
            )
        }
        if "session_message" not in tables:
            connection.close()
            return result
        rows = connection.execute(
            "select type, seq, time_created, data from session_message "
            "order by time_created, seq"
        ).fetchall()
    except sqlite3.Error:
        connection.close()
        return result

    try:
        first_user: int | None = None
        last_assistant: int | None = None
        total_agent = 0.0
        total_ttft = 0.0
        total_think = 0.0
        total_output = 0.0
        tool_totals = {"shell": 0.0, "write": 0.0, "read": 0.0, "other": 0.0}
        request_tokens: list[int] = []

        for message_type, _seq, row_created, raw in rows:
            try:
                data = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            times = data.get("time") or {}
            created = int(times.get("created") or row_created or 0)
            completed = int(times.get("completed") or 0)
            streamed = int(times.get("streamed") or completed or 0)

            if message_type == "user" and created:
                first_user = created if first_user is None else min(first_user, created)
                continue
            if message_type == "compaction":
                tokens = data.get("tokens") or {}
                cash = tokens.get("cache") or {}
                result["compaction_tokens"] += sum(
                    _json_int(tokens.get(key))
                    for key in ("input", "output", "reasoning")
                )
                result["compaction_tokens"] += _json_int(cash.get("read"))
                result["compaction_tokens"] += _json_int(cash.get("write"))
                try:
                    result["compaction_cost_usd"] += float(data.get("cost") or 0)
                except (TypeError, ValueError):
                    pass
                continue
            if message_type != "assistant":
                continue

            # Provider errors/retries are not model generation. They stay in
            # the wall clock (and are reported as retry_s) but must not inflate
            # think/output time or depress tok/s with zero-token attempts.
            errored = bool(data.get("error")) or str(
                data.get("finish") or ""
            ).lower() == "error"
            if errored:
                if created and (completed or streamed):
                    finished = completed or streamed
                    result["retries"] += 1
                    result["retry_s"] += max(0, finished - created) / 1000.0
                    last_assistant = (
                        finished
                        if last_assistant is None
                        else max(last_assistant, finished)
                    )
                continue

            result["assistant_steps"] += 1
            tokens = data.get("tokens") or {}
            cash = tokens.get("cache") or {}
            result["db_input_tokens"] += _json_int(tokens.get("input"))
            result["db_output_tokens"] += _json_int(tokens.get("output"))
            result["db_reasoning_tokens"] += _json_int(tokens.get("reasoning"))
            result["db_cache_read_tokens"] += _json_int(cash.get("read"))
            result["db_cache_write_tokens"] += _json_int(cash.get("write"))
            request_tokens.append(
                _json_int(tokens.get("input"))
                + _json_int(cash.get("read"))
                + _json_int(cash.get("write"))
            )

            if not created or not completed:
                continue
            if completed:
                last_assistant = completed if last_assistant is None else max(
                    last_assistant, completed
                )
            total_agent += max(0, completed - created) / 1000.0

            timed: list[tuple[int, dict, dict]] = []
            for block in data.get("content") or []:
                if not isinstance(block, dict):
                    continue
                block_time = block.get("time") or {}
                start = int(block_time.get("created") or 0)
                if start:
                    timed.append((start, block_time, block))
            if not timed:
                continue
            timed.sort(key=lambda item: item[0])

            first_block = timed[0][0]
            total_ttft += max(0, first_block - created) / 1000.0

            reasoning = [item for item in timed if item[2].get("type") == "reasoning"]
            if reasoning:
                reasoning_start = min(item[0] for item in reasoning)
                boundary = min(
                    (
                        item[0]
                        for item in timed
                        if item[2].get("type") != "reasoning"
                        and item[0] >= reasoning_start
                    ),
                    default=streamed or reasoning_start,
                )
                boundary = max(reasoning_start, min(boundary, streamed or boundary))
                total_think += max(0, boundary - reasoning_start) / 1000.0
                if streamed:
                    total_output += max(0, streamed - boundary) / 1000.0
            elif streamed:
                total_output += max(0, streamed - first_block) / 1000.0

            for _start, block_time, block in timed:
                if block.get("type") != "tool":
                    continue
                ran = int(block_time.get("ran") or 0)
                finished = int(block_time.get("completed") or ran or 0)
                if not ran or not finished or finished < ran:
                    continue
                duration = (finished - ran) / 1000.0
                name = str(block.get("name") or "other")
                state = block.get("state") or {}
                command = str((state.get("input") or {}).get("command") or "")
                output = " ".join(
                    str(part.get("text") or "")
                    for part in (state.get("content") or [])
                    if isinstance(part, dict)
                )
                lower_name = name.lower()
                test_tool = bool(
                    (name == "shell" and SELF_TEST_RE.search(command))
                    or any(
                        marker in lower_name
                        for marker in ("browser", "chrome", "playwright")
                    )
                )
                if test_tool:
                    result["self_test_s"] += duration
                    result["self_test_runs"] += 1
                    exit_code = (state.get("metadata") or {}).get("exit")
                    failed = bool(
                        re.search(r"\b[1-9]\d*\s+failed\b", output, re.IGNORECASE)
                        or re.search(r"\bFAILED\b", output)
                    )
                    if failed or (exit_code not in (None, 0, "0")):
                        result["self_test_failures"] += 1
                elif name == "shell" and BUILD_TOOL_RE.search(command):
                    result["build_tool_s"] += duration
                elif name == "shell" and DEV_TOOL_RE.search(command):
                    result["dev_tool_s"] += duration
                if name == "shell":
                    tool_totals["shell"] += duration
                elif name in ("write", "edit", "patch"):
                    tool_totals["write"] += duration
                elif name in ("read", "glob", "grep", "list"):
                    tool_totals["read"] += duration
                else:
                    tool_totals["other"] += duration

        if first_user and last_assistant and last_assistant >= first_user:
            result["span_s"] = (last_assistant - first_user) / 1000.0
        result["agent_s"] = total_agent
        result["ttft_s"] = total_ttft
        result["think_s"] = total_think
        result["output_s"] = total_output
        result["tool_s"] = sum(tool_totals.values())
        result["tool_shell_s"] = tool_totals["shell"]
        result["tool_write_s"] = tool_totals["write"]
        result["tool_read_s"] = tool_totals["read"]
        result["tool_other_s"] = tool_totals["other"]
        if request_tokens:
            result["max_request_tokens"] = max(request_tokens)
            result["mean_request_tokens"] = sum(request_tokens) / len(request_tokens)
        result["available"] = True
        return result
    finally:
        connection.close()


def compute_metrics(log_path: Path, data_dir: Path, wall_s: float) -> dict:
    """Merge log token usage and session timings into persisted metric fields."""
    usage = read_agent_usage(log_path)
    session = read_session_metrics(data_dir, wall_s)
    log_available = bool(usage.get("available"))
    db_available = bool(session.get("available"))

    if log_available:
        input_tokens = int(usage["input_tokens"])
        output_tokens = int(usage["output_tokens"])
        reasoning_tokens = int(usage["reasoning_tokens"])
        cache_read_tokens = int(usage["cache_read_tokens"])
        cache_write_tokens = int(usage["cache_write_tokens"])
        cost_usd = float(usage["cost_usd"])
        steps = int(usage["steps"])
    elif db_available:
        input_tokens = int(session["db_input_tokens"])
        output_tokens = int(session["db_output_tokens"])
        reasoning_tokens = int(session["db_reasoning_tokens"])
        cache_read_tokens = int(session["db_cache_read_tokens"])
        cache_write_tokens = int(session["db_cache_write_tokens"])
        cost_usd = float(session["compaction_cost_usd"])
        steps = int(session["assistant_steps"])
    else:
        input_tokens = output_tokens = reasoning_tokens = 0
        cache_read_tokens = cache_write_tokens = 0
        cost_usd = 0.0
        steps = 0

    max_request_tokens = usage.get("max_request_tokens")
    mean_request_tokens = usage.get("mean_request_tokens")
    if max_request_tokens is None and db_available:
        max_request_tokens = session.get("max_request_tokens")
        mean_request_tokens = session.get("mean_request_tokens")

    compaction_tokens = int(session.get("compaction_tokens") or 0) if db_available else 0
    if db_available:
        cost_usd += float(session.get("compaction_cost_usd") or 0)
    total_tokens = (
        input_tokens
        + output_tokens
        + reasoning_tokens
        + cache_read_tokens
        + cache_write_tokens
        + compaction_tokens
    )

    metrics: dict[str, object] = {
        "steps": steps,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "compaction_tokens": compaction_tokens,
        "total_tokens": total_tokens,
        "max_request_tokens": max_request_tokens,
        "mean_request_tokens": mean_request_tokens,
        "cost_usd": round(cost_usd, 6),
        "step_median_s": usage.get("step_median_s"),
        "step_p90_s": usage.get("step_p90_s"),
        "step_max_s": usage.get("step_max_s"),
        "gen_tokens_per_s_median": usage.get("gen_tokens_per_s_median"),
        "metrics_source": "db+log" if db_available and log_available else (
            "db" if db_available else ("log" if log_available else "none")
        ),
        "metrics_version": METRICS_VERSION,
    }

    if db_available:
        for key in (
            "span_s",
            "agent_s",
            "ttft_s",
            "think_s",
            "output_s",
            "tool_s",
            "tool_shell_s",
            "tool_write_s",
            "tool_read_s",
            "tool_other_s",
            "self_test_s",
            "self_test_runs",
            "self_test_failures",
            "build_tool_s",
            "dev_tool_s",
            "retries",
            "retry_s",
        ):
            metrics[key] = session.get(key)
        buckets = [
            metrics.get("ttft_s"),
            metrics.get("think_s"),
            metrics.get("output_s"),
            metrics.get("tool_s"),
        ]
        known = [float(value) for value in buckets if value is not None]
        accounted = sum(known)
        # Message timestamps can overlap slightly when turns are queued; the
        # harness wall time remains the hard total. Scale the buckets so they
        # cannot exceed it.
        if wall_s and accounted > float(wall_s):
            factor = float(wall_s) / accounted
            for key in ("ttft_s", "think_s", "output_s", "tool_s"):
                value = metrics.get(key)
                if value is not None:
                    metrics[key] = float(value) * factor
            # Self-test/build/dev are subsets of tool_s; scale them with it.
            tool_total = float(metrics.get("tool_s") or 0)
            for key in ("self_test_s", "build_tool_s", "dev_tool_s"):
                value = metrics.get(key)
                if value is not None:
                    metrics[key] = min(float(value) * factor, tool_total)
            accounted = float(wall_s)
        other = max(0.0, float(wall_s) - accounted) if wall_s else None
        metrics["other_s"] = other
        if other is not None and metrics.get("retry_s") is not None:
            metrics["retry_s"] = min(float(metrics["retry_s"]), float(other))

        def share(value: object) -> float | None:
            if value is None or not wall_s:
                return None
            return max(0.0, min(1.0, float(value) / float(wall_s)))

        metrics["ttft_share"] = share(metrics.get("ttft_s"))
        metrics["think_share"] = share(metrics.get("think_s"))
        metrics["output_share"] = share(metrics.get("output_s"))
        metrics["tool_share"] = share(metrics.get("tool_s"))
        metrics["other_share"] = share(other)
        metrics["self_test_share"] = share(metrics.get("self_test_s"))
        metrics["wall_ex_self_test_s"] = max(
            0.0, float(wall_s) - float(metrics.get("self_test_s") or 0)
        ) if wall_s else None
        generation_s = float(metrics.get("think_s") or 0) + float(
            metrics.get("output_s") or 0
        )
        if generation_s > 0:
            metrics["gen_tokens_per_s"] = (
                output_tokens + reasoning_tokens
            ) / generation_s
        else:
            metrics["gen_tokens_per_s"] = None
    else:
        for key in (
            "span_s",
            "agent_s",
            "ttft_s",
            "think_s",
            "output_s",
            "tool_s",
            "tool_shell_s",
            "tool_write_s",
            "tool_read_s",
            "tool_other_s",
            "other_s",
            "self_test_s",
            "self_test_runs",
            "self_test_failures",
            "build_tool_s",
            "dev_tool_s",
            "retries",
            "retry_s",
            "ttft_share",
            "think_share",
            "output_share",
            "tool_share",
            "other_share",
            "self_test_share",
            "gen_tokens_per_s",
        ):
            metrics[key] = None
        metrics["wall_ex_self_test_s"] = float(wall_s) if wall_s else None
        metrics["self_test_runs"] = (
            int(usage.get("self_test_runs") or 0) if log_available else None
        )

    metrics["tokens_per_step"] = (
        (input_tokens + output_tokens + reasoning_tokens) / steps if steps else None
    )
    cache_total = input_tokens + cache_read_tokens
    metrics["cache_hit_rate"] = (
        cache_read_tokens / cache_total if cache_total else None
    )
    return metrics



def workdir_manifest(root: Path) -> dict[str, str]:
    """Map relative path -> content hash for the seed and the generated app."""
    manifest: dict[str, str] = {}
    if not root.is_dir():
        return manifest
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in WORKDIR_ARTEFACTS)
        base = Path(directory)
        for filename in sorted(filenames):
            path = base / filename
            if path.is_symlink() or not path.is_file():
                continue
            try:
                digest = hashlib.sha1(path.read_bytes()).hexdigest()
            except OSError:
                continue
            manifest[path.relative_to(root).as_posix()] = digest
    return manifest


def count_changed_files(workdir: Path) -> int:
    """Files the agent added, removed or rewrote relative to the seed."""
    baseline = workdir_manifest(SEED_DIR)
    if PROMPT_PATH.exists():
        # copy_seed() overwrites any seed copy with the benchmark prompt.
        baseline["PROMPT.md"] = hashlib.sha1(PROMPT_PATH.read_bytes()).hexdigest()
    current = workdir_manifest(workdir)
    return sum(
        1 for name in baseline.keys() | current.keys() if baseline.get(name) != current.get(name)
    )


def classify_outcome(
    *, watchdog: str, completion: bool, files_changed: int, agent_ok: bool
) -> str:
    """How the agent finished, independent of the hidden test score.

    `watchdog: exit` only means the process ended by itself: an agent that
    stops after two steps looks identical to a successful one unless the diff
    and the completion file are checked.
    """
    if watchdog == "dry-run":
        return "dry-run"
    if completion:
        return "complete"
    if watchdog == "hard":
        return "timeout"
    if watchdog == "idle":
        return "stalled"
    if not agent_ok:
        return "error"
    if files_changed == 0:
        return "gave_up"
    return "incomplete"


def start_agent(
    model: str,
    config_dir: Path,
    workdir: Path,
    data_dir: Path,
    run_dir: Path,
    *,
    env_names: set[str],
    args: argparse.Namespace,
) -> dict:
    name = f"opencode-bench-{slug(provider_of(model))}-{uuid.uuid4().hex[:8]}"
    log_path = run_dir / "agent.log"
    command = [
        args.engine,
        "run",
        "--rm",
        "--name",
        name,
        "--shm-size=1g",
        "-v",
        f"{workdir}:/work",
        "-v",
        f"{config_dir}:/config/opencode:ro",
        "-v",
        f"{data_dir}:/home/agent/.local/share/opencode",
        "-e",
        "HOME=/home/agent",
        "-e",
        "XDG_CONFIG_HOME=/home/agent/.config",
        "-e",
        f"OPENCODE_BENCH_MODEL={model}",
    ]
    if args.variant:
        command += ["-e", f"OPENCODE_BENCH_VARIANT={args.variant}"]
    for key in sorted(env_names):
        command += ["-e", key]
    command += ["-w", "/work", args.image, "bash", "-lc", agent_command(args)]

    if args.dry_run:
        print("+ " + " ".join(shlex.quote(part) for part in command))
        return {
            "wall_s": 0.0,
            "watchdog": "dry-run",
            "completion": False,
            "agent_ok": True,
            "self_test_runs": 0,
        }

    start = time.time()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        handle.write("+ " + " ".join(shlex.quote(part) for part in command) + "\n\n")
        handle.flush()
        proc = subprocess.Popen(
            command,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

        completion_path = workdir / "COMPLETION.md"
        last_size = 0
        last_change = start
        completion_seen_at: float | None = None
        watchdog = "exit"

        while proc.poll() is None:
            now = time.time()
            try:
                size = log_path.stat().st_size
            except FileNotFoundError:
                size = 0
            if size != last_size:
                last_size = size
                last_change = now

            if completion_path.exists() and completion_seen_at is None:
                completion_seen_at = now
                print("  COMPLETION.md seen; stopping in 60s unless the agent exits")
            if completion_seen_at is not None and now - completion_seen_at > 60:
                watchdog = "completion"
                break
            if now - start > args.timeout:
                watchdog = "hard"
                break
            if now - last_change > args.idle_timeout:
                watchdog = "idle"
                break
            time.sleep(2)

        if watchdog != "exit":
            subprocess.run(
                [args.engine, "rm", "-f", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    wall_s = time.time() - start
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    self_tests = log_text.count("playwright test") + log_text.count("test:e2e")
    return {
        "wall_s": wall_s,
        "watchdog": watchdog,
        "completion": (workdir / "COMPLETION.md").exists(),
        "agent_ok": proc.returncode == 0,
        "self_test_runs": self_tests,
    }


def read_code(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def build_app(run_dir: Path, workdir: Path, *, engine: str, image: str, dry_run: bool) -> dict:
    command = [
        engine,
        "run",
        "--rm",
        "--network",
        "none",
        "-v",
        f"{workdir}:/work",
        "-v",
        f"{run_dir}:/out",
        "-w",
        "/work",
        image,
        "bash",
        "-lc",
        "set +e; "
        "npm run typecheck > /out/typecheck.log 2>&1; "
        "echo $? > /out/typecheck.code; "
        "npm run build > /out/build.log 2>&1; "
        "echo $? > /out/build.code; "
        "test -f /out/build.code",
    ]
    proc = run_command(command, log_path=run_dir / "build-run.log", dry_run=dry_run)
    if dry_run:
        return {"typecheck_ok": True, "build_ok": True, "dist": True}
    return {
        "typecheck_ok": read_code(run_dir / "typecheck.code") == 0,
        "build_ok": read_code(run_dir / "build.code") == 0 and proc.returncode == 0,
        "dist": (workdir / "dist" / "index.html").exists(),
    }


def run_tests(run_dir: Path, workdir: Path, *, engine: str, image: str, dry_run: bool) -> dict:
    result_file = run_dir / "playwright.json"
    if result_file.exists():
        result_file.unlink()
    command = [
        engine,
        "run",
        "--rm",
        "--network",
        "none",
        "--shm-size=1g",
        "-v",
        f"{workdir / 'dist'}:/app:ro",
        "-v",
        f"{TESTS_DIR}:/opt/opencode-bench/tests:ro",
        "-v",
        f"{run_dir}:/out",
        "-w",
        "/opt/opencode-bench",
        "-e",
        "BASE_URL=http://127.0.0.1:4173",
        "-e",
        "RESULT_FILE=/out/playwright.json",
        "-e",
        "OUTPUT_DIR=/out/test-results",
        image,
        "bash",
        "-lc",
        "npx playwright test --config /opt/opencode-bench/tests/playwright.config.ts",
    ]
    proc = run_command(command, log_path=run_dir / "test.log", dry_run=dry_run)
    if dry_run:
        return {"test_ok": True, "tests": {}, "error": None, "exit_code": 0}
    if not result_file.exists():
        return {
            "test_ok": False,
            "tests": {},
            "error": "playwright.json missing (suite failed to start)",
            "exit_code": proc.returncode,
        }
    try:
        data = json.loads(result_file.read_text())
    except json.JSONDecodeError as exc:
        return {
            "test_ok": False,
            "tests": {},
            "error": f"invalid playwright.json: {exc}",
            "exit_code": proc.returncode,
        }

    tests: dict[str, bool] = {}

    def walk(suites: list) -> None:
        for suite in suites:
            for spec in suite.get("specs", []):
                title = str(spec.get("title", ""))
                ok = spec.get("ok")
                if ok is None:
                    results = [
                        result
                        for test in spec.get("tests", [])
                        for result in test.get("results", [])
                    ]
                    ok = bool(results) and all(result.get("status") == "passed" for result in results)
                tests[title] = bool(ok)
            walk(suite.get("suites", []))

    walk(data.get("suites", []))
    return {
        "test_ok": proc.returncode == 0,
        "tests": tests,
        "error": None if proc.returncode == 0 else f"playwright exit {proc.returncode}",
        "exit_code": proc.returncode,
    }


def score_run(tests: dict[str, bool], build: dict, *, goal: str) -> dict:
    counts = {tier: {"pass": 0, "total": EXPECTED[tier]} for tier in EXPECTED}
    details = []
    for title, ok in tests.items():
        match = re.match(r"^\[(P[012])\]", title)
        tier = match.group(1) if match else "P2"
        if ok:
            counts[tier]["pass"] += 1
        details.append({"title": title, "tier": tier, "ok": ok})

    for tier, expected in EXPECTED.items():
        counts[tier]["pass"] = min(counts[tier]["pass"], expected)

    weighted_total = sum(WEIGHT[tier] * EXPECTED[tier] for tier in EXPECTED)
    weighted_pass = sum(WEIGHT[tier] * counts[tier]["pass"] for tier in EXPECTED)
    quality = weighted_pass / weighted_total if weighted_total else 0.0
    fatal = counts["P0"]["total"] - counts["P0"]["pass"]
    major = counts["P1"]["total"] - counts["P1"]["pass"]
    minor = counts["P2"]["total"] - counts["P2"]["pass"]
    eligible = bool(build.get("build_ok")) and fatal == 0 and quality >= 0.80

    return {
        "counts": counts,
        "details": details,
        "quality": quality,
        "fatal": fatal,
        "major": major,
        "minor": minor,
        "eligible": eligible,
        "goal": goal,
    }


def render_effort_lines(meta: dict) -> list[str]:
    """Markdown block for the per-run effort and token breakdown."""
    lines = ["## Effort", ""]
    has_timing = meta.get("metrics_source") in ("db+log", "db") and meta.get(
        "think_s"
    ) is not None
    if has_timing:
        buckets = [
            ("TTFT / prefill", "ttft_s", "ttft_share"),
            ("Thinking", "think_s", "think_share"),
            ("Model output", "output_s", "output_share"),
            ("Tool execution", "tool_s", "tool_share"),
            ("Other", "other_s", "other_share"),
        ]
        lines += ["| bucket | time | share |", "|---|---:|---:|"]
        for label, time_key, share_key in buckets:
            lines.append(
                f"| {label} | {format_optional_duration(meta.get(time_key))} | "
                f"{format_percent(meta.get(share_key))} |"
            )
        lines += [
            "",
            f"- tools: shell {format_optional_duration(meta.get('tool_shell_s'))}, "
            f"write/edit {format_optional_duration(meta.get('tool_write_s'))}, "
            f"read {format_optional_duration(meta.get('tool_read_s'))}, "
            f"other {format_optional_duration(meta.get('tool_other_s'))}",
            f"- self-test: {format_optional_duration(meta.get('self_test_s'))} "
            f"({format_percent(meta.get('self_test_share'))}) · "
            f"{meta.get('self_test_runs') or 0} runs · "
            f"{meta.get('self_test_failures') or 0} failed · "
            f"build/typecheck {format_optional_duration(meta.get('build_tool_s'))} · "
            f"wall excl. self-test {format_optional_duration(meta.get('wall_ex_self_test_s'))}",
            f"- retries: {meta.get('retries') or 0} · "
            f"{format_optional_duration(meta.get('retry_s'))} provider error/retry time",
            f"- steps: {meta.get('steps', 0)} "
            f"(median {format_optional_duration(meta.get('step_median_s'))}, "
            f"p90 {format_optional_duration(meta.get('step_p90_s'))}, "
            f"max {format_optional_duration(meta.get('step_max_s'))})",
        ]
    else:
        source = meta.get("metrics_source", "none")
        lines.append(
            "No OpenCode session timing data for this run "
            f"(`metrics_source={source}`); only token totals are available."
        )

    generation = metric_value(meta, "gen_tokens_per_s")
    generation_median = metric_value(meta, "gen_tokens_per_s_median")
    gen_text = f"{generation:.1f} tok/s" if generation is not None else "—"
    if generation_median is not None:
        gen_text += f" (median step {generation_median:.1f} tok/s)"
    lines += [
        "",
        f"- request context: peak {format_tokens(meta.get('max_request_tokens'))}, "
        f"mean {format_tokens(meta.get('mean_request_tokens'))} prompt tokens per request",
        f"- cumulative tokens: {format_tokens(meta.get('input_tokens'))} in, "
        f"{format_tokens(meta.get('output_tokens'))} out, "
        f"{format_tokens(meta.get('reasoning_tokens'))} reasoning (reported), "
        f"{format_tokens(meta.get('cache_read_tokens'))} cache read, "
        f"{format_tokens(meta.get('cache_write_tokens'))} cache write, "
        f"{format_tokens(meta.get('compaction_tokens'))} compaction",
        f"- total tokens: {format_tokens(meta.get('total_tokens'))}; "
        f"generation: {gen_text}",
        f"- cache hit rate: {format_percent(meta.get('cache_hit_rate'))}; "
        f"cost: {meta.get('cost_usd', 0)}",
        "",
    ]
    if meta.get("reasoning_tokens") in (0, "0", 0.0) and meta.get("think_s"):
        lines += [
            "> The provider reported zero reasoning tokens, but the session log "
            "shows a thinking phase. Treat the time split as authoritative and "
            "reasoning tokens as provider-reported only.",
            "",
        ]
    output_s = metric_value(meta, "output_s")
    if (
        output_s is not None
        and output_s < 1
        and (metric_value(meta, "output_tokens") or 0) > 1000
    ):
        lines += [
            "> No separate timestamped output block was recorded; output tokens "
            "may have been emitted inside the reasoning stream, so the output "
            "time is a lower bound.",
            "",
        ]
    return lines


def write_run_report(run_dir: Path, meta: dict, score: dict) -> None:
    lines = [
        f"# {meta['run_id']}",
        "",
        f"- model: `{meta['model']}`",
        f"- provider: {meta['provider']}",
        *([f"- hardware: {meta['hardware']}"] if meta.get("hardware") else []),
        f"- variant: {meta.get('variant', 'default')}",
        f"- thinking: {meta.get('thinking', 'unknown')}"
        + (f" ({meta['reasoning_effort']})" if meta.get("reasoning_effort") else ""),
        f"- goal: {meta['goal']}",
        f"- wall: {format_duration(meta['wall_s'])}",
        f"- compactions: {meta.get('compactions', 0)}",
        f"- watchdog: {meta['watchdog']}",
        f"- completion file: {meta['completion']}",
        f"- outcome: {meta.get('outcome', 'unknown')}",
        f"- steps: {meta.get('steps', 0)}",
        f"- files changed: {meta.get('files_changed', 0)}",
        f"- agent exit ok: {meta['agent_ok']}",
        f"- build: {'ok' if meta['build_ok'] else 'failed'}",
        f"- typecheck: {'ok' if meta['typecheck_ok'] else 'failed'}",
        f"- quality: {score['quality']:.3f}",
        f"- fatal/major/minor: {score['fatal']}/{score['major']}/{score['minor']}",
        f"- eligible: {score['eligible']}",
        "",
        *render_effort_lines(meta),
        "## Tests",
        "",
        "| tier | test | result |",
        "|---|---|---|",
    ]
    for detail in score["details"]:
        lines.append(
            f"| {detail['tier']} | {detail['title']} | {'pass' if detail['ok'] else 'FAIL'} |"
        )
    lines += [
        "",
        "## Artifacts",
        "",
        "- `agent.log`",
        "- `build.log`",
        "- `typecheck.log`",
        "- `test.log`",
        "- `playwright.json`",
        "",
    ]
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def _write_result_rows(rows: list[dict]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in CSV_FIELDS})
    atomic_write_text(RESULTS_PATH, buffer.getvalue())


def _ensure_results_schema_unlocked() -> None:
    if not RESULTS_PATH.exists():
        return
    with RESULTS_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        rows = list(reader)
    if header == CSV_FIELDS:
        return
    _write_result_rows(rows)


def ensure_results_schema() -> None:
    with results_lock():
        _ensure_results_schema_unlocked()


def _read_result_rows_unlocked() -> list[dict]:
    _ensure_results_schema_unlocked()
    if not RESULTS_PATH.exists():
        return []
    with RESULTS_PATH.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def rewrite_result(run_id: str, row: dict) -> None:
    with results_lock():
        rows = _read_result_rows_unlocked()
        changed = False
        for index, existing in enumerate(rows):
            if existing.get("run_id") == run_id:
                rows[index] = {key: row.get(key, "") for key in CSV_FIELDS}
                changed = True
                break
        if not changed:
            rows.append({key: row.get(key, "") for key in CSV_FIELDS})
        _write_result_rows(rows)


def append_result(row: dict) -> None:
    with results_lock():
        rows = _read_result_rows_unlocked()
        rows.append({key: row.get(key, "") for key in CSV_FIELDS})
        _write_result_rows(rows)


def print_summary(meta: dict, score: dict) -> None:
    counts = score["counts"]
    print("")
    print(f"{meta['model']}  rep {meta['rep']}")
    thinking = meta.get("thinking", "unknown")
    effort = meta.get("reasoning_effort") or ""
    print(
        f"  build {'ok' if meta['build_ok'] else 'FAIL'}"
        f"   typecheck {'ok' if meta['typecheck_ok'] else 'FAIL'}"
        f"   thinking {thinking}" + (f" ({effort})" if effort else "")
    )
    print(
        f"  P0 {counts['P0']['pass']}/{counts['P0']['total']}"
        f"   P1 {counts['P1']['pass']}/{counts['P1']['total']}"
        f"   P2 {counts['P2']['pass']}/{counts['P2']['total']}"
    )
    print(
        f"  quality {score['quality']:.2f}"
        f"   minor {score['minor']}   major {score['major']}"
        f"   {'ELIGIBLE' if score['eligible'] else 'not eligible'}"
    )
    print(
        f"  wall {format_duration(meta['wall_s'])}"
        f"   work {format_optional_duration(meta.get('wall_ex_self_test_s'))}"
        f"   self-test {format_optional_duration(meta.get('self_test_s'))}"
        f" ({meta.get('self_test_runs') or 0} runs)"
        f"   retries {meta.get('retries') or 0}"
        f"   compactions {meta.get('compactions', 0)}"
        f"   watchdog {meta['watchdog']}"
    )
    print(
        f"  outcome {meta.get('outcome', 'unknown')}"
        f"   steps {meta.get('steps', 0)}"
        f"   files changed {meta.get('files_changed', 0)}"
    )
    if meta.get("think_share") is not None:
        print(
            f"  effort  think {format_percent(meta.get('think_share'))}"
            f" · output {format_percent(meta.get('output_share'))}"
            f" · tools {format_percent(meta.get('tool_share'))}"
            f" · ttft {format_percent(meta.get('ttft_share'))}"
            f" · other {format_percent(meta.get('other_share'))}"
        )
    generation = metric_value(meta, "gen_tokens_per_s")
    generation_median = metric_value(meta, "gen_tokens_per_s_median")
    gen_text = f"{generation:.1f}" if generation is not None else "—"
    if generation_median is not None:
        gen_text += f" (median {generation_median:.1f})"
    print(
        f"  tokens  {format_tokens(meta.get('input_tokens'))} in"
        f" · {format_tokens(meta.get('output_tokens'))} out"
        f" · {format_tokens(meta.get('reasoning_tokens'))} reason"
        f" · {format_tokens(meta.get('cache_read_tokens'))} cache"
        f" · peak {format_tokens(meta.get('max_request_tokens'))} req tok"
        f" · {gen_text} tok/s"
    )
    print(f"  report {meta['report']}")


def cmd_build(args: argparse.Namespace) -> int:
    if not engine_available(args.engine, args.dry_run):
        eprint(f"error: {args.engine} is not available; start Docker/OrbStack or pass --engine")
        return 2
    command = [
        args.engine,
        "build",
        "-f",
        str(ROOT / "docker" / "agent.Dockerfile"),
        "-t",
        args.image,
    ]
    if args.playwright_image:
        command += ["--build-arg", f"PLAYWRIGHT_IMAGE={args.playwright_image}"]
    if args.opencode_version:
        command += ["--build-arg", f"OPENCODE_VERSION={args.opencode_version}"]
    command.append(str(ROOT))
    print("+ " + " ".join(shlex.quote(part) for part in command))
    if args.dry_run:
        return 0
    return subprocess.run(command, check=False).returncode


def provider_counts(models: list[str]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for model in models:
        provider = provider_of(model)
        counts[provider] = counts.get(provider, 0) + 1
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))


def cmd_models(args: argparse.Namespace) -> int:
    config_dir = resolve_config_dir(
        args.config, RUNS_DIR / f"_models-{uuid.uuid4().hex[:8]}"
    )
    models = list_models(args.provider, config_dir)
    if not models:
        eprint("error: `opencode models` returned no models")
        return 1
    if args.provider or args.all:
        for model in models:
            print(model)
    else:
        for provider, count in provider_counts(models):
            print(f"{provider:28} {count:4} model(s)")
    print(f"\n{len(models)} model(s); config {config_dir}")
    return 0


def choose_model(models: list[str], requested: str | None, provider: str | None) -> list[str]:
    if requested and requested != "all":
        if requested not in models:
            eprint(f"error: model '{requested}' is not in `opencode models`")
            eprint("hint: run ./bench.py models, or pass --no-preflight to try anyway")
            raise SystemExit(2)
        return [requested]
    if requested == "all":
        return models

    if not provider:
        counts = provider_counts(models)
        print("Providers:")
        for index, (name, count) in enumerate(counts, 1):
            print(f"  {index:3}. {name:28} {count:4} model(s)")
        raw = input("provider> ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(counts):
            provider = counts[int(raw) - 1][0]
        else:
            provider = raw or None

    candidates = [model for model in models if not provider or model.startswith(f"{provider}/")]
    if not candidates:
        eprint(f"error: no models under provider '{provider}'")
        raise SystemExit(2)

    if len(candidates) > 50:
        needle = input("filter (substring, empty for all)> ").strip().lower()
        if needle:
            candidates = [model for model in candidates if needle in model.lower()]
    if not candidates:
        eprint("error: no models matched")
        raise SystemExit(2)

    print(f"Models under {provider or 'all'}:")
    for index, model in enumerate(candidates, 1):
        print(f"  {index:3}. {model}")
    raw = input("model> ").strip()
    try:
        return [candidates[int(raw) - 1]]
    except (ValueError, IndexError):
        # Also accept a full model reference typed directly.
        if raw in candidates:
            return [raw]
        eprint("error: invalid selection")
        raise SystemExit(2)


def choose_variant(model: str, requested: str | None) -> str | None:
    variants = ordered_variants(model)
    ids = [variant_id for variant_id, _, _ in variants]
    if requested:
        if requested == "default":
            return None
        if ids and requested not in ids:
            eprint(f"error: variant '{requested}' is not defined for {model}")
            eprint(f"available: default, {', '.join(ids)}")
            raise SystemExit(2)
        return requested
    if not variants:
        return None
    if not sys.stdin.isatty():
        print(f"  variant: no TTY; using default (available: default, {', '.join(ids)})")
        return None

    default_thinking, default_effort = model_thinking(model, None)
    print(f"Variants for {model}:")
    default_label = f"default ({default_thinking}"
    if default_effort:
        default_label += f" {default_effort}"
    default_label += ")"
    print(f"   0. {default_label}")
    for index, (variant_id, thinking, effort) in enumerate(variants, 1):
        label = f"{variant_id} ({thinking}"
        if effort:
            label += f" {effort}"
        label += ")"
        print(f"  {index:2}. {label}")
    raw = input("variant> ").strip()
    if not raw or raw == "0" or raw == "default":
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(variants):
        return variants[int(raw) - 1][0]
    if raw in ids:
        return raw
    eprint("error: invalid variant")
    raise SystemExit(2)


def claim_run_dir(base_id: str) -> tuple[str, Path]:
    """Atomically claim a unique run directory, even from multiple shells."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, 100):
        run_id = base_id if attempt == 1 else f"{base_id}-{attempt}"
        run_dir = RUNS_DIR / run_id
        try:
            run_dir.mkdir()
        except FileExistsError:
            continue
        return run_id, run_dir
    raise SystemExit(f"error: could not claim a unique run directory for {base_id}")


def run_one(model: str, *, rep: int, args: argparse.Namespace, variant: str | None) -> dict:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    variant_slug = slug(variant) if variant else "default"
    base_id = f"{timestamp}-{slug(model)}-{variant_slug}-r{rep}"
    run_id, run_dir = claim_run_dir(base_id)
    workdir = run_dir / "workdir"
    data_dir = run_dir / "opencode-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    config_dir = resolve_config_dir(args.config, run_dir)
    copy_seed(workdir)

    env_names = config_env_names(config_dir) | set(args.env or [])
    if not args.no_preflight:
        available = list_models(provider_of(model), config_dir)
        if model not in available:
            raise SystemExit(
                f"error: '{model}' is not in `opencode models` for {config_dir}; "
                "run ./bench.py models or pass --no-preflight"
            )

    variant_label = variant or "default"
    thinking, effort = model_thinking(model, variant)
    run_args = argparse.Namespace(**vars(args))
    run_args.variant = variant
    print(f"run {run_id}")
    print(f"  workdir {workdir}")
    print(f"  model   {model}")
    print(f"  config  {config_dir}")
    print(f"  thinking {thinking}" + (f" ({effort})" if effort else "") + f"  variant={variant_label}")
    print(f"  goal    {args.goal}")

    agent = start_agent(
        model,
        config_dir,
        workdir,
        data_dir,
        run_dir,
        env_names=env_names,
        args=run_args,
    )
    steps = count_agent_steps(run_dir / "agent.log")
    metrics = compute_metrics(run_dir / "agent.log", data_dir, float(agent["wall_s"]))
    files_changed = count_changed_files(workdir)
    outcome = classify_outcome(
        watchdog=agent["watchdog"],
        completion=agent["completion"],
        files_changed=files_changed,
        agent_ok=agent["agent_ok"],
    )
    build = build_app(run_dir, workdir, engine=args.engine, image=args.image, dry_run=args.dry_run)
    if build["dist"]:
        tests = run_tests(run_dir, workdir, engine=args.engine, image=args.image, dry_run=args.dry_run)
    else:
        tests = {
            "test_ok": False,
            "tests": {},
            "error": "dist/index.html missing; suite not run",
            "exit_code": None,
        }

    if args.dry_run:
        print("dry run complete; no score or report written")
        shutil.rmtree(run_dir, ignore_errors=True)
        return {}

    score = score_run(tests["tests"], build, goal=args.goal)
    meta = {
        "run_id": run_id,
        "timestamp": timestamp,
        "model": model,
        "provider": provider_of(model),
        "hardware": (getattr(args, "hardware", None) or "").strip()[:300],
        "rep": rep,
        "goal": args.goal,
        "variant": variant_label,
        "thinking": thinking,
        "reasoning_effort": effort,
        "wall_s": agent["wall_s"],
        "compactions": count_compactions(data_dir),
        "watchdog": agent["watchdog"],
        "completion": agent["completion"],
        "outcome": outcome,
        "steps": steps,
        **metrics,
        "files_changed": files_changed,
        "agent_ok": agent["agent_ok"],
        "self_test_runs": (
            metrics.get("self_test_runs")
            if metrics.get("self_test_runs") is not None
            else agent["self_test_runs"]
        ),
        "build_ok": build["build_ok"],
        "typecheck_ok": build["typecheck_ok"],
        "test_ok": tests["test_ok"],
        "test_error": tests["error"],
        "counts": score["counts"],
        "quality": score["quality"],
        "fatal": score["fatal"],
        "major": score["major"],
        "minor": score["minor"],
        "eligible": score["eligible"],
        "report": str(run_dir / "report.md"),
        "agent_log": str(run_dir / "agent.log"),
        "build_log": str(run_dir / "build.log"),
        "typecheck_log": str(run_dir / "typecheck.log"),
        "test_log": str(run_dir / "test.log"),
    }
    write_run_report(run_dir, meta, score)
    (run_dir / "score.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    row = {
        **meta,
        "major_issues": score["major"],
        "minor_issues": score["minor"],
        "p0_pass": score["counts"]["P0"]["pass"],
        "p0_total": score["counts"]["P0"]["total"],
        "p1_pass": score["counts"]["P1"]["pass"],
        "p1_total": score["counts"]["P1"]["total"],
        "p2_pass": score["counts"]["P2"]["pass"],
        "p2_total": score["counts"]["P2"]["total"],
    }
    append_result(row)
    try:
        write_leaderboard()
    except Exception as exc:  # noqa: BLE001 - the run result is already saved
        eprint(f"warning: could not update leaderboard: {exc}")
    print_summary(meta, score)
    return meta


def cmd_run(args: argparse.Namespace) -> int:
    config_dir = resolve_config_dir(
        args.config, RUNS_DIR / f"_models-{uuid.uuid4().hex[:8]}"
    )
    models = list_models(args.provider, config_dir)
    if not models:
        eprint("error: `opencode models` returned no models")
        return 1
    names = choose_model(models, args.model, args.provider)
    failed = 0
    for model in names:
        if args.variant == "all":
            variants: list[str | None] = [
                variant_id for variant_id, _, _ in ordered_variants(model)
            ]
            if not variants:
                variants = [None]
        else:
            variants = [choose_variant(model, args.variant)]
        for variant in variants:
            for rep in range(1, args.reps + 1):
                try:
                    run_one(model, rep=rep, args=args, variant=variant)
                except (Exception, SystemExit) as exc:
                    failed += 1
                    eprint(f"error: run failed for {model} variant={variant or 'default'}: {exc}")
                    if not args.keep_going:
                        return 1
    if failed:
        eprint(f"{failed} run(s) failed")
        return 1
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    problems: list[str] = []
    if not PROMPT_PATH.exists():
        problems.append(f"missing {PROMPT_PATH}")
    if not (SEED_DIR / "package.json").exists():
        problems.append(f"missing {SEED_DIR / 'package.json'}")
    if not (SEED_DIR / "package-lock.json").exists():
        problems.append(f"missing {SEED_DIR / 'package-lock.json'}")
    hidden = sorted(TESTS_DIR.glob("*.spec.ts"))
    if not hidden:
        problems.append(f"no hidden specs in {TESTS_DIR}")
    tier_counts = {tier: 0 for tier in EXPECTED}
    for path in hidden:
        text = path.read_text(encoding="utf-8", errors="replace")
        for tier in tier_counts:
            tier_counts[tier] += len(re.findall(rf'test\("\[{tier}\]', text))
    for tier, expected in EXPECTED.items():
        if tier_counts[tier] != expected:
            problems.append(
                f"hidden suite has {tier_counts[tier]} {tier} tests, expected {expected}"
            )
    if not engine_available(args.engine, args.dry_run):
        problems.append(f"{args.engine} is not available")
    elif not args.dry_run and not image_exists(args.engine, args.image):
        problems.append(f"image {args.image} missing; run ./bench.py build")

    config_dir = None
    models: list[str] = []
    try:
        config_dir = resolve_config_dir(
            args.config, RUNS_DIR / f"_check-{uuid.uuid4().hex[:8]}"
        )
        models = list_models(None, config_dir)
        if not models:
            problems.append("`opencode models` returned no models")
    except SystemExit as exc:
        problems.append(str(exc))

    versions = None
    if not problems and not args.dry_run:
        try:
            smoke = subprocess.run(
                [
                    args.engine,
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    args.image,
                    "bash",
                    "-lc",
                    "opencode --version && node --version && npx playwright --version",
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired:
            problems.append("image smoke timed out")
        else:
            if smoke.returncode != 0:
                problems.append(f"image smoke failed: {smoke.stdout}{smoke.stderr}")
            else:
                versions = smoke.stdout.strip().replace("\n", ", ")

    if problems:
        for problem in problems:
            eprint(f"error: {problem}")
        return 1

    print("ok")
    print(f"  image   {args.image}")
    print(f"  seed    {SEED_DIR}")
    counts_label = " / ".join(f"{tier} {tier_counts[tier]}" for tier in EXPECTED)
    print(f"  tests   {TESTS_DIR} ({sum(tier_counts.values())} tests: {counts_label})")
    print(f"  config  {config_dir}")
    print(f"  models  {len(models)}")
    if versions:
        print(f"  tools   {versions}")
    return 0


def cmd_rescore(args: argparse.Namespace) -> int:
    if args.run:
        run_dir = RUNS_DIR / args.run
    else:
        runs = [path for path in RUNS_DIR.iterdir() if (path / "workdir").is_dir()] if RUNS_DIR.exists() else []
        if not runs:
            eprint("error: no runs found")
            return 1
        run_dir = max(runs, key=lambda path: (path / "workdir").stat().st_mtime)
    score_path = run_dir / "score.json"
    if not score_path.exists():
        eprint(f"error: {score_path} missing; this run has no agent metadata")
        return 1
    meta = json.loads(score_path.read_text())
    meta.setdefault("variant", "default")
    if not meta.get("thinking") or meta.get("thinking") == "unknown":
        selected_variant = meta["variant"] if meta["variant"] != "default" else None
        meta["thinking"], meta["reasoning_effort"] = model_thinking(meta["model"], selected_variant)
    if (run_dir / "opencode-data").exists():
        meta["compactions"] = count_compactions(run_dir / "opencode-data")
    workdir = run_dir / "workdir"
    metrics = compute_metrics(
        run_dir / "agent.log",
        run_dir / "opencode-data",
        float(meta.get("wall_s") or 0),
    )
    meta.update(metrics)
    meta["steps"] = metrics["steps"]
    meta["files_changed"] = count_changed_files(workdir)
    meta["outcome"] = classify_outcome(
        watchdog=meta.get("watchdog", "exit"),
        completion=bool(meta.get("completion")),
        files_changed=meta["files_changed"],
        agent_ok=bool(meta.get("agent_ok")),
    )
    build = build_app(run_dir, workdir, engine=args.engine, image=args.image, dry_run=False)
    if not build["dist"]:
        eprint("error: build produced no dist/index.html")
        return 1
    tests = run_tests(run_dir, workdir, engine=args.engine, image=args.image, dry_run=False)
    score = score_run(tests["tests"], build, goal=meta.get("goal", "balanced"))
    meta.update(
        {
            "build_ok": build["build_ok"],
            "typecheck_ok": build["typecheck_ok"],
            "test_ok": tests["test_ok"],
            "test_error": tests["error"],
            "counts": score["counts"],
            "quality": score["quality"],
            "fatal": score["fatal"],
            "major": score["major"],
            "minor": score["minor"],
            "eligible": score["eligible"],
        }
    )
    write_run_report(run_dir, meta, score)
    score_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    row = {
        **meta,
        "major_issues": score["major"],
        "minor_issues": score["minor"],
        "p0_pass": score["counts"]["P0"]["pass"],
        "p0_total": score["counts"]["P0"]["total"],
        "p1_pass": score["counts"]["P1"]["pass"],
        "p1_total": score["counts"]["P1"]["total"],
        "p2_pass": score["counts"]["P2"]["pass"],
        "p2_total": score["counts"]["P2"]["total"],
    }
    rewrite_result(meta["run_id"], row)
    try:
        write_leaderboard()
    except Exception as exc:  # noqa: BLE001 - the score is already saved
        eprint(f"warning: could not update leaderboard: {exc}")
    print_summary(meta, score)
    print("  rescored with the current hidden suite")
    return 0


def refresh_run_report_metrics(run_dir: Path, meta: dict) -> None:
    """Replace or insert the Effort section in an existing per-run report."""
    path = run_dir / "report.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    block = "\n".join(render_effort_lines(meta)).rstrip()
    pattern = re.compile(r"\n## Effort\n.*?(?=\n## |\Z)", re.S)
    if pattern.search(text):
        text = pattern.sub("\n" + block + "\n", text, count=1)
    elif "\n## Tests\n" in text:
        text = text.replace("\n## Tests\n", "\n" + block + "\n\n## Tests\n", 1)
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    atomic_write_text(path, text)


def _backfill_row(meta: dict, existing: dict) -> dict:
    row = dict(existing)
    row.update(meta)
    row["major_issues"] = meta.get("major", 0)
    row["minor_issues"] = meta.get("minor", 0)
    counts = meta.get("counts") or {}
    for tier in ("P0", "P1", "P2"):
        tier_counts = counts.get(tier) or {}
        row[f"{tier.lower()}_pass"] = tier_counts.get("pass", row.get(f"{tier.lower()}_pass", ""))
        row[f"{tier.lower()}_total"] = tier_counts.get("total", row.get(f"{tier.lower()}_total", ""))
    return row


def cmd_backfill(args: argparse.Namespace) -> int:
    if args.run:
        run_dirs = [RUNS_DIR / args.run]
    elif args.all:
        run_dirs = (
            sorted(path for path in RUNS_DIR.iterdir() if (path / "score.json").exists())
            if RUNS_DIR.exists()
            else []
        )
    else:
        candidates = (
            [
                path
                for path in RUNS_DIR.iterdir()
                if path.is_dir() and (path / "agent.log").exists()
            ]
            if RUNS_DIR.exists()
            else []
        )
        run_dirs = (
            [max(candidates, key=lambda path: (path / "agent.log").stat().st_mtime)]
            if candidates
            else []
        )
    if not run_dirs:
        eprint("error: no runs found")
        return 1

    rows = {} if args.dry_run else {
        row.get("run_id"): row for row in load_result_rows()
    }
    changed = 0
    for run_dir in run_dirs:
        score_path = run_dir / "score.json"
        if not run_dir.is_dir() or not score_path.exists():
            eprint(f"warning: skipping {run_dir.name}: score.json missing")
            continue
        try:
            meta = json.loads(score_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            eprint(f"warning: skipping {run_dir.name}: {exc}")
            continue
        run_id = str(meta.get("run_id") or run_dir.name)
        metrics = compute_metrics(
            run_dir / "agent.log",
            run_dir / "opencode-data",
            float(meta.get("wall_s") or 0),
        )
        old_source = meta.get("metrics_source", "none")
        old_think = metric_value(meta, "think_share")
        meta.update(metrics)
        if meta.get("files_changed") in (None, ""):
            workdir = run_dir / "workdir"
            if workdir.is_dir():
                meta["files_changed"] = count_changed_files(workdir)
        if not meta.get("outcome"):
            meta["outcome"] = classify_outcome(
                watchdog=str(meta.get("watchdog") or "exit"),
                completion=bool(meta.get("completion")),
                files_changed=int(meta.get("files_changed") or 0),
                agent_ok=bool(meta.get("agent_ok")),
            )
        if args.dry_run:
            print(
                f"{run_id}\n"
                f"  source {old_source} -> {meta.get('metrics_source')}\n"
                f"  think  {format_percent(old_think)} -> "
                f"{format_percent(meta.get('think_share'))}\n"
                f"  request {format_tokens(meta.get('max_request_tokens'))} peak / "
                f"{format_tokens(meta.get('mean_request_tokens'))} mean tok\n"
                f"  tokens {format_tokens(meta.get('total_tokens'))} "
                f"({format_tokens(meta.get('reasoning_tokens'))} reasoning)\n"
                f"  generation {format_tokens(meta.get('gen_tokens_per_s'))} tok/s "
                f"(median {format_tokens(meta.get('gen_tokens_per_s_median'))})\n"
                f"  self-test {format_optional_duration(meta.get('self_test_s'))} "
                f"({meta.get('self_test_runs') or 0} runs) · "
                f"retries {meta.get('retries') or 0}"
            )
            continue

        atomic_write_text(score_path, json.dumps(meta, indent=2) + "\n")
        refresh_run_report_metrics(run_dir, meta)
        rewrite_result(run_id, _backfill_row(meta, rows.get(run_id, {})))
        rows[run_id] = _backfill_row(meta, rows.get(run_id, {}))
        changed += 1
        print(
            f"{run_id}  think {format_percent(meta.get('think_share'))}"
            f"  out {format_percent(meta.get('output_share'))}"
            f"  tools {format_percent(meta.get('tool_share'))}"
            f"  req {format_tokens(meta.get('max_request_tokens'))} peak"
            f"  {format_tokens(meta.get('gen_tokens_per_s'))} tok/s"
            f"  med {format_tokens(meta.get('gen_tokens_per_s_median'))}"
            f"  self-test {format_optional_duration(meta.get('self_test_s'))}"
            f"  retries {meta.get('retries') or 0}"
        )

    if not args.dry_run:
        try:
            write_leaderboard()
        except Exception as exc:  # noqa: BLE001 - metrics are already saved
            eprint(f"warning: could not update leaderboard: {exc}")
        print(f"backfilled {changed} run(s)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    runs = [
        path
        for path in RUNS_DIR.iterdir()
        if path.is_dir() and (path / "agent.log").exists()
    ] if RUNS_DIR.exists() else []
    if not runs:
        print("no runs yet")
        return 0
    run_dir = max(runs, key=lambda path: (path / "agent.log").stat().st_mtime)
    log_path = run_dir / "agent.log"
    first = ""
    try:
        first = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        pass

    # Old run logs still carry the pre-rename environment variable.
    model_match = re.search(r"(?:OPENCODE_BENCH|SPRITEBENCH)_MODEL=([^\s]+)", first)
    name_match = re.search(r"--name\s+(\S+)", first)
    model = model_match.group(1) if model_match else "?"
    container = name_match.group(1) if name_match else None

    print(f"run        {run_dir.name}")
    print(f"model      {model}")
    print(f"agent log  {log_path}")
    if container:
        ps = subprocess.run(
            [args.engine, "ps", "--filter", f"name={container}", "--format", "{{.Status}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        status = ps.stdout.strip() or "not running"
        print(f"container  {container}  {status}")

    if (run_dir / "COMPLETION.md").exists():
        print("completion COMPLETION.md present")
    if (run_dir / "score.json").exists():
        try:
            meta = json.loads((run_dir / "score.json").read_text())
            thinking = meta.get("thinking", "unknown")
            effort = meta.get("reasoning_effort") or ""
            print(
                f"score      quality={meta.get('quality', 0):.2f} "
                f"eligible={meta.get('eligible')} wall={format_duration(meta.get('wall_s', 0))} "
                f"compactions={meta.get('compactions', 0)} "
                f"outcome={meta.get('outcome', '?')} steps={meta.get('steps', 0)} "
                f"thinking={thinking}" + (f" ({effort})" if effort else "")
            )
        except json.JSONDecodeError:
            pass

    # Best-effort live engine status. This is optional and provider-specific;
    # a missing /status endpoint is not an error.
    try:
        provider = provider_of(model)
        proc = subprocess.run(
            [OPENCODE, "debug", "config"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        sources = json.loads(proc.stdout)
        info: dict = {}
        for source in sources:
            if isinstance(source.get("info"), dict):
                info.update(source["info"])
        providers = info.get("providers") or info.get("provider") or {}
        entry = providers.get(provider) or {}
        settings = entry.get("settings") or entry.get("options") or {}
        base = settings.get("baseURL")
        if base:
            root = re.sub(r"/v1/?$", "", str(base).rstrip("/"))
            try:
                with urllib.request.urlopen(f"{root}/status", timeout=3) as response:
                    payload = response.read().decode("utf-8", errors="replace")
                print(f"engine     {payload[:600]}")
            except Exception as exc:  # noqa: BLE001 - status is best effort
                print(f"engine     no /status endpoint ({exc})")
    except (json.JSONDecodeError, OSError, subprocess.TimeoutExpired):
        pass

    print("")
    print("agent log tail:")
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]
    for line in lines:
        print(line[:400])
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    if args.run:
        run_dir = RUNS_DIR / args.run
    else:
        runs = [
            path
            for path in RUNS_DIR.iterdir()
            if (path / "workdir" / "dist" / "index.html").exists()
        ] if RUNS_DIR.exists() else []
        if not runs:
            eprint("error: no built run found")
            return 1
        run_dir = max(runs, key=lambda path: (path / "workdir" / "dist").stat().st_mtime)
    dist = run_dir / "workdir" / "dist"
    if not (dist / "index.html").exists():
        eprint(f"error: {dist}/index.html missing; run ./bench.py rescore")
        return 1
    print(f"serving {dist}")
    print(f"open http://127.0.0.1:{args.port}/")
    print("Ctrl+C to stop")
    return subprocess.run(
        ["node", str(ROOT / "docker" / "serve.mjs"), str(dist), str(args.port)],
        check=False,
    ).returncode


def failed_tests(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    failures: list[str] = []

    def walk(suites: list) -> None:
        for suite in suites:
            for spec in suite.get("specs", []):
                if spec.get("ok") is False:
                    failures.append(str(spec.get("title", "")))
            walk(suite.get("suites", []))

    walk(data.get("suites", []))
    return failures


def _valid_result_rows(rows: list[dict]) -> list[dict]:
    valid: list[dict] = []
    for row in rows:
        if not row.get("run_id") or not row.get("model"):
            continue
        try:
            float(row.get("quality") or "")
            float(row.get("wall_s") or "")
        except (TypeError, ValueError):
            eprint(f"warning: skipping malformed results row {row.get('run_id')!r}")
            continue
        valid.append(row)
    return valid


def load_result_rows() -> list[dict]:
    with results_lock():
        rows = _read_result_rows_unlocked()
    return _valid_result_rows(rows)


def compaction_count(row: dict) -> int | None:
    value = row.get("compactions")
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    try:
        return max(0, int(float(value)))
    except ValueError:
        return None


def leaderboard_entries(rows: list[dict]) -> list[dict]:
    weights = LEADERBOARD_WEIGHTS
    parsed: list[dict] = []
    for row in rows:
        quality = max(0.0, min(1.0, float(row["quality"])))
        wall_total = max(0.0, float(row["wall_s"]))
        wall_work_value = metric_value(row, "wall_ex_self_test_s")
        wall_work = (
            max(0.0, wall_work_value)
            if wall_work_value is not None
            else wall_total
        )
        compactions = compaction_count(row)
        eligible = str(row.get("eligible", "")).lower() == "true"
        compaction_score = (
            1.0 / (1.0 + compactions) if compactions is not None else 0.0
        )
        parsed.append(
            {
                "row": row,
                "quality": quality,
                "wall_total": wall_total,
                "wall_work": wall_work,
                "compactions": compactions,
                "compaction_score": compaction_score,
                "eligible": eligible,
            }
        )

    def fastest_for(key: str) -> float:
        # Only eligible runs set the speed baseline. A fast failed run must not
        # make every real run score the same, which was the old behaviour.
        values = [
            item[key] for item in parsed if item["eligible"] and item[key] > 0
        ]
        if not values:
            values = [item[key] for item in parsed if item[key] > 0]
        return min(values) if values else 0.0

    fastest_total = fastest_for("wall_total")
    fastest_work = fastest_for("wall_work")

    def scored(item: dict, wall: float, fastest: float) -> tuple[float, float, float]:
        speed = min(1.0, fastest / wall) if wall > 0 and fastest > 0 else 0.0
        raw = 100.0 * (
            weights["quality"] * item["quality"]
            + weights["wall"] * speed
            + weights["compactions"] * item["compaction_score"]
        )
        score = raw * (
            1.0 if item["eligible"] else LEADERBOARD_INELIGIBLE_FACTOR
        )
        return speed, raw, score

    entries: list[dict] = []
    for item in parsed:
        row = item["row"]
        speed_total, raw_total, score_total = scored(
            item, item["wall_total"], fastest_total
        )
        speed_work, raw_work, score_work = scored(
            item, item["wall_work"], fastest_work
        )
        entries.append(
            {
                "row": row,
                "quality": item["quality"],
                "wall": item["wall_total"],
                "wall_total": item["wall_total"],
                "wall_work": item["wall_work"],
                "compactions": item["compactions"],
                "speed": speed_total,
                "speed_total": speed_total,
                "speed_work": speed_work,
                "raw_score": raw_total,
                "raw_score_work": raw_work,
                "score": score_total,
                "score_total": score_total,
                "score_work": score_work,
                "eligible": item["eligible"],
                "think_share": metric_value(row, "think_share"),
                "output_share": metric_value(row, "output_share"),
                "tool_share": metric_value(row, "tool_share"),
                "ttft_share": metric_value(row, "ttft_share"),
                "other_share": metric_value(row, "other_share"),
                "think_s": metric_value(row, "think_s"),
                "ttft_s": metric_value(row, "ttft_s"),
                "output_s": metric_value(row, "output_s"),
                "tool_s": metric_value(row, "tool_s"),
                "other_s": metric_value(row, "other_s"),
                "self_test_s": metric_value(row, "self_test_s"),
                "self_test_share": metric_value(row, "self_test_share"),
                "self_test_runs": metric_value(row, "self_test_runs"),
                "self_test_failures": metric_value(row, "self_test_failures"),
                "build_tool_s": metric_value(row, "build_tool_s"),
                "retries": metric_value(row, "retries"),
                "retry_s": metric_value(row, "retry_s"),
                "hardware": str(row.get("hardware") or "").strip(),
                "tokens": metric_value(row, "total_tokens"),
                "max_request_tokens": metric_value(row, "max_request_tokens"),
                "mean_request_tokens": metric_value(row, "mean_request_tokens"),
                "reasoning_tokens": metric_value(row, "reasoning_tokens"),
                "toks": metric_value(row, "gen_tokens_per_s"),
                "toks_median": metric_value(row, "gen_tokens_per_s_median"),
            }
        )
    entries.sort(
        key=lambda entry: (
            -entry["score_total"],
            -entry["quality"],
            entry["wall_total"],
            entry["compactions"] is None,
            entry["compactions"] or 0,
            entry["row"]["run_id"],
        )
    )
    return entries


def thinking_label(row: dict) -> str:
    thinking = row.get("thinking") or "unknown"
    effort = row.get("reasoning_effort") or ""
    return f"{thinking} ({effort})" if effort else thinking


def run_started(row: dict) -> datetime | None:
    """Parse the run's UTC start timestamp."""
    value = (row.get("timestamp") or "").strip()
    if not value:
        run_id = (row.get("run_id") or "").strip()
        if len(run_id) >= 16:
            value = run_id[:16]
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def started_label(row: dict) -> tuple[str, str]:
    """Return a short UTC start time and a full tooltip for the runner."""
    started = run_started(row)
    if started is None:
        return "—", ""
    return (
        started.strftime("%b %d %H:%M"),
        started.strftime("%Y-%m-%d %H:%M:%S UTC"),
    )


def reasoning_key(row: dict) -> str:
    return (
        row.get("reasoning_effort") or row.get("thinking") or "unknown"
    ).strip().lower() or "unknown"


def reasoning_rank(row: dict) -> int:
    return REASONING_ORDER.get(reasoning_key(row), -1)


def provider_label(row: dict) -> str:
    return str(
        row.get("provider") or str(row.get("model", "")).split("/", 1)[0] or "unknown"
    )


def _data_attr(value: object) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return ""


def _render_effort_cell(entry: dict, escaped) -> str:
    shares = [
        ("ttft", entry.get("ttft_share"), entry.get("ttft_s")),
        ("think", entry.get("think_share"), entry.get("think_s")),
        ("out", entry.get("output_share"), entry.get("output_s")),
        ("tool", entry.get("tool_share"), entry.get("tool_s")),
        ("other", entry.get("other_share"), entry.get("other_s")),
    ]
    if all(share is None for _, share, _ in shares):
        return '<span class="muted">—</span>'
    values = [max(0.0, float(share or 0)) for _, share, _ in shares]
    total = sum(values)
    if total <= 0:
        return '<span class="muted">—</span>'
    segments = "".join(
        f'<span class="seg {name}" style="width:{value / total * 100:.2f}%"></span>'
        for (name, _, _), value in zip(shares, values)
    )
    labels = ["TTFT", "thinking", "output", "tools", "other"]
    parts = []
    for label, (_, share, seconds) in zip(labels, shares):
        if share is None:
            continue
        parts.append(
            f"{label} {format_optional_duration(seconds)} ({format_percent(share)})"
        )
    title = escaped(" · ".join(parts))
    row = entry["row"]
    percentages = "".join(
        f'<span class="pct {name}">{format_percent(share)}</span>'
        for label, (name, share, _) in zip(labels, shares)
        if share is not None
    )
    meta_parts = []
    if entry.get("max_request_tokens") is not None:
        meta_parts.append(f"peak {format_tokens(entry['max_request_tokens'])} req tok")
    if entry.get("toks_median") is not None:
        meta_parts.append(f"{float(entry['toks_median']):.0f} tok/s med")
    elif entry.get("toks") is not None:
        meta_parts.append(f"{float(entry['toks']):.0f} tok/s")
    meta = (
        f'<span class="effort-meta">{" · ".join(meta_parts)}</span>'
        if meta_parts
        else ""
    )
    run_id = escaped(str(row.get("run_id", "")))
    return (
        f'<button type="button" class="detail-btn" aria-label="Effort details for {run_id}">'
        f'<span class="split" role="img" aria-label="{title}">{segments}</span>'
        f'<span class="effort-pcts">{percentages}</span>'
        f"{meta}"
        "</button>"
    )


def _render_model_medians(entries: list[dict], escaped) -> str:
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for entry in entries:
        row = entry["row"]
        key = (
            str(row.get("model", "")),
            str(row.get("variant") or "default"),
            thinking_label(row),
        )
        groups.setdefault(key, []).append(entry)
    if not groups:
        return ""

    def values(items: list[dict], key: str) -> list[float]:
        return [float(item[key]) for item in items if item.get(key) is not None]

    rows_html = []
    for (model, variant, thinking), items in sorted(groups.items()):
        runs = len(items)
        quality = median_or_none([item["quality"] for item in items])
        wall = median_or_none([item["wall"] for item in items])
        work = median_or_none(values(items, "wall_work"))
        think = median_or_none(values(items, "think_share"))
        output = median_or_none(values(items, "output_share"))
        tool = median_or_none(values(items, "tool_share"))
        selftest = median_or_none(values(items, "self_test_share"))
        tokens = median_or_none(values(items, "max_request_tokens"))
        reason = median_or_none(values(items, "reasoning_tokens"))
        toks = median_or_none(values(items, "toks_median"))
        if toks is None:
            toks = median_or_none(values(items, "toks"))
        retries = median_or_none(values(items, "retries"))
        retries_cell = (
            f'<td class="num">{retries:.0f}</td>'
            if retries is not None
            else '<td class="num">—</td>'
        )
        rows_html.append(
            "<tr>"
            f"<td>{escaped(model)}</td>"
            f"<td>{escaped(variant)}</td>"
            f"<td>{escaped(thinking)}</td>"
            f'<td class="num">{runs}</td>'
            f'<td class="num">{quality:.2f}</td>'
            f'<td class="num">{format_optional_duration(wall)}</td>'
            f'<td class="num">{format_optional_duration(work)}</td>'
            f'<td class="num">{format_percent(think)}</td>'
            f'<td class="num">{format_percent(output)}</td>'
            f'<td class="num">{format_percent(tool)}</td>'
            f'<td class="num">{format_percent(selftest)}</td>'
            f'<td class="num">{format_tokens(tokens)}</td>'
            f'<td class="num">{format_tokens(reason)}</td>'
            f'<td class="num">{format_tokens(toks)}</td>'
            f"{retries_cell}"
            "</tr>"
        )
    return (
        '<details class="medians"><summary>Model medians (all runs)</summary>'
        '<div class="table-wrap"><table class="medians-table"><thead><tr>'
        "<th>Model</th><th>Variant</th><th>Thinking</th><th>Runs</th>"
        "<th>Quality med</th><th>Wall med</th><th>Work med</th><th>Think% med</th>"
        "<th>Out% med</th><th>Tool% med</th><th>Self-test% med</th>"
        "<th>Peak req tok med</th><th>Reason tok med</th><th>tok/s med</th>"
        "<th>Retries med</th>"
        "</tr></thead><tbody>"
        + "".join(rows_html)
        + "</tbody></table></div></details>"
    )


def write_leaderboard() -> Path:
    with results_lock():
        return _write_leaderboard_unlocked()


def _write_leaderboard_unlocked() -> Path:
    rows = _valid_result_rows(_read_result_rows_unlocked())
    entries = leaderboard_entries(rows)
    escaped = html.escape

    providers = sorted({provider_label(entry["row"]) for entry in entries})
    reasoning_values = sorted(
        {reasoning_key(entry["row"]) for entry in entries},
        key=lambda value: (-REASONING_ORDER.get(value, -1), value),
    )
    provider_options = '<option value="">All providers</option>' + "".join(
        f'<option value="{escaped(provider)}">{escaped(provider)}</option>'
        for provider in providers
    )
    reasoning_options = '<option value="">All reasoning</option>' + "".join(
        f'<option value="{escaped(value)}">{escaped(value)}</option>'
        for value in reasoning_values
    )

    think_values = [
        float(entry["think_share"])
        for entry in entries
        if entry.get("think_share") is not None
    ]
    toks_values = [
        float(
            entry["toks_median"]
            if entry.get("toks_median") is not None
            else entry["toks"]
        )
        for entry in entries
        if entry.get("toks_median") is not None or entry.get("toks") is not None
    ]
    fastest_eligible = min(
        (entry["wall"] for entry in entries if entry["eligible"]), default=None
    )
    retries_total = sum(int(entry.get("retries") or 0) for entry in entries)
    cards = [
        ("Runs", str(len(entries))),
        ("Eligible", str(sum(1 for entry in entries if entry["eligible"]))),
        (
            "Best quality",
            f"{max((entry['quality'] for entry in entries), default=0.0):.2f}",
        ),
        (
            "Fastest eligible",
            format_duration(fastest_eligible) if fastest_eligible is not None else "—",
        ),
        (
            "Median tok/s",
            f"{median_or_none(toks_values):.0f}" if toks_values else "—",
        ),
        ("Retries", str(retries_total)),
        (
            "Median think",
            format_percent(median_or_none(think_values)) if think_values else "—",
        ),
    ]
    cards_html = "".join(
        f'<div class="card"><span>{escaped(label)}</span><b>{escaped(value)}</b></div>'
        for label, value in cards
    )

    rank_rows: list[str] = []
    for rank, entry in enumerate(entries, 1):
        row = entry["row"]
        quality = entry["quality"]
        quality_class = "high" if quality >= 0.99 else "mid" if quality >= 0.8 else "low"
        eligible = entry["eligible"]
        model = escaped(str(row.get("model", "")))
        variant = escaped(str(row.get("variant") or "default"))
        thinking = escaped(thinking_label(row))
        run_id = escaped(str(row.get("run_id", "")))
        started, started_full = started_label(row)
        started = escaped(started)
        provider = provider_label(row)
        reasoning = reasoning_key(row)
        effort_rank = reasoning_rank(row)
        started_dt = run_started(row)
        started_epoch = int(started_dt.timestamp()) if started_dt else 0
        p0_pass = int(row.get("p0_pass") or 0)
        p1_pass = int(row.get("p1_pass") or 0)
        p2_pass = int(row.get("p2_pass") or 0)
        counts_rank = p0_pass * 100 + p1_pass * 10 + p2_pass
        compactions = "—" if entry["compactions"] is None else str(entry["compactions"])
        hardware = escaped(entry.get("hardware") or "")
        hardware_line = (
            f'<div class="hardware">{hardware}</div>' if hardware else ""
        )
        self_test_s = entry.get("self_test_s")
        self_test_runs = int(entry.get("self_test_runs") or 0)
        retries = int(entry.get("retries") or 0)
        wall_sub_parts = [f"work {format_duration(entry['wall_work'])}"]
        if self_test_s is not None:
            wall_sub_parts.append(
                f"test {format_optional_duration(self_test_s)} · {self_test_runs} runs"
            )
        if retries:
            wall_sub_parts.append(f"{retries} retr{'y' if retries == 1 else 'ies'}")
        wall_sub = " · ".join(wall_sub_parts)
        wall_title = (
            f"total {format_duration(entry['wall_total'])}; "
            f"excluding self-test {format_duration(entry['wall_work'])}; "
            f"self-test {format_optional_duration(self_test_s)}"
            + (f"; provider retries {retries}" if retries else "")
        )
        score_note = (
            ""
            if eligible
            else (
                f' title="raw total {entry["raw_score"]:.1f}, raw work '
                f'{entry["raw_score_work"]:.1f}; ineligible score '
                f'×{LEADERBOARD_INELIGIBLE_FACTOR:g}"'
            )
        )
        counts = " · ".join(
            f'{escaped(str(row.get(f"p{tier}_pass") or "0"))}/{escaped(str(row.get(f"p{tier}_total") or "0"))}'
            for tier in (0, 1, 2)
        )
        search = escaped(
            " ".join(
                [
                    str(row.get("model", "")),
                    str(row.get("variant", "")),
                    thinking_label(row),
                    started,
                    str(row.get("run_id", "")),
                ]
            ).lower()
        )
        think_share = entry.get("think_share")
        tokens_total = entry.get("tokens")
        toks = entry.get("toks")
        effort_cell = _render_effort_cell(entry, escaped)
        rank_rows.append(
            f'<tr class="{"ineligible" if not eligible else ""}" data-search="{search}"'
            f' data-provider="{escaped(provider)}" data-reasoning="{escaped(reasoning)}"'
            f' data-model="{escaped(str(row.get("model", "")))}"'
            f' data-started="{started_epoch}"'
            f' data-variant="{escaped(str(row.get("variant") or "default"))}"'
            f' data-thinking="{effort_rank}" data-quality="{quality:.6f}"'
            f' data-counts="{counts_rank}" data-wall="{entry["wall"]:.6f}"'
            f' data-wall-total="{entry["wall_total"]:.6f}"'
            f' data-wall-work="{entry["wall_work"]:.6f}"'
            f' data-score-total="{entry["score_total"]:.6f}"'
            f' data-score-work="{entry["score_work"]:.6f}"'
            f' data-speed-total="{entry["speed_total"]:.6f}"'
            f' data-speed-work="{entry["speed_work"]:.6f}"'
            f' data-raw-total="{entry["raw_score"]:.6f}"'
            f' data-raw-work="{entry["raw_score_work"]:.6f}"'
            f' data-self-test="{_data_attr(entry.get("self_test_s"))}"'
            f' data-self-test-runs="{_data_attr(entry.get("self_test_runs"))}"'
            f' data-self-test-failures="{_data_attr(entry.get("self_test_failures"))}"'
            f' data-build-tool="{_data_attr(entry.get("build_tool_s"))}"'
            f' data-retries="{_data_attr(entry.get("retries"))}"'
            f' data-retry-s="{_data_attr(entry.get("retry_s"))}"'
            f' data-hardware="{hardware}"'
            f' data-thinkpct="{_data_attr(think_share * 100 if think_share is not None else None)}"'
            f' data-tokens="{_data_attr(tokens_total)}"'
            f' data-tokens-max="{_data_attr(row.get("max_request_tokens"))}"'
            f' data-tokens-mean="{_data_attr(row.get("mean_request_tokens"))}"'
            f' data-toks="{_data_attr(toks)}"'
            f' data-toks-med="{_data_attr(entry.get("toks_median"))}"'
            f' data-agent="{_data_attr(row.get("agent_s"))}"'
            f' data-ttft="{_data_attr(row.get("ttft_s"))}"'
            f' data-think="{_data_attr(row.get("think_s"))}"'
            f' data-output="{_data_attr(row.get("output_s"))}"'
            f' data-tool="{_data_attr(row.get("tool_s"))}"'
            f' data-other="{_data_attr(row.get("other_s"))}"'
            f' data-tokens-in="{_data_attr(row.get("input_tokens"))}"'
            f' data-tokens-out="{_data_attr(row.get("output_tokens"))}"'
            f' data-tokens-reason="{_data_attr(row.get("reasoning_tokens"))}"'
            f' data-tokens-cache="{_data_attr(row.get("cache_read_tokens"))}"'
            f' data-tokens-cache-write="{_data_attr(row.get("cache_write_tokens"))}"'
            f' data-tokens-compaction="{_data_attr(row.get("compaction_tokens"))}"'
            f' data-step-median="{_data_attr(row.get("step_median_s"))}"'
            f' data-step-p90="{_data_attr(row.get("step_p90_s"))}"'
            f' data-step-max="{_data_attr(row.get("step_max_s"))}"'
            f' data-steps="{_data_attr(row.get("steps"))}"'
            f' data-files="{_data_attr(row.get("files_changed"))}"'
            f' data-tokens-step="{_data_attr(row.get("tokens_per_step"))}"'
            f' data-cache-rate="{_data_attr(row.get("cache_hit_rate"))}"'
            f' data-cost="{_data_attr(row.get("cost_usd"))}"'
            f' data-source="{escaped(str(row.get("metrics_source") or ""))}"'
            f' data-compactions="{entry["compactions"] if entry["compactions"] is not None else 0}"'
            f' data-eligible="{1 if eligible else 0}"'
            f' data-score="{entry["score"]:.6f}" data-run="{run_id}">'
            f'<td class="rank">{rank}</td>'
            f'<td><div class="score"{score_note}><span class="bar" style="width:{max(0.0, min(100.0, entry["score"])):.1f}%"></span>'
            f'<b>{entry["score"]:.1f}</b></div></td>'
            f'<td class="model"><div>{model}</div>{hardware_line}<div class="runid">{run_id}</div></td>'
            f'<td class="started" title="{escaped(started_full)}">{started}</td>'
            f'<td>{variant}</td>'
            f'<td>{thinking}</td>'
            f'<td><span class="quality {quality_class}">{quality:.2f}</span></td>'
            f'<td class="counts">{counts}</td>'
            f'<td class="wall" title="{escaped(wall_title)}">{format_duration(entry["wall"])}'
            f'<div class="wall-sub">{escaped(wall_sub)}</div></td>'
            f'<td class="effort">{effort_cell}</td>'
            f'<td class="num">{compactions}</td>'
            f'<td>{"yes" if eligible else "no"}</td>'
            f"</tr>"
        )

    if not entries:
        table = '<p class="empty">No runs yet. Start with <code>./bench.py run --model ...</code>.</p>'
    else:
        table = (
            '<div class="table-wrap"><table>'
            "<thead><tr>"
            "<th>#</th>"
            '<th data-sort="score" data-type="number" data-default-dir="desc">Score</th>'
            '<th data-sort="model" data-type="text" data-default-dir="asc">Model / run</th>'
            '<th data-sort="started" data-type="number" data-default-dir="desc">Started (UTC)</th>'
            '<th data-sort="variant" data-type="text" data-default-dir="asc">Variant</th>'
            '<th data-sort="thinking" data-type="number" data-default-dir="desc">Thinking</th>'
            '<th data-sort="quality" data-type="number" data-default-dir="desc">Quality</th>'
            '<th data-sort="counts" data-type="number" data-default-dir="desc">P0 · P1 · P2</th>'
            '<th data-sort="wall" data-type="number" data-default-dir="asc">Wall / work</th>'
            '<th>Effort</th>'
            '<th data-sort="compactions" data-type="number" data-default-dir="asc">Compactions</th>'
            '<th data-sort="eligible" data-type="number" data-default-dir="desc">Eligible</th>'
            "</tr></thead><tbody>"
            + "".join(rank_rows)
            + "</tbody></table></div>"
        )
        table += _render_model_medians(entries, escaped)

    updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    weights = LEADERBOARD_WEIGHTS
    css = """
:root { color-scheme: dark; --bg:#0f1117; --panel:#171a23; --line:#272c3a;
        --text:#e7ebf5; --muted:#8b93a7; --accent:#4a90d9; --good:#3ecf8e;
        --warn:#f0b429; --bad:#ef5b5b; --ttft:#5b6478; --think:#8b7cf6;
        --out:#3ecf8e; --tool:#f0b429; --other:#3a4050; }
* { box-sizing: border-box; }
body { margin:0; padding:32px; background:var(--bg); color:var(--text);
       font:15px/1.5 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
header { display:flex; gap:24px; align-items:flex-end; justify-content:space-between;
         flex-wrap:wrap; margin-bottom:20px; }
h1 { margin:0; font-size:28px; letter-spacing:-.02em; }
.sub { margin:6px 0 0; color:var(--muted); }
.cards { display:flex; gap:10px; flex-wrap:wrap; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:10px;
        padding:10px 14px; min-width:110px; }
.card span { display:block; color:var(--muted); font-size:12px; text-transform:uppercase;
             letter-spacing:.06em; }
.card b { font-size:20px; font-variant-numeric:tabular-nums; }
.formula { background:var(--panel); border:1px solid var(--line); border-radius:10px;
           padding:12px 16px; color:var(--muted); margin-bottom:18px; }
.formula b { color:var(--text); }
.toolbar { display:flex; gap:12px; align-items:center; margin:0 0 14px; flex-wrap:wrap; }
input[type="search"] { background:var(--panel); border:1px solid var(--line); color:var(--text);
                       border-radius:8px; padding:9px 12px; min-width:280px; }
.toolbar label { color:var(--muted); }
select { background:var(--panel); border:1px solid var(--line); color:var(--text);
         border-radius:8px; padding:9px 12px; font:inherit; }
button { background:var(--accent); border:0; color:#fff; border-radius:8px; padding:9px 14px;
         font:inherit; cursor:pointer; }
.table-wrap { overflow-x:auto; border:1px solid var(--line); border-radius:12px; }
table { width:100%; border-collapse:collapse; min-width:1350px; }
thead th { position:sticky; top:0; background:#1b1f2b; color:var(--muted); font-size:12px;
           text-transform:uppercase; letter-spacing:.05em; text-align:left; padding:12px 14px;
           border-bottom:1px solid var(--line); white-space:nowrap; }
thead th[data-sort] { cursor:pointer; user-select:none; }
thead th[data-sort]:hover { color:var(--text); }
thead th[data-sort]::after { content:"↕"; opacity:.35; margin-left:6px; font-size:10px; }
thead th[data-sort][data-dir="asc"]::after { content:"▲"; opacity:1; }
thead th[data-sort][data-dir="desc"]::after { content:"▼"; opacity:1; }
tbody td { padding:12px 14px; border-bottom:1px solid var(--line); vertical-align:middle; }
tbody tr:last-child td { border-bottom:0; }
tbody tr:hover { background:#1b1f2b; }
tr.ineligible { opacity:.55; }
.rank { font-size:18px; font-weight:700; color:var(--muted); }
tbody tr:nth-child(1) .rank { color:var(--warn); }
tbody tr:nth-child(2) .rank { color:#c9d1e3; }
tbody tr:nth-child(3) .rank { color:#b07d4f; }
.score { position:relative; min-width:120px; height:24px; display:flex; align-items:center; }
.score .bar { position:absolute; inset:0 auto 0 0; background:linear-gradient(90deg,#2f6db3,#4a90d9);
              border-radius:6px; opacity:.55; }
.score b { position:relative; padding-left:8px; font-variant-numeric:tabular-nums; }
.model div:first-child { font-weight:600; }
.runid { color:var(--muted); font:11px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
         word-break:break-all; max-width:360px; }
.started { color:var(--muted); white-space:nowrap; font-variant-numeric:tabular-nums; }
.counts, .wall, .num { font-variant-numeric:tabular-nums; white-space:nowrap; }
.wall-sub { color:var(--muted); font-size:11px; margin-top:2px; white-space:nowrap; }
.hardware { color:var(--muted); font-size:11px; margin-top:2px; max-width:360px;
            overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.retry-badge { display:inline-block; margin-left:6px; padding:1px 6px; border-radius:999px;
               background:rgba(240,180,41,.15); color:var(--warn); font-size:10px; }
.quality { display:inline-block; padding:2px 8px; border-radius:999px; font-weight:700;
           font-variant-numeric:tabular-nums; }
.quality.high { background:rgba(62,207,142,.15); color:var(--good); }
.quality.mid { background:rgba(240,180,41,.15); color:var(--warn); }
.quality.low { background:rgba(239,91,91,.15); color:var(--bad); }
.empty { color:var(--muted); }
.muted { color:var(--muted); }
.detail-btn { background:transparent; border:0; padding:0; color:inherit; text-align:left;
              cursor:pointer; }
.detail-btn:hover .split { filter:brightness(1.18); }
.split { display:flex; width:140px; height:10px; border-radius:999px; overflow:hidden;
         background:#222736; }
.seg { display:block; height:100%; }
.seg.ttft { background:var(--ttft); }
.seg.think { background:var(--think); }
.seg.out { background:var(--out); }
.seg.tool { background:var(--tool); }
.seg.other { background:var(--other); }
.effort { min-width:170px; }
.effort-pcts { display:flex; gap:7px; margin-top:4px; font-size:11px;
               font-variant-numeric:tabular-nums; }
.pct { cursor:help; }
.pct.ttft { color:#9aa4b8; }
.pct.think { color:#b8adff; }
.pct.out { color:#7ee2b5; }
.pct.tool { color:#ffd166; }
.pct.other { color:#8b93a7; }
.effort-meta { display:block; margin-top:2px; color:var(--muted); font-size:11px;
               white-space:nowrap; font-variant-numeric:tabular-nums; }
.medians { margin-top:18px; background:var(--panel); border:1px solid var(--line);
           border-radius:12px; padding:10px 14px; }
.medians summary { cursor:pointer; color:var(--muted); font-weight:600; }
.medians .table-wrap { margin-top:10px; border:0; }
.medians-table { min-width:1000px; }
.legend { display:flex; gap:14px; flex-wrap:wrap; margin-top:8px; font-size:12px;
          color:var(--muted); }
.legend i { display:inline-block; width:10px; height:10px; border-radius:3px;
            margin-right:5px; vertical-align:-1px; }
dialog.effort-dialog { background:var(--panel); color:var(--text); border:1px solid var(--line);
                       border-radius:14px; padding:0; max-width:640px; width:calc(100% - 32px); }
dialog.effort-dialog::backdrop { background:rgba(0,0,0,.55); }
.effort-dialog header { display:flex; justify-content:space-between; align-items:center;
                        padding:16px 20px; border-bottom:1px solid var(--line);
                        margin:0; gap:12px; flex-wrap:nowrap; }
.effort-dialog h2 { margin:0; font-size:18px; min-width:0; overflow-wrap:anywhere; }
.effort-dialog header form { margin:0; flex:0 0 auto; }
.effort-dialog .close { background:transparent; color:var(--muted); padding:2px 8px;
                        font-size:22px; line-height:1; }
.effort-dialog .body { padding:16px 20px 20px; display:grid;
                       grid-template-columns:1fr 1fr; gap:16px 24px; font-size:14px; }
.effort-dialog h3 { margin:0 0 6px; color:var(--muted); font-size:12px;
                    text-transform:uppercase; letter-spacing:.06em; }
.effort-dialog dl { margin:0; display:grid; grid-template-columns:auto 1fr; gap:4px 12px; }
.effort-dialog dt { color:var(--muted); }
.effort-dialog dd { margin:0; text-align:right; font-variant-numeric:tabular-nums; }
.effort-note { grid-column:1/-1; color:var(--warn); font-size:12px; }
.hover-tip { position:fixed; z-index:1000; max-width:300px; padding:7px 10px;
             background:#1b1f2b; color:var(--text); border:1px solid var(--line);
             border-radius:7px; box-shadow:0 8px 24px rgba(0,0,0,.35);
             font-size:12px; line-height:1.4; pointer-events:none; }
.hover-tip.effort-tip { max-width:440px; padding:10px 12px; }
.tip-title { font-weight:600; margin-bottom:6px; }
.tip-table { border-collapse:collapse; width:100%; min-width:0; font-size:12px; }
.tip-table td { padding:2px 7px 2px 0; white-space:nowrap; vertical-align:middle; }
.tip-table td.num { text-align:right; font-variant-numeric:tabular-nums; }
.tip-table .tip-tokens { color:var(--muted); }
.tip-table .dot { display:inline-block; width:8px; height:8px; border-radius:2px;
                  margin-right:6px; vertical-align:0; }
.dot.ttft { background:var(--ttft); }
.dot.think { background:var(--think); }
.dot.out { background:var(--out); }
.dot.tool { background:var(--tool); }
.dot.other { background:var(--other); }
.tip-footer { margin-top:6px; padding-top:6px; border-top:1px solid var(--line);
              color:var(--muted); font-variant-numeric:tabular-nums; }
.tip-note { margin-top:4px; color:var(--warn); }
footer { color:var(--muted); margin-top:18px; font-size:13px; }
code { background:#1b1f2b; border:1px solid var(--line); border-radius:5px; padding:1px 5px; }
@media (max-width:700px) { body { padding:18px; } h1 { font-size:22px; } }
"""
    text = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>opencode-bench leaderboard</title>
<style>{css}</style>
</head>
<body>
<header>
  <div>
    <h1>opencode-bench leaderboard</h1>
    <p class="sub">Build-and-play benchmark · updated {escaped(updated)}</p>
  </div>
  <div class="cards">{cards_html}</div>
</header>
<div class="formula">
  <b>Score = {weights["quality"]:.0%} quality + {weights["wall"]:.0%} speed + {weights["compactions"]:.0%} compactions.</b>
  Speed is relative to the fastest <b>eligible</b> run
  (<code>fastest eligible wall / run wall</code>), capped at 1.0;
  compactions score as <code>1 / (1 + count)</code>. Runs that fail eligibility
  keep {LEADERBOARD_INELIGIBLE_FACTOR:.0%} of their score, so they fall below
  quality runs (dimmed; hover the score for the raw value). The default sort is
  score, highest first. Use <b>Wall basis</b> to score total wall or wall
  excluding self-test execution; each basis uses its own eligible baseline.
  Click a column header to sort by it and click again to reverse. Use the
  Provider and Reasoning filters to narrow the table. Missing compaction data
  counts as zero; provider retries are excluded from tok/s and shown separately.
  <div class="legend">
    <span><i style="background:var(--ttft)"></i>TTFT / prefill</span>
    <span><i style="background:var(--think)"></i>thinking</span>
    <span><i style="background:var(--out)"></i>model output / code</span>
    <span><i style="background:var(--tool)"></i>tool execution</span>
    <span><i style="background:var(--other)"></i>startup / gaps</span>
  </div>
  <p style="margin:8px 0 0">Effort is derived from <code>opencode.db</code> timings,
  not from a model proxy. Request tokens are the per-request prompt size
  (input + cache reads/writes); peak and mean are shown rather than cumulative
  run totals, which can exceed the context window. Token counts are
  provider-reported; a thinking phase with <b>0 reasoning tokens</b> means the
  provider did not report them, not that the model did not think. Hover the
  effort cell for the breakdown, or click for step distribution, cache hit rate
  and cost. tok/s is hardware-dependent.</p>
</div>
<div class="toolbar">
  <input id="filter" type="search" placeholder="Filter model, variant, thinking or run id">
  <label for="filter-provider">Provider</label>
  <select id="filter-provider">{provider_options}</select>
  <label for="filter-reasoning">Reasoning</label>
  <select id="filter-reasoning">{reasoning_options}</select>
  <label for="filter-wall-basis">Wall basis</label>
  <select id="filter-wall-basis">
    <option value="total">Total wall</option>
    <option value="work">Excl. self-test</option>
  </select>
  <button type="button" onclick="location.reload()">Refresh</button>
</div>
{table}
<dialog id="effort-dialog" class="effort-dialog">
  <header>
    <h2 id="effort-title">Effort details</h2>
    <form method="dialog"><button class="close" aria-label="Close">×</button></form>
  </header>
  <div class="body" id="effort-body"></div>
</dialog>
<div id="hover-tip" class="hover-tip" hidden></div>
<footer>
  Generated by <code>./bench.py leaderboard</code>. That command serves the page
  and opens it in the browser; use <code>--write</code> to write the file only.
  Raw data: <code>results.csv</code>. Effort/token fields can be rebuilt from
  existing run artifacts with <code>./bench.py backfill --all</code>.
</footer>
<script>
const search = document.getElementById('filter');
const providerFilter = document.getElementById('filter-provider');
const reasoningFilter = document.getElementById('filter-reasoning');
const wallBasisFilter = document.getElementById('filter-wall-basis');
const tableBody = document.querySelector('tbody');
const SORT_STATE_KEY = 'opencode-bench-sort-state';
const FILTER_KEY = 'opencode-bench-filters';
const WALL_BASIS_KEY = 'opencode-bench-wall-basis';
let sortKey = 'score';
let sortDir = 'desc';
let wallBasis = 'total';
try {{
  const savedSort = JSON.parse(localStorage.getItem(SORT_STATE_KEY) || '{{}}');
  if (savedSort.key && document.querySelector(`th[data-sort="${{savedSort.key}}"]`)) {{
    sortKey = savedSort.key;
    sortDir = savedSort.dir === 'asc' ? 'asc' : 'desc';
  }}
  const savedFilters = JSON.parse(localStorage.getItem(FILTER_KEY) || '{{}}');
  if (savedFilters.provider && providerFilter) providerFilter.value = savedFilters.provider;
  if (savedFilters.reasoning && reasoningFilter) reasoningFilter.value = savedFilters.reasoning;
  const savedBasis = localStorage.getItem(WALL_BASIS_KEY);
  if (savedBasis === 'work' || savedBasis === 'total') wallBasis = savedBasis;
  if (wallBasisFilter) wallBasisFilter.value = wallBasis;
}} catch (error) {{}}

function saveFilters() {{
  try {{
    localStorage.setItem(FILTER_KEY, JSON.stringify({{
      provider: providerFilter?.value || '',
      reasoning: reasoningFilter?.value || ''
    }}));
  }} catch (error) {{}}
}}

function applyWallBasis() {{
  if (!tableBody) return;
  const work = wallBasis === 'work';
  for (const row of tableBody.querySelectorAll('tr')) {{
    const score = Number((work ? row.dataset.scoreWork : row.dataset.scoreTotal) || 0);
    row.dataset.score = score.toFixed(6);
    const value = row.querySelector('.score b');
    const bar = row.querySelector('.score .bar');
    if (value) value.textContent = score.toFixed(1);
    if (bar) bar.style.width = `${{Math.max(0, Math.min(100, score)).toFixed(1)}}%`;
  }}
  if (sortKey === 'score') sortRows();
  applyView();
}}

function updateRanks() {{
  if (!tableBody) return;
  let rank = 0;
  for (const row of tableBody.querySelectorAll('tr')) {{
    const cell = row.querySelector('.rank');
    if (row.style.display === 'none') {{
      if (cell) cell.textContent = '';
    }} else {{
      rank += 1;
      if (cell) cell.textContent = String(rank);
    }}
  }}
}}

function applyView() {{
  if (!tableBody) return;
  const needle = (search?.value || '').trim().toLowerCase();
  const provider = providerFilter?.value || '';
  const reasoning = reasoningFilter?.value || '';
  for (const row of tableBody.querySelectorAll('tr')) {{
    const ok = (!needle || row.dataset.search.includes(needle))
      && (!provider || row.dataset.provider === provider)
      && (!reasoning || row.dataset.reasoning === reasoning);
    row.style.display = ok ? '' : 'none';
  }}
  updateRanks();
}}

function sortRows() {{
  if (!tableBody) return;
  const header = document.querySelector(`th[data-sort="${{sortKey}}"]`);
  const type = header?.dataset.type || 'text';
  const direction = sortDir === 'asc' ? 1 : -1;
  const rows = [...tableBody.querySelectorAll('tr')];
  rows.sort((a, b) => {{
    let av;
    let bv;
    let aMissing = false;
    let bMissing = false;
    if (type === 'number') {{
      const rawA = a.dataset[sortKey];
      const rawB = b.dataset[sortKey];
      aMissing = rawA === undefined || rawA === '';
      bMissing = rawB === undefined || rawB === '';
      av = aMissing ? 0 : Number(rawA);
      bv = bMissing ? 0 : Number(rawB);
    }} else {{
      av = (a.dataset[sortKey] || '').toLowerCase();
      bv = (b.dataset[sortKey] || '').toLowerCase();
    }}
    if (type === 'number') {{
      if (aMissing && !bMissing) return 1;
      if (bMissing && !aMissing) return -1;
    }}
    if (av < bv) return -1 * direction;
    if (av > bv) return direction;
    const score = Number(b.dataset.score || 0) - Number(a.dataset.score || 0);
    if (score) return score;
    return (a.dataset.run || '').localeCompare(b.dataset.run || '');
  }});
  rows.forEach((row) => tableBody.appendChild(row));
  for (const th of document.querySelectorAll('th[data-sort]')) {{
    th.removeAttribute('data-dir');
    th.removeAttribute('aria-sort');
  }}
  if (header) {{
    header.dataset.dir = sortDir;
    header.setAttribute('aria-sort', sortDir === 'asc' ? 'ascending' : 'descending');
  }}
  updateRanks();
}}

for (const header of document.querySelectorAll('th[data-sort]')) {{
  header.addEventListener('click', () => {{
    const key = header.dataset.sort;
    if (key === sortKey) {{
      sortDir = sortDir === 'asc' ? 'desc' : 'asc';
    }} else {{
      sortKey = key;
      sortDir = header.dataset.defaultDir || 'asc';
    }}
    try {{
      localStorage.setItem(SORT_STATE_KEY, JSON.stringify({{key: sortKey, dir: sortDir}}));
    }} catch (error) {{}}
    sortRows();
    applyView();
  }});
}}

const effortDialog = document.getElementById('effort-dialog');
const effortBody = document.getElementById('effort-body');
const effortTitle = document.getElementById('effort-title');

function fmtSec(value) {{
  const number = Number(value);
  if (value === undefined || value === '' || Number.isNaN(number)) return '—';
  const total = Math.max(0, Math.round(number));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return `${{String(hours).padStart(2, '0')}}:${{String(minutes).padStart(2, '0')}}:${{String(seconds).padStart(2, '0')}}`;
}}

function fmtPct(value) {{
  const number = Number(value);
  if (value === undefined || value === '' || Number.isNaN(number)) return '—';
  return `${{(number * 100).toFixed(0)}}%`;
}}

function fmtTok(value) {{
  const number = Number(value);
  if (value === undefined || value === '' || Number.isNaN(number)) return '—';
  if (number >= 1e9) return `${{(number / 1e9).toFixed(1).replace(/\\.0$/, '')}}B`;
  if (number >= 1e6) return `${{(number / 1e6).toFixed(1).replace(/\\.0$/, '')}}M`;
  if (number >= 1e3) return `${{(number / 1e3).toFixed(1).replace(/\\.0$/, '')}}k`;
  return String(Math.round(number));
}}

function fmtNum(value, digits = 0) {{
  const number = Number(value);
  if (value === undefined || value === '' || Number.isNaN(number)) return '—';
  return number.toFixed(digits);
}}

function esc(value) {{
  return String(value).replace(/[&<>"']/g, (character) => ({{
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }}[character]));
}}

function detailList(items) {{
  return items.map(([label, value]) => `<dt>${{esc(label)}}</dt><dd>${{value}}</dd>`).join('');
}}

function effortBodyHtml(row) {{
  const data = row.dataset;
  const note = Number(data.tokensReason || 0) === 0 && Number(data.think || 0) > 60
    ? '<p class="effort-note">Provider reported zero reasoning tokens; the time split still shows the thinking phase.</p>'
    : '';
  const outputNote = Number(data.output || 0) < 1 && Number(data.tokensOut || 0) > 1000
    ? '<p class="effort-note">No separate timestamped output block was recorded; output tokens may be inside the reasoning stream, so output time is a lower bound.</p>'
    : '';
  const retryNote = Number(data.retries || 0) > 0
    ? '<p class="effort-note">Provider errors/retries are counted in wall time and other, but excluded from think/output and tok/s.</p>'
    : '';
  return `
    <div><h3>Time</h3><dl>${{detailList([
      ['Wall', fmtSec(data.wall)],
      ['Wall excl. self-test', fmtSec(Number(data.wall || 0) - Number(data.selfTest || 0))],
      ['Agent turns', fmtSec(data.agent)],
      ['TTFT / prefill', fmtSec(data.ttft)],
      ['Thinking', fmtSec(data.think)],
      ['Model output', fmtSec(data.output)],
      ['Tool execution', fmtSec(data.tool)],
      ['Self-test', fmtSec(data.selfTest)],
      ['Build / typecheck', fmtSec(data.buildTool)],
      ['Provider retry', fmtSec(data.retryS)],
      ['Other', fmtSec(data.other)]
    ])}}</dl></div>
    <div><h3>Request tokens</h3><dl>${{detailList([
      ['Peak request', fmtTok(data.tokensMax)],
      ['Mean request', fmtTok(data.tokensMean)]
    ])}}</dl></div>
    <div><h3>Steps</h3><dl>${{detailList([
      ['Steps', fmtNum(data.steps)],
      ['Files changed', fmtNum(data.files)],
      ['Median step', fmtSec(data.stepMedian)],
      ['p90 step', fmtSec(data.stepP90)],
      ['Max step', fmtSec(data.stepMax)]
    ])}}</dl></div>
    <div><h3>Rates</h3><dl>${{detailList([
      ['Generation tok/s', fmtNum(data.toks, 1)],
      ['Median step tok/s', fmtNum(data.toksMed, 1)],
      ['Self-test runs', fmtNum(data.selfTestRuns)],
      ['Self-test failures', fmtNum(data.selfTestFailures)],
      ['Retries', fmtNum(data.retries)],
      ['Tokens / step', fmtTok(data.tokensStep)],
      ['Cache hit rate', fmtPct(data.cacheRate)],
      ['Cost', fmtNum(data.cost, 4)]
    ])}}</dl></div>
    ${{note}}
    ${{outputNote}}
    ${{retryNote}}
    <p class="effort-note">Source: ${{esc(data.source || 'none')}}. tok/s excludes failed/retried attempts; aggregate is output+reasoning over think+output time, median is per step. Hardware-dependent.</p>
  `;
}}

function effortTipHtml(row) {{
  const data = row.dataset;
  const wall = Number(data.wall || 0);
  const share = (value) => wall > 0 ? `${{Math.round(Number(value || 0) / wall * 100)}}%` : '—';
  const reasoning = Number(data.tokensReason || 0);
  const thinkTokens = reasoning
    ? `${{fmtTok(reasoning)}} reasoning`
    : (Number(data.think || 0) > 0 ? 'not reported' : '—');
  const compaction = Number(data.tokensCompaction || 0);
  const rows = [
    ['ttft', 'TTFT', data.ttft, '—'],
    ['think', 'Thinking', data.think, thinkTokens],
    ['out', 'Output', data.output, `${{fmtTok(data.tokensOut)}} output tok`],
    ['tool', 'Tools', data.tool, 'no model tokens'],
    ['other', 'Other', data.other, compaction ? `${{fmtTok(compaction)}} compaction tok` : 'no model tokens']
  ];
  const rowsHtml = rows.map(([kind, label, seconds, tokens]) => (
    `<tr><td><i class="dot ${{kind}}"></i>${{esc(label)}}</td>`
    + `<td class="num">${{fmtSec(seconds)}}</td>`
    + `<td class="num">${{share(seconds)}}</td>`
    + `<td class="tip-tokens">${{tokens}}</td></tr>`
  )).join('');
  const caveat = reasoning === 0 && Number(data.think || 0) > 60 && Number(data.tokensOut || 0) > 1000
    ? '<div class="tip-note">Output tokens may include unreported reasoning.</div>'
    : '';
  const retries = Number(data.retries || 0);
  return `<div class="tip-title">Effort · ${{fmtSec(data.wall)}}</div>`
    + `<table class="tip-table">${{rowsHtml}}</table>`
    + `<div class="tip-footer">peak ${{fmtTok(data.tokensMax)}} / mean ${{fmtTok(data.tokensMean)}} req tok`
    + ` · ${{fmtNum(data.toks, 1)}} tok/s aggregate / ${{fmtNum(data.toksMed, 1)}} median`
    + ` · self-test ${{fmtSec(data.selfTest)}} (${{fmtNum(data.selfTestRuns)}} runs)`
    + (retries ? ` · ${{retries}} retr${{retries === 1 ? 'y' : 'ies'}}` : '')
    + ` · ${{fmtNum(data.steps)}} steps · ${{fmtPct(data.cacheRate)}} cache</div>`
    + caveat;
}}

function showEffort(row) {{
  if (hoverTip) hoverTip.hidden = true;
  effortTitle.textContent = `Effort · ${{row.dataset.run || 'run'}}`;
  effortBody.innerHTML = effortBodyHtml(row);
  if (typeof effortDialog.showModal === 'function') {{
    effortDialog.showModal();
  }} else {{
    effortDialog.setAttribute('open', '');
  }}
}}

for (const button of document.querySelectorAll('.detail-btn')) {{
  button.addEventListener('click', () => {{
    const row = button.closest('tr');
    if (row) showEffort(row);
  }});
}}

effortDialog?.addEventListener('click', (event) => {{
  if (event.target === effortDialog) effortDialog.close();
}});

const hoverTip = document.getElementById('hover-tip');
function placeHoverTip(event, target) {{
  const tipRect = hoverTip.getBoundingClientRect();
  let clientX;
  let clientY;
  if (event && typeof event.clientX === 'number' && event.clientX) {{
    clientX = event.clientX;
    clientY = event.clientY;
  }} else {{
    const rect = target.getBoundingClientRect();
    clientX = rect.left + rect.width / 2;
    clientY = rect.top;
  }}
  let x = clientX + 12;
  let y = clientY + 16;
  if (x + tipRect.width > window.innerWidth - 8) x = clientX - tipRect.width - 12;
  if (y + tipRect.height > window.innerHeight - 8) y = clientY - tipRect.height - 16;
  hoverTip.style.left = `${{Math.max(8, x)}}px`;
  hoverTip.style.top = `${{Math.max(8, y)}}px`;
}}
function showEffortTip(row, event, target) {{
  hoverTip.className = 'hover-tip effort-tip';
  hoverTip.innerHTML = effortTipHtml(row);
  hoverTip.hidden = false;
  placeHoverTip(event, target);
}}
for (const button of document.querySelectorAll('.detail-btn')) {{
  button.addEventListener('mouseenter', (event) => {{
    const row = button.closest('tr');
    if (row) showEffortTip(row, event, button);
  }});
  button.addEventListener('mousemove', (event) => placeHoverTip(event, button));
  button.addEventListener('mouseleave', () => {{ hoverTip.hidden = true; }});
}}
window.addEventListener('scroll', () => {{ hoverTip.hidden = true; }}, true);

search?.addEventListener('input', applyView);
providerFilter?.addEventListener('change', () => {{
  saveFilters();
  applyView();
}});
reasoningFilter?.addEventListener('change', () => {{
  saveFilters();
  applyView();
}});
wallBasisFilter?.addEventListener('change', () => {{
  wallBasis = wallBasisFilter.value === 'work' ? 'work' : 'total';
  try {{
    localStorage.setItem(WALL_BASIS_KEY, wallBasis);
  }} catch (error) {{}}
  applyWallBasis();
}});

applyWallBasis();
sortRows();
applyView();

if (new URLSearchParams(location.search).get('live') === '1') {{
  setTimeout(() => location.reload(), 30000);
}}
</script>
</body>
</html>
"""
    atomic_write_text(LEADERBOARD_PATH, text)
    return LEADERBOARD_PATH


class LeaderboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] not in ("/", "/index.html", "/leaderboard.html"):
            self.send_error(404)
            return
        try:
            write_leaderboard()
            body = LEADERBOARD_PATH.read_bytes()
        except OSError:
            self.send_error(500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


def cmd_leaderboard(args: argparse.Namespace) -> int:
    path = write_leaderboard()
    print(f"leaderboard {path}")
    if args.write:
        return 0
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), LeaderboardHandler)
    except OSError as exc:
        eprint(f"error: cannot listen on 127.0.0.1:{args.port}: {exc}")
        return 1
    url = f"http://127.0.0.1:{args.port}/?live=1"
    print(f"serving {url}")
    print("Ctrl+C to stop")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")
    finally:
        server.server_close()
    return 0


def _metric_list(items: list[dict], key: str) -> list[float]:
    values: list[float] = []
    for item in items:
        value = metric_value(item, key)
        if value is not None:
            values.append(value)
    return values


def _metric_median(items: list[dict], key: str) -> float | None:
    return median_or_none(_metric_list(items, key))


def _metric_mean(items: list[dict], key: str) -> float | None:
    values = _metric_list(items, key)
    return statistics.mean(values) if values else None


def cmd_report(args: argparse.Namespace) -> int:
    rows = load_result_rows()
    if not rows:
        print("no results yet; run ./bench.py run --model ...")
        return 0

    by_model: dict[str, list[dict]] = {}
    for row in rows:
        by_model.setdefault(row["model"], []).append(row)

    lines = [
        "# opencode-bench report",
        "",
        "| model | thinking | runs | quality med | wall med | work med | self-test% med | retries med | think% med | req tok peak med | tok/s med | compactions med | minor med | eligible |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model, items in sorted(by_model.items()):
        quality = statistics.median(float(item["quality"]) for item in items)
        wall = statistics.median(float(item["wall_s"]) for item in items)
        compactions = statistics.median(int(item.get("compactions") or 0) for item in items)
        minor = statistics.median(int(item["minor_issues"]) for item in items)
        eligible = any(item["eligible"] == "True" for item in items)
        labels = sorted(
            {
                (item.get("thinking") or "unknown")
                + (f" ({item['reasoning_effort']})" if item.get("reasoning_effort") else "")
                for item in items
            }
        )
        think = _metric_median(items, "think_share")
        work = _metric_median(items, "wall_ex_self_test_s")
        selftest = _metric_median(items, "self_test_share")
        retries = _metric_median(items, "retries")
        tokens = _metric_median(items, "max_request_tokens")
        toks = _metric_median(items, "gen_tokens_per_s_median")
        if toks is None:
            toks = _metric_median(items, "gen_tokens_per_s")
        toks_text = f"{toks:.0f}" if toks is not None else "—"
        retries_text = f"{retries:.0f}" if retries is not None else "—"
        lines.append(
            f"| {model} | {', '.join(labels)} | {len(items)} | {quality:.2f} | "
            f"{format_duration(wall)} | {format_optional_duration(work)} | "
            f"{format_percent(selftest)} | "
            f"{retries_text} | {format_percent(think)} | "
            f"{format_tokens(tokens)} | {toks_text} | "
            f"{compactions:.0f} | {minor:.0f} | {'yes' if eligible else 'no'} |"
        )

    by_variant: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        key = (
            str(row.get("model", "")),
            str(row.get("variant") or "default"),
            thinking_label(row),
        )
        by_variant.setdefault(key, []).append(row)
    lines += [
        "",
        "## Effort by variant",
        "",
        "| model | variant | thinking | runs | wall med | work med | self-test% med | retries med | think% med | out% med | tool% med | req tok peak med | req tok mean | reason tok med | tok/s med |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for (model, variant, thinking), items in sorted(by_variant.items()):
        wall = statistics.median(float(item["wall_s"]) for item in items)
        toks = _metric_median(items, "gen_tokens_per_s_median")
        if toks is None:
            toks = _metric_median(items, "gen_tokens_per_s")
        toks_text = f"{toks:.0f}" if toks is not None else "—"
        retries = _metric_median(items, "retries")
        retries_text = f"{retries:.0f}" if retries is not None else "—"
        lines.append(
            f"| {model} | {variant} | {thinking} | {len(items)} | "
            f"{format_duration(wall)} | "
            f"{format_optional_duration(_metric_median(items, 'wall_ex_self_test_s'))} | "
            f"{format_percent(_metric_median(items, 'self_test_share'))} | "
            f"{retries_text} | "
            f"{format_percent(_metric_median(items, 'think_share'))} | "
            f"{format_percent(_metric_median(items, 'output_share'))} | "
            f"{format_percent(_metric_median(items, 'tool_share'))} | "
            f"{format_tokens(_metric_median(items, 'max_request_tokens'))} | "
            f"{format_tokens(_metric_mean(items, 'mean_request_tokens'))} | "
            f"{format_tokens(_metric_median(items, 'reasoning_tokens'))} | "
            f"{toks_text} |"
        )

    lines += [
        "",
        "Request tokens are the per-request prompt size (input + cache reads/writes); "
        "peak is the largest single request. Cumulative token fields in "
        "`results.csv` include cache reads/writes and can exceed the context window; "
        "they are not context pressure. Reasoning tokens are provider-reported. "
        "See `results.csv` for `input_tokens`, `output_tokens`, `reasoning_tokens`, "
        "`cache_read_tokens` and `cache_write_tokens`.",
        "",
        "`wall_ex_self_test_s` is end-to-end wall minus classified self-test "
        "command time. `self_test_s` counts Playwright/Vitest/browser-test tool "
        "calls; `build_tool_s` counts build/typecheck commands. Provider "
        "errors/retries are excluded from think/output and tok/s, counted in "
        "`retries`/`retry_s`, and remain in wall time. tok/s medians are "
        "per-step and are more robust than the aggregate when a run retries.",
        "",
        "Wall time remains hardware-dependent. The leaderboard speed baseline "
        "uses the fastest eligible run and caps the speed ratio at 1.0.",
        "",
        "Full rows: `results.csv`. Per-run artifacts: `runs/<run_id>/`.",
        "",
        "Leaderboard: `leaderboard.html` (serve and open with `./bench.py leaderboard`; "
        "write only with `--write`).",
        "",
        "## Failures",
        "",
    ]
    any_failure = False
    for row in rows:
        report_path = Path(row.get("report", ""))
        failures = failed_tests(report_path.parent / "playwright.json")
        if failures:
            any_failure = True
            for failure in failures:
                lines.append(f"- `{row['model']}` / `{row['run_id']}`: {failure}")
    if not any_failure:
        lines.append("None.")
    lines.append("")
    text = "\n".join(lines)
    atomic_write_text(REPORT_PATH, text)
    write_leaderboard()
    print(text)
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="opencode-bench")
    parser.add_argument("--engine", default=DEFAULT_ENGINE, help="docker or podman")
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="build the agent/eval image")
    build.add_argument("--dry-run", action="store_true")
    build.add_argument(
        "--playwright-image",
        default=os.environ.get("OPENCODE_BENCH_PLAYWRIGHT_IMAGE", ""),
        help="override the Playwright base image tag",
    )
    build.add_argument(
        "--opencode-version",
        default=os.environ.get("OPENCODE_BENCH_OPENCODE_VERSION", ""),
        help="override the OpenCode 2 version installed in the image",
    )
    build.set_defaults(func=cmd_build)

    models = sub.add_parser("models", help="list models from `opencode models`")
    models.add_argument("--provider", default=None)
    models.add_argument("--all", action="store_true", help="print every model reference")
    models.add_argument("--config", default=None, help="OpenCode config file or directory")
    models.set_defaults(func=cmd_models)

    run = sub.add_parser("run", help="run one or more OpenCode models")
    run.add_argument("--model", default=None, help="provider/model, or 'all'")
    run.add_argument("--provider", default=None, help="filter the model menu")
    run.add_argument("--config", default=None, help="OpenCode config file or directory")
    run.add_argument("--env", action="append", default=[], metavar="KEY", help="pass an env var")
    run.add_argument(
        "--hardware",
        default=None,
        help="optional free-text hardware note stored with the run",
    )
    run.add_argument(
        "--variant",
        default=None,
        help="OpenCode model variant, or 'all' to run every variant (high to low) then stop",
    )
    run.add_argument("--agent", default=None, help="OpenCode agent name")
    run.add_argument("--pure", action="store_true", help="run OpenCode without external plugins")
    run.add_argument(
        "--keep-going",
        action="store_true",
        help="continue to the next model/variant if a run fails",
    )
    run.add_argument("--reps", type=int, default=1)
    run.add_argument("--goal", choices=["speed", "quality", "balanced"], default="balanced")
    run.add_argument("--timeout", type=int, default=4 * 3600, help="hard safety timeout")
    run.add_argument("--idle-timeout", type=int, default=1800, help="no-output timeout")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--no-preflight", action="store_true")
    run.set_defaults(func=cmd_run)

    check = sub.add_parser("check", help="validate config, seed, tests and image")
    check.add_argument("--dry-run", action="store_true")
    check.add_argument("--config", default=None, help="OpenCode config file or directory")
    check.set_defaults(func=cmd_check)

    rescore = sub.add_parser("rescore", help="rerun build+tests on an existing run")
    rescore.add_argument("--run", default=None, help="run directory name (default: latest)")
    rescore.set_defaults(func=cmd_rescore)

    backfill = sub.add_parser(
        "backfill", help="recompute effort/token metrics for existing runs"
    )
    backfill.add_argument("--run", default=None, help="run directory name (default: latest)")
    backfill.add_argument("--all", action="store_true", help="backfill every run in runs/")
    backfill.add_argument("--dry-run", action="store_true")
    backfill.set_defaults(func=cmd_backfill)

    preview = sub.add_parser("preview", help="serve the latest built app")
    preview.add_argument("--run", default=None, help="run directory name (default: latest)")
    preview.add_argument("--port", type=int, default=4173)
    preview.set_defaults(func=cmd_preview)

    leaderboard = sub.add_parser(
        "leaderboard", help="serve and open the all-runs leaderboard"
    )
    leaderboard.add_argument(
        "--write", action="store_true", help="write leaderboard.html without serving"
    )
    leaderboard.add_argument("--port", type=int, default=4174)
    leaderboard.set_defaults(func=cmd_leaderboard)

    status = sub.add_parser("status", help="show the latest run and live engine status")
    status.add_argument("--config", default=None, help="OpenCode config file or directory")
    status.set_defaults(func=cmd_status)

    report = sub.add_parser("report", help="render results.csv")
    report.set_defaults(func=cmd_report)

    return parser


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
