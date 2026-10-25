"""The two warehouse graphs from the assignment, in metres (Cartesian axes)."""

from dataclasses import asdict, dataclass
import math


def edge_key(a: str, b: str) -> tuple[str, str]:
    """One reservation key for both directions of an undirected edge."""
    return (a, b) if a <= b else (b, a)


def segment_distance(x: float, y: float, ax: float, ay: float,
                     bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    length_squared = dx * dx + dy * dy
    t = 0.0 if length_squared == 0 else max(
        0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length_squared)
    )
    return math.hypot(x - ax - t * dx, y - ay - t * dy)


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    x: float
    y: float
    parking: bool = False


@dataclass(frozen=True, slots=True)
class WarehouseMap:
    name: str
    nodes: dict[str, Node]
    adjacency: dict[str, tuple[str, ...]]
    edges: tuple[tuple[str, str], ...]
    width: float = 16.0
    height: float = 12.0

    @property
    def parking_nodes(self) -> tuple[str, ...]:
        return tuple(node.id for node in self.nodes.values() if node.parking)

    @property
    def walls(self) -> tuple[tuple[float, float, float, float], ...]:
        w, h = self.width, self.height
        return ((0.0, 0.0, w, 0.0), (w, 0.0, w, h),
                (w, h, 0.0, h), (0.0, h, 0.0, 0.0))

    def nearest_node(self, x: float, y: float) -> tuple[str, float]:
        node = min(self.nodes.values(), key=lambda n: math.hypot(x - n.x, y - n.y))
        return node.id, math.hypot(x - node.x, y - node.y)

    def distance_to_graph(self, x: float, y: float) -> float:
        return min(segment_distance(x, y, self.nodes[a].x, self.nodes[a].y,
                                    self.nodes[b].x, self.nodes[b].y)
                   for a, b in self.edges)

    def to_dict(self) -> dict:
        return {"name": self.name, "width": self.width, "height": self.height,
                "nodes": [asdict(node) for node in self.nodes.values()],
                "edges": [list(edge) for edge in self.edges],
                "walls": [list(wall) for wall in self.walls]}


def load_map(name: str = "normal") -> WarehouseMap:
    """Explicit rows preserve missing edges in the ASCII specification."""
    if name not in {"normal", "hardcore"}:
        raise ValueError("map must be 'normal' or 'hardcore'")
    nodes: dict[str, Node] = {}
    edges: set[tuple[str, str]] = set()

    def grid(row: int, col: int) -> str:
        node_id = f"r{row}c{col}"
        nodes.setdefault(node_id, Node(node_id, 2.0 + 2 * col, 10.0 - 2 * row))
        return node_id

    def connect(a: str, b: str) -> None:
        edges.add(edge_key(a, b))

    if name == "normal":
        for row in range(5):
            for col in range(5):
                grid(row, col)
                if row:
                    connect(grid(row - 1, col), grid(row, col))
                if col and row in (0, 2, 4):
                    connect(grid(row, col - 1), grid(row, col))
        parking_rows = (1, 2, 3)
        for row in parking_rows:
            grid(row, 5)
            if row > 1:
                connect(grid(row - 1, 5), grid(row, 5))
        connect(grid(2, 4), grid(2, 5))
    else:
        for row in (1, 2):
            for col in range(5):
                grid(row, col)
                if col:
                    connect(grid(row, col - 1), grid(row, col))
        for col in (0, 2, 4):
            connect(grid(1, col), grid(2, col))
        parking_rows = (0, 1, 2, 3, 4)
        for row in parking_rows:
            grid(row, 5)
            if row:
                connect(grid(row - 1, 5), grid(row, 5))
        connect(grid(2, 4), grid(2, 5))
    for index, row in enumerate(parking_rows, start=1):
        node_id = f"p{index}"
        nodes[node_id] = Node(node_id, 14.0, 10.0 - 2 * row, True)
        connect(grid(row, 5), node_id)
    neighbours: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for a, b in sorted(edges):
        neighbours[a].append(b)
        neighbours[b].append(a)
    return WarehouseMap(name, nodes,
                        {key: tuple(sorted(value)) for key, value in neighbours.items()},
                        tuple(sorted(edges)))
