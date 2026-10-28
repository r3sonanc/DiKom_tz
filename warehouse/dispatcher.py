from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from heapq import heappop, heappush
from itertools import permutations
import math
import random
import time
from typing import Any

from warehouse.map import segment_distance
from warehouse.robot import NODE_TOLERANCE, ROBOT_RADIUS

TELEMETRY_MAX_AGE = 0.5
TELEMETRY_FUTURE_TOLERANCE = 0.1

@dataclass
class RobotRecord:
    state: dict[str, Any] = field(default_factory=dict)
    received_at: float = -math.inf
    seq: int = -1
    boot_id: str | None = None
    retired_boots: set[str] = field(default_factory=set)
    link_state: str = "OFFLINE"
    recovery_reason: str = "Ожидание первой телеметрии"
    protected_resources: set[str] = field(default_factory=set)
    protected_command: dict[str, Any] | None = None
    reboot_pending: bool = False

class CooperativePlanner:
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
            self.reason = "Два робота сообщают об одном и том же узле"
            return None
        if len(set(goals.values())) != len(goals):
            self.reason = "Точки назначения роботов должны различаться"
            return None
        if any(node in blocked for node in starts.values()):
            self.reason = "Начальный узел заблокирован"
            return None
        if any(math.isinf(self.distance(starts[r], goals[r])) for r in starts):
            self.reason = "Точка назначения недоступна"
            return None
        robot_ids = sorted(starts, key=lambda r: (starts[r] == goals[r], r))
        horizon = 3 * len(self.map.nodes) + len(robot_ids) ** 2
        for order in permutations(robot_ids):
            self.last_attempts += 1
            planned: dict[str, list[str]] = {}
            for index, robot_id in enumerate(order):
                unplanned = {starts[r] for r in order[index + 1:]}
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
                self.reason = "Лимит поиска исчерпан; роботы остаются на месте"
                return None
        self.reason = "Безопасный маршрут в пределах глубины поиска не найден"
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
        self.reason = "Ожидание телеметрии"
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
        self._last_now = 0.0
        self._safety_stop = False
        self._confirmed_arrivals: dict[str, str] = {}
        self._events = deque(maxlen=256)
        self._last_planning_failure: str | None = None
        self.metrics = {"accepted_telemetry": 0, "discarded_telemetry": 0,
                        "commands": 0, "completed_moves": 0, "plans": 0,
                        "planning_failures": 0, "completed_tasks": 0, "staged_plans": 0,
                        "link_loss_events_total": 0, "link_recovery_events_total": 0,
                        "fleet_safety_stops_total": 0, "robot_errors_total": 0,
                        "planning_duration_seconds": 0.0,
                        "planning_duration_seconds_sum": 0.0,
                        "planning_duration_seconds_count": 0}

    def _emit(self, code: str, robot_id: str | None, reason: str, level: str = "info") -> None:
        self._events.append({"code": code, "robot_id": robot_id,
                             "reason": str(reason), "level": level})

    def drain_events(self) -> list[dict[str, Any]]:
        events = list(self._events)
        self._events.clear()
        return events

    def _protect(self, robot_id: str) -> None:
        record = self.robots[robot_id]
        record.protected_resources.update(self._resources(robot_id))
        command = self._round.get(robot_id) or self._held.get(robot_id)
        if command is not None:
            record.protected_command = dict(command)

    def _enter_safety_stop(self, robot_id: str | None, reason: str) -> None:
        if not self._safety_stop:
            self._safety_stop = True
            self.metrics["fleet_safety_stops_total"] += 1
            self._emit("FLEET_SAFETY_STOP", robot_id, reason, "warning")
        self._frozen = True

    def _mark_offline(self, robot_id: str) -> None:
        record = self.robots[robot_id]
        if record.boot_id is None or record.link_state == "OFFLINE":
            return
        self._protect(robot_id)
        record.link_state = "OFFLINE"
        record.recovery_reason = "Нет свежей телеметрии; последнее положение не подтверждено"
        self.metrics["link_loss_events_total"] += 1
        self._emit("LINK_LOST", robot_id, "telemetry timeout", "warning")
        self._enter_safety_stop(robot_id, "telemetry timeout")

    def _recovery_problem(self, robot_id: str, now: float, operator: bool = False) -> str | None:
        record = self.robots[robot_id]
        state = record.state
        manual_repair = self.paused and self._manual_robot == robot_id and not record.reboot_pending
        if now - record.received_at > self.heartbeat_timeout:
            return "Нет свежей телеметрии"
        error = state.get("error")
        repair_error = manual_repair and isinstance(error, dict) and error.get("code") == "OFF_GRAPH"
        if error and not repair_error:
            return "Ошибка робота ещё не сброшена"
        if not self._stationary(state):
            return "Ожидание подтверждения остановки робота"
        node = state.get("node_id")
        edge = state.get("edge")
        if node is not None and (not isinstance(node, str) or node not in self.map.nodes):
            return "Неизвестный навигационный узел"
        if edge is not None and (not isinstance(edge, (list, tuple)) or len(edge) != 2
                or not all(isinstance(n, str) and n in self.map.nodes for n in edge)
                or edge[1] not in self.map.adjacency[edge[0]]):
            return "Некорректное ребро робота"
        pending = record.protected_command or self._round.get(robot_id) or self._held.get(robot_id)
        relocating = manual_repair or operator and record.reboot_pending and node in self.map.nodes
        if node is None and not manual_repair and (
                pending is None or tuple(edge or ()) != tuple(pending["path"])):
            return "Положение не подтверждено на последнем разрешённом ребре"
        if edge and pending and tuple(edge) != tuple(pending["path"]):
            return "Робот сообщает другое активное ребро"
        if record.protected_resources and node and node not in record.protected_resources and not relocating:
            return "Узел не принадлежит последнему разрешённому участку"
        active = state.get("current_order")
        manual_order = manual_repair and isinstance(active, dict) and active.get("type") == "manual"
        if active is not None and not manual_order:
            if not isinstance(active, dict) or pending is None:
                return "Активный приказ не подтверждён диспетчером"
            if (active.get("type") != "move" or active.get("command_id") != pending["command_id"]
                    or active.get("path") != pending["path"]):
                return "Активный приказ не совпадает с разрешённым переходом"
        if active is not None and not manual_order and edge is None:
            return "Активный переход не содержит подтверждённого ребра"
        pose = state.get("pose")
        if manual_repair and node is None and "pose" not in state:
            return "Ручное восстановление требует подтверждённых координат"
        if "pose" in state:
            if not isinstance(pose, dict) or not all(
                    isinstance(pose.get(key), (int, float)) and not isinstance(pose.get(key), bool)
                    and math.isfinite(pose[key]) for key in ("x", "y", "yaw")):
                return "Некорректные координаты робота"
            x, y = pose["x"], pose["y"]
            if not (ROBOT_RADIUS < x < self.map.width - ROBOT_RADIUS
                    and ROBOT_RADIUS < y < self.map.height - ROBOT_RADIUS):
                return "Корпус робота выходит за границы склада"
            if node:
                expected = self.map.nodes[node]
                if math.hypot(x - expected.x, y - expected.y) > NODE_TOLERANCE:
                    return "Координаты не совпадают с заявленным узлом"
            if edge:
                source, target = (self.map.nodes[n] for n in edge)
                if segment_distance(x, y, source.x, source.y, target.x, target.y) > NODE_TOLERANCE:
                    return "Координаты не совпадают с заявленным ребром"
            for rid, other in self.robots.items():
                other_pose = other.state.get("pose")
                if rid != robot_id and isinstance(other_pose, dict):
                    ox, oy = other_pose.get("x"), other_pose.get("y")
                    if (isinstance(ox, (int, float)) and isinstance(oy, (int, float))
                            and math.isfinite(ox) and math.isfinite(oy)
                            and math.hypot(x - ox, y - oy) < 2 * ROBOT_RADIUS):
                        return "Положение конфликтует с последним положением другого робота"
        if node and node in self._occupied_by_others(robot_id):
            return "Заявленный узел зарезервирован другим роботом"
        return None

    def _confirm_recovery(self, robot_id: str) -> None:
        record = self.robots[robot_id]
        pending = record.protected_command or self._round.get(robot_id)
        if pending and record.state.get("node_id") == pending["path"][-1]:
            self._confirmed_arrivals[robot_id] = pending["command_id"]
        record.link_state = "ONLINE"
        record.recovery_reason = ""
        record.protected_resources.clear()
        record.protected_command = None

    def _update_links(self, now: float) -> None:
        for robot_id, record in self.robots.items():
            if now - record.received_at > self.heartbeat_timeout:
                self._mark_offline(robot_id)
            elif record.link_state == "RECOVERING":
                problem = self._recovery_problem(robot_id, now, operator=record.reboot_pending)
                if problem is not None:
                    record.recovery_reason = problem
                elif record.reboot_pending:
                    record.recovery_reason = "Положение подтверждено; требуется возобновление оператором"
                else:
                    self._confirm_recovery(robot_id)
        if self._safety_stop and all(
                record.link_state == "ONLINE" and now - record.received_at <= self.heartbeat_timeout
                and self._stationary(record.state) for record in self.robots.values()):
            self._safety_stop = False
            if not self.paused:
                self._emit("FLEET_RESUMED", None, "fresh stationary fleet confirmed")

    def ingest(self, state: dict[str, Any], now: float) -> bool:
        robot_id = state.get("robot_id")
        if not isinstance(robot_id, str) or (self._configured_ids and robot_id not in self.robots):
            return False
        if robot_id not in self.robots:
            self.robots[robot_id] = RobotRecord()
        robot = self.robots[robot_id]
        seq, boot_id = state.get("seq"), state.get("boot_id")
        if (not isinstance(seq, int) or isinstance(seq, bool) or seq < 0
                or not isinstance(boot_id, str) or not boot_id or len(boot_id) > 128
                or not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now)):
            return False
        if boot_id in robot.retired_boots or (boot_id == robot.boot_id and seq <= robot.seq):
            self.metrics["discarded_telemetry"] += 1
            return False
        source_at = state.get("sim_time", now)
        previous_source = robot.state.get("sim_time")
        if (not isinstance(source_at, (int, float)) or isinstance(source_at, bool)
                or source_at < now - TELEMETRY_MAX_AGE
                or source_at > now + TELEMETRY_FUTURE_TOLERANCE or not math.isfinite(source_at)
                or boot_id == robot.boot_id and isinstance(previous_source, (int, float))
                and source_at < previous_source):
            self.metrics["discarded_telemetry"] += 1
            return False
        self._last_now = max(self._last_now, now)
        previous_error = robot.state.get("error")
        restarted = robot.boot_id is not None and boot_id != robot.boot_id
        if robot.boot_id is not None and now - robot.received_at > self.heartbeat_timeout:
            self._mark_offline(robot_id)
        if robot.boot_id is not None and robot.link_state == "OFFLINE":
            self.metrics["link_recovery_events_total"] += 1
            self._emit("LINK_RECOVERED", robot_id, "fresh telemetry received")
        if restarted:
            self._protect(robot_id)
            robot.retired_boots.add(robot.boot_id)
            robot.reboot_pending = True
            robot.link_state = "RECOVERING"
            robot.recovery_reason = "Запуск робота изменился; требуется проверка и возобновление оператором"
            self.paused = True
            self.reason = f"Робот {robot_id} перезапущен; проверьте его положение перед возобновлением"
            self._schedule_dirty = True
            self._enter_safety_stop(robot_id, "robot boot_id changed")
            self._emit("ROBOT_RESTARTED", robot_id, "robot boot_id changed", "warning")
        elif robot.boot_id is not None and robot.link_state == "OFFLINE":
            robot.link_state = "RECOVERING"
            robot.recovery_reason = "Связь восстановлена; ожидается подтверждение безопасной остановки"
        elif robot.boot_id is None:
            robot.link_state = "ONLINE"
            robot.recovery_reason = ""
        robot.state = dict(state)
        robot.received_at, robot.seq, robot.boot_id = min(now, source_at), seq, boot_id
        self.metrics["accepted_telemetry"] += 1
        error = state.get("error")
        if error and (error != previous_error or restarted):
            self.metrics["robot_errors_total"] += 1
            reason = error.get("message", error.get("code", "robot error")) if isinstance(error, dict) else str(error)
            self._emit("ROBOT_ERROR", robot_id, reason, "error")
        elif previous_error and not error and not restarted:
            self._emit("ERROR_CLEARED", robot_id, "robot confirmed error cleared")
        return True

    def submit(self, robot_id: str, kind: str | dict[str, Any], target: str | None = None,
               linear: float = 0.0, angular: float = 0.0, now: float | None = None) -> dict[str, Any]:
        if robot_id not in self.robots:
            raise ValueError(f"Неизвестный робот: {robot_id}")
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
                    raise ValueError("Нет свободной парковки")
                target = min(candidates, key=lambda n: (self.planner.distance(source, n), n))
            if target not in self.map.nodes:
                raise ValueError(f"Неизвестная точка назначения: {target}")
            if target in {g for r, g in self.goals.items() if r != robot_id}:
                raise ValueError("Эта точка назначения уже назначена другому роботу")
            self.goals[robot_id] = target
            self._auto_goals.discard(robot_id)
            self._schedule_dirty = True
        elif kind == "stop":
            self.paused = True
            self._manual_robot = None
            self._stop_commands.clear()
            self.reason = "Парк остановлен оператором"
        elif kind == "resume":
            check_now = self._last_now if now is None else now
            was_paused = self.paused
            requires_confirmation = self.paused or self._safety_stop
            for rid, robot in self.robots.items():
                state = robot.state
                if check_now - robot.received_at > self.heartbeat_timeout:
                    raise ValueError(f"Перед возобновлением дождитесь свежей телеметрии робота {rid}")
                if state.get("error"):
                    raise ValueError(f"Перед возобновлением сбросьте ошибку у робота {rid}")
                if requires_confirmation or robot.link_state == "RECOVERING":
                    problem = self._recovery_problem(rid, check_now, operator=robot.reboot_pending)
                    if problem is not None:
                        raise ValueError(f"Робот {rid}: {problem}")
                if state.get("node_id") is None and (rid not in self._round or rid == self._manual_robot):
                    raise ValueError(f"Перед возобновлением верните робота {rid} к навигационному узлу")
            for rid, robot in self.robots.items():
                if robot.reboot_pending:
                    if robot.state.get("node_id") in self.map.nodes:
                        self._round.pop(rid, None)
                        self._held.pop(rid, None)
                    robot.reboot_pending = False
                    self._confirm_recovery(rid)
            if self._manual_robot is not None:
                self._round.pop(self._manual_robot, None)
                self._held.pop(self._manual_robot, None)
            self.paused = False
            self._manual_robot = None
            self._stop_commands.clear()
            self._schedule_dirty = True
            self._last_plan_signature = None
            if was_paused and not self._safety_stop:
                self._emit("FLEET_RESUMED", robot_id, "operator confirmed resume")
        elif kind == "manual":
            if self._manual_robot not in (None, robot_id):
                raise ValueError("Ручное управление доступно только для одного робота одновременно")
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (linear, angular)):
                raise ValueError("Скорости ручного управления должны быть конечными числами")
            self.paused = True
            self._manual_robot = robot_id
            self._manual_velocity = (max(-0.5, min(0.5, linear)), max(-1.5, min(1.5, angular)))
            self._manual_updated = now if now is not None else math.inf
            self.reason = "Ручное управление: остальной парк на паузе"
            self._schedule_dirty = True
        elif kind == "clear_error":
            self._outbox.append(self._command(robot_id, "clear_error"))
            self._recover.add(robot_id)
            self._schedule_dirty = True
        else:
            raise ValueError(f"Неизвестный тип команды: {kind}")
        return {"accepted": True, "robot_id": robot_id, "type": kind, "target": target}

    def set_dark_factory(self, enabled: bool) -> None:
        self.dark_factory = bool(enabled)

    def tick(self, now: float) -> list[dict[str, Any]]:
        self._last_now = max(self._last_now, now)
        self._update_links(now)
        commands, self._outbox = self._outbox, []
        stale = [r for r, record in self.robots.items()
                 if now - record.received_at > self.heartbeat_timeout]
        recovering = [r for r, record in self.robots.items() if record.link_state == "RECOVERING"]
        unavailable = stale or recovering or self._safety_stop
        if self._manual_updated == math.inf:
            self._manual_updated = now
        if unavailable or self.paused:
            self._frozen = True
            if stale:
                self.reason = "Нет свежей телеметрии: " + ", ".join(stale)
            elif recovering:
                self.reason = "Проверка безопасного восстановления: " + ", ".join(recovering)
            elif self._safety_stop:
                self.reason = "Ожидание подтверждённой остановки всего парка"
            commands.extend(self._stops(exclude=self._manual_robot if not unavailable else None))
            manual_ready = (not unavailable and self._manual_robot is not None
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
            self.reason = "Ожидание подключения роботов"
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
                                or self._confirmed_arrivals.get(robot_id) == command["command_id"]
                                or refresh_round))
                if arrived:
                    self.metrics["completed_moves"] += 1
                    self._confirmed_arrivals.pop(robot_id, None)
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
            self.reason = "Ожидание заданий"
            return self._count(commands)
        if not self._schedule:
            self._build_schedule(now)
        while self._schedule and max(map(len, self._schedule.values())) > 1:
            moving = {r: p[:2] for r, p in self._schedule.items() if len(p) > 1 and p[0] != p[1]}
            if moving:
                for robot_id, path in moving.items():
                    self._round[robot_id] = self._command(robot_id, "move", path=path)
                commands.extend(self._round.values())
                self._last_motion_sent = now
                self.reason = "Выполнение согласованных маршрутов"
                break
            for path in self._schedule.values():
                if len(path) > 1:
                    path.pop(0)
        if self._schedule and not self._round:
            self._schedule = {}
        return self._count(commands)

    def _build_schedule(self, now: float) -> None:
        previous_plans = self.metrics["plans"]
        previous_failures = self.metrics["planning_failures"]
        started = time.perf_counter()
        try:
            self._plan_schedule(now)
        finally:
            if self.metrics["plans"] != previous_plans:
                duration = time.perf_counter() - started
                self.metrics["planning_duration_seconds"] = duration
                self.metrics["planning_duration_seconds_sum"] += duration
                self.metrics["planning_duration_seconds_count"] += 1
        if self.metrics["planning_failures"] != previous_failures:
            reason = self.reason or "Безопасный маршрут не найден"
            if reason != self._last_planning_failure:
                self._emit("PLANNING_FAILED", None, reason, "warning")
            self._last_planning_failure = reason
        elif self.metrics["plans"] != previous_plans:
            self._last_planning_failure = None

    def _plan_schedule(self, now: float) -> None:
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
            self.reason = "Ожидание остановленных или неисправных роботов"
            return
        goals = self._choose_endpoints(starts, active, blocked)
        if goals is None:
            self.reason = "Нет свободного узла, чтобы уступить дорогу"
            return
        signature = (tuple(sorted(starts.items())), tuple(sorted(goals.items())), tuple(sorted(blocked)))
        if signature == self._last_plan_signature and now - self._last_plan_at < 1.0:
            return
        self._last_plan_signature, self._last_plan_at = signature, now
        self.metrics["plans"] += 1
        schedule = self.planner.plan(starts, goals, blocked)
        if schedule is None and any(r not in self._auto_goals for r in active):
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
            candidates.sort(reverse=True)
            _, target = self._random.choice(candidates[:max(1, len(candidates) // 3)])
            self.goals[robot_id] = target
            self._auto_goals.add(robot_id)
            occupied_goals.add(target)
            self._schedule_dirty = True

    def snapshot(self, now: float) -> dict[str, Any]:
        offline = sum(now - record.received_at > self.heartbeat_timeout for record in self.robots.values())
        safety_stop = self._safety_stop or bool(offline) or any(
            record.link_state == "RECOVERING" for record in self.robots.values())
        return {"paused": self.paused, "dark_factory": self.dark_factory, "safety_stop": safety_stop,
                "manual_robot": self._manual_robot, "reason": self.reason,
                "goals": dict(self.goals), "reservations": {r: sorted(self._resources(r)) for r in self.robots},
                "robots": {r: {"online": now - v.received_at <= self.heartbeat_timeout,
                               "age": max(0.0, now - v.received_at) if math.isfinite(v.received_at) else None,
                               "goal": self.goals.get(r),
                               "link_state": "OFFLINE" if now - v.received_at > self.heartbeat_timeout else v.link_state,
                               "recovery_reason": v.recovery_reason,
                               "recovery_ready": (v.link_state == "RECOVERING"
                                   and self._recovery_problem(r, now, operator=v.reboot_pending) is None)}
                           for r, v in self.robots.items()},
                "metrics": {**self.metrics, "offline_robots": offline},
                "planner": {"expansions": self.planner.last_expansions,
                            "attempts": self.planner.last_attempts}}

    def _resources(self, robot_id: str) -> set[str]:
        state = self.robots[robot_id].state
        resources = set(self.robots[robot_id].protected_resources)
        node = state.get("node_id")
        if isinstance(node, str) and node in self.map.nodes:
            resources.add(node)
        edge = state.get("edge")
        if isinstance(edge, (list, tuple)):
            resources.update(n for n in edge if isinstance(n, str) and n in self.map.nodes)
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
        velocity = state.get("velocity", {})
        if not isinstance(velocity, dict):
            return False
        values = (velocity.get("linear", 0.0), velocity.get("angular", 0.0))
        return (state.get("status") in {"idle", "paused", "manual", "error"}
                and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                        and math.isfinite(value) and abs(value) < 0.001 for value in values))

    def _count(self, commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.metrics["commands"] += len(commands)
        return commands
