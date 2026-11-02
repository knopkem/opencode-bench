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
import json
import os
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
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

EXPECTED = {"P0": 4, "P1": 9, "P2": 5}
WEIGHT = {"P0": 3, "P1": 2, "P2": 1}
CSV_FIELDS = [
    "run_id",
    "timestamp",
    "model",
    "provider",
    "rep",
    "goal",
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
    parts = [
        'exec opencode run --format json --auto --model "$SPRITEBENCH_MODEL"',
    ]
    if args.variant:
        parts.append(f"--variant {shlex.quote(args.variant)}")
    if args.agent:
        parts.append(f"--agent {shlex.quote(args.agent)}")
    if args.pure:
        parts.append("--pure")
    parts.append('"$(cat /work/PROMPT.md)"')
    return " ".join(parts)


def start_agent(
    model: str,
    config_dir: Path,
    workdir: Path,
    run_dir: Path,
    *,
    env_names: set[str],
    args: argparse.Namespace,
) -> dict:
    name = f"spritebench-{slug(provider_of(model))}-{int(time.time())}"
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
        "-e",
        "HOME=/home/agent",
        "-e",
        "XDG_CONFIG_HOME=/home/agent/.config",
        "-e",
        f"SPRITEBENCH_MODEL={model}",
    ]
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
        f"- goal: {meta['goal']}",
        f"- wall: {format_duration(meta['wall_s'])}",
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


def rewrite_result(run_id: str, row: dict) -> None:
    if not RESULTS_PATH.exists():
        return
    with RESULTS_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    changed = False
    for index, existing in enumerate(rows):
        if existing.get("run_id") == run_id:
            rows[index] = {key: row.get(key, "") for key in CSV_FIELDS}
            changed = True
            break
    if not changed:
        rows.append({key: row.get(key, "") for key in CSV_FIELDS})
    with RESULTS_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def append_result(row: dict) -> None:
    exists = RESULTS_PATH.exists()
    with RESULTS_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in CSV_FIELDS})


def print_summary(meta: dict, score: dict) -> None:
    counts = score["counts"]
    print("")
    print(f"{meta['model']}  rep {meta['rep']}")
    print(
        f"  build {'ok' if meta['build_ok'] else 'FAIL'}"
        f"   typecheck {'ok' if meta['typecheck_ok'] else 'FAIL'}"
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
    config_dir = resolve_config_dir(args.config, RUNS_DIR / "_models")
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


def run_one(model: str, *, rep: int, args: argparse.Namespace) -> dict:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{timestamp}-{slug(model)}-r{rep}"
    run_dir = RUNS_DIR / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    workdir = run_dir / "workdir"
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

    print(f"run {run_id}")
    print(f"  workdir {workdir}")
    print(f"  model   {model}")
    print(f"  config  {config_dir}")
    print(f"  goal    {args.goal}")

    agent = start_agent(
        model,
        config_dir,
        workdir,
        run_dir,
        env_names=env_names,
        args=args,
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
        "wall_s": agent["wall_s"],
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
    print_summary(meta, score)
    return meta


def cmd_run(args: argparse.Namespace) -> int:
    config_dir = resolve_config_dir(args.config, RUNS_DIR / "_models")
    models = list_models(args.provider, config_dir)
    if not models:
        eprint("error: `opencode models` returned no models")
        return 1
    names = choose_model(models, args.model, args.provider)
    for model in names:
        for rep in range(1, args.reps + 1):
            run_one(model, rep=rep, args=args)
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
    if not engine_available(args.engine, args.dry_run):
        problems.append(f"{args.engine} is not available")
    elif not args.dry_run and not image_exists(args.engine, args.image):
        problems.append(f"image {args.image} missing; run ./bench.py build")

    config_dir = None
    models: list[str] = []
    try:
        config_dir = resolve_config_dir(args.config, RUNS_DIR / "_check")
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
    print(f"  tests   {TESTS_DIR} ({len(hidden)} spec files)")
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
            print(
                f"score      quality={meta.get('quality', 0):.2f} "
                f"eligible={meta.get('eligible')} wall={format_duration(meta.get('wall_s', 0))}"
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


def cmd_report(args: argparse.Namespace) -> int:
    if not RESULTS_PATH.exists():
        print("no results yet; run ./bench.py run --model ...")
        return 0
    with RESULTS_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        print("no results yet")
        return 0

    by_model: dict[str, list[dict]] = {}
    for row in rows:
        by_model.setdefault(row["model"], []).append(row)

    lines = [
        "# spritebench report",
        "",
        "| model | runs | quality med | wall med | minor med | eligible |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for model, items in sorted(by_model.items()):
        quality = statistics.median(float(item["quality"]) for item in items)
        wall = statistics.median(float(item["wall_s"]) for item in items)
        minor = statistics.median(int(item["minor_issues"]) for item in items)
        eligible = any(item["eligible"] == "True" for item in items)
        lines.append(
            f"| {model} | {len(items)} | {quality:.2f} | {format_duration(wall)} | {minor:.0f} | "
            f"{'yes' if eligible else 'no'} |"
        )
    lines += [
        "",
        "Full rows: `results.csv`. Per-run artifacts: `runs/<run_id>/`.",
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
    REPORT_PATH.write_text(text, encoding="utf-8")
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
    run.add_argument("--variant", default=None, help="OpenCode model variant")
    run.add_argument("--agent", default=None, help="OpenCode agent name")
    run.add_argument("--pure", action="store_true", help="run OpenCode without external plugins")
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
