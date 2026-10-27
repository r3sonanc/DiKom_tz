import math
import unittest

from warehouse.map import load_map
from warehouse.robot import MANUAL_LEASE, MOVE_LEASE, ROBOT_RADIUS, RobotCore, WorldCore

class RobotTests(unittest.TestCase):
    def setUp(self):
        self.robot = RobotCore("robot1", load_map(), "p1")
        self.now = 0.0
        self.sequence = 0

    def command(self, kind, **fields):
        self.sequence += 1
        payload = {"type": kind, "command_id": f"c{self.sequence}", "command_seq": self.sequence, **fields}
        return self.robot.command(payload, self.now), payload

    def advance(self, seconds, renew=None, others=()):
        for _ in range(round(seconds / 0.02)):
            self.now += 0.02
            if renew:
                self.robot.command(renew, self.now, others)
            self.robot.tick(0.02, self.now, others)

    def test_autonomous_move_turns_and_finishes_on_exact_node(self):
        self.robot.yaw = 0
        accepted, move = self.command("move", path=["p1", "r1c5"])
        self.assertTrue(accepted)
        self.advance(6, move)
        self.assertEqual(self.robot.node_id, "r1c5")
        self.assertEqual(self.robot.status, "idle")
        self.assertEqual(self.robot.completed_command_id, move["command_id"])
        self.assertAlmostEqual(abs(self.robot.yaw), math.pi)
        self.assertEqual(self.robot.linear, 0)

    def test_lease_stops_and_duplicate_resumes_without_restarting(self):
        _, move = self.command("move", path=["p1", "r1c5"])
        self.advance(MOVE_LEASE + 0.2)
        position = self.robot.x
        self.assertEqual(self.robot.status, "paused")
        self.advance(1)
        self.assertEqual(self.robot.x, position)
        self.advance(4, move)
        self.assertEqual(self.robot.node_id, "r1c5")

    def test_stop_cannot_be_undone_by_delayed_command(self):
        _, move = self.command("move", path=["p1", "r1c5"])
        self.advance(0.5, move)
        self.command("stop")
        x = self.robot.x
        self.robot.command(move, self.now)
        self.advance(0.4)
        self.assertEqual(self.robot.x, x)
        accepted, resumed = self.command("move", path=["p1", "r1c5"])
        self.assertTrue(accepted)
        self.advance(4, resumed)
        self.assertEqual(self.robot.node_id, "r1c5")

    def test_manual_reverse_rotation_and_deadman(self):
        _, reverse = self.command("manual", linear=-0.6, angular=0)
        self.advance(0.2, reverse)
        self.assertGreater(self.robot.x, 14)
        self.command("manual", linear=0, angular=1)
        yaw = self.robot.yaw
        self.advance(0.2)
        self.assertAlmostEqual((self.robot.yaw - yaw) % (2 * math.pi), 0.2)
        self.advance(MANUAL_LEASE + 0.1)
        self.assertEqual(self.robot.status, "idle")
        self.assertEqual(self.robot.angular, 0)

    def test_physical_wall_blocks_manual_motion_and_allows_backing_away(self):
        self.robot.yaw = 0
        _, manual = self.command("manual", linear=0.8)
        self.advance(4, manual)
        self.assertEqual(self.robot.error["code"], "COLLISION_WALL")
        self.assertLess(self.robot.x + ROBOT_RADIUS, self.robot.map.width)
        x = self.robot.x
        _, reverse = self.command("manual", linear=-0.8)
        self.advance(0.3, reverse)
        self.assertLess(self.robot.x, x)
        self.assertIsNone(self.robot.error)

    def test_robots_cannot_overlap_even_without_dispatcher(self):
        other = RobotCore("robot2", self.robot.map, "r1c5")
        _, manual = self.command("manual", linear=0.8)
        self.advance(4, manual, [other])
        self.assertEqual(self.robot.error["code"], "COLLISION_ROBOT")
        self.assertGreaterEqual(math.hypot(self.robot.x - other.x, self.robot.y - other.y), 2 * ROBOT_RADIUS)

    def test_unknown_node_and_non_edge_are_rejected(self):
        self.assertFalse(self.command("move", path=["p1", "missing"])[0])
        self.assertEqual(self.robot.error["code"], "POINT_NOT_FOUND")
        self.command("clear_error")
        self.assertFalse(self.command("move", path=["p1", "p2"])[0])
        self.assertEqual(self.robot.error["code"], "INVALID_PATH")

    def test_nonfinite_manual_velocity_is_rejected(self):
        self.assertFalse(self.command("manual", linear=math.nan)[0])
        self.assertEqual(self.robot.error["code"], "INVALID_COMMAND")
        self.assertEqual(self.robot.linear, 0)
        self.assertFalse(self.command("manual", angular=10 ** 400)[0])

    def test_off_graph_recovery_requires_nearby_node(self):
        self.robot.x, self.robot.y = 5, 9
        self.assertFalse(self.command("move", path=["p1", "r1c5"])[0])
        self.assertEqual(self.robot.error["code"], "OFF_GRAPH")
        self.assertFalse(self.command("clear_error")[0])
        self.robot.x, self.robot.y = 14.1, 8
        self.assertTrue(self.command("clear_error")[0])
        self.assertEqual(self.robot.node_id, "p1")

    def test_manual_watchdog_reports_off_graph_and_manual_can_recover(self):
        self.robot.x, self.robot.y = 5, 9
        self.assertTrue(self.command("manual", linear=0)[0])
        self.advance(MANUAL_LEASE + 0.1)
        self.assertEqual(self.robot.error["code"], "OFF_GRAPH")
        self.assertTrue(self.command("manual", linear=0.2)[0])
        self.assertIsNone(self.robot.error)

    def test_injected_stuck_stops_and_can_resume_on_retained_edge(self):
        _, move = self.command("move", path=["p1", "r1c5"])
        self.advance(0.8, move)
        self.robot.set_stuck(True)
        x = self.robot.x
        self.advance(1, move)
        self.assertEqual(self.robot.x, x)
        self.assertEqual(self.robot.error["code"], "STUCK")
        self.assertFalse(self.command("clear_error")[0])
        self.robot.set_stuck(False)
        self.assertTrue(self.command("clear_error")[0])
        accepted, resumed = self.command("move", path=["p1", "r1c5"])
        self.assertTrue(accepted)
        self.advance(4, resumed)
        self.assertEqual(self.robot.node_id, "r1c5")

    def test_error_clear_near_destination_preserves_edge_for_resume(self):
        _, move = self.command("move", path=["p1", "r1c5"])
        self.advance(2.3, move)
        self.robot.set_stuck(True)
        self.robot.set_stuck(False)
        x = self.robot.x
        self.assertTrue(self.command("clear_error")[0])
        self.assertEqual(self.robot.x, x)
        self.assertEqual(self.robot.edge, ("p1", "r1c5"))
        accepted, resumed = self.command("move", path=["p1", "r1c5"])
        self.assertTrue(accepted)
        self.advance(1, resumed)
        self.assertEqual(self.robot.node_id, "r1c5")

    def test_telemetry_has_wheel_speeds_and_monotonic_sequence(self):
        self.command("manual", linear=0.5, angular=1)
        self.advance(0.02)
        first, second = self.robot.telemetry(self.now), self.robot.telemetry(self.now)
        self.assertGreater(second["seq"], first["seq"])
        self.assertEqual(first["boot_id"], second["boot_id"])
        self.assertAlmostEqual(first["velocity"]["left_wheel"], 2.5)
        self.assertAlmostEqual(first["velocity"]["right_wheel"], 7.5)

class WorldTests(unittest.TestCase):
    def test_reset_is_idempotent_and_rejects_old_world(self):
        world = WorldCore()
        reset = {"type": "reset", "world_id": "new", "reset_seq": 10, "map": "hardcore", "robots": 5}
        self.assertTrue(world.command(reset, 0))
        boot = world.robots["robot1"].boot_id
        self.assertTrue(world.command(reset, 0))
        self.assertEqual(world.robots["robot1"].boot_id, boot)
        self.assertFalse(world.command({"world_id": "initial", "type": "stop", "robot_id": "robot1"}, 0))
        self.assertEqual(len(world.robots), 5)
        self.assertFalse(world.command({**reset, "world_id": "bad", "robots": 6}, 0))
        self.assertEqual(world.world_id, "new")

    def test_reset_rejects_reordered_unknown_world_and_changed_same_world_generation(self):
        world = WorldCore()
        reset = {"type": "reset", "world_id": "new", "reset_seq": 30, "map": "hardcore", "robots": 5}
        self.assertTrue(world.command(reset, 0))
        boot = world.robots["robot1"].boot_id
        self.assertFalse(world.command({**reset, "world_id": "never-seen-old", "reset_seq": 20}, 0))
        self.assertFalse(world.command({**reset, "reset_seq": 31}, 0))
        self.assertFalse(world.command({**reset, "world_id": "newer", "reset_seq": True}, 0))
        self.assertEqual(world.robots["robot1"].boot_id, boot)
        self.assertTrue(world.command({**reset, "world_id": "newer", "reset_seq": 31}, 0))
        self.assertFalse(world.command(reset, 0))
        self.assertEqual(world.world_id, "newer")

    def test_link_loss_stops_physics_and_suppresses_both_directions(self):
        world = WorldCore(robot_count=1)
        move = {"world_id": "initial", "robot_id": "robot1", "command_id": "move", "command_seq": 1,
                "type": "move", "path": ["p1", "r1c5"]}
        fault = {"world_id": "initial", "robot_id": "robot1", "type": "fault", "link_lost": True}
        self.assertTrue(world.command(move, 0))
        self.assertTrue(world.command(fault, 0.1))
        self.assertFalse(world.command(move, 0.2))
        self.assertEqual(world.telemetry(0.2), [])
        for step in range(1, 81):
            world.tick(0.02, step * 0.02)
        robot = world.robots["robot1"]
        self.assertEqual(robot.status, "paused")
        x = robot.x
        self.assertTrue(world.command({**fault, "link_lost": False}, 1.7))
        self.assertTrue(world.command(move, 1.7))
        world.tick(0.02, 1.72)
        self.assertLess(robot.x, x)
        self.assertEqual(world.telemetry(1.72)[0]["world_id"], "initial")

    def test_throttle_limits_receive_and_telemetry_to_one_hertz(self):
        world = WorldCore(robot_count=1)
        world.command({"world_id": "initial", "robot_id": "robot1", "type": "fault", "throttle": True}, 0)
        move = {"world_id": "initial", "robot_id": "robot1", "type": "move", "command_id": "m",
                "command_seq": 1, "path": ["p1", "r1c5"]}
        self.assertTrue(world.command(move, 0))
        self.assertFalse(world.command(move, 0.5))
        self.assertTrue(world.command(move, 1.0))
        self.assertEqual(len(world.telemetry(0)), 1)
        self.assertEqual(len(world.telemetry(0.5)), 0)
        self.assertEqual(len(world.telemetry(1)), 1)


if __name__ == "__main__":
    unittest.main()
