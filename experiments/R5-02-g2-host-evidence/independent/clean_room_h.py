#!/usr/bin/env python3
"""Clean-room headroom estimator H (R5 Phase 1b), written from PROTOCOL_1B.json estimator.operational_choices,
ENDPOINTS.json headroom_bound_H / critical_path_rule and the r5-ets-v1 trace format. Streams one trace at a time.

Written by an independent verifier that did not open headroom.py, acquire.py, their tests or G2_RESULT.json. Kept as run
for the G2 seal; only the two paths were made arguments:  python clean_room_h.py HOST_PHASE1B_DIR RESULT.json"""
import bisect, gzip, hashlib, json, sys, time
from fractions import Fraction

BASE = sys.argv[1] if len(sys.argv) > 1 else "phase1b-headroom"
OUT = sys.argv[2] if len(sys.argv) > 2 else "clean_room_result.json"
F_BOUND, F_SYNC, F_FALLBACK, F_SYNC_ISSUE = 2, 4, 8, 16
NOHIDE = F_SYNC | F_FALLBACK | F_SYNC_ISSUE
THREADS = 12


def analyse(path):
    fails = []
    def fail(msg):
        if len(fails) < 50:
            fails.append(msg)
    cfg = {}
    graph_ntok = {}
    # node aggregate: [min_in, max_out, tids(set), firsts(set), count, crit(wend, dur, wstart) or None, max_dur]
    nodes = {}
    reads = []  # (layer, issue, complete, kind, flags, ok, tid)
    bad = 0
    t_incoh = [False]; i_incoh = [False]
    h = hashlib.sha256()
    with open(path, "rb") as raw:
        class Tee:
            def read(self, n=-1):
                b = raw.read(n)
                h.update(b)
                return b
        with gzip.open(Tee(), "rt", encoding="utf-8", newline="") as fh:
            for line in fh:
                s = line.rstrip("\r\n")
                if not s:
                    continue
                f = s.split("\t")
                t = f[0]
                try:
                    if t == "T":
                        if len(f) != 17:
                            raise ValueError("T len")
                        v = list(map(int, f[1:]))
                        g, l, k, tid, mode, first, hin, ws, we, hout = v[:10]
                        if hout < hin:
                            t_incoh[0] = True; fail(f"T hook_out<hook_in {g},{l},{k},{tid}")
                        if ws > 0 and not (hin <= ws <= we <= hout):
                            t_incoh[0] = True; fail(f"T incoherent wait {g},{l},{k},{tid}")
                        key = (g, l, k)
                        n = nodes.get(key)
                        if n is None:
                            n = [hin, hout, set(), set(), 0, None, 0]
                            nodes[key] = n
                        else:
                            if hin < n[0]: n[0] = hin
                            if hout > n[1]: n[1] = hout
                        if tid in n[2]:
                            fail(f"duplicate tid {tid} in node {key}")
                        n[2].add(tid); n[3].add(first); n[4] += 1
                        if ws > 0:
                            dur = we - ws
                            c = (we, dur, ws)
                            if n[5] is None or (c[0], c[1]) > (n[5][0], n[5][1]):
                                n[5] = c
                            if dur > n[6]: n[6] = dur
                    elif t == "I":
                        if len(f) != 13:
                            raise ValueError("I len")
                        v = list(map(int, f[1:]))
                        req, tid, l, e, k, by, iss, comp, ws, we, ok, fl = v
                        if ok != 1:
                            fail(f"failed read req {req}")
                        if comp < iss:
                            i_incoh[0] = True; fail(f"read complete<issue req {req}")
                        reads.append((l, iss, comp, k, fl, ok, tid))
                    elif t == "R":
                        g = int(f[1]); nt = int(f[4])
                        graph_ntok.setdefault(g, set()).add(nt)
                    elif t == "C":
                        if len(f) >= 3:
                            cfg[f[1]] = f[2]
                    elif t in ("H", "A", "E", "U", "N", "S"):
                        pass
                    else:
                        raise ValueError("unknown record " + t)
                except (ValueError, IndexError) as ex:
                    bad += 1
                    fail(f"unparseable: {ex}")
    checks = {}
    checks["no_unparseable_records"] = bad == 0
    checks["T_records_coherent"] = not t_incoh[0]
    checks["I_records_coherent"] = not i_incoh[0]
    checks["schema_r5_ets_v1"] = cfg.get("ets_schema") == "r5-ets-v1"
    # thread identity
    tidsets = set(frozenset(n[2]) for n in nodes.values())
    checks["12_records_12_distinct_tids_per_node"] = all(n[4] == THREADS and len(n[2]) == THREADS for n in nodes.values())
    checks["same_tid_set_every_node"] = len(tidsets) == 1
    tidset = next(iter(tidsets)) if len(tidsets) == 1 else frozenset()
    checks["first_flag_consistent_within_node"] = all(len(n[3]) == 1 for n in nodes.values())
    # events
    events = {}
    for key, n in nodes.items():
        events.setdefault((key[0], key[1]), []).append(key)
    for ev in events.values():
        ev.sort(key=lambda k: (nodes[k][0], k[2]))
    first_ok = True; multi_ok = True
    for ge, ev in events.items():
        if len(ev) < 2:
            multi_ok = False; fail(f"single-node event {ge}")
        fs = [k for k in ev if 1 in nodes[k][3]]
        if len(fs) != 1 or fs[0] != ev[0] or nodes[ev[0]][0] == (nodes[ev[1]][0] if len(ev) > 1 else None):
            first_ok = False; fail(f"first node problem {ge}")
    checks["exactly_one_first_node_and_earliest"] = first_ok
    checks["no_single_node_events"] = multi_ok
    # read attribution: every read in exactly one node window of its layer
    win = {}
    for key, n in nodes.items():
        win.setdefault(key[1], []).append((n[0], n[1], key))
    wstarts = {}; pmax = {}
    for l, w in win.items():
        w.sort()
        wstarts[l] = [x[0] for x in w]
        m = -1; pm = []
        for x in w:
            m = max(m, x[1]); pm.append(m)
        pmax[l] = pm
    by_node = {}
    attrib_ok = True; tid_ok = True
    for r in reads:
        l, iss = r[0], r[1]
        if r[6] not in tidset:
            tid_ok = False
        w = win.get(l)
        if not w:
            attrib_ok = False; fail("read on layer without nodes"); continue
        j = bisect.bisect_right(wstarts[l], iss) - 1
        hits = []
        k = j
        while k >= 0 and pmax[l][k] >= iss:
            if w[k][0] <= iss <= w[k][1]:
                hits.append(w[k][2])
            k -= 1
        if len(hits) != 1:
            attrib_ok = False; fail(f"read attributable to {len(hits)} windows (layer {l}, issue {iss})"); continue
        by_node.setdefault(hits[0], []).append(r)
    checks["reads_attributable_to_exactly_one_window"] = attrib_ok
    checks["read_tids_in_node_tid_set"] = tid_ok
    checks["all_reads_ok"] = not any(r[5] != 1 for r in reads)
    # decode
    # decode graph = every R record of the graph has n_tokens == 1 (prefill graphs carry one
    # n_tokens==1 R record at the output-only last layer, so "any" would misclassify them)
    dec = sorted(g for g, nt in graph_ntok.items() if nt == {1})
    decset = set(dec)
    checks["decode_graphs_511"] = len(dec) == 511
    checks["decode_graphs_have_events"] = all(any((g, l) in events for l in range(48)) for g in dec)
    h_ns = 0; nev = 0; inv_ok = True; nonzero = 0
    for (g, l), ev in events.items():
        if g not in decset:
            continue
        nev += 1
        fk = ev[0]; fn = nodes[fk]; last = nodes[ev[-1]]
        W = max(0, last[0] - fn[1])
        crit = fn[5]
        if crit is None:
            hl = 0
        else:
            wend, _, wst = crit
            rs = by_node.get(fk, [])
            c_first = None; c_all = None
            for (_, iss, comp, kind, fl, ok, tid) in rs:
                if kind == fk[2]:
                    t = iss if (fl & F_BOUND) else comp
                    if c_first is None or t > c_first: c_first = t
                if not (fl & NOHIDE):
                    if c_all is None or comp > c_all: c_all = comp
            if c_first is None:
                c_first = wst
            if c_all is None:
                T_tail = 0
            else:
                T_tail = max(0, min(c_all, wend) - max(c_first, wst))
            hl = min(T_tail, W)
        if hl > fn[6]:
            inv_ok = False; fail(f"H_layer>first_node_wait {g},{l}")
        if hl > 0: nonzero += 1
        h_ns += hl
    checks["invariant_H_layer_le_first_node_wait"] = inv_ok
    return dict(h_ns=h_ns, events=nev, decode_graphs=len(dec), nodes=len(nodes), reads=len(reads),
                nonzero_events=nonzero, gz_sha256=h.hexdigest(), checks=checks,
                telemetry_valid=all(checks.values()), failures=fails, config=cfg)


def main():
    launches = []
    for line in open(f"{BASE}/PHASE1B_LOG.jsonl"):
        r = json.loads(line)
        if r.get("type") == "launch" and r.get("counted") is True:
            launches.append(r)
    out = {"launches": []}
    num_ms = Fraction(0); den = Fraction(0)
    all_valid = len(launches) == 8
    for r in launches:
        t0 = time.time()
        st = r["server_timings"]
        fn = r["trace"]["kept"]
        a = analyse(f"{BASE}/traces/{fn}")
        pms = Fraction(float(st["predicted_ms"]))
        a["checks"]["decode_graphs_eq_predicted_n_minus_1"] = a["decode_graphs"] == st["predicted_n"] - 1
        a["checks"]["gz_sha256_matches_log"] = a["gz_sha256"] == r["trace"].get("gz_sha256")
        a["checks"]["arm_A"] = r.get("arm") == "A"
        a["telemetry_valid"] = all(a["checks"].values())
        all_valid &= a["telemetry_valid"]
        hl = Fraction(a["h_ns"], 10**6) / pms
        rec = dict(seq=r["seq"], trace=fn, prompt=r.get("prompt"), predicted_ms=st["predicted_ms"],
                   predicted_n=st["predicted_n"], h_ns=a.pop("h_ns"), launch_H=float(hl),
                   launch_H_fraction=[hl.numerator, hl.denominator], seconds=round(time.time() - t0, 1), **a)
        rec.pop("config")
        out["launches"].append(rec)
        num_ms += Fraction(rec["h_ns"], 10**6); den += pms
        print(json.dumps({k: rec[k] for k in ("seq", "h_ns", "events", "decode_graphs", "telemetry_valid", "seconds")}), flush=True)
    H = num_ms / den
    out.update(n_launches=len(launches), sum_h_ms=float(num_ms), sum_predicted_ms=float(den),
               H=repr(float(H)), H_numerator=H.numerator, H_denominator=H.denominator,
               gate="H >= 1/25", G2=("PASS" if H >= Fraction(1, 25) else "FAIL"),
               telemetry_all_valid=all_valid)
    if not all_valid:
        out["note"] = "telemetry fail-closed: at least one check failed; per protocol no H should be reported"
    json.dump(out, open(OUT, "w"), indent=1)
    print("H", out["H"], H.numerator, H.denominator, out["G2"], "telemetry_all_valid", all_valid)

if __name__ == "__main__":
    main()
