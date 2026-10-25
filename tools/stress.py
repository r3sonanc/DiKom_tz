"""Run real physics with an accelerated deterministic clock, without ROS."""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.support import FleetHarness  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--seeds", type=int, default=3)
    args = parser.parse_args()
    results = []
    for map_name, count in (("normal", 3), ("hardcore", 5)):
        for seed in range(args.seeds):
            started = time.perf_counter()
            fleet = FleetHarness(map_name, count, seed)
            fleet.step()
            fleet.dispatch.set_dark_factory(True)
            fleet.advance(args.seconds)
            result = {"map": map_name, "robots": count, "seed": seed,
                      "simulation_seconds": args.seconds,
                      "wall_seconds": round(time.perf_counter() - started, 3),
                      "minimum_distance": fleet.minimum_distance,
                      "errors": [r.error for r in fleet.world.robots.values() if r.error],
                      **fleet.dispatch.metrics}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    if any(r["errors"] or r["completed_tasks"] < r["robots"] for r in results):
        raise SystemExit("Stress check failed")


if __name__ == "__main__":
    main()
