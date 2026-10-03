import copy
import unittest
from native_acceptance_metrics import unique_icon_origin


class DerivedOriginTests(unittest.TestCase):
    def capture(self):
        tags = ["result-followup-1-visual", "result-followup-2-visual"]
        nodes, measurements, sources = {}, {}, []
        for index, tag in enumerate(tags):
            rect = [20 + index * 50, 100, 60 + index * 50, 129]
            node = {"node_id": index + 10, "match_count": 1,
                    "visible_rect_px": rect, "unclipped_rect_px": list(rect),
                    "ancestor_tags": ["main-safe-content"]}
            nodes[tag] = node
            measurements[tag] = dict(zip(("x", "y", "width", "height"),
                                          [rect[0], rect[1], 40, 29]))
            sources.append({"source_tag": tag, **copy.deepcopy(node)})
        nodes["reading-followup-visual-union"] = {
            "evidence_kind": "derived_visible_bounds_union", "semantic_tag_present": False,
            "source_tags": tags, "source_semantic_nodes": sources,
            "visible_rect_px": [20, 100, 110, 129], "unclipped_rect_px": [20, 100, 110, 129]}
        return {"node_semantics": nodes, "measurements": measurements}

    def test_complete_same_capture_union_is_accepted(self):
        self.assertTrue(unique_icon_origin(self.capture(), "reading-followup-visual-union"))

    def test_missing_source_is_rejected(self):
        value = self.capture()
        del value["node_semantics"]["result-followup-1-visual"]
        self.assertFalse(unique_icon_origin(value, "reading-followup-visual-union"))

    def test_clipped_source_is_rejected(self):
        value = self.capture()
        value["node_semantics"]["reading-followup-visual-union"]["source_semantic_nodes"][0]["unclipped_rect_px"][1] -= 1
        self.assertFalse(unique_icon_origin(value, "reading-followup-visual-union"))

    def test_fabricated_unique_node_or_union_is_rejected(self):
        for field, replacement in (("match_count", 2), ("node_id", 99), ("visible_rect_px", [0, 0, 5, 5])):
            value = self.capture()
            value["node_semantics"]["reading-followup-visual-union"]["source_semantic_nodes"][0][field] = replacement
            self.assertFalse(unique_icon_origin(value, "reading-followup-visual-union"))

    def test_wrong_union_tag_cannot_claim_derived_evidence(self):
        value = self.capture()
        value["node_semantics"]["other"] = value["node_semantics"]["reading-followup-visual-union"]
        self.assertFalse(unique_icon_origin(value, "other"))

    def test_real_origin_requires_unique_semantics(self):
        value = self.capture()
        self.assertTrue(unique_icon_origin(value, "result-followup-1-visual"))
        value["node_semantics"]["result-followup-1-visual"]["match_count"] = 2
        self.assertFalse(unique_icon_origin(value, "result-followup-1-visual"))


if __name__ == "__main__":
    unittest.main()
