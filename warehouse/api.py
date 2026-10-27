import asyncio
import copy
import json
import logging
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
from warehouse.map import load_map
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

    def event(self, message, level="info"):
        self.events.append(
            {"time": datetime.now(timezone.utc).isoformat(), "level": level, "message": message}
        )
        LOG.log({"info": logging.INFO, "warning": logging.WARNING,
                 "error": logging.ERROR}.get(level, logging.INFO), message)
        self.invalidate_snapshot()

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
        self.event(f"Новая симуляция: {MAP_NAMES[map_name]}, роботов: {robots}")

    def send(self, command):
        self.transport.send({**command, "world_id": self.world_id})

    def step(self, now):
        start = time.perf_counter()
        for state in self.transport.receive():
            if state.get("world_id") != self.world_id:
                continue
            robot_id = state.get("robot_id")
            if robot_id not in self.robot_ids:
                continue
            previous = self.raw_states.get(robot_id)
            if previous and previous.get("boot_id") == state.get("boot_id"):
                if state.get("seq", -1) <= previous.get("seq", -1):
                    continue
            if not self.dispatcher.ingest(state, now):
                continue
            if state.get("error") != (previous or {}).get("error") and state.get("error"):
                error = state["error"]
                message = error.get("message", "Ошибка робота") if isinstance(error, dict) else "Ошибка робота"
                self.event(f"Робот {robot_id.removeprefix('robot')}: {message}", "error")
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

    def metrics_snapshot(self):
        return {
            **self.dispatcher.metrics,
            "controller_ticks": self.tick_count,
            "controller_tick_ms": round(self.tick_seconds * 1000, 3),
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
                    "connected": control.get("online", False),
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
            "metrics": self.metrics_snapshot(),
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
    def health():
        ready = (runtime.running and not runtime._reset_pending
                 and time.monotonic() - runtime.last_received < 3)
        return JSONResponse(
            {"ok": ready, "world_id": runtime.world_id, "transport": "ROS2 Jazzy"},
            status_code=200 if ready else 503,
        )

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
        except (ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from exc
        if request.type != "manual":
            action = COMMAND_NAMES[request.type]
            if request.type == "move":
                action += f" к узлу {request.target}"
            runtime.event(f"Робот {robot_id.removeprefix('robot')}: {action}")
        return {"ok": True, "result": result}

    @app.post("/api/robots/{robot_id}/faults")
    async def fault(robot_id: str, request: Fault):
        if robot_id not in runtime.robot_ids:
            raise HTTPException(404, "Робот не найден")
        changes = request.model_dump(exclude_none=True)
        runtime.faults[robot_id].update(changes)
        runtime.invalidate_snapshot()
        runtime.send({"type": "fault", "robot_id": robot_id, **runtime.faults[robot_id]})
        if changes:
            description = "; ".join(
                f"{FAULT_NAMES[name]} — {'включено' if enabled else 'выключено'}"
                for name, enabled in changes.items()
            )
            runtime.event(f"Робот {robot_id.removeprefix('robot')}: {description}", "warning")
        return {"ok": True}

    @app.post("/api/dark-factory")
    async def dark_factory(request: DarkFactory):
        runtime.dispatcher.set_dark_factory(request.enabled)
        runtime._dark = request.enabled
        runtime.event(f"Тёмный цех: {'включён' if request.enabled else 'выключен'}")
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
