"""Deterministic differential-drive physics, independent of ROS and the UI."""

from collections import OrderedDict
import math
import uuid

from warehouse.map import WarehouseMap, load_map, segment_distance


BODY_LENGTH = 0.8
BODY_WIDTH = 0.6
ROBOT_RADIUS = math.hypot(BODY_LENGTH / 2, BODY_WIDTH / 2)
WHEEL_TRACK = 0.5
WHEEL_RADIUS = 0.1
MAX_LINEAR = 0.8
MAX_ANGULAR = 1.8
MOVE_LEASE = 0.9
MANUAL_LEASE = 0.35
GRAPH_TOLERANCE = 0.6
NODE_TOLERANCE = 0.06


def angle_difference(target: float, current: float) -> float:
    return (target - current + math.pi) % (2 * math.pi) - math.pi


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


class RobotCore:
    def __init__(self, robot_id: str, warehouse_map: WarehouseMap, start_node: str):
        if start_node not in warehouse_map.nodes:
            raise ValueError("unknown spawn node")
        self.robot_id = robot_id
        self.map = warehouse_map
        node = self.map.nodes[start_node]
        self.x, self.y, self.yaw = node.x, node.y, math.pi
        self.linear = self.angular = 0.0
        self.last_node_id = start_node
        self.edge: tuple[str, str] | None = None
        self.status = "idle"
        self.error: dict | None = None
        self.current_order: dict | None = None
        self.last_command_id: str | None = None
        self.completed_command_id: str | None = None
        self.boot_id = uuid.uuid4().hex
        self.seq = 0
        self._lease_until = 0.0
        self._manual = (0.0, 0.0)
        self._injected_stuck = False
        # Bounded deduplication also prevents a delayed old move undoing a stop.
        self._seen: OrderedDict[str, int] = OrderedDict()
        self._last_command_seq = -1

    @property
    def node_id(self) -> str | None:
        node_id, distance = self.map.nearest_node(self.x, self.y)
        return node_id if distance <= NODE_TOLERANCE else None

    def _fail(self, code: str, message: str) -> bool:
        self.error = {"code": code, "message": message}
        self.status = "error"
        self.linear = self.angular = 0.0
        return False

    def _remember(self, command_id: str, command_seq: int) -> None:
        self._seen[command_id] = command_seq
        if len(self._seen) > 256:
            self._seen.popitem(last=False)
        self.last_command_id = command_id
        self._last_command_seq = command_seq

    def set_stuck(self, enabled: bool) -> None:
        self._injected_stuck = enabled
        if enabled:
            self._fail("STUCK", "Drive is blocked by the injected obstacle")

    def _on_edge(self, edge: tuple[str, str]) -> bool:
        a, b = (self.map.nodes[node_id] for node_id in edge)
        return segment_distance(self.x, self.y, a.x, a.y, b.x, b.y) <= NODE_TOLERANCE

    def command(self, command: dict, now: float, others=()) -> bool:
        """Validate before moving. Repeated move IDs only renew their lease."""
        if not isinstance(command, dict):
            return self._fail("INVALID_COMMAND", "Command must be an object")
        kind = command.get("type")
        command_id = command.get("command_id")
        if not isinstance(command_id, str) or not command_id or len(command_id) > 128:
            return self._fail("INVALID_COMMAND", "A command_id string is required")
        command_seq = command.get("command_seq")
        if isinstance(command_seq, bool) or not isinstance(command_seq, int) or command_seq < 0:
            return self._fail("INVALID_COMMAND", "A nonnegative command_seq integer is required")
        if command_id in self._seen:
            if self._seen[command_id] != command_seq or command_seq < self._last_command_seq:
                return False
            if self.current_order and self.current_order["command_id"] == command_id:
                if kind == "move" and command.get("path") == self.current_order["path"]:
                    self._lease_until = now + MOVE_LEASE
                    if not self.error:
                        self.status = "moving"
                elif kind == "manual":
                    self._lease_until = now + MANUAL_LEASE
            return True
        if command_seq <= self._last_command_seq:
            return False
        self._remember(command_id, command_seq)
        if kind == "stop":
            self.current_order = None
            self.linear = self.angular = 0.0
            self.status = "error" if self.error else "idle"
            self.completed_command_id = command_id
            if not self.error and self.map.distance_to_graph(self.x, self.y) > GRAPH_TOLERANCE:
                self._fail("OFF_GRAPH", "Robot stopped too far from the navigation graph")
            return True
        if kind == "clear_error":
            if self._injected_stuck or self._collision(self.x, self.y, others):
                return self._fail("STUCK", "Remove the obstacle before clearing the error")
            node_id, distance = self.map.nearest_node(self.x, self.y)
            retained_edge = bool(self.edge and self._on_edge(self.edge))
            if distance > 0.2 and not retained_edge:
                return self._fail("OFF_GRAPH", "Use manual control to return within 0.2 m of a node")
            node = self.map.nodes[node_id]
            if not retained_edge:
                if self._collision(node.x, node.y, others):
                    return self._fail("STUCK", "The nearest node is physically occupied")
                self.x, self.y = node.x, node.y
            self.error = None
            self.current_order = None
            self.status = "idle"
            self.linear = self.angular = 0.0
            self.completed_command_id = command_id
            if self.node_id and not retained_edge:
                self.last_node_id, self.edge = self.node_id, None
            return True
        if kind == "manual":
            try:
                linear = _number(command.get("linear", 0.0), "linear")
                angular = _number(command.get("angular", 0.0), "angular")
            except ValueError as exc:
                return self._fail("INVALID_COMMAND", str(exc))
            if self._injected_stuck:
                return self._fail("STUCK", "Remove the injected obstacle first")
            self._manual = (max(-MAX_LINEAR, min(MAX_LINEAR, linear)),
                            max(-MAX_ANGULAR, min(MAX_ANGULAR, angular)))
            self._lease_until = now + MANUAL_LEASE
            self.current_order = {"command_id": command_id, "type": "manual"}
            self.edge = None
            # Manual recovery is possible after a collision or leaving the graph.
            self.error = None
            self.status = "manual"
            return True
        if kind != "move":
            return self._fail("INVALID_COMMAND", "Unsupported command type")
        if self.error:
            return False
        path = command.get("path")
        if not isinstance(path, list) or len(path) != 2 or not all(isinstance(n, str) for n in path):
            return self._fail("INVALID_PATH", "A move must contain [source, destination]")
        if any(node_id not in self.map.nodes for node_id in path):
            return self._fail("POINT_NOT_FOUND", "The requested node does not exist")
        source, destination = path
        if destination not in self.map.adjacency[source]:
            return self._fail("INVALID_PATH", "Movement is only allowed along an existing edge")
        if self.map.distance_to_graph(self.x, self.y) > GRAPH_TOLERANCE:
            return self._fail("OFF_GRAPH", "Robot is too far from the navigation graph")
        source_node = self.map.nodes[source]
        resume = self.edge == (source, destination) and self._on_edge(self.edge)
        if math.hypot(self.x - source_node.x, self.y - source_node.y) > NODE_TOLERANCE and not resume:
            return self._fail("NOT_AT_NODE", "Robot must be at the source node; recover manually")
        self.current_order = {"command_id": command_id, "type": "move", "path": path.copy()}
        self.edge = (source, destination)
        self.last_node_id = source
        self._lease_until = now + MOVE_LEASE
        self.status = "moving"
        return True

    def _collision(self, x: float, y: float, others, padding: float = 0.0) -> str | None:
        # A circumscribed circle safely covers the complete box during rotation.
        radius = ROBOT_RADIUS + padding
        if (x - radius <= 0 or x + radius >= self.map.width
                or y - radius <= 0 or y + radius >= self.map.height):
            return "COLLISION_WALL"
        for other in others:
            if other is self or other.robot_id == self.robot_id:
                continue
            if segment_distance(other.x, other.y, self.x, self.y, x, y) < 2 * ROBOT_RADIUS + padding - 1e-9:
                return "COLLISION_ROBOT"
        return None

    def _integrate(self, linear: float, angular: float, dt: float, others) -> bool:
        next_yaw = self.yaw + angular * dt
        if abs(angular) < 1e-10:
            x = self.x + linear * math.cos(self.yaw) * dt
            y = self.y + linear * math.sin(self.yaw) * dt
        else:
            # Exact unicycle integration, rather than a frame-rate-dependent Euler step.
            x = self.x + linear / angular * (math.sin(next_yaw) - math.sin(self.yaw))
            y = self.y - linear / angular * (math.cos(next_yaw) - math.cos(self.yaw))
        # Bound the tiny arc/chord deviation as well as the swept straight segment.
        collision = self._collision(x, y, others, abs(linear * angular) * dt * dt / 8)
        if collision:
            return self._fail(collision, "Motion stopped before physical contact")
        self.x, self.y = x, y
        self.yaw = angle_difference(next_yaw, 0.0)
        self.linear, self.angular = linear, angular
        return True

    def tick(self, dt: float, now: float, others=()) -> None:
        """Advance at most 100 ms; split long frames so obstacles cannot be skipped."""
        if not math.isfinite(dt) or dt <= 0:
            return
        dt = min(dt, 0.1)
        self.linear = self.angular = 0.0
        if self.error or self._injected_stuck:
            return
        if not self.current_order:
            return
        if now >= self._lease_until:
            if self.current_order["type"] == "move":
                self.status = "paused"
            else:
                self.status = "idle"
                self.current_order = None
                if self.map.distance_to_graph(self.x, self.y) > GRAPH_TOLERANCE:
                    self._fail("OFF_GRAPH", "Manual watchdog stopped a robot outside the graph")
            return
        steps = max(1, math.ceil(dt / 0.02))
        step = dt / steps
        for _ in range(steps):
            if self.current_order["type"] == "manual":
                if not self._integrate(*self._manual, step, others):
                    return
                if self.node_id:
                    self.last_node_id, self.edge = self.node_id, None
                continue
            destination = self.map.nodes[self.current_order["path"][1]]
            dx, dy = destination.x - self.x, destination.y - self.y
            distance = math.hypot(dx, dy)
            if distance <= 1e-7:
                self.x, self.y = destination.x, destination.y
                self.last_node_id, self.edge = destination.id, None
                self.completed_command_id = self.current_order["command_id"]
                self.current_order = None
                self.status = "idle"
                self.linear = self.angular = 0.0
                return
            heading = math.atan2(dy, dx)
            turn = angle_difference(heading, self.yaw)
            if abs(turn) > 1e-8:
                angular = max(-MAX_ANGULAR, min(MAX_ANGULAR, turn / step))
                if not self._integrate(0.0, angular, step, others):
                    return
            elif not self._integrate(min(MAX_LINEAR, distance / step), 0.0, step, others):
                return

    def telemetry(self, now: float) -> dict:
        self.seq += 1
        return {"robot_id": self.robot_id, "boot_id": self.boot_id, "seq": self.seq,
                "sim_time": now, "pose": {"x": self.x, "y": self.y, "yaw": self.yaw},
                "velocity": {"linear": self.linear, "angular": self.angular,
                             "left_wheel": (self.linear - self.angular * WHEEL_TRACK / 2) / WHEEL_RADIUS,
                             "right_wheel": (self.linear + self.angular * WHEEL_TRACK / 2) / WHEEL_RADIUS},
                "node_id": self.node_id, "last_node_id": self.last_node_id,
                "edge": list(self.edge) if self.edge else None,
                "status": self.status, "error": self.error.copy() if self.error else None,
                "current_order": dict(self.current_order) if self.current_order else None,
                "last_command_id": self.last_command_id,
                "last_command_seq": self._last_command_seq,
                "completed_command_id": self.completed_command_id}


class WorldCore:
    """One shared clock and collision world, also usable in integration tests."""

    def __init__(self, map_name: str = "normal", robot_count: int = 3,
                 world_id: str = "initial"):
        self.map = load_map(map_name)
        if isinstance(robot_count, bool) or not isinstance(robot_count, int) or not 1 <= robot_count <= len(self.map.parking_nodes):
            raise ValueError("robot count must be between one and the number of parking nodes")
        if not isinstance(world_id, str) or not world_id or len(world_id) > 128:
            raise ValueError("world_id must be a nonempty string of at most 128 characters")
        self.world_id = world_id
        self._reset_seq = 0
        self.robots = {f"robot{index + 1}": RobotCore(f"robot{index + 1}", self.map, node_id)
                       for index, node_id in enumerate(self.map.parking_nodes[:robot_count])}
        self.faults = {robot_id: {"stuck": False, "link_lost": False, "throttle": False}
                       for robot_id in self.robots}
        self._next_receive = dict.fromkeys(self.robots, 0.0)
        self._next_publish = dict.fromkeys(self.robots, 0.0)

    def command(self, command: dict, now: float) -> bool:
        if not isinstance(command, dict):
            return False
        if command.get("type") == "reset":
            sequence = command.get("reset_seq")
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
                return False
            if command.get("world_id") == self.world_id:
                return sequence == self._reset_seq
            if sequence <= self._reset_seq:
                return False
            try:
                replacement = WorldCore(command.get("map", "normal"),
                                        command.get("robots", 3), command.get("world_id"))
            except (ValueError, TypeError):
                return False
            self.__dict__.update(replacement.__dict__)
            self._reset_seq = sequence
            return True
        if command.get("world_id") != self.world_id:
            return False
        robot_id = command.get("robot_id")
        if not isinstance(robot_id, str) or robot_id not in self.robots:
            return False
        robot, faults = self.robots[robot_id], self.faults[robot_id]
        if command.get("type") == "fault":
            for key in faults:
                if key in command and not isinstance(command[key], bool):
                    return False
            faults.update({key: command[key] for key in faults if key in command})
            robot.set_stuck(faults["stuck"])
            return True
        if faults["link_lost"]:
            return False
        if faults["throttle"]:
            if now < self._next_receive[robot_id]:
                return False
            self._next_receive[robot_id] = now + 1.0
        return robot.command(command, now, self.robots.values())

    def tick(self, dt: float, now: float) -> None:
        for robot in self.robots.values():
            robot.tick(dt, now, self.robots.values())

    def _state(self, robot: RobotCore, now: float) -> dict:
        state = robot.telemetry(now)
        state["world_id"] = self.world_id
        state["reset_seq"] = self._reset_seq
        state["fault"] = self.faults[robot.robot_id].copy()
        return state

    def snapshot(self, now: float) -> list[dict]:
        return [self._state(robot, now) for robot in self.robots.values()]

    def telemetry(self, now: float) -> list[dict]:
        states = []
        for robot_id, robot in self.robots.items():
            faults = self.faults[robot_id]
            if faults["link_lost"] or now < self._next_publish[robot_id]:
                continue
            self._next_publish[robot_id] = now + (1.0 if faults["throttle"] else 0.1)
            states.append(self._state(robot, now))
        return states
