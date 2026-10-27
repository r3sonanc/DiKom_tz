import unittest

from warehouse.map import edge_key, load_map, segment_distance

class MapTests(unittest.TestCase):
    def test_normal_fixture_matches_ascii(self):
        graph = load_map("normal")
        self.assertEqual(len(graph.nodes), 31)
        self.assertEqual(len(graph.edges), 38)
        self.assertEqual(graph.parking_nodes, ("p1", "p2", "p3"))
        for row in range(5):
            for col in range(5):
                node_id = f"r{row}c{col}"
                self.assertIn(node_id, graph.nodes)
                if col < 4:
                    self.assertEqual(f"r{row}c{col + 1}" in graph.adjacency[node_id], row in (0, 2, 4))
        self.assertEqual(graph.adjacency["r1c5"], ("p1", "r2c5"))
        self.assertEqual(graph.adjacency["r2c5"], ("p2", "r1c5", "r2c4", "r3c5"))
        self.assertEqual(graph.adjacency["r3c5"], ("p3", "r2c5"))

    def test_hardcore_fixture_matches_ascii(self):
        graph = load_map("hardcore")
        self.assertEqual(len(graph.nodes), 20)
        self.assertEqual(len(graph.edges), 21)
        self.assertEqual(graph.parking_nodes, ("p1", "p2", "p3", "p4", "p5"))
        for col in range(5):
            self.assertEqual(f"r2c{col}" in graph.adjacency[f"r1c{col}"], col in (0, 2, 4))
        self.assertEqual(graph.adjacency["r1c5"], ("p2", "r0c5", "r2c5"))
        self.assertNotIn("r1c5", graph.adjacency["r1c4"])
        self.assertIn("r2c5", graph.adjacency["r2c4"])

    def test_graphs_are_connected_symmetric_and_inside_walls(self):
        for name in ("normal", "hardcore"):
            graph = load_map(name)
            visited, pending = set(), [graph.parking_nodes[0]]
            while pending:
                node_id = pending.pop()
                if node_id in visited:
                    continue
                visited.add(node_id)
                pending.extend(graph.adjacency[node_id])
            self.assertEqual(visited, set(graph.nodes))
            for a, b in graph.edges:
                self.assertIn(a, graph.adjacency[b])
                self.assertIn(b, graph.adjacency[a])
            for node in graph.nodes.values():
                self.assertTrue(0 < node.x < graph.width and 0 < node.y < graph.height)
            self.assertEqual(len(graph.walls), 4)

    def test_geometry_and_export(self):
        graph = load_map()
        self.assertEqual(graph.nearest_node(14, 8), ("p1", 0))
        self.assertAlmostEqual(graph.distance_to_graph(13, 8.2), 0.2)
        self.assertEqual(segment_distance(3, 2, 0, 0, 1, 0), 8 ** 0.5)
        self.assertEqual(edge_key("b", "a"), ("a", "b"))
        self.assertEqual(len(graph.to_dict()["nodes"]), 31)
        with self.assertRaises(ValueError):
            load_map("unknown")


if __name__ == "__main__":
    unittest.main()
