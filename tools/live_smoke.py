"""Check a running ROS-backed application; this replaces its simulation world."""

import argparse
import json
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--ros", action="store_true", help="Also check actual ROS node discovery")
    parser.add_argument("--output", help="Optional path for the JSON report")
    args = parser.parse_args()
    results = []

    def call(path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(args.url + path, data, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=5) as response:
                return json.load(response)
        except HTTPError as exc:
            raise AssertionError(f"{path}: HTTP {exc.code}, {exc.read().decode()}") from exc

    def wait(predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = call("/api/state")
            if predicate(snapshot):
                return snapshot
            time.sleep(0.1)
        raise AssertionError(f"Timed out: {snapshot}")

    def robot(snapshot, robot_id="robot1"):
        return next(item for item in snapshot["robots"] if item["id"] == robot_id)

    def command(kind, **fields):
        return call("/api/robots/robot1/commands", {"type": kind, **fields})

    if args.ros:
        output = subprocess.run(["ros2", "node", "list"], check=True, text=True,
                                capture_output=True, timeout=15).stdout
        assert {"/warehouse_simulator", "/warehouse_dispatcher"} <= set(output.split())
        results.append("actual ROS node discovery")

    call("/api/reset", {"map": "normal", "robots": 2})
    wait(lambda s: len(s["robots"]) == 2 and all(r["connected"] for r in s["robots"]))
    command("move", target="r1c5")
    wait(lambda s: robot(s)["node_id"] == "r1c5" and not s["dispatcher"]["goals"])
    results.append("HTTP -> dispatcher -> ROS -> physics -> telemetry: move completed")

    command("move", target="r2c4")
    wait(lambda s: robot(s)["node_id"] is None)
    call("/api/robots/robot1/faults", {"link_lost": True})
    wait(lambda s: not robot(s)["connected"])
    command("stop")
    time.sleep(1)
    call("/api/robots/robot1/faults", {"link_lost": False})
    stopped = wait(lambda s: robot(s)["connected"] and abs(robot(s)["v"]) < 0.001)
    assert stopped["dispatcher"]["paused"]
    assert stopped["dispatcher"]["reservations"]["robot1"]
    command("resume")
    wait(lambda s: robot(s)["node_id"] == "r2c4" and not s["dispatcher"]["goals"])
    results.append("bidirectional link loss, safe pause, retained edge, recovery")

    call("/api/robots/robot1/faults", {"stuck": True})
    wait(lambda s: (robot(s)["error"] or {}).get("code") == "STUCK")
    call("/api/robots/robot1/faults", {"stuck": False})
    command("clear_error")
    wait(lambda s: robot(s)["error"] is None)
    command("manual", angular=0.8)
    for _ in range(15):
        command("manual", angular=0.8)
        time.sleep(0.1)
    wait(lambda s: abs(robot(s)["omega"]) > 0.1)
    command("manual", angular=0)
    wait(lambda s: abs(robot(s)["omega"]) < 0.001)
    command("resume")
    results.append("stuck/clear and manual turn/stop/resume")

    call("/api/robots/robot1/faults", {"throttle": True})
    time.sleep(1.2)
    assert robot(call("/api/state"))["fault"]["throttle"]
    call("/api/robots/robot1/faults", {"throttle": False})
    wait(lambda s: robot(s)["last_seen"] is not None and robot(s)["last_seen"] < 0.3)
    results.append("throttled telemetry and restoration")

    call("/api/reset", {"map": "hardcore", "robots": 5})
    wait(lambda s: len(s["robots"]) == 5 and all(r["connected"] for r in s["robots"]))
    call("/api/dark-factory", {"enabled": True})
    wait(lambda s: s["metrics"]["completed_tasks"] >= 2, timeout=90)
    assert all(r["error"] is None for r in call("/api/state")["robots"])
    results.append("hardcore map, five robots, dark-factory tasks completed")
    call("/api/reset", {"map": "normal", "robots": 3})
    wait(lambda s: len(s["robots"]) == 3 and all(r["connected"] for r in s["robots"]))
    report = {"ok": True, "checks": results, "url": args.url}
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
