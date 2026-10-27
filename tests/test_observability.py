"""Service/fleet health and transition-based observability contracts."""

import copy
import json
import logging
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from warehouse.api import create_app
from warehouse.robot import WorldCore
from warehouse.transport import RosTransport

class ObservedTransport:
    def __init__(self):
        self.pending = []
        self.sent = []
        self.healthy = True

    def receive(self):
        states, self.pending = self.pending, []
        return copy.deepcopy(states)

    def send(self, command):
        self.sent.append(copy.deepcopy(command))

    def is_healthy(self):
        return self.healthy

def prepared_app(robots=2, now=10.0):
    transport = ObservedTransport()
    app = create_app(transport, robots=robots)
    runtime = app.state.runtime
    world = WorldCore("normal", robots, runtime.world_id)
    transport.pending = world.snapshot(now)
    runtime.step(now)
    runtime.running = True
    return app, runtime, transport, world

def fixed_http_clock(monkeypatch, now):
    # Replace only the API module's clock; keep TestClient/asyncio clocks real.
    monkeypatch.setattr("warehouse.api.time", SimpleNamespace(monotonic=lambda: now))

def lose_first_link(runtime, transport, world):
    for now in (10.5, 11.0, 11.5, 11.6):
        transport.pending = [world.snapshot(now)[1]]
        runtime.step(now)

def metric_values(body):
    return {line.split()[0]: float(line.split()[1])
            for line in body.splitlines() if line and not line.startswith("#")}

def test_partial_fleet_health_is_independent_from_service_status(monkeypatch):
    app, runtime, transport, world = prepared_app()
    lose_first_link(runtime, transport, world)
    fixed_http_clock(monkeypatch, 11.6)
    client = TestClient(app)
    try:
        response = client.get("/api/health")
    finally:
        client.close()
    assert response.status_code == 200
    state = response.json()
    assert state["ok"] is True
    assert state["world_id"] == runtime.world_id
    assert state["controller"] == "running"
    assert state["transport"] == "ROS2 Jazzy"
    assert state["service"]["transport_healthy"]
    assert state["fleet"]["total"] == 2
    assert state["fleet"]["online"] == 1
    assert state["fleet"]["offline"] == 1
    assert state["fleet"]["safety_stop"] is True

def test_health_returns_503_after_all_current_world_telemetry_goes_stale(monkeypatch):
    app, runtime, _, _ = prepared_app()
    fixed_http_clock(monkeypatch, 13.1)
    client = TestClient(app)
    try:
        response = client.get("/api/health")
    finally:
        client.close()
    assert response.status_code == 503
    state = response.json()
    assert state["ok"] is False
    assert state["fleet"]["offline"] == 2
    assert state["service"]["telemetry_recent"] is False
    assert state["service"]["telemetry_age"] > 3

def test_health_checks_controller_and_transport_lifecycle(monkeypatch):
    app, runtime, transport, _ = prepared_app()
    fixed_http_clock(monkeypatch, 10.1)
    client = TestClient(app)
    try:
        transport.healthy = False
        unavailable = client.get("/api/health")
        assert unavailable.status_code == 503
        assert unavailable.json()["service"]["transport_healthy"] is False
        transport.healthy = True
        runtime.running = False
        stopped = client.get("/api/health")
        assert stopped.status_code == 503
        assert stopped.json()["controller"] == "stopped"
    finally:
        client.close()

def test_health_is_503_until_initial_world_has_telemetry(monkeypatch):
    transport = ObservedTransport()
    app = create_app(transport, robots=2)
    app.state.runtime.running = True
    fixed_http_clock(monkeypatch, 10.0)
    client = TestClient(app)
    try:
        response = client.get("/api/health")
    finally:
        client.close()
    assert response.status_code == 503
    assert response.json()["service"]["initializing"] is True
    assert response.json()["service"]["telemetry_age"] is None

def test_metrics_expose_new_counters_and_a_live_offline_gauge(monkeypatch):
    app, runtime, transport, world = prepared_app()
    lose_first_link(runtime, transport, world)
    fixed_http_clock(monkeypatch, 11.6)
    client = TestClient(app)
    try:
        response = client.get("/metrics")
    finally:
        client.close()
    values = metric_values(response.text)
    for name in ("link_loss_events_total", "link_recovery_events_total",
                 "fleet_safety_stops_total", "robot_errors_total",
                 "planning_duration_seconds", "planning_duration_seconds_sum",
                 "planning_duration_seconds_count"):
        assert "warehouse_" + name in values
    assert values["warehouse_link_loss_events_total"] == 1
    assert values["warehouse_offline_robots"] == 1
    assert values["warehouse_fleet_safety_stops_total"] == 1
    assert "warehouse_completed_tasks" in values
    assert "warehouse_controller_ticks" in values
    assert runtime.metrics_snapshot(20)["offline_robots"] == 2

def test_structured_link_events_and_counters_do_not_repeat_on_each_tick(caplog):
    with caplog.at_level(logging.INFO, logger="warehouse.api"):
        _, runtime, transport, world = prepared_app()
        lose_first_link(runtime, transport, world)
        for now in (11.7, 11.8, 11.9):
            transport.pending = [world.snapshot(now)[1]]
            runtime.step(now)
    losses = [event for event in runtime.events if event.get("code") == "LINK_LOST"]
    stops = [event for event in runtime.events if event.get("code") == "FLEET_SAFETY_STOP"]
    assert len(losses) == len(stops) == 1
    assert losses[0]["robot_id"] == "robot1"
    assert losses[0]["reason"]
    assert losses[0]["level"] == "warning"
    assert re.match(r"^\d{4}-\d\d-\d\dT.*Z$", losses[0]["time"])
    records = [record.getMessage() for record in caplog.records
               if getattr(record, "event_code", None) == "LINK_LOST"]
    assert len(records) == 1
    assert " | WARNING | robot1 | LINK_LOST | " in records[0]
    assert runtime.dispatcher.metrics["link_loss_events_total"] == 1

def test_recovery_counter_counts_the_first_fresh_packet_once_not_duplicates():
    _, runtime, transport, world = prepared_app()
    lose_first_link(runtime, transport, world)
    returned = world.snapshot(11.7)
    transport.pending = returned
    runtime.step(11.7)
    for now in (11.8, 11.9, 12.0):
        transport.pending = copy.deepcopy(returned)
        runtime.step(now)
    assert runtime.dispatcher.metrics["link_loss_events_total"] == 1
    assert runtime.dispatcher.metrics["link_recovery_events_total"] == 1
    events = [event for event in runtime.events if event.get("code") == "LINK_RECOVERED"]
    assert len(events) == 1
    assert events[0]["robot_id"] == "robot1"

def test_malformed_sequence_cannot_crash_the_controller_or_refresh_health():
    _, runtime, transport, _ = prepared_app(robots=1)
    prior = copy.deepcopy(runtime.raw_states["robot1"])
    malformed = copy.deepcopy(prior)
    malformed["seq"] = "not-an-integer"
    transport.pending = [malformed]
    runtime.step(11.0)
    assert runtime.raw_states["robot1"] == prior
    assert runtime.last_received == 10.0

@pytest.mark.parametrize("field,value", [
    ("pose", None),
    ("pose", {"x": float("nan"), "y": 8, "yaw": 0}),
    ("pose", {"x": True, "y": 8, "yaw": 0}),
    ("velocity", None),
    ("velocity", {"linear": 0, "angular": float("inf")}),
    ("node_id", "r0c0"),
    ("edge", ["p1", "p2"]),
    ("current_order", {"type": "move", "command_id": "wrong", "path": ["p1", "r1c5"]}),
    ("boot_id", ""),
    ("status", []),
    ("current_order", {"type": [], "command_id": "malformed"}),
])
def test_incomplete_or_inconsistent_telemetry_cannot_refresh_link(field, value):
    _, runtime, transport, world = prepared_app(robots=1)
    malformed = world.snapshot(11.0)[0]
    malformed[field] = value
    transport.pending = [malformed]
    runtime.step(11.0)
    assert runtime.last_received == 10.0
    assert runtime.dispatcher.robots["robot1"].received_at == 10.0

def test_duplicate_error_telemetry_does_not_duplicate_logs_or_metrics(caplog):
    with caplog.at_level(logging.INFO, logger="warehouse.api"):
        _, runtime, transport, world = prepared_app(robots=1)
        state = world.snapshot(10.1)[0]
        state["error"] = {"code": "STUCK", "message": "Движение заблокировано"}
        state["status"] = "error"
        transport.pending = [state]
        runtime.step(10.1)
        for now in (10.2, 10.3):
            transport.pending = [copy.deepcopy(state)]
            runtime.step(now)
        fresh = copy.deepcopy(state)
        fresh["seq"] += 1
        transport.pending = [fresh]
        runtime.step(10.4)
        cleared = world.snapshot(10.5)[0]
        cleared["seq"] = fresh["seq"] + 1
        transport.pending = [cleared]
        runtime.step(10.5)
    assert runtime.dispatcher.metrics["robot_errors_total"] == 1
    errors = [event for event in runtime.events if event.get("code") == "ROBOT_ERROR"]
    clears = [event for event in runtime.events if event.get("code") == "ERROR_CLEARED"]
    assert len(errors) == len(clears) == 1
    assert errors[0]["robot_id"] == clears[0]["robot_id"] == "robot1"

def test_health_reports_robot_errors_without_failing_the_service(monkeypatch):
    app, runtime, transport, world = prepared_app(robots=1)
    state = world.snapshot(10.1)[0]
    state["error"] = {"code": "STUCK", "message": "Движение заблокировано"}
    state["status"] = "error"
    transport.pending = [state]
    runtime.step(10.1)
    fixed_http_clock(monkeypatch, 10.2)
    client = TestClient(app)
    try:
        response = client.get("/api/health")
    finally:
        client.close()
    assert response.status_code == 200
    assert response.json()["fleet"]["errors"] == 1
    assert response.json()["fleet"]["online"] == 1
    assert runtime.snapshot(10.2)["robots"][0]["availability"] == "ERROR"

def test_repeating_the_same_fault_request_does_not_repeat_a_state_change_log():
    app, runtime, _, _ = prepared_app(robots=1)
    client = TestClient(app)
    try:
        for _ in range(3):
            assert client.post("/api/robots/robot1/faults", json={"stuck": True}).status_code == 200
    finally:
        client.close()
    events = [event for event in runtime.events if event.get("code") == "FAULT_INJECTION_CHANGED"]
    assert len(events) == 1

def test_log_records_keep_utc_fields_on_one_line(caplog):
    _, runtime, _, _ = prepared_app(robots=1)
    with caplog.at_level(logging.INFO, logger="warehouse.api"):
        runtime.event("Текст для журнала", "warning", code="TEST_EVENT", robot_id="robot1",
                      reason="first line\nsecond line")
    event = runtime.events[-1]
    assert event["reason"] == "first line\nsecond line"
    record = next(item for item in caplog.records if getattr(item, "event_code", None) == "TEST_EVENT")
    assert "\n" not in record.getMessage()
    assert "first line\\nsecond line" in record.getMessage()

def test_snapshot_separates_recovering_from_freshness_and_stale_position():
    _, runtime, _, _ = prepared_app(robots=1)
    original = runtime.dispatcher.snapshot

    def recovering(now):
        result = original(now)
        result["robots"]["robot1"].update(
            link_state="RECOVERING", recovery_reason="waiting for stationary pose",
            recovery_ready=False)
        result["safety_stop"] = True
        return result

    runtime.dispatcher.snapshot = recovering
    snapshot = runtime.snapshot(10.1)
    robot = snapshot["robots"][0]
    assert robot["connected"] is True
    assert robot["link_state"] == "RECOVERING"
    assert robot["availability"] == "RECOVERING"
    assert robot["position_stale"] is True
    assert robot["recovery_ready"] is False
    assert robot["recovery_reason"]
    # Returned snapshots still have no mutable aliases into the shared cache.
    snapshot["robots"][0]["recovery_reason"] = "tampered"
    assert json.loads(runtime.snapshot_json(10.1))["robots"][0]["recovery_reason"] != "tampered"

def test_active_manual_robot_is_online_while_automatic_fleet_is_paused():
    _, runtime, _, _ = prepared_app(robots=1)
    runtime.dispatcher.submit("robot1", "manual", now=10.01)
    runtime.raw_states["robot1"]["status"] = "manual"
    robot = runtime.snapshot(10.02)["robots"][0]
    assert robot["state"] == "manual"
    assert robot["availability"] == "ONLINE"

def test_transport_health_is_false_without_a_live_ros_executor():
    transport = RosTransport()
    assert transport.is_healthy() is False
