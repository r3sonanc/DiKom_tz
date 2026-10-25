import pytest

from tests.support import FleetHarness


@pytest.mark.parametrize("map_name", ["normal", "hardcore"])
def test_single_robot_can_visit_every_graph_node(map_name):
    fleet = FleetHarness(map_name, 1)
    fleet.step()
    for destination in fleet.world.map.nodes:
        fleet.dispatch.submit("robot1", "move", destination)
        fleet.until(lambda: not fleet.dispatch.goals, timeout=120)
        robot = fleet.world.robots["robot1"]
        assert robot.node_id == destination
        assert robot.error is None


@pytest.mark.parametrize("map_name,robots", [("normal", 3), ("hardcore", 5)])
def test_dark_factory_makes_progress_without_physical_collisions(map_name, robots):
    fleet = FleetHarness(map_name, robots)
    fleet.step()
    fleet.dispatch.set_dark_factory(True)
    fleet.advance(240)
    assert fleet.dispatch.metrics["completed_tasks"] >= robots * 2
    assert all(robot.error is None for robot in fleet.world.robots.values())


def test_lost_link_stops_robot_by_lease_and_recovers():
    fleet = FleetHarness("normal", 2)
    fleet.step()
    fleet.dispatch.submit("robot1", "move", "r0c0")
    fleet.until(lambda: fleet.world.robots["robot1"].linear > 0)
    fleet.send({"type": "fault", "robot_id": "robot1", "link_lost": True})
    fleet.advance(3)
    robot = fleet.world.robots["robot1"]
    assert robot.linear == 0
    assert not fleet.dispatch.snapshot(fleet.now)["robots"]["robot1"]["online"]
    position = (robot.x, robot.y)
    fleet.advance(2)
    assert position == (robot.x, robot.y)
    fleet.send({"type": "fault", "robot_id": "robot1", "link_lost": False})
    fleet.advance(1)
    if fleet.dispatch.paused:
        fleet.dispatch.submit("robot1", "resume")
    fleet.until(lambda: not fleet.dispatch.goals)
    assert robot.node_id == "r0c0"
    assert robot.error is None


def test_throttled_link_retains_reservations_and_completes():
    fleet = FleetHarness("normal", 2)
    fleet.step()
    fleet.send({"type": "fault", "robot_id": "robot1", "throttle": True})
    fleet.dispatch.submit("robot1", "move", "r2c3")
    fleet.advance(12)
    assert fleet.world.robots["robot1"].error is None
    fleet.send({"type": "fault", "robot_id": "robot1", "throttle": False})
    fleet.until(lambda: not fleet.dispatch.goals)
    assert fleet.world.robots["robot1"].node_id == "r2c3"


def test_stop_resume_preserves_mid_edge_reservation():
    fleet = FleetHarness("normal", 1)
    fleet.step()
    fleet.dispatch.submit("robot1", "move", "r2c0")
    fleet.until(lambda: fleet.world.robots["robot1"].node_id is None)
    fleet.dispatch.submit("robot1", "stop")
    fleet.advance(1)
    robot = fleet.world.robots["robot1"]
    position = (robot.x, robot.y)
    assert robot.linear == 0
    assert len(fleet.dispatch.snapshot(fleet.now)["reservations"]["robot1"]) >= 2
    fleet.advance(1)
    assert position == (robot.x, robot.y)
    fleet.dispatch.submit("robot1", "resume")
    fleet.until(lambda: not fleet.dispatch.goals)
    assert robot.node_id == "r2c0"


def test_stuck_robot_recovery_on_an_edge():
    fleet = FleetHarness("normal", 1)
    fleet.step()
    fleet.dispatch.submit("robot1", "move", "r0c0")
    fleet.until(lambda: fleet.world.robots["robot1"].node_id is None)
    fleet.send({"type": "fault", "robot_id": "robot1", "stuck": True})
    fleet.advance(2)
    assert fleet.world.robots["robot1"].error["code"] == "STUCK"
    assert len(fleet.dispatch.snapshot(fleet.now)["reservations"]["robot1"]) >= 2
    fleet.send({"type": "fault", "robot_id": "robot1", "stuck": False})
    fleet.dispatch.submit("robot1", "clear_error")
    fleet.advance(0.5)
    fleet.until(lambda: not fleet.dispatch.goals)
    assert fleet.world.robots["robot1"].node_id == "r0c0"
