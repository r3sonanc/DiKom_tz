"""FastAPI adapter: one event loop owns all mutable dispatch state."""

import asyncio
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
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from warehouse.dispatcher import Dispatcher
from warehouse.map import load_map
from warehouse.transport import RosTransport

LOG = logging.getLogger(__name__)
STATIC = Path(__file__).with_name("static")


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
        self.reset(map_name, robots)

    def event(self, message, level="info"):
        self.events.append(
            {"time": datetime.now(timezone.utc).isoformat(), "level": level, "message": message}
        )

    def reset(self, map_name, robots):
        graph = load_map(map_name)
        parking = [node.id for node in graph.nodes.values() if node.parking]
        if not 1 <= robots <= len(parking):
            raise ValueError(f"На карте {map_name} доступно парковок: {len(parking)}")
        self.graph = graph
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
        self.event(f"Новая симуляция: {map_name}, роботов: {robots}")

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
                self.event(f"{robot_id}: {state['error']}", "error")
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

    async def run(self):
        self.running = True
        try:
            while True:
                self.step(time.monotonic())
                await asyncio.sleep(0.1)
        finally:
            self.running = False

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        dispatch = self.dispatcher.snapshot(now)
        # The adapter keeps the public browser contract independent of telemetry.
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
        metrics = {
            **dispatch.get("metrics", {}),
            "controller_ticks": self.tick_count,
            "controller_tick_ms": round(self.tick_seconds * 1000, 3),
        }
        return {
            "type": "snapshot",
            "revision": self.tick_count,
            "time": datetime.now(timezone.utc).isoformat(),
            "world_id": self.world_id,
            "map": self.graph.to_dict(),
            "robots": robots,
            "dark_factory": self._dark,
            "metrics": metrics,
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
            raise HTTPException(409, "Ожидаем начальную телеметрию ROS2")
        if request.type == "move" and not request.target:
            raise HTTPException(422, "Для перемещения нужен target")
        try:
            result = runtime.dispatcher.submit(
                robot_id,
                request.type,
                target=request.target,
                linear=request.linear,
                angular=request.angular,
                now=time.monotonic(),
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from exc
        if request.type != "manual":
            runtime.event(f"{robot_id}: {request.type} {request.target or ''}".strip())
        return {"ok": True, "result": result}

    @app.post("/api/robots/{robot_id}/faults")
    async def fault(robot_id: str, request: Fault):
        if robot_id not in runtime.robot_ids:
            raise HTTPException(404, "Робот не найден")
        changes = request.model_dump(exclude_none=True)
        runtime.faults[robot_id].update(changes)
        runtime.send({"type": "fault", "robot_id": robot_id, **runtime.faults[robot_id]})
        runtime.event(f"{robot_id}: имитация неисправности {changes}", "warning")
        return {"ok": True}

    @app.post("/api/dark-factory")
    async def dark_factory(request: DarkFactory):
        runtime.dispatcher.set_dark_factory(request.enabled)
        runtime._dark = request.enabled
        runtime.event(f"Тёмный цех: {'включён' if request.enabled else 'выключен'}")
        return {"ok": True}

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics():
        values = runtime.snapshot()["metrics"]
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
                await asyncio.wait_for(socket.send_json(runtime.snapshot()), timeout=2)
                await asyncio.sleep(0.2)
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
            pass

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def main(args=None):
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.getenv("WAREHOUSE_PORT", "8000")))


if __name__ == "__main__":
    main()
