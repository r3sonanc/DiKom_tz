import math
import random
import unittest
from heapq import heappop, heappush
from types import MethodType

from warehouse.robot import ROBOT_RADIUS, WorldCore
from tests.support import FleetHarness


def _angle_is_swept(angle, start, sweep):
    if sweep >= 0:
        return (angle - start) % math.tau <= sweep + 1e-12
    return (start - angle) % math.tau <= -sweep + 1e-12


def trajectory_points(x, y, yaw, speed, turn, dt, observers=()):
    if abs(turn) < 1e-12 or speed == 0:
        end = (x + speed * math.cos(yaw) * dt,
               y + speed * math.sin(yaw) * dt)
        points = [(x, y), end]
        dx, dy = end[0] - x, end[1] - y
        length = dx * dx + dy * dy
        if length:
            for ox, oy in observers:
                projection = ((ox - x) * dx + (oy - y) * dy) / length
                t = min(1, max(0, projection))
                points.append((x + t * dx, y + t * dy))
        return points
    center = (x - speed / turn * math.sin(yaw),
              y + speed / turn * math.cos(yaw))
    radius = abs(speed / turn)
    start = math.atan2(y - center[1], x - center[0])
    sweep = turn * dt
    candidates = [start, start + sweep]
    candidates.extend(angle for angle in (0, math.pi / 2, math.pi, 3 * math.pi / 2)
                      if _angle_is_swept(angle, start, sweep))
    candidates.extend(angle for angle in
                      (math.atan2(oy - center[1], ox - center[0]) for ox, oy in observers)
                      if _angle_is_swept(angle, start, sweep))
    return [(center[0] + radius * math.cos(angle),
             center[1] + radius * math.sin(angle)) for angle in candidates]

def _segment_clearance(a, b, c, d):
    def cross(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    boxes_overlap = all(max(min(a[i], b[i]), min(c[i], d[i])) <=
                        min(max(a[i], b[i]), max(c[i], d[i])) for i in (0, 1))
    if (boxes_overlap and cross(a, b, c) * cross(a, b, d) <= 0
            and cross(c, d, a) * cross(c, d, b) <= 0):
        return 0.0

    def distance(point, start, end):
        direction = (end[0] - start[0], end[1] - start[1])
        length = direction[0] ** 2 + direction[1] ** 2
        t = sum((point[i] - start[i]) * direction[i] for i in (0, 1)) / length
        closest = tuple(start[i] + min(1, max(0, t)) * direction[i] for i in (0, 1))
        return math.dist(point, closest)

    return min(distance(a, c, d), distance(b, c, d), distance(c, a, b), distance(d, a, b))

class MapSafetyAssumptionTests(unittest.TestCase):
    def test_nodes_fit_robot_bodies_and_are_physically_distinct(self):
        for map_name in ("normal", "hardcore"):
            graph = WorldCore(map_name, 1).map
            nodes = list(graph.nodes.values())
            for index, node in enumerate(nodes):
                self.assertGreater(node.x, ROBOT_RADIUS)
                self.assertLess(node.x, graph.width - ROBOT_RADIUS)
                self.assertGreater(node.y, ROBOT_RADIUS)
                self.assertLess(node.y, graph.height - ROBOT_RADIUS)
                for other in nodes[index + 1:]:
                    self.assertGreaterEqual(math.dist((node.x, node.y), (other.x, other.y)),
                                            2 * ROBOT_RADIUS)

    def test_edges_with_disjoint_reserved_endpoints_have_body_clearance(self):
        for map_name in ("normal", "hardcore"):
            graph = WorldCore(map_name, 1).map
            for index, edge in enumerate(graph.edges):
                for other in graph.edges[index + 1:]:
                    if set(edge) & set(other):
                        continue
                    endpoints = [graph.nodes[node] for node in (*edge, *other)]
                    points = [(node.x, node.y) for node in endpoints]
                    self.assertGreaterEqual(_segment_clearance(*points), 2 * ROBOT_RADIUS,
                                            (map_name, edge, other))

class CollisionRegressionTests(unittest.TestCase):
    def test_manual_arc_cannot_cross_wall_between_safe_endpoints(self):
        world = WorldCore("normal", 1)
        robot = world.robots["robot1"]
        robot.x, robot.y, robot.yaw = 4, 0.500009, -0.009
        before = (robot.x, robot.y)
        points = trajectory_points(robot.x, robot.y, robot.yaw, 0.8, 1.8, 0.02)
        self.assertLess(min(y for _, y in points), ROBOT_RADIUS)
        self.assertTrue(world.command({"world_id": world.world_id, "robot_id": "robot1",
            "type": "manual", "command_id": "curve", "command_seq": 1,
            "linear": 0.8, "angular": 1.8}, 0))
        world.tick(0.02, 0.02)
        self.assertEqual((robot.x, robot.y), before)
        self.assertEqual(robot.error["code"], "COLLISION_WALL")

    def test_subnanometre_overlap_is_rejected_instead_of_allowed_by_epsilon(self):
        world = WorldCore("normal", 2)
        robot, other = world.robots.values()
        robot.x, robot.y, robot.yaw = 4, 4, 0
        other.x, other.y = 5.016 - 5e-10, 4
        before = (robot.x, robot.y)
        accepted = robot._integrate(0.8, 0, 0.02, world.robots.values())
        self.assertFalse(accepted)
        self.assertEqual((robot.x, robot.y), before)
        self.assertEqual(robot.error["code"], "COLLISION_ROBOT")

    def test_arrival_snap_cannot_teleport_into_another_robot(self):
        world = WorldCore("normal", 2)
        robot, other = world.robots.values()
        robot.x, robot.y = 6 - 8e-8, 6
        other.x, other.y = 7 - 4e-8, 6
        robot.edge = ("r2c1", "r2c2")
        before = (robot.x, robot.y)
        self.assertGreater(math.dist(before, (other.x, other.y)), 2 * ROBOT_RADIUS)
        self.assertTrue(world.command({"world_id": world.world_id, "robot_id": "robot1",
            "type": "move", "path": ["r2c1", "r2c2"],
            "command_id": "arrival", "command_seq": 1}, 0))
        world.tick(0.02, 0.02)
        self.assertGreaterEqual(math.dist((robot.x, robot.y), (other.x, other.y)),
                                2 * ROBOT_RADIUS)
        self.assertEqual((robot.x, robot.y), before)
        self.assertEqual(robot.error["code"], "COLLISION_ROBOT")

    def test_clear_error_snap_cannot_move_body_into_a_neighbour(self):
        world = WorldCore("normal", 2)
        robot, other = world.robots.values()
        robot.x, robot.y = 5.9, 6
        other.x, other.y = 6.95, 6
        robot.error = {"code": "STUCK", "message": "Recover near a graph node"}
        before = (robot.x, robot.y)
        self.assertGreater(math.dist(before, (other.x, other.y)), 2 * ROBOT_RADIUS)
        accepted = world.command({"world_id": world.world_id, "robot_id": "robot1",
            "type": "clear_error", "command_id": "recover", "command_seq": 1}, 0)
        self.assertFalse(accepted)
        self.assertEqual((robot.x, robot.y), before)
        self.assertEqual(robot.error["code"], "STUCK")

class MotionAudit:
    def __init__(self, world):
        self.world = world
        self.accepted = 0
        self.rejected = 0
        self.minimum_distance = math.inf
        for robot in world.robots.values():
            original = robot._integrate

            def checked(instance, linear, angular, dt, others, integrate=original):
                before = (instance.x, instance.y, instance.yaw)
                neighbours = [(other.x, other.y) for other in others
                              if other is not instance and other.robot_id != instance.robot_id]
                accepted = integrate(linear, angular, dt, others)
                if not accepted:
                    self.rejected += 1
                    assert (instance.x, instance.y, instance.yaw) == before, (
                        "rejected motion changed pose", before, instance.x, instance.y, instance.yaw)
                    return False
                self.accepted += 1
                points = trajectory_points(*before, linear, angular, dt, neighbours)
                for x, y in points:
                    assert x >= ROBOT_RADIUS, ("left wall", before, x, y)
                    assert x <= world.map.width - ROBOT_RADIUS, ("right wall", before, x, y)
                    assert y >= ROBOT_RADIUS, ("bottom wall", before, x, y)
                    assert y <= world.map.height - ROBOT_RADIUS, ("top wall", before, x, y)
                    for neighbour in neighbours:
                        distance = math.dist((x, y), neighbour)
                        self.minimum_distance = min(self.minimum_distance, distance)
                        assert distance >= 2 * ROBOT_RADIUS, (
                            "swept overlap", before, linear, angular, dt, neighbour, distance)
                return True

            robot._integrate = MethodType(checked, robot)
            original_tick = robot.tick

            def checked_tick(instance, dt, now, others=(), tick=original_tick):
                tick(dt, now, others)
                self.assert_positions()

            robot.tick = MethodType(checked_tick, robot)

    def assert_positions(self):
        robots = list(self.world.robots.values())
        for index, robot in enumerate(robots):
            assert ROBOT_RADIUS <= robot.x <= self.world.map.width - ROBOT_RADIUS
            assert ROBOT_RADIUS <= robot.y <= self.world.map.height - ROBOT_RADIUS
            for other in robots[index + 1:]:
                distance = math.dist((robot.x, robot.y), (other.x, other.y))
                self.minimum_distance = min(self.minimum_distance, distance)
                assert distance >= 2 * ROBOT_RADIUS, (robot.robot_id, other.robot_id, distance)

class AuditedFleet(FleetHarness):
    def __init__(self, map_name="normal", robots=3, seed=7):
        super().__init__(map_name, robots, seed)
        self.audit = MotionAudit(self.world)

    def step(self, seconds=0.1):
        super().step(seconds)
        self.audit.assert_positions()

class UnreliableFleet(AuditedFleet):

    def __init__(self, map_name="hardcore", robots=5, seed=7):
        super().__init__(map_name, robots, seed)
        self.network = random.Random(seed + 1000)
        self.pending_states = []
        self.pending_commands = []
        self.message_number = 0

    def _queue(self, queue, payload):
        if self.network.random() < 0.12:
            return
        self.message_number += 1
        delivery = self.now + self.network.uniform(0, 0.45)
        heappush(queue, (delivery, self.message_number, dict(payload)))
        if self.network.random() < 0.2:
            self.message_number += 1
            heappush(queue, (delivery + self.network.uniform(0, 0.6),
                            self.message_number, dict(payload)))

    def send(self, command):
        command = dict(command)
        command_id = command.get("command_id")
        if command_id is not None:
            if command_id not in self.revisions:
                self.sequence += 1
                self.revisions[command_id] = self.sequence
            command["command_seq"] = self.revisions[command_id]
        command["world_id"] = self.world.world_id
        self._queue(self.pending_commands, command)
        return True

    def step(self, seconds=0.1):
        for _ in range(round(seconds / 0.02)):
            self.now = round(self.now + 0.02, 8)
            while self.pending_commands and self.pending_commands[0][0] <= self.now:
                _, _, command = heappop(self.pending_commands)
                self.world.command(command, self.now)
            self.world.tick(0.02, self.now)
            self.audit.assert_positions()
        for state in self.world.telemetry(self.now):
            self._queue(self.pending_states, state)
        while self.pending_states and self.pending_states[0][0] <= self.now:
            _, _, state = heappop(self.pending_states)
            self.dispatch.ingest(state, self.now)
        for command in self.dispatch.tick(self.now):
            self.send(command)

    def fault(self, robot_id, **fields):
        return self.world.command({"world_id": self.world.world_id,
                                   "robot_id": robot_id, "type": "fault", **fields}, self.now)

class SweptSafetyTests(unittest.TestCase):
    def test_large_time_step_manual_forward_reverse_and_turns_stay_inside_walls(self):
        for speed in (-0.8, 0.8):
            for turn in (-1.8, 0.0, 1.8):
                with self.subTest(speed=speed, turn=turn):
                    world = WorldCore("normal", 1)
                    robot = world.robots["robot1"]
                    robot.x, robot.y = world.map.width - 0.51, 6
                    robot.yaw = 0 if speed > 0 else math.pi
                    audit = MotionAudit(world)
                    world.command({"world_id": world.world_id, "robot_id": "robot1",
                        "type": "manual", "command_id": "large-step", "command_seq": 1,
                        "linear": speed, "angular": turn}, 0)
                    world.tick(100, 0.1)
                    audit.assert_positions()
                    self.assertEqual(robot.error["code"], "COLLISION_WALL")

    def test_two_manual_robots_keep_exact_swept_separation_under_adversarial_commands(self):
        rng = random.Random(314159)
        accepted = rejected = 0
        for case in range(80):
            world = WorldCore("normal", 2)
            first, second = world.robots.values()
            first.x, first.y = 4, 6
            second.x, second.y = 5.05, 6
            first.yaw = 0 if case == 0 else rng.uniform(-math.pi, math.pi)
            second.yaw = math.pi if case == 0 else rng.uniform(-math.pi, math.pi)
            audit = MotionAudit(world)
            for frame in range(1, 21):
                now = frame / 10
                for robot in world.robots.values():
                    world.command({"world_id": world.world_id, "robot_id": robot.robot_id,
                        "type": "manual", "command_id": f"manual-{frame}", "command_seq": frame,
                        "linear": 0.8 if case == 0 else rng.choice((-0.8, 0.8)),
                        "angular": 0 if case == 0 else rng.choice((-1.8, 0.0, 1.8))}, now)
                world.tick(0.1, now)
                audit.assert_positions()
            accepted += audit.accepted
            rejected += audit.rejected
        self.assertGreater(accepted, 100)
        self.assertGreater(rejected, 0)

    def test_head_on_tasks_and_narrow_corridor_yield_with_continuous_clearance(self):
        scenarios = (("normal", ("r2c0", "r2c4"), ("r2c4", "r2c0")),
                     ("hardcore", ("r0c5", "r2c5"), ("r4c5", "r0c5")))
        for name, starts, goals in scenarios:
            with self.subTest(map=name):
                fleet = AuditedFleet(name, 2)
                for robot, start in zip(fleet.world.robots.values(), starts):
                    node = fleet.world.map.nodes[start]
                    robot.x, robot.y = node.x, node.y
                    robot.last_node_id = start
                fleet.step()
                for robot_id, target in zip(fleet.world.robots, goals):
                    fleet.dispatch.submit(robot_id, "move", target)
                fleet.until(lambda: not fleet.dispatch.goals, timeout=180)
                self.assertTrue(all(robot.error is None for robot in fleet.world.robots.values()))
                self.assertGreaterEqual(fleet.audit.minimum_distance, 2 * ROBOT_RADIUS)

    def test_multiseed_dark_factory_no_overlaps_in_any_accepted_line_or_arc(self):
        for name, count in (("normal", 3), ("hardcore", 5)):
            for seed in (0, 7, 19, 97):
                with self.subTest(map=name, seed=seed):
                    fleet = AuditedFleet(name, count, seed)
                    fleet.step()
                    fleet.dispatch.set_dark_factory(True)
                    fleet.advance(600)
                    self.assertGreaterEqual(fleet.dispatch.metrics["completed_tasks"], count * 2)
                    self.assertGreater(fleet.audit.accepted, 1000)
                    self.assertTrue(all(robot.error is None for robot in fleet.world.robots.values()))
                    self.assertGreaterEqual(fleet.audit.minimum_distance, 2 * ROBOT_RADIUS)

    def test_delayed_reordered_duplicate_and_lost_packets_preserve_separation(self):
        for seed in (3, 17, 43):
            with self.subTest(seed=seed):
                fleet = UnreliableFleet(seed=seed)
                fleet.dispatch.set_dark_factory(True)
                for frame in range(1800):
                    if frame == 200:
                        fleet.fault("robot1", link_lost=True)
                    elif frame == 280:
                        fleet.fault("robot1", link_lost=False)
                    elif frame == 500:
                        fleet.fault("robot2", throttle=True)
                    elif frame == 650:
                        fleet.fault("robot2", throttle=False)
                    fleet.step()
                self.assertGreater(fleet.dispatch.metrics["discarded_telemetry"], 0)
                self.assertGreater(fleet.dispatch.metrics["completed_tasks"], 0)
                self.assertTrue(all(robot.error is None for robot in fleet.world.robots.values()))
                self.assertGreaterEqual(fleet.audit.minimum_distance, 2 * ROBOT_RADIUS)
