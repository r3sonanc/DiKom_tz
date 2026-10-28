"""Recovery barriers, retained resources and transition-only monitoring."""

import copy
import math
import unittest
from unittest.mock import patch

from warehouse.dispatcher import Dispatcher
from warehouse.robot import RobotCore, WorldCore


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.world = WorldCore("normal", 2, "recovery-tests")
        self.dispatch = Dispatcher(self.world.map, list(self.world.robots))
        self.publish(0)

    def publish(self, now, robot_id=None, **changes):
        for state in self.world.snapshot(now):
            if robot_id is None or state["robot_id"] == robot_id:
                state.update(copy.deepcopy(changes))
                self.assertTrue(self.dispatch.ingest(state, now))

    def start_transition(self, target="r2c4"):
        self.dispatch.submit("robot1", "move", target, now=0)
        command = next(c for c in self.dispatch.tick(0) if c["robot_id"] == "robot1")
        self.assertTrue(self.world.command({**command, "world_id": self.world.world_id,
                                            "command_seq": 1}, 0))
        self.world.tick(0.1, 0.1)
        self.publish(0.1)
        return command

    def lose_first(self):
        for now in (0.5, 1.0, 1.5, 1.9):
            self.publish(now, "robot2")
        commands = self.dispatch.tick(2)
        self.assertTrue(commands)
        self.assertTrue(all(c["type"] == "stop" for c in commands))
        self.world.tick(0.1, 2)
        return commands

    def test_loss_in_an_edge_keeps_both_endpoints_and_local_lease_stops_motion(self):
        command = self.start_transition()
        position = (self.world.robots["robot1"].x, self.world.robots["robot1"].y)
        self.lose_first()
        robot = self.world.robots["robot1"]
        self.assertEqual((robot.x, robot.y), position)
        self.assertEqual(robot.linear, 0)
        self.assertEqual(robot.status, "paused")
        for now in (2, 10, 1000):
            self.dispatch.tick(now)
            snapshot = self.dispatch.snapshot(now)
            self.assertEqual(set(snapshot["reservations"]["robot1"]), set(command["path"]))
            self.assertEqual(snapshot["robots"]["robot1"]["link_state"], "OFFLINE")
            self.assertTrue(snapshot["safety_stop"])

    def test_fresh_moving_state_remains_recovering_until_stopped_state_is_confirmed(self):
        original = self.start_transition()
        self.lose_first()
        self.publish(2.1, "robot1", status="moving", velocity={"linear": 0.8, "angular": 0})
        self.publish(2.1, "robot2")
        self.assertEqual(self.dispatch.snapshot(2.1)["robots"]["robot1"]["link_state"], "RECOVERING")
        self.assertTrue(all(c["type"] == "stop" for c in self.dispatch.tick(2.1)))
        self.assertFalse(self.dispatch.snapshot(2.1)["robots"]["robot1"]["recovery_ready"])
        self.publish(2.2)
        commands = self.dispatch.tick(2.2)
        resumed = next(c for c in commands if c["robot_id"] == "robot1" and c["type"] == "move")
        self.assertEqual(resumed["path"], original["path"])
        self.assertNotEqual(resumed["command_id"], original["command_id"])
        self.assertEqual(self.dispatch.snapshot(2.2)["robots"]["robot1"]["link_state"], "ONLINE")
        self.assertEqual(self.dispatch.metrics["link_loss_events_total"], 1)
        self.assertEqual(self.dispatch.metrics["link_recovery_events_total"], 1)

    def test_old_or_duplicate_state_does_not_restore_the_link(self):
        self.start_transition()
        previous = copy.deepcopy(self.dispatch.robots["robot1"].state)
        self.lose_first()
        self.assertFalse(self.dispatch.ingest(previous, 2.1))
        previous["seq"] -= 1
        self.assertFalse(self.dispatch.ingest(previous, 2.2))
        self.assertEqual(self.dispatch.snapshot(2.2)["robots"]["robot1"]["link_state"], "OFFLINE")
        self.assertEqual(self.dispatch.metrics["link_recovery_events_total"], 0)

    def test_delayed_increasing_sequences_cannot_restore_link_or_release_resources(self):
        original = self.start_transition()
        queued = [self.world.snapshot(stamp)[0] for stamp in (0.2, 0.3, 0.4)]
        self.lose_first()
        previous = copy.deepcopy(self.dispatch.robots["robot1"].state)
        for offset, state in enumerate(queued):
            now = 2.1 + offset / 10
            self.assertFalse(self.dispatch.ingest(state, now))
            self.publish(now, "robot2")
            self.assertTrue(all(c["type"] == "stop" for c in self.dispatch.tick(now)))
            snapshot = self.dispatch.snapshot(now)
            self.assertEqual(snapshot["robots"]["robot1"]["link_state"], "OFFLINE")
            self.assertTrue(set(original["path"]) <= set(snapshot["reservations"]["robot1"]))
        self.assertEqual(self.dispatch.robots["robot1"].state, previous)
        self.assertEqual(self.dispatch.metrics["link_recovery_events_total"], 0)
        self.publish(2.4)
        self.dispatch.tick(2.4)
        self.assertEqual(self.dispatch.robots["robot1"].link_state, "ONLINE")

    def test_source_timestamp_is_finite_bounded_and_does_not_run_backwards(self):
        for stamp in (math.nan, math.inf, True, "0.1", 0.201, 10 ** 400, -(10 ** 400)):
            with self.subTest(stamp=stamp):
                state = self.world.snapshot(0.1)[0]
                state["sim_time"] = stamp
                self.assertFalse(self.dispatch.ingest(state, 0.1))
        self.publish(0.2)
        state = self.world.snapshot(0.3)[0]
        state["sim_time"] = 0.1
        self.assertFalse(self.dispatch.ingest(state, 0.3))
        self.assertEqual(self.dispatch.robots["robot1"].received_at, 0.2)

    def test_delivery_delay_does_not_extend_source_heartbeat(self):
        state = self.world.snapshot(0.1)[0]
        self.assertTrue(self.dispatch.ingest(state, 0.5))
        self.assertEqual(self.dispatch.robots["robot1"].received_at, 0.1)
        self.publish(1.61, "robot2")
        self.dispatch.tick(1.61)
        self.assertEqual(self.dispatch.robots["robot1"].link_state, "OFFLINE")

    def test_contradictory_pose_keeps_old_resources_and_blocks_resume(self):
        original = self.start_transition()
        self.lose_first()
        self.publish(2.1, "robot1", node_id="r0c0", edge=None,
                     pose={"x": 8.0, "y": 8.0, "yaw": 0.0}, current_order=None)
        self.publish(2.1, "robot2")
        self.assertTrue(all(c["type"] == "stop" for c in self.dispatch.tick(2.1)))
        state = self.dispatch.snapshot(2.1)
        self.assertEqual(state["robots"]["robot1"]["link_state"], "RECOVERING")
        self.assertFalse(state["robots"]["robot1"]["recovery_ready"])
        self.assertTrue(set(original["path"]) <= set(state["reservations"]["robot1"]))
        with self.assertRaises(ValueError):
            self.dispatch.submit("robot1", "resume", now=2.1)

    def test_unknown_active_command_cannot_restart_a_transition(self):
        original = self.start_transition()
        self.lose_first()
        self.publish(2.1, "robot1", current_order={"type": "move", "path": original["path"],
                                                  "command_id": "unknown-permit"})
        self.publish(2.1, "robot2")
        self.assertTrue(all(c["type"] == "stop" for c in self.dispatch.tick(2.1)))
        self.assertFalse(self.dispatch.snapshot(2.1)["robots"]["robot1"]["recovery_ready"])
        self.assertIn("приказ", self.dispatch.snapshot(2.1)["robots"]["robot1"]["recovery_reason"])

    def test_completed_transition_after_loss_does_not_create_another_mission_or_permit(self):
        original = self.start_transition(target="r1c5")
        self.lose_first()
        destination = self.world.map.nodes[original["path"][-1]]
        self.publish(2.1, "robot1", node_id=destination.id, edge=None,
                     pose={"x": destination.x, "y": destination.y, "yaw": math.pi},
                     status="idle", velocity={"linear": 0, "angular": 0}, current_order=None,
                     completed_command_id=original["command_id"])
        self.publish(2.1, "robot2")
        self.assertFalse(any(c["type"] == "move" for c in self.dispatch.tick(2.1)))
        self.assertEqual(self.dispatch.metrics["completed_tasks"], 1)
        self.assertFalse(self.dispatch.goals)
        repeated = copy.deepcopy(self.dispatch.robots["robot1"].state)
        for number in range(1, 4):
            repeated["seq"] += 1
            self.assertTrue(self.dispatch.ingest(repeated, 2.1 + number / 10))
            self.dispatch.tick(2.1 + number / 10)
        self.assertEqual(self.dispatch.metrics["completed_tasks"], 1)
        self.assertEqual(self.dispatch.metrics["completed_moves"], 1)

    def test_reboot_in_transition_preserves_mission_and_requires_explicit_resume(self):
        original = self.start_transition()
        old_state = copy.deepcopy(self.dispatch.robots["robot1"].state)
        self.world.robots["robot1"] = RobotCore("robot1", self.world.map, "p1")
        self.publish(0.2)
        state = self.dispatch.snapshot(0.2)
        self.assertTrue(state["paused"])
        self.assertTrue(state["safety_stop"])
        self.assertEqual(state["robots"]["robot1"]["link_state"], "RECOVERING")
        self.assertTrue(state["robots"]["robot1"]["recovery_ready"])
        self.assertTrue(set(original["path"]) <= set(state["reservations"]["robot1"]))
        self.assertTrue(all(c["type"] == "stop" for c in self.dispatch.tick(0.2)))
        old_state["seq"] = 10000
        self.assertFalse(self.dispatch.ingest(old_state, 0.25))
        self.dispatch.submit("robot1", "resume", now=0.3)
        resumed = next(c for c in self.dispatch.tick(0.3) if c["type"] == "move")
        self.assertNotEqual(resumed["command_id"], original["command_id"])
        self.assertEqual(self.dispatch.goals["robot1"], "r2c4")
        self.assertEqual(self.dispatch.metrics["completed_tasks"], 0)

    def test_reboot_at_a_different_confirmed_node_retains_union_until_operator_ack(self):
        original = self.start_transition()
        self.world.robots["robot1"] = RobotCore("robot1", self.world.map, "r0c0")
        self.publish(0.2)
        protected = set(self.dispatch.snapshot(0.2)["reservations"]["robot1"])
        self.assertTrue(set(original["path"]) <= protected)
        self.assertIn("r0c0", protected)
        self.assertTrue(all(c["type"] == "stop" for c in self.dispatch.tick(0.2)))
        self.dispatch.submit("robot1", "resume", now=0.3)
        moves = [c for c in self.dispatch.tick(0.3) if c["type"] == "move"]
        self.assertEqual(moves[0]["path"][0], "r0c0")
        self.assertFalse(self.dispatch.robots["robot1"].protected_resources)

    def test_reboot_arrival_confirmation_cannot_complete_a_different_permit(self):
        original = self.start_transition()
        self.world.robots["robot1"] = RobotCore("robot1", self.world.map, original["path"][-1])
        self.publish(0.2)
        self.dispatch.tick(0.2)
        self.dispatch.submit("robot1", "resume", now=0.3)
        permit = next(c for c in self.dispatch.tick(0.3) if c["robot_id"] == "robot1" and c["type"] == "move")
        self.assertNotEqual(permit["command_id"], original["command_id"])
        destination = self.world.map.nodes[permit["path"][-1]]
        self.publish(0.4, "robot1", node_id=destination.id, edge=None,
                     pose={"x": destination.x, "y": destination.y, "yaw": 0},
                     completed_command_id="not-the-current-permit")
        self.publish(0.4, "robot2")
        commands = self.dispatch.tick(0.4)
        self.assertEqual(self.dispatch.metrics["completed_moves"], 0)
        self.assertIn(permit, commands)

    def test_manual_repair_after_link_loss_does_not_enable_automatic_resume_off_graph(self):
        robot = self.world.robots["robot1"]
        robot.x, robot.y = 13.0, 9.0
        self.publish(0.1)
        self.dispatch.submit("robot1", "manual", linear=0, now=0.1)
        self.publish(1.9, "robot2")
        stops = self.dispatch.tick(2)
        stop = next(c for c in stops if c["robot_id"] == "robot1")
        self.assertTrue(self.world.command({**stop, "world_id": self.world.world_id,
                                            "command_seq": 1}, 2))
        self.assertEqual(robot.error["code"], "OFF_GRAPH")
        self.publish(2.1)
        self.dispatch.tick(2.1)
        snapshot = self.dispatch.snapshot(2.1)
        self.assertEqual(snapshot["robots"]["robot1"]["link_state"], "ONLINE")
        self.assertTrue(snapshot["paused"])
        self.assertEqual(robot.error["code"], "OFF_GRAPH")
        with self.assertRaises(ValueError):
            self.dispatch.submit("robot1", "resume", now=2.1)
        self.dispatch.submit("robot1", "manual", linear=-0.2, now=2.1)
        self.assertTrue(any(c["type"] == "manual" for c in self.dispatch.tick(2.1)))
        robot.x, robot.y = 12.0, 8.0
        robot.error = None
        robot.current_order = None
        robot.status = "idle"
        self.publish(2.2)
        self.dispatch.submit("robot1", "resume", now=2.2)
        self.assertFalse(self.dispatch.paused)


class DispatcherMetricTests(unittest.TestCase):
    def setUp(self):
        self.world = WorldCore("normal", 2)
        self.dispatch = Dispatcher(self.world.map, list(self.world.robots))
        for state in self.world.snapshot(0):
            self.dispatch.ingest(state, 0)

    def publish(self, now, **changes):
        for state in self.world.snapshot(now):
            state.update(copy.deepcopy(changes))
            self.dispatch.ingest(state, now)

    def test_outage_counts_each_link_once_and_one_stop_for_whole_fleet(self):
        self.dispatch.tick(2)
        self.dispatch.tick(2.1)
        self.dispatch.tick(2.2)
        self.assertEqual(self.dispatch.metrics["link_loss_events_total"], 2)
        self.assertEqual(self.dispatch.metrics["fleet_safety_stops_total"], 1)
        events = self.dispatch.drain_events()
        self.assertEqual(sum(e["code"] == "LINK_LOST" for e in events), 2)
        self.assertEqual(sum(e["code"] == "FLEET_SAFETY_STOP" for e in events), 1)
        self.publish(2.3)
        self.assertEqual(self.dispatch.metrics["link_recovery_events_total"], 2)
        self.dispatch.tick(2.3)
        self.dispatch.tick(2.4)
        events = self.dispatch.drain_events()
        self.assertEqual(sum(e["code"] == "FLEET_RESUMED" for e in events), 1)
        self.assertFalse(self.dispatch.snapshot(2.4)["safety_stop"])

    def test_startup_and_snapshot_reads_do_not_create_outage_events(self):
        fresh = Dispatcher(self.world.map, list(self.world.robots))
        fresh.tick(0)
        before = dict(fresh.metrics)
        for _ in range(10):
            self.assertEqual(fresh.snapshot(100)["metrics"]["offline_robots"], 2)
        self.assertEqual(fresh.metrics, before)
        self.assertEqual(fresh.metrics["link_loss_events_total"], 0)
        self.assertEqual(fresh.metrics["fleet_safety_stops_total"], 0)
        self.assertFalse(fresh.drain_events())

    def test_error_transitions_are_counted_once_and_clear_requires_confirmed_state(self):
        state = self.world.snapshot(0.1)[0]
        state["error"] = {"code": "STUCK", "message": "blocked"}
        self.assertTrue(self.dispatch.ingest(state, 0.1))
        self.assertFalse(self.dispatch.ingest(state, 0.2))
        state["seq"] += 1
        self.assertTrue(self.dispatch.ingest(state, 0.2))
        self.assertEqual(self.dispatch.metrics["robot_errors_total"], 1)
        self.dispatch.submit("robot1", "clear_error", now=0.2)
        self.assertFalse(any(e["code"] == "ERROR_CLEARED" for e in self.dispatch.drain_events()))
        state["seq"] += 1
        state["error"] = None
        self.assertTrue(self.dispatch.ingest(state, 0.3))
        self.assertEqual([e["code"] for e in self.dispatch.drain_events()], ["ERROR_CLEARED"])

    def test_planning_duration_covers_the_whole_attempt_and_failure_log_is_deduplicated(self):
        self.dispatch.submit("robot1", "move", "r2c4")
        self.dispatch.planner.reason = "forced search failure"
        with patch.object(self.dispatch.planner, "plan", return_value=None):
            with patch("warehouse.dispatcher.time.perf_counter", side_effect=[10.0, 10.25]):
                self.dispatch.tick(0)
            self.dispatch.tick(1.1)
        self.assertEqual(self.dispatch.metrics["planning_duration_seconds_count"], 2)
        self.assertGreaterEqual(self.dispatch.metrics["planning_duration_seconds_sum"], 0.25)
        self.assertEqual(sum(e["code"] == "PLANNING_FAILED" for e in self.dispatch.drain_events()), 1)

    def test_event_buffer_is_bounded_and_drain_is_destructive(self):
        for number in range(300):
            self.dispatch._emit("TEST_EVENT", None, str(number))
        self.assertEqual(len(self.dispatch.drain_events()), 256)
        self.assertEqual(self.dispatch.drain_events(), [])


class CompletedCommandTests(unittest.TestCase):
    def test_a_completed_move_is_not_executed_again_after_duplicate_delivery(self):
        world = WorldCore("normal", 1)
        command = {"world_id": world.world_id, "robot_id": "robot1", "type": "move",
                   "command_id": "one-permit", "command_seq": 1, "path": ["p1", "r1c5"]}
        for frame in range(160):
            now = frame * 0.02
            self.assertTrue(world.command(command, now))
            world.tick(0.02, now)
        robot = world.robots["robot1"]
        self.assertEqual(robot.completed_command_id, command["command_id"])
        before = (robot.x, robot.y, robot.yaw)
        self.assertTrue(world.command(command, 4))
        world.tick(0.1, 4.1)
        self.assertEqual((robot.x, robot.y, robot.yaw), before)
        self.assertIsNone(robot.current_order)
        self.assertEqual(robot.status, "idle")


if __name__ == "__main__":
    unittest.main()
