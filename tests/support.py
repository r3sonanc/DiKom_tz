import math

from warehouse.dispatcher import Dispatcher
from warehouse.robot import ROBOT_RADIUS, WorldCore

class FleetHarness:
    def __init__(self, map_name="normal", robots=3, seed=7):
        self.world = WorldCore(map_name, robots, "test-world")
        self.dispatch = Dispatcher(self.world.map, list(self.world.robots), seed=seed)
        self.now = 0.0
        self.sequence = 0
        self.revisions = {}
        self.minimum_distance = math.inf

    def send(self, command):
        command = dict(command)
        key = command.get("command_id")
        if key is not None:
            if key not in self.revisions:
                self.sequence += 1
                self.revisions[key] = self.sequence
            command["command_seq"] = self.revisions[key]
        command["world_id"] = self.world.world_id
        return self.world.command(command, self.now)

    def step(self, seconds=0.1):
        for _ in range(round(seconds / 0.02)):
            self.now = round(self.now + 0.02, 8)
            self.world.tick(0.02, self.now)
            robots = list(self.world.robots.values())
            for index, a in enumerate(robots):
                for b in robots[index + 1:]:
                    distance = math.hypot(a.x - b.x, a.y - b.y)
                    self.minimum_distance = min(self.minimum_distance, distance)
                    assert distance >= 2 * ROBOT_RADIUS - 1e-7, "Physical collision"
        for state in self.world.telemetry(self.now):
            self.dispatch.ingest(state, self.now)
        for command in self.dispatch.tick(self.now):
            self.send(command)

    def advance(self, seconds):
        for _ in range(round(seconds / 0.1)):
            self.step()

    def until(self, predicate, timeout=120):
        for _ in range(round(timeout / 0.1)):
            self.step()
            if predicate():
                return
        raise AssertionError(
            f"Condition timed out at {self.now}: {self.dispatch.snapshot(self.now)}; "
            f"states={self.world.snapshot(self.now)}"
        )
