"""Deterministic API-state contracts, independent of ROS or wall-clock sleeps."""

import copy
import asyncio
import json
import math

import pytest
from fastapi.testclient import TestClient

from warehouse.api import FleetRuntime, create_app
from warehouse.robot import WorldCore


class QueuedTransport:
    def __init__(self):
        self.pending = []
        self.sent = []

    def receive(self):
        result, self.pending = self.pending, []
        return copy.deepcopy(result)

    def send(self, command):
        self.sent.append(copy.deepcopy(command))


def ready_runtime(map_name="normal", robots=1, now=10.0):
    transport = QueuedTransport()
    runtime = FleetRuntime(transport, map_name, robots)
    world = WorldCore(map_name, robots, runtime.world_id)
    transport.pending = world.snapshot(now)
    runtime.step(now)
    return runtime, transport, world


def test_snapshot_reports_robot_link_age_without_new_telemetry():
    runtime, _, _ = ready_runtime(now=10.0)
    fresh = runtime.snapshot(10.2)["robots"][0]
    assert fresh["connected"]
    assert math.isclose(fresh["last_seen"], 0.2)
    boundary = runtime.snapshot(11.5)["robots"][0]
    assert boundary["connected"]
    stale = runtime.snapshot(11.5001)["robots"][0]
    assert not stale["connected"]
    assert stale["last_seen"] > 1.5
    assert stale["x"] == fresh["x"]


def test_runtime_waits_for_every_robot_after_reset_and_repeats_reset_safely():
    transport = QueuedTransport()
    runtime = FleetRuntime(transport, "normal", 2)
    runtime.step(1.0)
    resets = [message for message in transport.sent if message["type"] == "reset"]
    assert len(resets) == 1
    assert runtime._reset_pending
    world = WorldCore("normal", 2, runtime.world_id)
    states = world.snapshot(1.1)
    transport.pending = [states[0]]
    runtime.step(1.1)
    assert runtime._reset_pending
    transport.pending = [states[1]]
    runtime.step(1.2)
    assert not runtime._reset_pending
    assert all(robot["connected"] for robot in runtime.snapshot(1.2)["robots"])


def test_reset_discards_old_world_and_changes_map_and_robot_ids():
    runtime, transport, world = ready_runtime(now=10.0)
    old_world = runtime.world_id
    old_states = world.snapshot(10.1)
    runtime.reset("hardcore", 5)
    transport.pending = old_states
    runtime.step(10.2)
    snapshot = runtime.snapshot(10.2)
    assert runtime.world_id != old_world
    assert runtime.raw_states == {}
    assert snapshot["map"]["name"] == "hardcore"
    assert [robot["id"] for robot in snapshot["robots"]] == [f"robot{i}" for i in range(1, 6)]
    assert not any(robot["connected"] for robot in snapshot["robots"])


def test_duplicate_or_reordered_state_cannot_refresh_robot_link():
    runtime, transport, world = ready_runtime(now=10.0)
    accepted = runtime.raw_states["robot1"]
    duplicate = copy.deepcopy(accepted)
    duplicate["pose"]["x"] = -999
    transport.pending = [duplicate]
    runtime.step(11.4)
    assert runtime.raw_states["robot1"]["pose"]["x"] != -999
    assert runtime.snapshot(11.6)["robots"][0]["connected"] is False
    fresh = world.snapshot(11.7)[0]
    transport.pending = [fresh]
    runtime.step(11.7)
    assert runtime.snapshot(11.7)["robots"][0]["connected"]


def test_snapshot_metrics_goal_and_reservations_agree_with_controller():
    runtime, _, _ = ready_runtime(now=10.0)
    runtime.dispatcher.submit("robot1", "move", target="r1c5", now=10.0)
    runtime.step(10.1)
    snapshot = runtime.snapshot(10.1)
    assert snapshot["revision"] == runtime.tick_count
    assert snapshot["dispatcher"]["goals"]["robot1"] == "r1c5"
    assert snapshot["robots"][0]["order"]["target"] == "r1c5"
    assert set(snapshot["dispatcher"]["reservations"]["robot1"]) == {"p1", "r1c5"}
    assert snapshot["metrics"]["controller_ticks"] == runtime.tick_count


def test_two_runtime_instances_do_not_share_map_world_or_events():
    first, _, _ = ready_runtime("normal", 1)
    second, _, _ = ready_runtime("hardcore", 5)
    first.event("first runtime only")
    assert first.snapshot(10)["world_id"] != second.snapshot(10)["world_id"]
    assert first.snapshot(10)["map"]["name"] == "normal"
    assert second.snapshot(10)["map"]["name"] == "hardcore"
    assert all(event["message"] != "first runtime only" for event in second.snapshot(10)["events"])


def test_json_frame_is_shared_within_time_bucket_and_rebuilt_when_time_advances(monkeypatch):
    runtime, _, _ = ready_runtime()
    original = runtime._snapshot
    builds = []

    def tracked(now):
        builds.append(now)
        return original(now)

    monkeypatch.setattr(runtime, "_snapshot", tracked)
    first = runtime.snapshot_json(10.01)
    for _ in range(50):
        assert runtime.snapshot_json(10.09) is first
    assert len(builds) == 1
    later = runtime.snapshot_json(10.11)
    assert len(builds) == 2
    assert json.loads(later)["robots"][0]["last_seen"] > json.loads(first)["robots"][0]["last_seen"]


def test_public_snapshot_cannot_mutate_live_state_or_cached_map():
    runtime, _, _ = ready_runtime()
    encoded = runtime.snapshot_json(10.01)
    snapshot = runtime.snapshot(10.01)
    snapshot["map"]["nodes"][0]["x"] = -999
    snapshot["robots"][0]["fault"]["stuck"] = True
    snapshot["events"][0]["message"] = "tampered"
    snapshot["dispatcher"]["goals"]["robot1"] = "bad-node"
    fresh = runtime.snapshot(10.01)
    assert fresh["map"]["nodes"][0]["x"] != -999
    assert not fresh["robots"][0]["fault"]["stuck"]
    assert fresh["events"][0]["message"] != "tampered"
    assert fresh["dispatcher"]["goals"] == {}
    assert runtime.snapshot_json(10.02) == encoded


def test_static_map_is_not_reconverted_for_every_snapshot(monkeypatch):
    runtime, _, _ = ready_runtime()

    def unexpected_conversion(self):
        raise AssertionError("Static map was converted again")

    monkeypatch.setattr(type(runtime.graph), "to_dict", unexpected_conversion)
    assert runtime.snapshot(10.01)["map"]["name"] == "normal"
    assert json.loads(runtime.snapshot_json(10.01))["map"]["name"] == "normal"
    assert json.loads(runtime.snapshot_json(10.21))["map"]["name"] == "normal"


def test_json_frame_updates_link_age_and_timestamp_without_controller_ticks():
    runtime, _, _ = ready_runtime()
    first = json.loads(runtime.snapshot_json(10.01))
    later = json.loads(runtime.snapshot_json(11.61))
    assert first["robots"][0]["connected"]
    assert later["robots"][0]["connected"] is False
    assert later["robots"][0]["last_seen"] > 1.5
    assert later["time"] >= first["time"]
    assert later["time"].endswith("+00:00")
    assert later["revision"] == first["revision"]


def test_event_and_controller_step_invalidate_a_frame_inside_same_time_bucket():
    runtime, _, _ = ready_runtime()
    before = json.loads(runtime.snapshot_json(10.01))
    runtime.event("new event without controller step")
    event_frame = json.loads(runtime.snapshot_json(10.02))
    assert event_frame["events"][-1]["message"] == "new event without controller step"
    runtime.dispatcher.submit("robot1", "move", target="r1c5", now=10.02)
    runtime.step(10.03)
    stepped = json.loads(runtime.snapshot_json(10.04))
    assert stepped["revision"] > before["revision"]
    assert stepped["dispatcher"]["goals"] == {"robot1": "r1c5"}


@pytest.mark.parametrize("path,body,field,expected", [
    ("/api/robots/robot1/commands", {"type": "manual", "linear": 0.2}, "manual", "robot1"),
    ("/api/robots/robot1/faults", {"stuck": True}, "fault", True),
    ("/api/dark-factory", {"enabled": True}, "dark", True),
    ("/api/reset", {"map": "hardcore", "robots": 5}, "map", "hardcore"),
])
def test_http_actions_invalidate_cached_json_without_waiting_for_a_tick(path, body, field, expected):
    transport = QueuedTransport()
    app = create_app(transport, robots=1)
    runtime = app.state.runtime
    world = WorldCore("normal", 1, runtime.world_id)
    transport.pending = world.snapshot(10.0)
    runtime.step(10.0)
    before = json.loads(runtime.snapshot_json(10.01))
    # Outside a lifespan context TestClient makes requests without starting the controller loop.
    with_lifespan_disabled = TestClient(app)
    try:
        response = with_lifespan_disabled.post(path, json=body)
    finally:
        with_lifespan_disabled.close()
    assert response.status_code == 200
    after = json.loads(runtime.snapshot_json(10.02))
    values = {"manual": after["dispatcher"]["manual_robot"],
              "fault": after["robots"][0]["fault"]["stuck"],
              "dark": after["dark_factory"], "map": after["map"]["name"]}
    assert values[field] == expected
    if field == "map":
        assert after["world_id"] != before["world_id"]
        assert len(after["robots"]) == 5
    else:
        assert after["revision"] == before["revision"]


def test_metrics_endpoint_does_not_build_a_world_snapshot(monkeypatch):
    app = create_app(QueuedTransport(), robots=1)
    runtime = app.state.runtime

    def unexpected_snapshot(*args, **kwargs):
        raise AssertionError("Metrics should not serialize the world")

    monkeypatch.setattr(runtime, "snapshot", unexpected_snapshot)
    monkeypatch.setattr(runtime, "_snapshot", unexpected_snapshot)
    client = TestClient(app)
    try:
        response = client.get("/metrics")
    finally:
        client.close()
    assert response.status_code == 200
    assert "warehouse_controller_ticks" in response.text


def test_http_state_response_mutation_does_not_poison_map_or_json_cache():
    app = create_app(QueuedTransport(), robots=1)
    runtime = app.state.runtime
    before = runtime.snapshot_json(10.01)
    client = TestClient(app)
    try:
        response = client.get("/api/state").json()
        response["map"]["nodes"][0]["x"] = -999
        response["robots"][0]["fault"]["stuck"] = True
        response["events"][0]["message"] = "tampered by client"
        fresh = client.get("/api/state").json()
    finally:
        client.close()
    assert fresh["map"]["nodes"][0]["x"] != -999
    assert not fresh["robots"][0]["fault"]["stuck"]
    assert fresh["events"][0]["message"] != "tampered by client"
    assert runtime.snapshot_json(10.02) == before


def test_a_blocked_websocket_sender_does_not_block_other_clients():
    app = create_app(QueuedTransport(), robots=1)
    endpoint = next(route.endpoint for route in app.routes if route.path == "/ws")

    class Socket:
        def __init__(self, blocked):
            self.blocked = blocked
            self.received = asyncio.Event()
            self.payload = None

        async def accept(self):
            pass

        async def send_text(self, text):
            if self.blocked:
                await asyncio.Event().wait()
            self.payload = text
            self.received.set()

    async def scenario():
        slow, fast = Socket(True), Socket(False)
        tasks = [asyncio.create_task(endpoint(slow)), asyncio.create_task(endpoint(fast))]
        try:
            await asyncio.wait_for(fast.received.wait(), timeout=1)
            assert json.loads(fast.payload)["type"] == "snapshot"
            assert slow.payload is None
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())
