#!/usr/bin/env python3
"""spritebench: one-command build-and-play benchmark for OpenCode models.

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

DEFAULT_IMAGE = os.environ.get("SPRITEBENCH_IMAGE", "spritebench:latest")
DEFAULT_ENGINE = os.environ.get("SPRITEBENCH_ENGINE", "docker")
OPENCODE = os.environ.get("SPRITEBENCH_OPENCODE", "opencode")

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
CSV_FIELDS = [
    "run_id",
    "timestamp",
    "model",
    "provider",
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
    "agent_ok",
    "self_test_runs",
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
    model_ref = '"$SPRITEBENCH_MODEL"'
    if args.variant:
        # OpenCode 2 encodes the variant in the model reference, not a flag.
        model_ref = '"${SPRITEBENCH_MODEL}#${SPRITEBENCH_VARIANT}"'
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
    name = f"spritebench-{slug(provider_of(model))}-{uuid.uuid4().hex[:8]}"
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
        f"SPRITEBENCH_MODEL={model}",
    ]
    if args.variant:
        command += ["-e", f"SPRITEBENCH_VARIANT={args.variant}"]
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
        f"{TESTS_DIR}:/opt/spritebench/tests:ro",
        "-v",
        f"{run_dir}:/out",
        "-w",
        "/opt/spritebench",
        "-e",
        "BASE_URL=http://127.0.0.1:4173",
        "-e",
        "RESULT_FILE=/out/playwright.json",
        "-e",
        "OUTPUT_DIR=/out/test-results",
        image,
        "bash",
        "-lc",
        "npx playwright test --config /opt/spritebench/tests/playwright.config.ts",
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


def write_run_report(run_dir: Path, meta: dict, score: dict) -> None:
    lines = [
        f"# {meta['run_id']}",
        "",
        f"- model: `{meta['model']}`",
        f"- provider: {meta['provider']}",
        f"- variant: {meta.get('variant', 'default')}",
        f"- thinking: {meta.get('thinking', 'unknown')}"
        + (f" ({meta['reasoning_effort']})" if meta.get("reasoning_effort") else ""),
        f"- goal: {meta['goal']}",
        f"- wall: {format_duration(meta['wall_s'])}",
        f"- compactions: {meta.get('compactions', 0)}",
        f"- watchdog: {meta['watchdog']}",
        f"- completion file: {meta['completion']}",
        f"- agent exit ok: {meta['agent_ok']}",
        f"- build: {'ok' if meta['build_ok'] else 'failed'}",
        f"- typecheck: {'ok' if meta['typecheck_ok'] else 'failed'}",
        f"- quality: {score['quality']:.3f}",
        f"- fatal/major/minor: {score['fatal']}/{score['major']}/{score['minor']}",
        f"- eligible: {score['eligible']}",
        "",
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
        f"   compactions {meta.get('compactions', 0)}"
        f"   watchdog {meta['watchdog']}"
        f"   self-tests {meta['self_test_runs']}"
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
        "rep": rep,
        "goal": args.goal,
        "variant": variant_label,
        "thinking": thinking,
        "reasoning_effort": effort,
        "wall_s": agent["wall_s"],
        "compactions": count_compactions(data_dir),
        "watchdog": agent["watchdog"],
        "completion": agent["completion"],
        "agent_ok": agent["agent_ok"],
        "self_test_runs": agent["self_test_runs"],
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

    model_match = re.search(r"SPRITEBENCH_MODEL=([^\s]+)", first)
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
    value = (row.get("compactions") or "").strip()
    if not value:
        return None
    try:
        return max(0, int(float(value)))
    except ValueError:
        return None


def leaderboard_entries(rows: list[dict]) -> list[dict]:
    walls = [float(row["wall_s"]) for row in rows]
    fastest = min(walls) if walls else 0.0
    weights = LEADERBOARD_WEIGHTS
    entries: list[dict] = []
    for row in rows:
        quality = max(0.0, min(1.0, float(row["quality"])))
        wall = max(0.0, float(row["wall_s"]))
        compactions = compaction_count(row)
        speed = fastest / wall if wall > 0 and fastest > 0 else 0.0
        compaction_score = 1.0 / (1.0 + compactions) if compactions is not None else 0.0
        score = 100.0 * (
            weights["quality"] * quality
            + weights["wall"] * speed
            + weights["compactions"] * compaction_score
        )
        entries.append(
            {
                "row": row,
                "quality": quality,
                "wall": wall,
                "compactions": compactions,
                "speed": speed,
                "score": score,
                "eligible": str(row.get("eligible", "")).lower() == "true",
            }
        )
    entries.sort(
        key=lambda entry: (
            -entry["score"],
            -entry["quality"],
            entry["wall"],
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


def write_leaderboard() -> Path:
    with results_lock():
        return _write_leaderboard_unlocked()


def _write_leaderboard_unlocked() -> Path:
    rows = _valid_result_rows(_read_result_rows_unlocked())
    entries = leaderboard_entries(rows)
    escaped = html.escape

    cards = [
        ("Runs", str(len(entries))),
        ("Eligible", str(sum(1 for entry in entries if entry["eligible"]))),
        (
            "Best quality",
            f"{max((entry['quality'] for entry in entries), default=0.0):.2f}",
        ),
        (
            "Fastest",
            format_duration(min((entry["wall"] for entry in entries), default=0.0)),
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
        compactions = "—" if entry["compactions"] is None else str(entry["compactions"])
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
                    str(row.get("run_id", "")),
                ]
            ).lower()
        )
        rank_rows.append(
            f'<tr class="{"ineligible" if not eligible else ""}" data-search="{search}">'
            f'<td class="rank">{rank}</td>'
            f'<td><div class="score"><span class="bar" style="width:{min(100.0, entry["score"]):.1f}%"></span>'
            f'<b>{entry["score"]:.1f}</b></div></td>'
            f'<td class="model"><div>{model}</div><div class="runid">{run_id}</div></td>'
            f'<td>{variant}</td>'
            f'<td>{thinking}</td>'
            f'<td><span class="quality {quality_class}">{quality:.2f}</span></td>'
            f'<td class="counts">{counts}</td>'
            f'<td class="wall">{format_duration(entry["wall"])}</td>'
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
            "<th>#</th><th>Score</th><th>Model / run</th><th>Variant</th><th>Thinking</th>"
            "<th>Quality</th><th>P0 · P1 · P2</th><th>Wall</th><th>Compactions</th><th>Eligible</th>"
            "</tr></thead><tbody>"
            + "".join(rank_rows)
            + "</tbody></table></div>"
        )

    updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    weights = LEADERBOARD_WEIGHTS
    css = """
:root { color-scheme: dark; --bg:#0f1117; --panel:#171a23; --line:#272c3a;
        --text:#e7ebf5; --muted:#8b93a7; --accent:#4a90d9; --good:#3ecf8e;
        --warn:#f0b429; --bad:#ef5b5b; }
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
button { background:var(--accent); border:0; color:#fff; border-radius:8px; padding:9px 14px;
         font:inherit; cursor:pointer; }
.table-wrap { overflow-x:auto; border:1px solid var(--line); border-radius:12px; }
table { width:100%; border-collapse:collapse; min-width:1050px; }
thead th { position:sticky; top:0; background:#1b1f2b; color:var(--muted); font-size:12px;
           text-transform:uppercase; letter-spacing:.05em; text-align:left; padding:12px 14px;
           border-bottom:1px solid var(--line); white-space:nowrap; }
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
.counts, .wall, .num { font-variant-numeric:tabular-nums; white-space:nowrap; }
.quality { display:inline-block; padding:2px 8px; border-radius:999px; font-weight:700;
           font-variant-numeric:tabular-nums; }
.quality.high { background:rgba(62,207,142,.15); color:var(--good); }
.quality.mid { background:rgba(240,180,41,.15); color:var(--warn); }
.quality.low { background:rgba(239,91,91,.15); color:var(--bad); }
.empty { color:var(--muted); }
footer { color:var(--muted); margin-top:18px; font-size:13px; }
code { background:#1b1f2b; border:1px solid var(--line); border-radius:5px; padding:1px 5px; }
@media (max-width:700px) { body { padding:18px; } h1 { font-size:22px; } }
"""
    text = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>spritebench leaderboard</title>
<style>{css}</style>
</head>
<body>
<header>
  <div>
    <h1>spritebench leaderboard</h1>
    <p class="sub">Build-and-play benchmark · updated {escaped(updated)}</p>
  </div>
  <div class="cards">{cards_html}</div>
</header>
<div class="formula">
  <b>Score = {weights["quality"]:.0%} quality + {weights["wall"]:.0%} speed + {weights["compactions"]:.0%} compactions.</b>
  Speed is relative to the fastest run (<code>fastest wall / run wall</code>);
  compactions score as <code>1 / (1 + count)</code>. Ranked by score, then quality,
  wall time and compactions. Missing compaction data counts as zero.
</div>
<div class="toolbar">
  <input id="filter" type="search" placeholder="Filter model, variant, thinking or run id">
  <button type="button" onclick="location.reload()">Refresh</button>
</div>
{table}
<footer>
  Generated by <code>./bench.py leaderboard</code>. Serve live with
  <code>./bench.py leaderboard --serve</code>; add <code>?live=1</code> for auto-refresh.
  Raw data: <code>results.csv</code>.
</footer>
<script>
const filter = document.getElementById('filter');
filter?.addEventListener('input', () => {{
  const needle = filter.value.trim().toLowerCase();
  for (const row of document.querySelectorAll('tbody tr')) {{
    row.style.display = !needle || row.dataset.search.includes(needle) ? '' : 'none';
  }}
}});
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
    if not args.serve:
        if args.open:
            webbrowser.open(path.as_uri())
        return 0
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), LeaderboardHandler)
    except OSError as exc:
        eprint(f"error: cannot listen on 127.0.0.1:{args.port}: {exc}")
        return 1
    url = f"http://127.0.0.1:{args.port}/?live=1"
    print(f"serving {url}")
    print("Ctrl+C to stop")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")
    finally:
        server.server_close()
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    rows = load_result_rows()
    if not rows:
        print("no results yet; run ./bench.py run --model ...")
        return 0

    by_model: dict[str, list[dict]] = {}
    for row in rows:
        by_model.setdefault(row["model"], []).append(row)

    lines = [
        "# spritebench report",
        "",
        "| model | thinking | runs | quality med | wall med | compactions med | minor med | eligible |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
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
        lines.append(
            f"| {model} | {', '.join(labels)} | {len(items)} | {quality:.2f} | "
            f"{format_duration(wall)} | {compactions:.0f} | {minor:.0f} | "
            f"{'yes' if eligible else 'no'} |"
        )
    lines += [
        "",
        "Full rows: `results.csv`. Per-run artifacts: `runs/<run_id>/`.",
        "",
        "Leaderboard: `leaderboard.html` (rebuild with `./bench.py leaderboard --serve`).",
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
    parser = argparse.ArgumentParser(description="spritebench")
    parser.add_argument("--engine", default=DEFAULT_ENGINE, help="docker or podman")
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="build the agent/eval image")
    build.add_argument("--dry-run", action="store_true")
    build.add_argument(
        "--playwright-image",
        default=os.environ.get("SPRITEBENCH_PLAYWRIGHT_IMAGE", ""),
        help="override the Playwright base image tag",
    )
    build.add_argument(
        "--opencode-version",
        default=os.environ.get("SPRITEBENCH_OPENCODE_VERSION", ""),
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

    preview = sub.add_parser("preview", help="serve the latest built app")
    preview.add_argument("--run", default=None, help="run directory name (default: latest)")
    preview.add_argument("--port", type=int, default=4173)
    preview.set_defaults(func=cmd_preview)

    leaderboard = sub.add_parser(
        "leaderboard", help="render and optionally serve the all-runs leaderboard"
    )
    leaderboard.add_argument("--serve", action="store_true", help="serve it on localhost")
    leaderboard.add_argument("--port", type=int, default=4174)
    leaderboard.add_argument("--open", action="store_true", help="open it in the browser")
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
