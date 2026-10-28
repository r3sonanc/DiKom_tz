"""Reproducible CPU/Python-allocation measurements; no ROS or test dependencies."""

import argparse
import cProfile
import hashlib
import io
import json
import math
import platform
import pstats
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from warehouse.api import FleetRuntime  # noqa: E402
from warehouse.dispatcher import Dispatcher  # noqa: E402
from warehouse.robot import WorldCore  # noqa: E402


class NullTransport:
    def receive(self):
        return []

    def send(self, command):
        pass


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def simulate(map_name, robots, seconds, seed, collect_timings=False):
    world = WorldCore(map_name, robots, "benchmark")
    dispatcher = Dispatcher(world.map, list(world.robots), seed=seed)
    dispatcher.set_dark_factory(True)
    revisions = {}
    sequence = 0
    tick_samples = []
    for frame in range(1, round(seconds * 50) + 1):
        now = round(frame / 50, 8)
        world.tick(0.02, now)
        if frame % 5:
            continue
        for state in world.telemetry(now):
            dispatcher.ingest(state, now)
        started = time.perf_counter() if collect_timings else 0.0
        commands = dispatcher.tick(now)
        if collect_timings:
            tick_samples.append((time.perf_counter() - started) * 1000)
        for command in commands:
            command = {**command, "world_id": world.world_id}
            command_id = command.get("command_id")
            if command_id not in revisions:
                sequence += 1
                revisions[command_id] = sequence
            command["command_seq"] = revisions[command_id]
            world.command(command, now)
    errors = [robot.error for robot in world.robots.values() if robot.error]
    result = {"completed_tasks": dispatcher.metrics["completed_tasks"],
            "planning_failures": dispatcher.metrics["planning_failures"],
            "errors": errors}
    if tick_samples:
        result["dispatcher_tick_ms"] = {
            "p50": round(statistics.median(tick_samples), 6),
            "p95": round(percentile(tick_samples, 0.95), 6),
            "max": round(max(tick_samples), 6),
            "count": len(tick_samples),
        }
    metrics = dispatcher.metrics
    count = metrics.get("planning_duration_seconds_count", 0)
    total = metrics.get("planning_duration_seconds_sum", 0.0)
    result["planning"] = {
        "last_seconds": metrics.get("planning_duration_seconds", 0.0),
        "total_seconds": total,
        "count": count,
        "mean_seconds": total / count if count else 0.0,
    }
    return result


def make_runtime(map_name="normal", robots=3):
    runtime = FleetRuntime(NullTransport(), map_name, robots)
    world = WorldCore(map_name, robots, runtime.world_id)
    for state in world.snapshot(10.0):
        runtime.raw_states[state["robot_id"]] = state
        runtime.dispatcher.ingest(state, 10.0)
    return runtime


def serialize(runtime, now):
    if hasattr(runtime, "snapshot_json"):
        return runtime.snapshot_json(now)
    return json.dumps(runtime.snapshot(now), ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)


def snapshot_frames(runtime, clients, rounds):
    timings = []
    size = 0
    for frame in range(rounds):
        now = 10.0 + frame * 0.2
        started = time.perf_counter()
        for _ in range(clients):
            payload = serialize(runtime, now)
            size = len(payload)
        timings.append((time.perf_counter() - started) * 1000)
    return timings, size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--seeds", type=int, nargs="+", default=[7, 19, 41])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--snapshot-rounds", type=int, default=200)
    parser.add_argument("--clients", type=int, nargs="+", default=[1, 10, 50])
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-commit", help="Commit containing the measured sources")
    args = parser.parse_args()
    if (args.seconds <= 0 or args.repeats < 1 or args.snapshot_rounds < 1
            or not args.seeds or any(count < 1 for count in args.clients)):
        parser.error("positive durations, repetitions and client counts are required")
    report = {"source_commit": args.source_commit,
              "python": platform.python_version(), "platform": platform.platform(),
              "simulation_seconds": args.seconds, "seeds": args.seeds,
              "repeats": args.repeats,
              "memory_scope": "tracemalloc Python allocations, not process RSS/container/DDS",
              "core_memory_scope": "tracing starts before WorldCore/Dispatcher creation; includes the direct-transport harness",
              "snapshot_memory_scope": "tracing starts after make_runtime; excludes retained graph/state/map initialization; includes per-frame/cache allocations and timings list",
              "core_transport": "direct in-memory commands and states, without ROS/DDS/HTTP",
              "snapshots": "sequential virtual clients at one shared frame timestamp; excludes network I/O",
              "serialization": "cached" if hasattr(FleetRuntime, "snapshot_json") else "uncached",
              "source_sha256": {name: hashlib.sha256((Path(__file__).resolve().parents[1] / name).read_bytes()).hexdigest()
                                for name in ("warehouse/dispatcher.py", "warehouse/api.py", "warehouse/robot.py")},
              "tick_timing_scope": "stdlib perf_counter around each tick; summary from the last repetition; tracemalloc run excludes timing sample storage",
              "core": [], "snapshot": []}
    for map_name, robots in (("normal", 3), ("hardcore", 5)):
        for seed in args.seeds:
            timings = []
            for _ in range(args.repeats):
                started = time.perf_counter()
                result = simulate(map_name, robots, args.seconds, seed, collect_timings=True)
                timings.append(time.perf_counter() - started)
            tracemalloc.start()
            simulate(map_name, robots, args.seconds, seed)
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            report["core"].append({"map": map_name, "robots": robots, "seed": seed,
                "seconds_p50": round(statistics.median(timings), 6),
                "seconds_p95": round(percentile(timings, 0.95), 6),
                "python_peak_kib": round(peak / 1024, 3), **result})
    for clients in args.clients:
        runtime = make_runtime()
        timings, size = snapshot_frames(runtime, clients, args.snapshot_rounds)
        memory_runtime = make_runtime()
        tracemalloc.start()
        snapshot_frames(memory_runtime, clients, args.snapshot_rounds)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        report["snapshot"].append({"map": "normal", "robots": 3, "clients": clients,
            "rounds": args.snapshot_rounds, "frame_ms_p50": round(statistics.median(timings), 6),
            "frame_ms_p95": round(percentile(timings, 0.95), 6),
            "python_peak_kib": round(peak / 1024, 3), "payload_characters": size})
    if args.profile:
        profiler = cProfile.Profile()
        profiler.runcall(simulate, "hardcore", 5, args.seconds, args.seeds[0])
        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumtime").print_stats(18)
        report["core_profile"] = stream.getvalue()
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    if any(row["errors"] for row in report["core"]):
        raise SystemExit("Robot errors during benchmark")


if __name__ == "__main__":
    main()
