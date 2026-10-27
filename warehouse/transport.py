import json
import threading
from collections import OrderedDict

class RosTransport:
    def __init__(self):
        self._lock = threading.Lock()
        self._latest = {}
        self._revisions = OrderedDict()
        self._sequence = 0
        self._node = None
        self._thread = None
        self._closing = False

    def start(self):
        import rclpy
        from rclpy.context import Context
        from rclpy.duration import Duration
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        from std_msgs.msg import String

        self._closing = False
        self._context = Context()
        rclpy.init(args=[], context=self._context)
        self._node = Node("warehouse_dispatcher", context=self._context)
        commands = QoSProfile(
            depth=100,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
            lifespan=Duration(seconds=0.5),
        )
        states = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._publisher = self._node.create_publisher(String, "/warehouse/commands", commands)
        self._node.create_subscription(String, "/warehouse/states", self._receive, states)
        self._executor = SingleThreadedExecutor(context=self._context)
        self._executor.add_node(self._node)
        self._thread = threading.Thread(target=self._executor.spin, daemon=True, name="ros-bridge")
        self._thread.start()

    def is_healthy(self):
        if self._closing or self._node is None or self._thread is None:
            return False
        try:
            return self._thread.is_alive() and self._context.ok()
        except (RuntimeError, AttributeError):
            return False

    def _receive(self, message):
        try:
            state = json.loads(message.data)
            robot_id = state["robot_id"]
            if robot_id not in {"robot1", "robot2", "robot3", "robot4", "robot5"}:
                return
            with self._lock:
                self._latest[robot_id] = state
        except (ValueError, KeyError, TypeError):
            if self._node:
                self._node.get_logger().warning("Некорректная телеметрия робота отклонена")

    def receive(self):
        with self._lock:
            states = list(self._latest.values())
            self._latest.clear()
        return states

    def send(self, command):
        from std_msgs.msg import String

        command = dict(command)
        command_id = command.get("command_id")
        if command_id:
            key = (command.get("world_id"), command.get("robot_id"), command_id)
            if key not in self._revisions:
                self._sequence += 1
                self._revisions[key] = self._sequence
            self._revisions.move_to_end(key)
            command["command_seq"] = self._revisions[key]
            if len(self._revisions) > 1024:
                self._revisions.popitem(last=False)
        self._publisher.publish(String(data=json.dumps(command, allow_nan=False)))

    def close(self):
        self._closing = True
        if self._thread:
            self._executor.shutdown(timeout_sec=2)
            self._thread.join(timeout=2)
            self._node.destroy_node()
            self._context.try_shutdown()
