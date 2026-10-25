import copy
import math
import time

from fastapi.testclient import TestClient

from warehouse.api import create_app
from warehouse.robot import WorldCore


class InMemoryTransport:
    """Exercise the exact JSON boundary with real physics, without rclpy."""

    def __init__(self):
        self.world = WorldCore()
        self.revisions = {}
        self.closed = False
        self.last_time = time.monotonic()

    def start(self):
        pass

    def receive(self):
        now = time.monotonic()
        self.world.tick(min(0.1, now - self.last_time), now)
        self.last_time = now
        return self.world.telemetry(now)

    def send(self, command):
        command = dict(command)
        if command.get("command_id"):
            key = command["command_id"]
            self.revisions.setdefault(key, len(self.revisions) + 1)
            command["command_seq"] = self.revisions[key]
        self.world.command(command, time.monotonic())

    def close(self):
        self.closed = True


def test_api_health_snapshot_websocket_and_metrics():
    transport = InMemoryTransport()
    app = create_app(transport)
    with TestClient(app) as client:
        for _ in range(30):
            if client.get("/api/health").status_code == 200:
                break
            time.sleep(0.02)
        assert client.get("/api/health").status_code == 200
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        snapshot = client.get("/api/state").json()
        assert len(snapshot["robots"]) == 3
        assert all(r["connected"] for r in snapshot["robots"])
        assert all(math.isfinite(r["x"]) for r in snapshot["robots"])
        with client.websocket_connect("/ws") as websocket:
            assert websocket.receive_json()["type"] == "snapshot"
        assert "warehouse_controller_ticks" in client.get("/metrics").text
        assert client.post("/api/dark-factory", json={"enabled": True}).json()["ok"]
        assert client.get("/api/state").json()["dark_factory"]
        assert client.post("/api/robots/robot1/commands", json={"type": "stop"}).status_code == 200
        assert client.post("/api/robots/robot1/faults", json={"stuck": True}).status_code == 200
    assert transport.closed


def test_api_rejects_invalid_requests_and_reset_counts():
    with TestClient(create_app(InMemoryTransport())) as client:
        for request in (
            {"map": "normal", "robots": 4},
            {"map": "missing", "robots": 1},
            {"map": "normal", "robots": 0},
            {"map": "normal", "robots": 1, "unknown": True},
        ):
            assert client.post("/api/reset", json=request).status_code == 422
        assert client.post("/api/robots/no-such-robot/commands", json={"type": "stop"}).status_code == 404
        assert client.post("/api/robots/robot1/commands", json={"type": "manual", "linear": 4}).status_code == 422
        assert client.post("/api/robots/robot1/commands", json={"type": "unknown"}).status_code == 422
        assert client.post("/api/reset", json={"map": "hardcore", "robots": 5}).status_code == 200


def test_runtime_filters_old_world_and_out_of_order_telemetry():
    transport = InMemoryTransport()
    app = create_app(transport, robots=1)
    runtime = app.state.runtime
    runtime.step(time.monotonic())
    runtime.step(time.monotonic() + 0.2)
    world_id = runtime.world_id
    state = transport.world.snapshot(time.monotonic())[0]
    state["seq"] += 10
    queued = [state]
    transport.receive = lambda: copy.deepcopy(queued)
    runtime.step(time.monotonic() + 0.3)
    accepted = runtime.raw_states["robot1"]["seq"]
    state["seq"] -= 5
    runtime.step(time.monotonic() + 0.4)
    assert runtime.raw_states["robot1"]["seq"] == accepted
    runtime.reset("normal", 1)
    assert runtime.world_id != world_id
    runtime.step(time.monotonic() + 0.5)
    assert runtime.raw_states == {}
