import asyncio
import copy
import json
import logging
import math
import os
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from warehouse.dispatcher import Dispatcher
from warehouse.map import load_map, segment_distance
from warehouse.robot import NODE_TOLERANCE, ROBOT_RADIUS
from warehouse.transport import RosTransport

LOG = logging.getLogger(__name__)
STATIC = Path(__file__).with_name("static")
SNAPSHOT_PERIOD = 0.1
MAP_NAMES = {"normal": "обычный склад", "hardcore": "узкие коридоры"}
COMMAND_NAMES = {
    "move": "перемещение", "park": "возврат на парковку", "stop": "остановка парка",
    "resume": "возобновление движения", "clear_error": "сброс ошибки",
}
FAULT_NAMES = {
    "stuck": "застревание", "link_lost": "потеря связи", "throttle": "замедление связи",
}
EVENT_NAMES = {
    "LINK_LOST": "Потеря связи: последняя позиция может быть устаревшей",
    "LINK_RECOVERED": "Связь восстановлена, проверяется безопасное продолжение",
    "FLEET_SAFETY_STOP": "Защитная остановка флота",
    "ROBOT_ERROR": "Ошибка робота",
    "ERROR_CLEARED": "Ошибка робота успешно сброшена",
    "FLEET_RESUMED": "Движение флота возобновлено",
    "PLANNING_FAILED": "Безопасный маршрут не построен",
    "ROBOT_RESTARTED": "Робот перезапущен: требуется подтверждение продолжения",
}

def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class Command(StrictModel):
    type: Literal["move", "park", "stop", "manual", "resume", "clear_error"]
    target: str | None = Field(default=None, max_length=32)
    linear: float = Field(default=0, ge=-0.8, le=0.8)
    angular: float = Field(default=0, ge=-1.5, le=1.5)

class Reset(StrictModel):
    map: Literal["normal", "hardcore"] = "normal"
    robots: int = Field(default=3, ge=1, le=5)

class DarkFactory(StrictModel):
    enabled: bool

class Fault(StrictModel):
    stuck: bool | None = None
    link_lost: bool | None = None
    throttle: bool | None = None

class FleetRuntime:
    def __init__(self, transport, map_name="normal", robots=3):
        self.transport = transport
        self.events = deque(maxlen=100)
        self.running = False
        self.tick_count = 0
        self.tick_seconds = 0.0
        self.last_received = 0.0
        self._snapshot_cache_key = None
        self._snapshot_cache = None
        self.reset(map_name, robots)

    def invalidate_snapshot(self):
        self._snapshot_cache_key = None
        self._snapshot_cache = None

    def event(self, message, level="info", *, code="OPERATOR_EVENT", robot_id=None, reason=""):
        level = level.lower()
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self.events.append({"time": timestamp, "level": level, "message": message,
                            "code": code, "robot_id": robot_id, "reason": reason})
        log_reason = str(reason or message).replace("\r", "\\r").replace("\n", "\\n")
        LOG.log({"info": logging.INFO, "warning": logging.WARNING,
                 "error": logging.ERROR}.get(level, logging.INFO),
                "%s | %s | %s | %s | %s", timestamp, level.upper(), robot_id or "-", code, log_reason,
                extra={"event_code": code, "robot_id": robot_id, "event_reason": reason})
        self.invalidate_snapshot()

    def drain_dispatcher_events(self):
        for change in self.dispatcher.drain_events():
            code = change["code"]
            robot_id = change.get("robot_id")
            reason = change.get("reason", "")
            message = EVENT_NAMES.get(code, code)
            if robot_id:
                message = f"Робот {robot_id.removeprefix('robot')}: {message}"
            if reason:
                message += f": {reason}"
            self.event(message, change.get("level", "info"), code=code,
                       robot_id=robot_id, reason=reason)

    def reset(self, map_name, robots):
        graph = load_map(map_name)
        parking = [node.id for node in graph.nodes.values() if node.parking]
        if not 1 <= robots <= len(parking):
            raise ValueError(f"На карте «{MAP_NAMES[map_name]}» доступно парковок: {len(parking)}")
        self.graph = graph
        self._map_snapshot = graph.to_dict()
        self.robot_ids = [f"robot{i + 1}" for i in range(robots)]
        self.dispatcher = Dispatcher(graph, self.robot_ids)
        self.world_id = uuid.uuid4().hex
        self.reset_seq = time.time_ns()
        self.raw_states = {}
        self.faults = {
            robot_id: {"stuck": False, "link_lost": False, "throttle": False}
            for robot_id in self.robot_ids
        }
        self._last_reset = float("-inf")
        self._reset_pending = True
        self._dark = False
        self.event(f"Новая симуляция: {MAP_NAMES[map_name]}, роботов: {robots}",
                   code="WORLD_RESET", reason=f"map={map_name}, robots={robots}")

    def send(self, command):
        self.transport.send({**command, "world_id": self.world_id})

    def _valid_telemetry(self, state):
        sequence = state.get("seq")
        boot_id = state.get("boot_id")
        if (isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0
                or not isinstance(boot_id, str) or not 1 <= len(boot_id) <= 128):
            return False
        pose, velocity = state.get("pose"), state.get("velocity")
        if not isinstance(pose, dict) or not isinstance(velocity, dict):
            return False
        if (not all(_finite_number(pose.get(key)) for key in ("x", "y", "yaw"))
                or not all(_finite_number(velocity.get(key)) for key in ("linear", "angular"))):
            return False
        x, y = pose["x"], pose["y"]
        if not (ROBOT_RADIUS <= x <= self.graph.width - ROBOT_RADIUS
                and ROBOT_RADIUS <= y <= self.graph.height - ROBOT_RADIUS):
            return False
        if (not isinstance(state.get("status"), str)
                or state["status"] not in {"idle", "moving", "paused", "manual", "error"}):
            return False
        node = state.get("node_id")
        if node is not None:
            if not isinstance(node, str) or node not in self.graph.nodes:
                return False
            point = self.graph.nodes[node]
            if math.hypot(x - point.x, y - point.y) > NODE_TOLERANCE + 1e-9:
                return False
        edge = state.get("edge")
        if edge is not None:
            if (not isinstance(edge, (list, tuple)) or len(edge) != 2
                    or not all(isinstance(item, str) and item in self.graph.nodes for item in edge)
                    or edge[1] not in self.graph.adjacency[edge[0]]):
                return False
            start, end = (self.graph.nodes[item] for item in edge)
            if (segment_distance(x, y, start.x, start.y, end.x, end.y) > NODE_TOLERANCE + 1e-9
                    or node is not None and node not in edge):
                return False
        order = state.get("current_order")
        if order is not None:
            if (not isinstance(order, dict) or not isinstance(order.get("command_id"), str)
                    or not order["command_id"] or not isinstance(order.get("type"), str)
                    or order["type"] not in {"move", "manual"}):
                return False
            if order["type"] == "move" and (edge is None or order.get("path") != list(edge)):
                return False
        error = state.get("error")
        if error is not None and (not isinstance(error, dict)
                                  or not isinstance(error.get("code"), str) or not error["code"]):
            return False
        return True

    def step(self, now):
        start = time.perf_counter()
        for state in self.transport.receive():
            if not isinstance(state, dict) or state.get("world_id") != self.world_id:
                continue
            robot_id = state.get("robot_id")
            if robot_id not in self.robot_ids:
                continue
            if not self._valid_telemetry(state):
                continue
            if not self.dispatcher.ingest(state, now):
                continue
            self.raw_states[robot_id] = state
            self.last_received = now
        if len(self.raw_states) == len(self.robot_ids):
            self._reset_pending = False
        if self._reset_pending and now - self._last_reset >= 0.5:
            self.send(
                {"type": "reset", "map": self.graph.name, "robots": len(self.robot_ids),
                 "reset_seq": self.reset_seq}
            )
            self._last_reset = now
        for command in self.dispatcher.tick(now):
            self.send(command)
        self.drain_dispatcher_events()
        self.tick_count += 1
        self.tick_seconds = time.perf_counter() - start
        self.invalidate_snapshot()

    async def run(self):
        self.running = True
        try:
            while True:
                self.step(time.monotonic())
                await asyncio.sleep(0.1)
        finally:
            self.running = False

    def snapshot(self, now=None):
        return copy.deepcopy(self._snapshot(now))

    def snapshot_json(self, now=None):
        now = time.monotonic() if now is None else now
        key = int(now / SNAPSHOT_PERIOD)
        if self._snapshot_cache_key != key:
            self._snapshot_cache = json.dumps(
                self._snapshot(now), ensure_ascii=False, separators=(",", ":"), allow_nan=False
            )
            self._snapshot_cache_key = key
        return self._snapshot_cache

    def metrics_snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        return {
            **self.dispatcher.metrics,
            "offline_robots": sum(now - record.received_at > self.dispatcher.heartbeat_timeout
                                  for record in self.dispatcher.robots.values()),
            "controller_ticks": self.tick_count,
            "controller_tick_ms": round(self.tick_seconds * 1000, 3),
        }

    def health(self, now=None):
        now = time.monotonic() if now is None else now
        dispatch = self.dispatcher.snapshot(now)
        entries = list(dispatch["robots"].values())
        online = sum(bool(robot.get("online")) for robot in entries)
        probe = getattr(self.transport, "is_healthy", None)
        try:
            transport_healthy = bool(probe()) if callable(probe) else self.running
        except (RuntimeError, AttributeError):
            transport_healthy = False
        age = max(0.0, now - self.last_received) if self.raw_states else None
        recent = age is not None and age < 3
        ready = self.running and transport_healthy and not self._reset_pending and recent
        return {
            "ok": bool(ready), "world_id": self.world_id, "controller": "running" if self.running else "stopped",
            "transport": "ROS2 Jazzy",
            "service": {"controller_running": self.running, "transport_healthy": transport_healthy,
                        "telemetry_recent": recent, "telemetry_age": age,
                        "initializing": self._reset_pending},
            "fleet": {"total": len(self.robot_ids), "online": online,
                      "offline": len(self.robot_ids) - online,
                      "recovering": sum(robot.get("online", False)
                                        and robot.get("link_state") == "RECOVERING" for robot in entries),
                      "errors": sum(bool(state.get("error")) for state in self.raw_states.values()),
                      "safety_stop": bool(dispatch.get("safety_stop") or online < len(self.robot_ids))},
        }

    def _snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        dispatch = self.dispatcher.snapshot(now)
        entries = dispatch.get("robots", [])
        if isinstance(entries, dict):
            entries = [{"id": key, **value} for key, value in entries.items()]
        by_id = {entry.get("id", entry.get("robot_id")): entry for entry in entries}
        robots = []
        for robot_id in self.robot_ids:
            state = self.raw_states.get(robot_id, {})
            control = by_id.get(robot_id, {})
            pose = state.get("pose", {})
            velocity = state.get("velocity", {})
            current = state.get("current_order") or {}
            target = control.get("goal") or (current.get("path") or [None])[-1]
            order = None
            if current or target:
                order = {
                    "id": current.get("command_id"),
                    "target": target,
                    "type": current.get("type", "move"),
                    "status": "paused" if dispatch.get("paused") else "active",
                }
            status = state.get("status", "connecting")
            if dispatch.get("paused") and status == "idle":
                status = "paused"
            online = control.get("online", False)
            link_state = control.get("link_state", "ONLINE") if online else "OFFLINE"
            manual_active = dispatch.get("manual_robot") == robot_id and status == "manual"
            availability = (link_state if link_state != "ONLINE" else "ERROR" if state.get("error")
                            else "PAUSED" if (dispatch.get("paused") and not manual_active)
                            or dispatch.get("safety_stop")
                            else "ONLINE")
            robots.append(
                {
                    **control,
                    "id": robot_id,
                    "x": pose.get("x", 0),
                    "y": pose.get("y", 0),
                    "theta": pose.get("yaw", 0),
                    "v": velocity.get("linear", 0),
                    "omega": velocity.get("angular", 0),
                    "state": status,
                    "connected": online,
                    "link_state": link_state,
                    "availability": availability,
                    "recovery_reason": control.get("recovery_reason", ""),
                    "recovery_ready": control.get("recovery_ready", False),
                    "position_stale": link_state != "ONLINE",
                    "last_seen": control.get("age"),
                    "order": order,
                    "route": list(self.dispatcher._schedule.get(robot_id, [])),
                    "error": state.get("error"),
                    "node_id": state.get("node_id"),
                    "edge": state.get("edge"),
                    "fault": self.faults[robot_id],
                }
            )
        return {
            "type": "snapshot",
            "revision": self.tick_count,
            "time": datetime.now(timezone.utc).isoformat(),
            "world_id": self.world_id,
            "map": self._map_snapshot,
            "robots": robots,
            "dark_factory": self._dark,
            "metrics": self.metrics_snapshot(now),
            "events": list(self.events),
            "dispatcher": {key: value for key, value in dispatch.items() if key != "robots"},
        }

def create_app(transport=None, map_name=None, robots=None):
    transport = RosTransport() if transport is None else transport
    runtime = FleetRuntime(
        transport,
        map_name or os.getenv("WAREHOUSE_MAP", "normal"),
        robots if robots is not None else int(os.getenv("WAREHOUSE_ROBOTS", "3")),
    )

    @asynccontextmanager
    async def lifespan(app):
        transport.start()
        task = asyncio.create_task(runtime.run(), name="fleet-controller")
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            transport.close()

    app = FastAPI(title="DiKom · Симуляция склада", version="1.0.0", lifespan=lifespan)
    app.state.runtime = runtime

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        names = {
            "map": "Карта", "robots": "Число роботов", "type": "Команда",
            "target": "Точка назначения", "linear": "Линейная скорость",
            "angular": "Угловая скорость", "enabled": "Состояние режима",
            "stuck": "Застревание", "link_lost": "Потеря связи",
            "throttle": "Замедление связи",
        }
        messages = {
            "missing": "обязательное поле",
            "extra_forbidden": "неизвестное поле",
            "literal_error": "выберите одно из допустимых значений",
            "int_type": "нужно целое число", "int_parsing": "нужно целое число",
            "int_from_float": "нужно целое число",
            "float_type": "нужно число", "float_parsing": "нужно число",
            "bool_type": "нужно значение «да» или «нет»",
            "bool_parsing": "нужно значение «да» или «нет»",
            "string_type": "нужна строка", "string_too_long": "слишком длинная строка",
            "finite_number": "нужно конечное число",
            "json_invalid": "некорректный формат JSON",
            "model_attributes_type": "нужен объект с параметрами",
        }
        details = []
        for error in exc.errors():
            location = error.get("loc", ())
            field = location[-1] if location and isinstance(location[-1], str) else "body"
            label = names.get(field, "Запрос" if field == "body" else "Поле запроса")
            kind = error["type"]
            context = error.get("ctx", {})
            if kind == "greater_than_equal":
                message = f"значение должно быть не меньше {context['ge']}"
            elif kind == "less_than_equal":
                message = f"значение должно быть не больше {context['le']}"
            else:
                message = messages.get(kind, "некорректное значение")
            details.append({"loc": list(location), "msg": f"{label}: {message}", "type": kind})
        return JSONResponse(status_code=422, content={"detail": details})

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health")
    async def health():
        status = runtime.health()
        return JSONResponse(status, status_code=200 if status["ok"] else 503)

    @app.get("/api/state")
    async def state():
        return runtime.snapshot()

    @app.post("/api/reset")
    async def reset(request: Reset):
        try:
            runtime.reset(request.map, request.robots)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True, "world_id": runtime.world_id}

    @app.post("/api/robots/{robot_id}/commands")
    async def command(robot_id: str, request: Command):
        if robot_id not in runtime.robot_ids:
            raise HTTPException(404, "Робот не найден")
        if runtime._reset_pending:
            raise HTTPException(409, "Ожидаем начальную телеметрию роботов")
        if request.type == "move" and not request.target:
            raise HTTPException(422, "Для перемещения выберите точку назначения")
        try:
            result = runtime.dispatcher.submit(
                robot_id,
                request.type,
                target=request.target,
                linear=request.linear,
                angular=request.angular,
                now=time.monotonic(),
            )
            runtime.invalidate_snapshot()
            runtime.drain_dispatcher_events()
        except (ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from exc
        if request.type != "manual":
            action = COMMAND_NAMES[request.type]
            if request.type == "move":
                action += f" к узлу {request.target}"
            runtime.event(f"Робот {robot_id.removeprefix('robot')}: {action}",
                          code="COMMAND_ACCEPTED", robot_id=robot_id, reason=request.type)
        return {"ok": True, "result": result}

    @app.post("/api/robots/{robot_id}/faults")
    async def fault(robot_id: str, request: Fault):
        if robot_id not in runtime.robot_ids:
            raise HTTPException(404, "Робот не найден")
        requested = request.model_dump(exclude_none=True)
        changes = {name: enabled for name, enabled in requested.items()
                   if runtime.faults[robot_id][name] != enabled}
        runtime.faults[robot_id].update(requested)
        runtime.invalidate_snapshot()
        runtime.send({"type": "fault", "robot_id": robot_id, **runtime.faults[robot_id]})
        if changes:
            description = "; ".join(
                f"{FAULT_NAMES[name]} — {'включено' if enabled else 'выключено'}"
                for name, enabled in changes.items()
            )
            runtime.event(f"Робот {robot_id.removeprefix('robot')}: {description}", "warning",
                          code="FAULT_INJECTION_CHANGED", robot_id=robot_id, reason=description)
        return {"ok": True}

    @app.post("/api/dark-factory")
    async def dark_factory(request: DarkFactory):
        changed = runtime._dark != request.enabled
        runtime.dispatcher.set_dark_factory(request.enabled)
        runtime._dark = request.enabled
        if changed:
            runtime.event(f"Тёмный цех: {'включён' if request.enabled else 'выключен'}",
                          code="DARK_FACTORY_CHANGED", reason=f"enabled={request.enabled}")
        return {"ok": True}

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics():
        values = runtime.metrics_snapshot()
        lines = []
        for key, value in values.items():
            if isinstance(value, (int, float)):
                safe_key = "".join(c if c.isalnum() or c == "_" else "_" for c in key)
                lines.append(f"warehouse_{safe_key} {float(value)}")
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    @app.websocket("/ws")
    async def websocket(socket: WebSocket):
        await socket.accept()
        try:
            while True:
                await asyncio.wait_for(socket.send_text(runtime.snapshot_json()), timeout=2)
                await asyncio.sleep(0.2)
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
            pass

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app

def main(args=None):
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    port = int(os.getenv("WAREHOUSE_PORT", "8000"))
    LOG.info("Запуск веб-интерфейса: http://localhost:%s", port)
    uvicorn.run(create_app(), host="0.0.0.0", port=port, log_level="warning", access_log=False)

if __name__ == "__main__":
    main()
