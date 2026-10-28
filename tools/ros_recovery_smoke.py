import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import rclpy
from std_msgs.msg import String

from warehouse.api import FleetRuntime, create_app
from warehouse.robot import RobotCore
from warehouse.ros_simulator import SimulatorNode
from warehouse.transport import RosTransport


def serve_browser(simulator, port):
    import uvicorn
    from rclpy.executors import ExternalShutdownException

    app = create_app(RosTransport(), "normal", 2)
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port,
                                         log_level="warning", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True, name="browser-test-api")
    trigger = Path("/tmp/dikom-browser-reboot")
    trigger.unlink(missing_ok=True)
    thread.start()
    print(f"Browser harness: port={port}, trigger={trigger}", flush=True)
    try:
        while rclpy.ok() and thread.is_alive():
            rclpy.spin_once(simulator, timeout_sec=0.02)
            robot = simulator.world.robots["robot1"]
            if trigger.exists() and robot.node_id is None and robot.current_order:
                source = robot.current_order["path"][0]
                simulator.world.robots["robot1"] = RobotCore("robot1", simulator.world.map, source)
                trigger.unlink()
                print(f"Browser test: robot1 rebooted mid-edge at {source}", flush=True)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", help="Commit containing the checked sources")
    parser.add_argument("--output")
    parser.add_argument("--browser-port", type=int, help="Serve the actual app for a browser reboot check")
    args = parser.parse_args()
    if not args.browser_port and (not args.source_commit or not args.output):
        parser.error("Automatic smoke mode requires --source-commit and --output")
    if not os.environ.get("ROS_DOMAIN_ID"):
        parser.error("Set an isolated ROS_DOMAIN_ID, for example 91")
    os.environ["WAREHOUSE_MAP"], os.environ["WAREHOUSE_ROBOTS"] = "normal", "2"
    rclpy.init(args=[])
    simulator = SimulatorNode()
    transport = RosTransport()
    next_step = 0.0
    checks = []
    try:
        if args.browser_port:
            serve_browser(simulator, args.browser_port)
            return
        transport.start()
        runtime = FleetRuntime(transport, "normal", 2)

        def wait(predicate, timeout=30, inject=None):
            nonlocal next_step
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                rclpy.spin_once(simulator, timeout_sec=0.02)
                if inject:
                    inject()
                now = time.monotonic()
                if now >= next_step:
                    runtime.step(now)
                    next_step = now + 0.1
                    assert not any(robot.error for robot in simulator.world.robots.values())
                    if predicate(runtime.dispatcher.snapshot(now)):
                        return runtime.dispatcher.snapshot(now)
            raise AssertionError(f"Timed out: {runtime.dispatcher.snapshot(time.monotonic())}")

        wait(lambda s: len(runtime.raw_states) == 2 and all(r["online"] for r in s["robots"].values()))
        nodes = set(simulator.get_node_names())
        assert {"warehouse_simulator", "warehouse_dispatcher"} <= nodes
        checks.append("two actual ROS nodes discovered and current world synchronized")
        runtime.dispatcher.submit("robot1", "move", "r2c4", now=time.monotonic())
        wait(lambda s: runtime.raw_states["robot1"]["node_id"] is None)
        original = dict(runtime.dispatcher._round["robot1"])
        old_state = copy.deepcopy(runtime.raw_states["robot1"])
        simulator.world.robots["robot1"] = RobotCore("robot1", simulator.world.map, original["path"][0])
        recovering = wait(lambda s: s["robots"]["robot1"]["link_state"] == "RECOVERING"
                          and s["robots"]["robot1"]["recovery_ready"])
        assert recovering["paused"] and recovering["safety_stop"]
        assert set(original["path"]) <= set(recovering["reservations"]["robot1"])
        assert recovering["goals"]["robot1"] == "r2c4"
        assert simulator.world.robots["robot1"].current_order is None
        checks.append("reboot mid-edge preserves both endpoint reserves and same mission; RECOVERING and recovery_ready")

        new_boot = runtime.dispatcher.robots["robot1"].boot_id
        discarded = runtime.dispatcher.metrics["discarded_telemetry"]
        old_state["seq"] += 10000
        simulator.world.faults["robot1"]["link_lost"] = True
        try:
            wait(lambda s: s["metrics"]["discarded_telemetry"] > discarded, timeout=1,
                 inject=lambda: simulator._publisher.publish(String(data=json.dumps(old_state))))
        finally:
            simulator.world.faults["robot1"]["link_lost"] = False
        assert runtime.dispatcher.robots["robot1"].boot_id == new_boot
        checks.append("high-sequence telemetry from retired boot rejected through DDS")
        wait(lambda s: s["robots"]["robot1"]["recovery_ready"])
        runtime.dispatcher.submit("robot1", "resume", now=time.monotonic())
        wait(lambda s: s["robots"]["robot1"]["link_state"] == "ONLINE" and not s["safety_stop"])
        completed = wait(lambda s: s["metrics"]["completed_tasks"] == 1 and not s["goals"], timeout=45)
        assert runtime.raw_states["robot1"]["node_id"] == "r2c4"
        settled_at = time.monotonic()
        wait(lambda s: time.monotonic() - settled_at >= 0.5
             and s["metrics"]["completed_tasks"] == 1 and not s["goals"], timeout=2)
        assert not any(robot.error for robot in simulator.world.robots.values())
        checks.append("backend operator resume revalidates safety, replans and completes original mission exactly once without robot errors")
        report = {
            "ok": True, "source_commit": args.source_commit,
            "ros_domain_id": int(os.environ["ROS_DOMAIN_ID"]), "checks": checks,
            "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                              for name in ("warehouse/api.py", "warehouse/dispatcher.py", "warehouse/robot.py",
                                           "warehouse/transport.py", "warehouse/ros_simulator.py")},
            "recovering_snapshot": recovering, "final_metrics": completed["metrics"],
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "checks": checks}, ensure_ascii=False))
    finally:
        transport.close()
        simulator.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
