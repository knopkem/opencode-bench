#!/usr/bin/env python3
"""spritebench: one-command build-and-play benchmark for local deployments.

Usage:
    ./bench.py build
    ./bench.py models
    ./bench.py run --model pc/swift-iq2-xs
    ./bench.py report

The agent runs in a container with only the workdir and its own config mounted.
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
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "models.json"
TASK_DIR = ROOT / "tasks" / "spriteforge-lite"
SEED_DIR = TASK_DIR / "seed"
TESTS_DIR = TASK_DIR / "tests"
PROMPT_PATH = ROOT / "PROMPT.md"
RUNS_DIR = ROOT / "runs"
RESULTS_PATH = ROOT / "results.csv"
REPORT_PATH = ROOT / "report.md"

DEFAULT_IMAGE = os.environ.get("SPRITEBENCH_IMAGE", "spritebench:latest")
DEFAULT_ENGINE = os.environ.get("SPRITEBENCH_ENGINE", "docker")

EXPECTED = {"P0": 4, "P1": 9, "P2": 5}
WEIGHT = {"P0": 3, "P1": 2, "P2": 1}
CSV_FIELDS = [
    "run_id",
    "timestamp",
    "model",
    "machine",
    "provider",
    "quant",
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
    "tokens_in",
    "tokens_out",
    "reasoning_tokens",
    "ttft_ms",
    "prefill_tps",
    "decode_tps",
    "report",
]


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def load_models() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except FileNotFoundError:
        sys.exit(f"error: {CONFIG_PATH} missing")
    except json.JSONDecodeError as exc:
        sys.exit(f"error: {CONFIG_PATH}: {exc}")
    models = data.get("models")
    if not isinstance(models, dict) or not models:
        sys.exit(f"error: {CONFIG_PATH} has no models")
    return models


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", value.lower()).strip("-")


def model_ref(entry: dict) -> str:
    return f"{entry['provider']}/{entry['model']}"


def api_key(entry: dict) -> str:
    name = entry.get("api_key_env")
    if name:
        return os.environ.get(name, "local-benchmark")
    return os.environ.get("BENCH_API_KEY", "local-benchmark")


def run_command(
    cmd: list[str],
    *,
    log_path: Path | None = None,
    dry_run: bool = False,
    check: bool = False,
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
            proc = subprocess.run(
                cmd,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                check=False,
            )
        return proc
    return subprocess.run(cmd, text=True, env=env, check=check)


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


def preflight(entry: dict, timeout: float = 5.0) -> tuple[bool, str]:
    base = str(entry.get("base_url", "")).rstrip("/")
    if not base:
        return False, "missing base_url"
    url = f"{base}/models"
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key(entry)}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        # The server answered. 401/404 still means it is reachable.
        return True, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - report the transport failure
        return False, str(exc)


def write_opencode_config(entry: dict, path: Path) -> None:
    provider = str(entry["provider"])
    model = str(entry["model"])
    context = int(entry.get("context", 32768))
    output = int(entry.get("output", 16384))
    thinking = str(entry.get("thinking", "medium"))
    config = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"{provider}/{model}",
        "small_model": f"{provider}/{model}",
        "provider": {
            provider: {
                "npm": "@ai-sdk/openai-compatible",
                "name": entry.get("display_name", provider),
                "options": {
                    "baseURL": entry["base_url"],
                    "apiKey": api_key(entry),
                },
                "models": {
                    model: {
                        "name": entry.get("display_name", model),
                        "tool_call": True,
                        "reasoning": True,
                        "temperature": True,
                        "interleaved": {"field": "reasoning_content"},
                        "attachment": True,
                        "limit": {
                            "context": context,
                            "input": max(1024, context - output),
                            "output": output,
                        },
                        "options": {
                            "reasoningEffort": thinking,
                            "temperature": entry.get("temperature", 0.6),
                            "top_p": entry.get("top_p", 0.95),
                            "top_k": entry.get("top_k", 20),
                        },
                    }
                },
            }
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def copy_seed(destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        SEED_DIR,
        destination,
        ignore=shutil.ignore_patterns("node_modules", ".git", "dist"),
    )
    shutil.copy2(PROMPT_PATH, destination / "PROMPT.md")


def start_agent(
    entry: dict,
    workdir: Path,
    config_dir: Path,
    run_dir: Path,
    *,
    engine: str,
    image: str,
    timeout: int,
    idle_timeout: int,
    dry_run: bool,
) -> dict:
    name = f"spritebench-{slug(entry['provider'])}-{int(time.time())}"
    log_path = run_dir / "agent.log"
    ref = model_ref(entry)
    command = [
        engine,
        "run",
        "--rm",
        "--name",
        name,
        "--shm-size=1g",
        "-v",
        f"{workdir}:/work",
        "-v",
        f"{config_dir}:/config:ro",
        "-e",
        "OPENCODE_CONFIG=/config/opencode.json",
        "-e",
        "HOME=/home/agent",
        "-e",
        "XDG_CONFIG_HOME=/home/agent/.config",
        "-e",
        f"SPRITEBENCH_MODEL={ref}",
        "-w",
        "/work",
        image,
        "bash",
        "-lc",
        'exec opencode run --format json --auto --model "$SPRITEBENCH_MODEL" "$(cat /work/PROMPT.md)"',
    ]

    if dry_run:
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
                print(f"  COMPLETION.md seen; stopping in 60s unless the agent exits")
            if completion_seen_at is not None and now - completion_seen_at > 60:
                watchdog = "completion"
                break
            if now - start > timeout:
                watchdog = "hard"
                break
            if now - last_change > idle_timeout:
                watchdog = "idle"
                break
            time.sleep(2)

        if watchdog != "exit":
            subprocess.run(
                [engine, "rm", "-f", name],
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
    typecheck_code = read_code(run_dir / "typecheck.code")
    build_code = read_code(run_dir / "build.code")
    return {
        "typecheck_ok": typecheck_code == 0,
        "build_ok": build_code == 0 and proc.returncode == 0,
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
        # Missing tests are failures, not invisible.
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
        f"- machine: {meta['machine']}",
        f"- provider: {meta['provider']}",
        f"- goal: {meta['goal']}",
        f"- wall: {meta['wall_s']:.1f}s",
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
        f"  wall {meta['wall_s']:.1f}s"
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
    command.append(str(ROOT))
    print("+ " + " ".join(shlex.quote(part) for part in command))
    if args.dry_run:
        return 0
    return subprocess.run(command, check=False).returncode


def cmd_models(args: argparse.Namespace) -> int:
    models = load_models()
    print(f"{'name':28} {'machine':14} {'provider':9} {'model'}")
    print("-" * 100)
    for name, entry in models.items():
        print(
            f"{name:28} {entry.get('machine', '?'):14} "
            f"{entry.get('provider', '?'):9} {entry.get('model', '?')}"
        )
    return 0


def choose_model(models: dict, requested: str | None) -> list[str]:
    if requested and requested != "all":
        if requested not in models:
            eprint(f"error: unknown model '{requested}' (have: {', '.join(models)})")
            raise SystemExit(2)
        return [requested]
    if requested == "all":
        return list(models)
    names = list(models)
    print("Select a deployment:")
    for index, name in enumerate(names, 1):
        entry = models[name]
        print(f"  {index}. {name:28} {entry.get('machine', '?')}")
    raw = input("model> ").strip()
    try:
        return [names[int(raw) - 1]]
    except (ValueError, IndexError):
        eprint("error: invalid selection")
        raise SystemExit(2)


def run_one(
    name: str,
    entry: dict,
    *,
    rep: int,
    args: argparse.Namespace,
) -> dict:
    models = load_models()
    if not args.no_preflight and not args.dry_run:
        ok, detail = preflight(entry)
        print(f"  endpoint {entry['base_url']}: {'ok' if ok else 'FAIL'} ({detail})")
        if not ok:
            raise SystemExit("error: model endpoint is not reachable; start Splash/Strata or pass --no-preflight")

    if not args.dry_run and not image_exists(args.engine, args.image):
        raise SystemExit(f"error: image {args.image} missing; run ./bench.py build")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{timestamp}-{slug(name)}-r{rep}"
    run_dir = RUNS_DIR / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    workdir = run_dir / "workdir"
    config_dir = run_dir / "config"
    copy_seed(workdir)
    write_opencode_config(entry, config_dir / "opencode.json")

    print(f"run {run_id}")
    print(f"  workdir {workdir}")
    print(f"  model   {model_ref(entry)}")
    print(f"  goal    {args.goal}")

    agent = start_agent(
        entry,
        workdir,
        config_dir,
        run_dir,
        engine=args.engine,
        image=args.image,
        timeout=args.timeout,
        idle_timeout=args.idle_timeout,
        dry_run=args.dry_run,
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
        "model": name,
        "machine": entry.get("machine", ""),
        "provider": entry.get("provider", ""),
        "quant": entry.get("quant", entry.get("model", "")),
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
    models = load_models()
    names = choose_model(models, args.model)
    if args.dry_run:
        print(f"dry run: {len(names)} model(s), {args.reps} rep(s)")
    for name in names:
        for rep in range(1, args.reps + 1):
            run_one(name, models[name], rep=rep, args=args)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    problems: list[str] = []
    if not CONFIG_PATH.exists():
        problems.append(f"missing {CONFIG_PATH}")
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
    print(f"  models  {len(load_models())}")
    if versions:
        print(f"  tools   {versions}")
    return 0


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
            f"| {model} | {len(items)} | {quality:.2f} | {wall:.0f}s | {minor:.0f} | "
            f"{'yes' if eligible else 'no'} |"
        )
    lines += [
        "",
        "Full rows: `results.csv`. Per-run artifacts: `runs/<run_id>/`.",
        "",
    ]
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
    build.set_defaults(func=cmd_build)

    models = sub.add_parser("models", help="list configured deployments")
    models.set_defaults(func=cmd_models)

    run = sub.add_parser("run", help="run one or more deployments")
    run.add_argument("--model", default=None, help="model key, or 'all'")
    run.add_argument("--reps", type=int, default=1)
    run.add_argument("--goal", choices=["speed", "quality", "balanced"], default="balanced")
    run.add_argument("--timeout", type=int, default=4 * 3600, help="hard safety timeout")
    run.add_argument("--idle-timeout", type=int, default=1800, help="no-output timeout")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--no-preflight", action="store_true")
    run.set_defaults(func=cmd_run)

    check = sub.add_parser("check", help="validate config, seed, tests and image")
    check.add_argument("--dry-run", action="store_true")
    check.set_defaults(func=cmd_check)

    report = sub.add_parser("report", help="render results.csv")
    report.set_defaults(func=cmd_report)

    return parser


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
