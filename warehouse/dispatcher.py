"""Central dispatcher with conservative, bounded space-time reservations.

The planner knows graph positions, never simulated physics. Both ends of a moving
edge remain occupied until its robot acknowledges arrival. This deliberately
forbids following another robot into a just-vacated node in the same round.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from heapq import heappop, heappush
from itertools import permutations
import math
import random
from typing import Any


@dataclass
class RobotRecord:
    state: dict[str, Any] = field(default_factory=dict)
    received_at: float = -math.inf
    seq: int = -1
    boot_id: str | None = None
    retired_boots: set[str] = field(default_factory=set)


class CooperativePlanner:
    """Prioritized Cooperative A* with bounded priority-order retries.

    A complete safe plan is required before issuing its first edge. The search
    is deliberately bounded: difficult or impossible requests wait safely and
    expose a reason instead of consuming unbounded CPU or guessing a route.
    """

    def __init__(self, warehouse_map: Any, max_expansions: int = 300_000):
        self.map = warehouse_map
        self.max_expansions = max_expansions
        self.last_expansions = 0
        self.last_attempts = 0
        self.reason = ""
        self._distances: dict[str, dict[str, int]] = {}
        for source in self.map.nodes:
            distances = {source: 0}
            queue = deque([source])
            while queue:
                node = queue.popleft()
                for neighbor in self.map.adjacency[node]:
                    if neighbor not in distances:
                        distances[neighbor] = distances[node] + 1
                        queue.append(neighbor)
            self._distances[source] = distances

    def distance(self, source: str, destination: str) -> float:
        return self._distances.get(source, {}).get(destination, math.inf)

    def plan(self, starts: dict[str, str], goals: dict[str, str],
             blocked: set[str] | None = None) -> dict[str, list[str]] | None:
        self.last_expansions = self.last_attempts = 0
        blocked = set(blocked or ())
        self.reason = ""
        if len(set(starts.values())) != len(starts):
            self.reason = "Two robots report the same node"
            return None
        if len(set(goals.values())) != len(goals):
            self.reason = "Destination nodes must be distinct"
            return None
        if any(node in blocked for node in starts.values()):
            self.reason = "A start node is blocked"
            return None
        if any(math.isinf(self.distance(starts[r], goals[r])) for r in starts):
            self.reason = "A destination is unreachable"
            return None
        # Active robots first; deterministic tie breaking aids reproducibility.
        robot_ids = sorted(starts, key=lambda r: (starts[r] == goals[r], r))
        horizon = 3 * len(self.map.nodes) + len(robot_ids) ** 2
        for order in permutations(robot_ids):
            self.last_attempts += 1
            planned: dict[str, list[str]] = {}
            for index, robot_id in enumerate(order):
                unplanned = {starts[r] for r in order[index + 1:]}
                # Reserve a short initial interval for robots that have not yet
                # been planned. Descending intervals let a queue clear from its
                # free end before a higher-priority robot enters its corridor.
                initial_hold = (len(order) - index - 1) * len(order)
                path = self._search(starts[robot_id], goals[robot_id], planned,
                                    blocked, unplanned, initial_hold, horizon)
                if path is None:
                    break
                planned[robot_id] = path
            else:
                length = max(map(len, planned.values()), default=1)
                return {r: p + [p[-1]] * (length - len(p)) for r, p in planned.items()}
            if self.last_expansions >= self.max_expansions:
                self.reason = "Planning budget exhausted; robots remain stopped"
                return None
        self.reason = "No safe plan found within the planning horizon"
        return None

    def _search(self, start: str, goal: str, planned: dict[str, list[str]],
                blocked: set[str], unplanned: set[str], initial_hold: int,
                horizon: int) -> list[str] | None:
        occupied: list[set[str]] = [set(blocked) for _ in range(horizon + 1)]
        for path in planned.values():
            for time in range(1, horizon + 1):
                occupied[time].update((path[min(time - 1, len(path) - 1)],
                                       path[min(time, len(path) - 1)]))
        for time in range(1, min(initial_hold, horizon) + 1):
            occupied[time].update(unplanned)
        if goal in blocked:
            return None
        latest_goal_use = max((t for t in range(1, horizon + 1)
                               if goal in occupied[t]), default=0)
        parents: dict[tuple[str, int], tuple[str, int] | None] = {(start, 0): None}
        queue: list[tuple[float, int, str]] = [(self.distance(start, goal), 0, start)]
        while queue:
            _, time, node = heappop(queue)
            self.last_expansions += 1
            if self.last_expansions > self.max_expansions:
                return None
            if node == goal and time >= latest_goal_use:
                path = []
                cursor: tuple[str, int] | None = (node, time)
                while cursor is not None:
                    path.append(cursor[0])
                    cursor = parents[cursor]
                return path[::-1]
            if time >= horizon or node in occupied[time + 1]:
                continue
            for neighbor in (*self.map.adjacency[node], node):
                state = (neighbor, time + 1)
                if neighbor in occupied[time + 1] or state in parents:
                    continue
                distance = self.distance(neighbor, goal)
                if math.isinf(distance) or time + 1 + distance > horizon:
                    continue
                parents[state] = (node, time)
                heappush(queue, (time + 1 + distance, time + 1, neighbor))
        return None


class Dispatcher:
    """Transport-independent fleet state machine; all times are monotonic seconds."""

    def __init__(self, warehouse_map: Any, robot_ids: list[str] | tuple[str, ...] = (),
                 heartbeat_timeout: float = 1.5, command_lease: float = 0.9,
                 seed: int = 7):
        self.map = warehouse_map
        self.robots = {r: RobotRecord() for r in robot_ids}
        self._configured_ids = bool(robot_ids)
        self.goals: dict[str, str] = {}
        self._auto_goals: set[str] = set()
        self.planner = CooperativePlanner(warehouse_map)
        self.heartbeat_timeout = heartbeat_timeout
        self.command_lease = command_lease
        self.dark_factory = False
        self.paused = False
        self.reason = "Waiting for telemetry"
        self._random = random.Random(seed)
        self._serial = 0
        self._round: dict[str, dict[str, Any]] = {}
        self._held: dict[str, dict[str, Any]] = {}
        self._recover: set[str] = set()
        self._schedule: dict[str, list[str]] = {}
        self._schedule_dirty = False
        self._outbox: list[dict[str, Any]] = []
        self._manual_robot: str | None = None
        self._manual_velocity = (0.0, 0.0)
        self._manual_updated = -math.inf
        self._last_motion_sent = -math.inf
        self._stop_commands: dict[str, dict[str, Any]] = {}
        self._frozen = False
        self._last_plan_signature: Any = None
        self._last_plan_at = -math.inf
        self.metrics = {"accepted_telemetry": 0, "discarded_telemetry": 0,
                        "commands": 0, "completed_moves": 0, "plans": 0,
                        "planning_failures": 0, "completed_tasks": 0, "staged_plans": 0}

    def ingest(self, state: dict[str, Any], now: float) -> bool:
        robot_id = state.get("robot_id")
        if not isinstance(robot_id, str) or (self._configured_ids and robot_id not in self.robots):
            return False
        if robot_id not in self.robots:
            self.robots[robot_id] = RobotRecord()
        robot = self.robots[robot_id]
        seq, boot_id = state.get("seq"), state.get("boot_id")
        if not isinstance(seq, int) or isinstance(seq, bool) or not isinstance(boot_id, str):
            return False
        if boot_id in robot.retired_boots or (boot_id == robot.boot_id and seq <= robot.seq):
            self.metrics["discarded_telemetry"] += 1
            return False
        if robot.boot_id is not None and boot_id != robot.boot_id:
            robot.retired_boots.add(robot.boot_id)
            self.paused = True
            self.reason = f"Robot {robot_id} restarted; resume after checking its position"
            self._schedule_dirty = True
        robot.state = dict(state)
        robot.received_at, robot.seq, robot.boot_id = now, seq, boot_id
        self.metrics["accepted_telemetry"] += 1
        return True

    def submit(self, robot_id: str, kind: str | dict[str, Any], target: str | None = None,
               linear: float = 0.0, angular: float = 0.0, now: float | None = None) -> dict[str, Any]:
        if robot_id not in self.robots:
            raise ValueError(f"Unknown robot: {robot_id}")
        if isinstance(kind, dict):
            payload = kind
            return self.submit(robot_id, payload.get("type", ""), payload.get("target"),
                               payload.get("linear", 0.0), payload.get("angular", 0.0), now)
        if kind in {"move", "park"}:
            if kind == "park":
                occupied = set(self.goals.values()) | self._occupied_by_others(robot_id)
                source = self.robots[robot_id].state.get("node_id")
                candidates = [n for n, node in self.map.nodes.items()
                              if node.parking and n not in occupied]
                if not candidates:
                    raise ValueError("No free parking node")
                target = min(candidates, key=lambda n: (self.planner.distance(source, n), n))
            if target not in self.map.nodes:
                raise ValueError(f"Unknown destination: {target}")
            if target in {g for r, g in self.goals.items() if r != robot_id}:
                raise ValueError("Another robot already has this destination")
            self.goals[robot_id] = target
            self._auto_goals.discard(robot_id)
            self._schedule_dirty = True
        elif kind == "stop":
            self.paused = True
            self._manual_robot = None
            self._stop_commands.clear()
            self.reason = "Fleet stopped by operator"
        elif kind == "resume":
            for rid, robot in self.robots.items():
                state = robot.state
                if state.get("error"):
                    raise ValueError(f"Clear the error on {rid} before resuming")
                if state.get("node_id") is None and (rid not in self._round or rid == self._manual_robot):
                    raise ValueError(f"Robot {rid} must return to a graph node before resuming")
            if self._manual_robot is not None:
                self._round.pop(self._manual_robot, None)
                self._held.pop(self._manual_robot, None)
            self.paused = False
            self._manual_robot = None
            self._stop_commands.clear()
            self._schedule_dirty = True
        elif kind == "manual":
            if self._manual_robot not in (None, robot_id):
                raise ValueError("Only one robot can be controlled manually")
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (linear, angular)):
                raise ValueError("Manual velocity must be finite")
            self.paused = True
            self._manual_robot = robot_id
            self._manual_velocity = (max(-0.5, min(0.5, linear)), max(-1.5, min(1.5, angular)))
            # The adapter should pass its monotonic clock. Without it the next
            # tick starts the manual watchdog, retaining a convenient pure API.
            self._manual_updated = now if now is not None else math.inf
            self.reason = "Manual control: fleet is paused"
            self._schedule_dirty = True
        elif kind == "clear_error":
            self._outbox.append(self._command(robot_id, "clear_error"))
            self._recover.add(robot_id)
            self._schedule_dirty = True
        else:
            raise ValueError(f"Unknown command type: {kind}")
        return {"accepted": True, "robot_id": robot_id, "type": kind, "target": target}

    def set_dark_factory(self, enabled: bool) -> None:
        self.dark_factory = bool(enabled)

    def tick(self, now: float) -> list[dict[str, Any]]:
        commands, self._outbox = self._outbox, []
        stale = [r for r, record in self.robots.items()
                 if now - record.received_at > self.heartbeat_timeout]
        if self._manual_updated == math.inf:
            self._manual_updated = now
        # Missing communication stops renewals; the robot's independent lease
        # watchdog brakes even when the stop command itself cannot be delivered.
        if stale or self.paused:
            self._frozen = True
            if stale:
                self.reason = "Telemetry stale: " + ", ".join(stale)
            commands.extend(self._stops(exclude=self._manual_robot if not stale else None))
            manual_ready = (not stale and self._manual_robot is not None
                            and now - self._last_motion_sent >= self.command_lease
                            and all(self._stationary(r.state) for rid, r in self.robots.items()
                                    if rid != self._manual_robot))
            if manual_ready:
                velocity = self._manual_velocity if now - self._manual_updated <= 0.35 else (0.0, 0.0)
                commands.append(self._command(self._manual_robot, "manual",
                                              linear=velocity[0], angular=velocity[1]))
            elif self._manual_robot is not None:
                commands.extend(self._stops(only=self._manual_robot))
            return self._count(commands)
        refresh_round = self._frozen
        if refresh_round:
            self._frozen = False
            self._stop_commands.clear()
        if not self.robots:
            self.reason = "Waiting for robots"
            return commands
        if self._round:
            unfinished = {}
            for robot_id, command in self._round.items():
                state = self.robots[robot_id].state
                if state.get("error"):
                    self._held[robot_id] = command
                    self._schedule_dirty = True
                    commands.extend(self._stops(only=robot_id))
                    continue
                arrived = (state.get("node_id") == command["path"][-1]
                           and state.get("status") == "idle"
                           and (state.get("completed_command_id") == command["command_id"]
                                or refresh_round))
                if arrived:
                    self.metrics["completed_moves"] += 1
                else:
                    unfinished[robot_id] = (self._command(robot_id, "move", path=command["path"])
                                            if refresh_round else command)
            self._round = unfinished
            if self._round:
                commands.extend(self._round.values())
                self._last_motion_sent = now
                return self._count(commands)
            for path in self._schedule.values():
                if len(path) > 1:
                    path.pop(0)
        if self._schedule_dirty:
            self._schedule = {}
            self._schedule_dirty = False
        for robot_id in sorted(self._recover):
            if robot_id in self._held and not self.robots[robot_id].state.get("error"):
                command = self._held.pop(robot_id)
                self._round[robot_id] = self._command(robot_id, "move", path=command["path"])
        self._recover.difference_update(self._round)
        if self._round:
            commands.extend(self._round.values())
            self._last_motion_sent = now
            return self._count(commands)
        for robot_id, target in list(self.goals.items()):
            if self.robots[robot_id].state.get("node_id") == target:
                del self.goals[robot_id]
                self._auto_goals.discard(robot_id)
                self.metrics["completed_tasks"] += 1
        if self.dark_factory:
            self._assign_random_goals(now)
        if not self.goals:
            self._schedule = {}
            self.reason = "Idle"
            return self._count(commands)
        if not self._schedule:
            self._build_schedule(now)
        # Empty all-wait rounds need no transport command or wall-clock delay.
        while self._schedule and max(map(len, self._schedule.values())) > 1:
            moving = {r: p[:2] for r, p in self._schedule.items() if len(p) > 1 and p[0] != p[1]}
            if moving:
                for robot_id, path in moving.items():
                    self._round[robot_id] = self._command(robot_id, "move", path=path)
                commands.extend(self._round.values())
                self._last_motion_sent = now
                self.reason = "Executing reserved routes"
                break
            for path in self._schedule.values():
                if len(path) > 1:
                    path.pop(0)
        if self._schedule and not self._round:
            self._schedule = {}
        return self._count(commands)

    def _build_schedule(self, now: float) -> None:
        starts: dict[str, str] = {}
        blocked: set[str] = set()
        for robot_id, record in self.robots.items():
            state = record.state
            node = state.get("node_id")
            if state.get("error") or node not in self.map.nodes or state.get("status") == "manual":
                blocked.update(self._resources(robot_id))
            else:
                starts[robot_id] = node
        active = {r: g for r, g in self.goals.items() if r in starts}
        if not active:
            self.reason = "Waiting for stopped or faulty robots"
            return
        goals = self._choose_endpoints(starts, active, blocked)
        if goals is None:
            self.reason = "No free node for yielding"
            return
        signature = (tuple(sorted(starts.items())), tuple(sorted(goals.items())), tuple(sorted(blocked)))
        if signature == self._last_plan_signature and now - self._last_plan_at < 1.0:
            return
        self._last_plan_signature, self._last_plan_at = signature, now
        self.metrics["plans"] += 1
        schedule = self.planner.plan(starts, goals, blocked)
        if schedule is None and any(r not in self._auto_goals for r in active):
            # A joint assignment may defeat prioritized planning even though a
            # single task is easy. Complete one explicit task while the others
            # wait/yield; their original goals remain queued. All attempts share
            # the original expansion budget, preserving the CPU bound.
            budget = self.planner.max_expansions
            expansions, attempts = self.planner.last_expansions, self.planner.last_attempts
            try:
                for robot_id in sorted(active, key=lambda r: (r in self._auto_goals,
                                                              self.planner.distance(starts[r], active[r]), r)):
                    if expansions >= budget:
                        break
                    single_goals = self._choose_endpoints(starts, {robot_id: active[robot_id]}, blocked)
                    if single_goals is None:
                        continue
                    self.planner.max_expansions = budget - expansions
                    schedule = self.planner.plan(starts, single_goals, blocked)
                    expansions += self.planner.last_expansions
                    attempts += self.planner.last_attempts
                    if schedule is not None:
                        self.metrics["staged_plans"] += 1
                        break
            finally:
                self.planner.max_expansions = budget
                self.planner.last_expansions, self.planner.last_attempts = expansions, attempts
        if schedule is None:
            self.metrics["planning_failures"] += 1
            self.reason = self.planner.reason
            # Random workload destinations are disposable. Re-select them on
            # the next tick instead of indefinitely retrying an unlucky joint
            # assignment; explicit operator destinations are never discarded.
            if self.dark_factory:
                for robot_id in self._auto_goals:
                    self.goals.pop(robot_id, None)
                self._auto_goals.clear()
        else:
            self._schedule = schedule

    def _choose_endpoints(self, starts: dict[str, str], active: dict[str, str],
                          blocked: set[str]) -> dict[str, str] | None:
        goals = dict(active)
        used = set(goals.values()) | blocked
        active_routes = {node for rid, goal in active.items() for node in self.map.nodes
                         if self.planner.distance(starts[rid], node)
                         + self.planner.distance(node, goal)
                         == self.planner.distance(starts[rid], goal)}
        # Preserve idle endpoints whenever possible. An idle robot occupying a
        # requested endpoint is assigned a nearby free yield node instead.
        for robot_id, source in starts.items():
            if robot_id in goals:
                continue
            if source not in used:
                goals[robot_id] = source
                used.add(source)
        for robot_id, source in starts.items():
            if robot_id in goals:
                continue
            candidates = [n for n in self.map.nodes if n not in used and n not in starts.values()]
            if not candidates:
                return None
            target = min(candidates, key=lambda n: (n in active_routes,
                                                    self.planner.distance(source, n),
                                                    len(self.map.adjacency[n]) < 2, n))
            goals[robot_id] = target
            used.add(target)
        return goals

    def _assign_random_goals(self, now: float) -> None:
        occupied_goals = set(self.goals.values())
        for robot_id, record in sorted(self.robots.items()):
            source = record.state.get("node_id")
            if robot_id in self.goals or source not in self.map.nodes or record.state.get("error"):
                continue
            candidates = [(self.planner.distance(source, n), n) for n in self.map.nodes
                          if n not in occupied_goals and n != source]
            candidates = [(d, n) for d, n in candidates if not math.isinf(d)]
            if not candidates:
                continue
            # The farthest third avoids near-neighbor jitter while preserving
            # deterministic seeded variety in the dark-factory workload.
            candidates.sort(reverse=True)
            _, target = self._random.choice(candidates[:max(1, len(candidates) // 3)])
            self.goals[robot_id] = target
            self._auto_goals.add(robot_id)
            occupied_goals.add(target)
            self._schedule_dirty = True

    def snapshot(self, now: float) -> dict[str, Any]:
        return {"paused": self.paused, "dark_factory": self.dark_factory,
                "manual_robot": self._manual_robot, "reason": self.reason,
                "goals": dict(self.goals), "reservations": {r: sorted(self._resources(r)) for r in self.robots},
                "robots": {r: {"online": now - v.received_at <= self.heartbeat_timeout,
                               "age": max(0.0, now - v.received_at) if math.isfinite(v.received_at) else None,
                               "goal": self.goals.get(r)} for r, v in self.robots.items()},
                "metrics": dict(self.metrics),
                "planner": {"expansions": self.planner.last_expansions,
                            "attempts": self.planner.last_attempts}}

    def _resources(self, robot_id: str) -> set[str]:
        state = self.robots[robot_id].state
        resources = {n for n in (state.get("node_id"),) if n in self.map.nodes}
        resources.update(n for n in (state.get("edge") or ()) if n in self.map.nodes)
        if robot_id in self._round:
            resources.update(self._round[robot_id]["path"])
        if robot_id in self._held:
            resources.update(self._held[robot_id]["path"])
        return resources

    def _occupied_by_others(self, robot_id: str) -> set[str]:
        return set().union(*(self._resources(r) for r in self.robots if r != robot_id))

    def _command(self, robot_id: str, kind: str, **kwargs: Any) -> dict[str, Any]:
        self._serial += 1
        if kind != "stop":
            self._stop_commands.pop(robot_id, None)
        return {"robot_id": robot_id, "command_id": f"dispatch-{self._serial}", "type": kind, **kwargs}

    def _stops(self, exclude: str | None = None, only: str | None = None) -> list[dict[str, Any]]:
        result = []
        for robot_id in self.robots:
            if robot_id == exclude or (only is not None and robot_id != only):
                continue
            if robot_id not in self._stop_commands:
                self._stop_commands[robot_id] = self._command(robot_id, "stop")
            result.append(self._stop_commands[robot_id])
        return result

    @staticmethod
    def _stationary(state: dict[str, Any]) -> bool:
        velocity = state.get("velocity") or {}
        return (state.get("status") in {"idle", "paused", "manual", "error"}
                and abs(velocity.get("linear", 0.0)) < 0.001
                and abs(velocity.get("angular", 0.0)) < 0.001)

    def _count(self, commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.metrics["commands"] += len(commands)
        return commands
