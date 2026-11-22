"""R2-BRIDGE-01 Phase 2A.1 -- regression tests for the graph-boundary
instrumentation repair in argine.research.rawlog.to_trace().

Root cause (see _absorb_split_tail_graphs docstring): llama.cpp can revisit
the layer axis a second time within one logical forward pass for a small
trailing sub-batch, and the C++ side's "layer went backwards -> new forward
pass" heuristic files that trailing sub-batch's records under a new graph
id -- including the one record (for the output/top layer) that belongs with
the main batch's sibling layers. The repair detects this narrow signature at
parse time and re-attributes that one record to the graph id its siblings
belong to; it changes no expert id, no token count, no decode-step
accounting, and does not touch any genuinely separate step.

These tests use small synthetic logs (4 MoE layers, not Qwen3's 48) purely
for speed/readability; the detection logic in _absorb_split_tail_graphs does
not depend on any specific layer count or token count, so it is exercised
identically to the real 48-layer / 512-token case documented in
PHASE2A1-INSTRUMENTATION-REPAIR.json.
"""
import unittest

from argine.research import rawlog
from argine.research.runner import trace_completeness


def _r(graph, ts, layer, n_tok, n_used, ids):
    assert len(ids) == n_tok * n_used
    return "R\t{}\t{}\t{}\t{}\t{}\t{}".format(
        graph, ts, layer, n_tok, n_used, "\t".join(str(x) for x in ids))


def _header(n_layers_hint=4):
    return "H\targine-raw\t2\t1000\t5000000\t1"


def _ids(n_tok, n_used, base=1):
    return [base + (i % 5) for i in range(n_tok * n_used)]


def _build_log(lines):
    text = "\n".join([_header()] + lines) + "\n"
    import tempfile, pathlib
    p = pathlib.Path(tempfile.mkstemp(suffix=".tsv")[1])
    p.write_text(text)
    return rawlog.parse(p)


class TestGraphSplitTailAbsorption(unittest.TestCase):
    """4 MoE layers (0..3); layer 3 is the "top"/output layer."""

    def _main_batch(self, graph, ts0, n_tok, n_used=2):
        return [_r(graph, ts0 + l, l, n_tok, n_used, _ids(n_tok, n_used, base=l))
                for l in range(3)]  # layers 0,1,2 only -- layer 3 missing

    def _trailing_batch(self, graph, ts0, n_tok_other, n_tok_top, n_used=2):
        lines = [_r(graph, ts0 + l, l, n_tok_other, n_used,
                    _ids(n_tok_other, n_used, base=10 + l)) for l in range(3)]
        lines.append(_r(graph, ts0 + 3, 3, n_tok_top, n_used,
                        _ids(n_tok_top, n_used, base=99)))
        return lines

    def _assert_boundary_fixed(self, n_tok_main, n_tok_trailing_other, n_tok_trailing_top):
        lines = self._main_batch(10, 1000, n_tok_main)
        lines += self._trailing_batch(11, 2000, n_tok_trailing_other, n_tok_trailing_top)
        log = _build_log(lines)
        tr = rawlog.to_trace(log)
        steps_by_graph = {s.graph: s for s in tr.steps}
        self.assertIn(10, steps_by_graph, "the main-batch graph must survive")
        self.assertNotIn(11, steps_by_graph,
                          "the trailing split-tail graph must be absorbed, not left as a phantom step")
        self.assertEqual(len(steps_by_graph[10].routes), 4,
                          "all 4 layers (including the output layer) must be present after the repair")
        self.assertEqual(steps_by_graph[10].routes[3],
                          [tuple(x) for x in
                           [_ids(n_tok_trailing_top, 2, base=99)[i * 2:(i + 1) * 2]
                            for i in range(n_tok_trailing_top)]],
                          "the absorbed layer's expert ids must be exactly what was recorded, unaltered")
        self.assertTrue(tr.meta.notes, "the repair must leave a provenance note")

    def test_boundary_511(self):
        self._assert_boundary_fixed(n_tok_main=511, n_tok_trailing_other=21, n_tok_trailing_top=1)

    def test_boundary_512(self):
        # the exact real-world signature observed on bridge-summarization-v1
        self._assert_boundary_fixed(n_tok_main=512, n_tok_trailing_other=21, n_tok_trailing_top=1)

    def test_boundary_513(self):
        self._assert_boundary_fixed(n_tok_main=513, n_tok_trailing_other=21, n_tok_trailing_top=1)

    def test_boundary_1023(self):
        self._assert_boundary_fixed(n_tok_main=1023, n_tok_trailing_other=9, n_tok_trailing_top=1)

    def test_boundary_1024(self):
        self._assert_boundary_fixed(n_tok_main=1024, n_tok_trailing_other=9, n_tok_trailing_top=1)

    def test_boundary_1025(self):
        self._assert_boundary_fixed(n_tok_main=1025, n_tok_trailing_other=9, n_tok_trailing_top=1)

    def test_trace_completeness_becomes_true_after_repair(self):
        lines = self._main_batch(10, 1000, 512)
        lines += self._trailing_batch(11, 2000, 21, 1)
        # one more ordinary decode-shaped step so predicted_n/markers line up
        log = _build_log(lines)
        markers = [
            {"event": "request_start", "prompt_id": "p1", "segment": "w1", "system_ns": 5000000 - 1000 + 999},
            {"event": "request_end", "prompt_id": "p1", "segment": "w1", "system_ns": 5000000 - 1000 + 3010},
        ]
        responses = [{"prompt_id": "p1", "predicted_n": 1}]
        tr = rawlog.to_trace(log, markers=markers)
        tc = trace_completeness(log, tr, markers, responses)
        self.assertTrue(tc["requests"][0]["complete"],
                         "after the repair, the request's forward pass must be reported complete")
        self.assertEqual(tc["requests"][0]["partial_steps"], 0)

    def test_decode_step_not_merged_even_if_top_layer_absent(self):
        """A genuine new forward pass (e.g. the next decode step) must never
        be silently absorbed, even though its layer sequence also starts
        with a 'layer went backwards' transition. If it is itself missing
        the top layer, that must remain visible as incomplete, not be
        papered over -- distinguishing a real defect from this repair's
        narrow target."""
        lines = self._main_batch(10, 1000, 512)  # missing layer 3, as before
        # a genuine decode step: uniform n_tok=1 across ALL FOUR layers would
        # be indistinguishable from "fine"; here we construct the adversarial
        # case where the next graph is uniform n_tok=1 but ALSO missing the
        # top layer, i.e. truly incomplete on its own terms.
        lines += [_r(11, 2000 + l, l, 1, 2, _ids(1, 2, base=20 + l)) for l in range(3)]
        log = _build_log(lines)
        tr = rawlog.to_trace(log)
        steps_by_graph = {s.graph: s for s in tr.steps}
        self.assertIn(10, steps_by_graph)
        self.assertIn(11, steps_by_graph,
                       "a graph whose remaining layers are NOT a smaller uniform trailing "
                       "recompute of the same batch must be left as its own, separately "
                       "checkable step")
        # graph 10 remains genuinely incomplete (no absorption happened for it)
        self.assertEqual(len(steps_by_graph[10].routes), 3)
        self.assertFalse(tr.meta.notes)

    def test_missing_middle_layer_not_merged(self):
        """If a non-output (middle) layer is missing, that is a different and
        more concerning signature than this repair's target and must never
        be silently absorbed."""
        # layers 0 and 2 present, layer 1 (middle) missing, layer 3 present
        lines = [
            _r(10, 1000, 0, 512, 2, _ids(512, 2, base=0)),
            _r(10, 1001, 2, 512, 2, _ids(512, 2, base=2)),
            _r(10, 1002, 3, 512, 2, _ids(512, 2, base=3)),
        ]
        lines += self._trailing_batch(11, 2000, 21, 1)
        log = _build_log(lines)
        tr = rawlog.to_trace(log)
        steps_by_graph = {s.graph: s for s in tr.steps}
        self.assertEqual(len(steps_by_graph[10].routes), 3,
                          "a missing middle layer must never be silently merged away")
        self.assertFalse(tr.meta.notes)

    def test_non_boundary_trace_round_trips_unchanged(self):
        """An ordinary trace with no split-tail signature anywhere must be
        completely unaffected by the repair (regression for every already-
        valid coding/reasoning/instruction-style trace)."""
        lines = []
        for g, ts0 in [(0, 1000), (1, 2000), (2, 3000)]:
            lines += [_r(g, ts0 + l, l, 9, 2, _ids(9, 2, base=g * 10 + l)) for l in range(4)]
        log = _build_log(lines)
        tr = rawlog.to_trace(log)
        self.assertEqual(len(tr.steps), 3)
        self.assertFalse(tr.meta.notes)
        for s in tr.steps:
            self.assertEqual(len(s.routes), 4)

    def test_eviction_io_access_records_untouched(self):
        """The repair must not touch any non-R record type."""
        lines = self._main_batch(10, 1000, 512)
        lines += self._trailing_batch(11, 2000, 21, 1)
        lines.append("A\t1500\t0\t0\t3\t0\tm\t100\t1")
        lines.append("I\t1\t0\t0\t3\t0\t100\t1300\t1700\t1500\t1800\t1\t1")
        lines.append("E\t2000\t0\t3\t0\t100\t1700\t1800\t2")
        log = _build_log(lines)
        self.assertEqual(len(log.accesses), 1)
        self.assertEqual(len(log.io), 1)
        self.assertEqual(len(log.evictions), 1)
        rawlog.to_trace(log)  # must not raise, and must not mutate these
        self.assertEqual(len(log.accesses), 1)
        self.assertEqual(len(log.io), 1)
        self.assertEqual(len(log.evictions), 1)


if __name__ == "__main__":
    unittest.main()
