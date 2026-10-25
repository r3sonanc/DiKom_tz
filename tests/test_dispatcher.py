"""Safety, liveness and communication regression tests for the pure dispatcher."""

import random
import unittest

from warehouse.dispatcher import CooperativePlanner, Dispatcher
from warehouse.map import load_map


def telemetry(robot_id, node, seq=1, boot="boot", **changes):
    state = {"robot_id": robot_id, "node_id": node, "edge": None,
             "status": "idle", "error": None, "seq": seq, "boot_id": boot,
             "velocity": {"linear": 0.0, "angular": 0.0},
             "completed_command_id": None}
    state.update(changes)
    return state


class PlannerTests(unittest.TestCase):
    def assert_safe(self, warehouse_map, plan, starts, goals, blocked=()):
        self.assertIsNotNone(plan)
        for rid, path in plan.items():
            self.assertEqual(path[0], starts[rid])
            self.assertEqual(path[-1], goals[rid])
            self.assertFalse(set(path) & set(blocked))
            for source, target in zip(path, path[1:]):
                self.assertTrue(source == target or target in warehouse_map.adjacency[source])
        for time in range(1, len(next(iter(plan.values())))):
            occupied = set()
            for path in plan.values():
                resources = {path[time - 1], path[time]}
                self.assertFalse(occupied & resources,
                                 f"Collision at round {time}: {plan}")
                occupied.update(resources)

    def test_head_on_swap_uses_a_bypass(self):
        for name in ("normal", "hardcore"):
            with self.subTest(map=name):
                warehouse_map = load_map(name)
                planner = CooperativePlanner(warehouse_map)
                starts = {"a": "r2c0", "b": "r2c4"}
                goals = {"a": "r2c4", "b": "r2c0"}
                self.assert_safe(warehouse_map, planner.plan(starts, goals), starts, goals)

    def test_narrow_parking_corridor_requires_yielding(self):
        warehouse_map = load_map("hardcore")
        starts = {"a": "r0c5", "b": "r2c5"}
        goals = {"a": "r4c5", "b": "r0c5"}
        plan = CooperativePlanner(warehouse_map).plan(starts, goals)
        self.assert_safe(warehouse_map, plan, starts, goals)
        self.assertTrue(any(n.startswith("p") or n == "r2c4"
                            for path in plan.values() for n in path))

    def test_idle_robot_can_temporarily_leave_its_node(self):
        warehouse_map = load_map("hardcore")
        starts = {"a": "p1", "b": "r1c5"}
        goals = {"a": "p5", "b": "r1c5"}
        plan = CooperativePlanner(warehouse_map).plan(starts, goals)
        self.assert_safe(warehouse_map, plan, starts, goals)
        self.assertGreater(len(set(plan["b"])), 1)

    def test_five_robots_on_each_assignment_map(self):
        for name in ("normal", "hardcore"):
            warehouse_map = load_map(name)
            rng = random.Random(17)
            for scenario in range(8):
                with self.subTest(map=name, scenario=scenario):
                    starts = dict(zip("abcde", rng.sample(list(warehouse_map.nodes), 5)))
                    goals = dict(zip("abcde", rng.sample(list(warehouse_map.nodes), 5)))
                    planner = CooperativePlanner(warehouse_map)
                    self.assert_safe(warehouse_map, planner.plan(starts, goals), starts, goals)
                    self.assertLessEqual(planner.last_expansions, planner.max_expansions + 1)

    def test_blocked_bridge_returns_safe_failure(self):
        warehouse_map = load_map("hardcore")
        planner = CooperativePlanner(warehouse_map)
        starts, goals = {"a": "r2c0"}, {"a": "p1"}
        self.assertIsNone(planner.plan(starts, goals, {"r2c4"}))


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.map = load_map("hardcore")
        self.dispatcher = Dispatcher(self.map, ["a", "b"])
        self.dispatcher.ingest(telemetry("a", "p1"), 0)
        self.dispatcher.ingest(telemetry("b", "p2"), 0)

    def finish(self, command, now, seq):
        self.dispatcher.ingest(telemetry(command["robot_id"], command["path"][-1], seq,
                                         completed_command_id=command["command_id"]), now)

    def test_repeats_keep_id_and_barrier_waits_for_actual_arrival(self):
        self.dispatcher.submit("a", "move", "r2c0")
        commands = self.dispatcher.tick(0)
        first = next(c for c in commands if c["type"] == "move")
        self.assertEqual(self.dispatcher.tick(0.1), commands)
        wrong = telemetry(first["robot_id"], first["path"][-1], 2,
                          completed_command_id="other")
        self.dispatcher.ingest(wrong, 0.2)
        self.assertEqual(self.dispatcher.tick(0.2), commands)

    def test_old_sequence_does_not_extend_heartbeat(self):
        self.assertFalse(self.dispatcher.ingest(telemetry("a", "p1", 1), 1.4))
        self.dispatcher.ingest(telemetry("b", "p2", 2), 1.4)
        self.dispatcher.submit("a", "move", "r2c0")
        commands = self.dispatcher.tick(1.6)
        self.assertTrue(all(c["type"] == "stop" for c in commands))
        self.assertFalse(self.dispatcher.snapshot(1.6)["robots"]["a"]["online"])

    def test_reboot_rejects_delayed_packets_from_retired_boot(self):
        self.assertTrue(self.dispatcher.ingest(telemetry("a", "p1", 0, "new"), 0.1))
        self.assertTrue(self.dispatcher.paused)
        self.assertFalse(self.dispatcher.ingest(telemetry("a", "r2c0", 100, "boot"), 0.2))
        self.assertEqual(self.dispatcher.robots["a"].state["node_id"], "p1")

    def test_stale_mid_edge_keeps_resources_and_renews_with_new_id(self):
        self.dispatcher.submit("a", "move", "r2c0")
        first = self.dispatcher.tick(0)[0]
        self.dispatcher.ingest(telemetry("a", None, 2, edge=first["path"], status="moving"), 0.1)
        self.dispatcher.tick(2)
        self.assertEqual(set(self.dispatcher.snapshot(2)["reservations"]["a"]), set(first["path"]))
        self.dispatcher.ingest(telemetry("a", None, 3, edge=first["path"], status="paused"), 2.1)
        self.dispatcher.ingest(telemetry("b", "p2", 2), 2.1)
        resumed = self.dispatcher.tick(2.1)
        move = next(c for c in resumed if c["robot_id"] == "a" and c["type"] == "move")
        self.assertEqual(move["path"], first["path"])
        self.assertNotEqual(move["command_id"], first["command_id"])

    def test_occupied_goal_is_freed_by_an_idle_robot(self):
        self.dispatcher.submit("a", "move", "p2")
        for step in range(100):
            now = step / 10
            for rid, record in list(self.dispatcher.robots.items()):
                state = dict(record.state)
                state["seq"] += 1
                self.dispatcher.ingest(state, now)
            commands = self.dispatcher.tick(now)
            for command in commands:
                if command["type"] == "move":
                    self.finish(command, now, self.dispatcher.robots[command["robot_id"]].seq + 1)
            if not self.dispatcher.goals:
                break
        self.assertFalse(self.dispatcher.goals)
        self.assertEqual(self.dispatcher.robots["a"].state["node_id"], "p2")
        self.assertNotEqual(self.dispatcher.robots["b"].state["node_id"], "p2")

    def test_faulty_robot_holds_edge_but_others_can_use_a_bypass(self):
        self.dispatcher.ingest(telemetry("a", "r1c1", 2), 0)
        self.dispatcher.ingest(telemetry("b", "r2c0", 2), 0)
        self.dispatcher.submit("a", "move", "r1c3")
        first_commands = self.dispatcher.tick(0)
        first = next(c for c in first_commands if c["robot_id"] == "a")
        self.dispatcher.ingest(telemetry("a", None, 3, edge=first["path"], status="error",
                                         error={"code": "STUCK"}), 0.1)
        self.dispatcher.tick(0.1)
        self.dispatcher.submit("b", "move", "r2c3")
        moves = self.dispatcher.tick(0.2)
        self.assertTrue(any(c["robot_id"] == "b" and c["type"] == "move" for c in moves))
        held = set(self.dispatcher.snapshot(0.2)["reservations"]["a"])
        self.assertEqual(held, set(first["path"]))
        for command in moves:
            if command["type"] == "move":
                self.assertFalse(held & set(command["path"]))

    def test_manual_control_waits_for_fleet_stop_and_lease(self):
        self.dispatcher.submit("a", "move", "r2c0")
        self.dispatcher.tick(0)
        self.dispatcher.submit("a", "manual", linear=0.3, now=0.1)
        self.assertFalse(any(c["type"] == "manual" for c in self.dispatcher.tick(0.1)))
        self.dispatcher.submit("a", "manual", linear=0.3, now=1.0)
        manual = [c for c in self.dispatcher.tick(1.0) if c["type"] == "manual"]
        self.assertEqual(manual[0]["linear"], 0.3)
        expired = [c for c in self.dispatcher.tick(1.4) if c["type"] == "manual"]
        self.assertEqual(expired[0]["linear"], 0.0)

    def test_dark_factory_picks_far_unique_destinations(self):
        self.dispatcher.set_dark_factory(True)
        self.dispatcher.tick(0)
        self.assertEqual(len(set(self.dispatcher.goals.values())), 2)
        for rid, goal in self.dispatcher.goals.items():
            source = self.dispatcher.robots[rid].state["node_id"]
            self.assertGreaterEqual(self.dispatcher.planner.distance(source, goal), 5)

    def test_difficult_joint_operator_orders_make_progress_in_stages(self):
        # This feasible assignment defeats the fixed-priority joint search.
        # The fallback must preserve all orders and finish at least one of them.
        starts = {"a": "r2c2", "b": "p2", "c": "r4c5", "d": "r1c5", "e": "p3"}
        goals = {"a": "r1c0", "b": "r2c0", "c": "p1", "d": "r0c5", "e": "r1c2"}
        self.assertIsNone(CooperativePlanner(self.map).plan(starts, goals))
        dispatcher = Dispatcher(self.map, list(starts))
        for rid, node in starts.items():
            dispatcher.ingest(telemetry(rid, node), 0)
            dispatcher.submit(rid, "move", goals[rid])
        for step in range(100):
            now = step / 10
            for rid, record in dispatcher.robots.items():
                state = dict(record.state)
                state["seq"] += 1
                dispatcher.ingest(state, now)
            for command in dispatcher.tick(now):
                if command["type"] == "move":
                    rid = command["robot_id"]
                    dispatcher.ingest(telemetry(rid, command["path"][-1],
                                               dispatcher.robots[rid].seq + 1,
                                               completed_command_id=command["command_id"]), now)
            if dispatcher.metrics["completed_tasks"]:
                break
        self.assertGreater(dispatcher.metrics["staged_plans"], 0)
        self.assertGreater(dispatcher.metrics["completed_tasks"], 0)
        self.assertTrue(all(target == goals[rid] for rid, target in dispatcher.goals.items()))
        self.assertLessEqual(dispatcher.planner.last_expansions, dispatcher.planner.max_expansions + 1)

    def test_failed_dark_plan_reselects_only_automatic_targets(self):
        from unittest.mock import patch

        self.dispatcher.set_dark_factory(True)
        self.dispatcher.submit("a", "move", "r2c0")
        self.dispatcher.goals["b"] = "r1c0"
        self.dispatcher._auto_goals.add("b")
        with patch.object(self.dispatcher.planner, "plan", return_value=None):
            self.dispatcher.tick(0)
        self.assertEqual(self.dispatcher.goals, {"a": "r2c0"})


if __name__ == "__main__":
    unittest.main()
