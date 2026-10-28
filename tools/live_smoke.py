import argparse
import hashlib
import json
import platform
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from websockets.sync.client import connect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--ros", action="store_true", help="Also check actual ROS node discovery")
    parser.add_argument("--output", help="Optional path for the JSON report")
    parser.add_argument("--source-commit", help="Commit containing the checked application sources")
    args = parser.parse_args()
    results = []

    def call(path, payload=None, expected_status=200):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(args.url + path, data, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=5) as response:
                assert response.status == expected_status, (path, response.status, expected_status)
                return json.load(response)
        except HTTPError as exc:
            if exc.code == expected_status:
                return json.load(exc)
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
    socket_url = args.url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
    with connect(socket_url + "/ws", open_timeout=5) as first, connect(
            socket_url + "/ws", open_timeout=5) as second:
        initial = json.loads(first.recv(timeout=5))
        command("move", target="r1c5")
        for socket in (first, second):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                snapshot = json.loads(socket.recv(timeout=5))
                assert snapshot["world_id"] == initial["world_id"]
                if robot(snapshot)["node_id"] == "r1c5" and not snapshot["dispatcher"]["goals"]:
                    break
            else:
                raise AssertionError("A WebSocket observer did not receive arrival")
    results.append("HTTP -> dispatcher -> ROS -> physics -> telemetry: move completed")
    results.append("two independent WebSocket observers received the completed movement")

    command("move", target="r2c4")
    wait(lambda s: robot(s)["node_id"] is None)
    call("/api/robots/robot1/faults", {"link_lost": True})
    unavailable = wait(lambda s: not robot(s)["connected"] and s["dispatcher"]["safety_stop"]
                       and s["metrics"]["link_loss_events_total"] == 1)
    assert robot(unavailable)["link_state"] == "OFFLINE"
    assert robot(unavailable)["position_stale"]
    retained = set(unavailable["dispatcher"]["reservations"]["robot1"])
    assert len(retained) == 2
    partial_health = call("/api/health")
    assert partial_health["ok"] and partial_health["fleet"]["offline"] == 1
    assert partial_health["fleet"]["safety_stop"]
    assert unavailable["metrics"]["link_loss_events_total"] == 1
    assert unavailable["metrics"]["fleet_safety_stops_total"] == 1
    command("stop")
    time.sleep(1)
    call("/api/robots/robot1/faults", {"link_lost": False})
    stopped = wait(lambda s: robot(s)["connected"] and abs(robot(s)["v"]) < 0.001)
    assert stopped["dispatcher"]["paused"]
    assert stopped["dispatcher"]["reservations"]["robot1"]
    assert retained <= set(stopped["dispatcher"]["reservations"]["robot1"])
    assert stopped["metrics"]["link_recovery_events_total"] == 1
    command("resume")
    wait(lambda s: robot(s)["node_id"] == "r2c4" and not s["dispatcher"]["goals"])
    results.append("bidirectional link loss, safe pause, retained edge, recovery")
    results.append("partial fleet loss preserves service HTTP 200 and counts one loss/recovery episode")

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

    events_before_reset = {event["code"] for event in call("/api/state")["events"]}
    assert {"LINK_LOST", "LINK_RECOVERED", "FLEET_SAFETY_STOP", "FLEET_RESUMED",
            "ROBOT_ERROR", "ERROR_CLEARED"} <= events_before_reset
    for robot_id in ("robot1", "robot2"):
        call(f"/api/robots/{robot_id}/faults", {"link_lost": True})
    wait(lambda s: all(not entry["connected"] and entry["last_seen"] > 3.05 for entry in s["robots"]))
    all_offline_health = call("/api/health", expected_status=503)
    assert not all_offline_health["ok"] and all_offline_health["fleet"]["offline"] == 2
    with connect(socket_url + "/ws", open_timeout=5) as observer:
        assert all(not entry["connected"] for entry in json.loads(observer.recv(timeout=5))["robots"])
    results.append("all robot telemetry lost: service HTTP 503 while WebSocket remains observable")

    call("/api/reset", {"map": "hardcore", "robots": 5})
    wait(lambda s: len(s["robots"]) == 5 and all(r["connected"] for r in s["robots"]))
    call("/api/dark-factory", {"enabled": True})
    wait(lambda s: s["metrics"]["completed_tasks"] >= 2, timeout=90)
    assert all(r["error"] is None for r in call("/api/state")["robots"])
    results.append("hardcore map, five robots, dark-factory tasks completed")
    call("/api/reset", {"map": "normal", "robots": 3})
    wait(lambda s: len(s["robots"]) == 3 and all(r["connected"] for r in s["robots"]))
    release = Path("/etc/os-release")
    report = {"ok": True, "checks": results, "url": args.url,
              "source_commit": args.source_commit,
              "source_sha256": {name: hashlib.sha256((Path(__file__).resolve().parents[1] / name).read_bytes()).hexdigest()
                                for name in ("warehouse/api.py", "warehouse/dispatcher.py", "warehouse/robot.py")},
              "python": platform.python_version(), "platform": platform.platform(),
              "os_release": release.read_text() if release.exists() else None,
              "observability": {"partial_loss_health": partial_health,
                                "all_offline_health": all_offline_health,
                                "event_codes": sorted(events_before_reset)}}
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
