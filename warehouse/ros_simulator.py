"""ROS 2 Jazzy adapter: one physics world, individual robot telemetry on DDS."""

import json
import os
import time

import rclpy
from rclpy.duration import Duration
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from warehouse.robot import WorldCore


class SimulatorNode(Node):
    def __init__(self):
        super().__init__("warehouse_simulator")
        self.world = WorldCore(os.environ.get("WAREHOUSE_MAP", "normal"),
                               int(os.environ.get("WAREHOUSE_ROBOTS", "3")),
                               os.environ.get("WAREHOUSE_WORLD_ID", "initial"))
        commands_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST, depth=100,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
            lifespan=Duration(seconds=0.5),
        )
        states_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST, depth=10,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        self._publisher = self.create_publisher(String, "/warehouse/states", states_qos)
        self._subscription = self.create_subscription(
            String, "/warehouse/commands", self._command, commands_qos)
        self._last_tick = time.monotonic()
        # Single-threaded executor serializes all physics and command mutations.
        self._timer = self.create_timer(0.02, self._tick)
        self.get_logger().info("Warehouse simulator ready (50 Hz physics, 10 Hz telemetry)")

    def _command(self, message: String) -> None:
        if len(message.data) > 16384:
            self.get_logger().warning("Rejected oversized command")
            return
        try:
            payload = json.loads(message.data)
            self.world.command(payload, time.monotonic())
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            self.get_logger().warning(f"Rejected malformed command: {exc}")

    def _tick(self) -> None:
        now = time.monotonic()
        dt, self._last_tick = now - self._last_tick, now
        self.world.tick(dt, now)
        for state in self.world.telemetry(now):
            self._publisher.publish(String(data=json.dumps(state, allow_nan=False)))


def main(args=None) -> None:
    rclpy.init(args=args)
    node = None
    try:
        node = SimulatorNode()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
