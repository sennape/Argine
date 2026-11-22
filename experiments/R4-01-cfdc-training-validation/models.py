"""Research 4 Phase 1 -- the two frozen learners (numpy only): CHEAP (CART, depth<=3, <=4 features, integer thresholds/ranks) and RICH (GBRT, diagnostic)."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

HERE = Path(__file__).resolve().parent
FEATURE_ORDER = ["events_since_last_ref", "decayed_count_fx", "events_since_admission", "hits_since_admission", "reload_count",
                 "prev_reuse_interval", "mean_reuse_interval", "in_prev_layer_event", "occupancy_permille", "pending_misses",
                 "recency_rank", "frequency_rank", "expert_bytes", "layer_id", "expert_id", "layer_cycle_offset"]
NODES = 127


def sha_bytes(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def canon(o: Any) -> bytes: return json.dumps(o, sort_keys=True, separators=(",", ":")).encode()


# ====================================================================== CHEAP: CART
CHEAP_CFG = {"max_depth": 3, "min_samples_leaf": 1000, "min_decisions_per_leaf": 50, "max_distinct_features": 4, "random_seed": 0}


def _best_split(X, y, g, idx, cols, cfg):
    """Exhaustive integer-threshold split (x <= t) maximising SSE reduction; leaf-support rule: each child >= 1000 rows from >= 50 distinct decisions.
    Ties: lower feature index (position in `cols`), then lower t. Returns (gain, col, t) or None."""
    n = len(idx); yy = y[idx]; gg = g[idx]; S = float(yy.sum()); base = S * S / n
    starts = np.flatnonzero(np.concatenate(([True], gg[1:] != gg[:-1])))
    best = None
    for c in cols:
        x = X[idx, c].astype(np.int64)
        o = np.argsort(x, kind="stable"); xs = x[o]; cs = np.cumsum(yy[o])
        pos = np.flatnonzero(xs[:-1] != xs[1:])
        if len(pos) == 0: continue
        nl = pos + 1; nr = n - nl
        mn = np.sort(np.minimum.reduceat(x, starts)); mx = np.sort(np.maximum.reduceat(x, starts)); t = xs[pos]
        ld = np.searchsorted(mn, t, side="right"); rd = len(mx) - np.searchsorted(mx, t, side="right")
        ok = (nl >= cfg["min_samples_leaf"]) & (nr >= cfg["min_samples_leaf"]) & (ld >= cfg["min_decisions_per_leaf"]) & (rd >= cfg["min_decisions_per_leaf"])
        if not ok.any(): continue
        sl = cs[pos]; sr = S - sl
        gain = np.where(ok, sl * sl / nl + sr * sr / nr - base, -np.inf)
        b = int(np.argmax(gain))
        if best is None or gain[b] > best[0]: best = (float(gain[b]), c, int(t[b]))
    return best if best is not None and best[0] > 0 else None


def _grow(X, y, g, cols, cfg):
    nodes: List[Dict[str, Any]] = []

    def rec(idx, depth):
        nid = len(nodes); mean = float(y[idx].mean())
        node = {"id": nid, "depth": depth, "n_rows": int(len(idx)), "n_decisions": int(len(np.unique(g[idx]))), "mean_y": mean, "feature": -1, "threshold": 0, "left": -1, "right": -1, "gain": 0.0}
        nodes.append(node)
        if depth >= cfg["max_depth"]: return nid
        sp = _best_split(X, y, g, idx, cols, cfg)
        if sp is None: return nid
        gain, c, t = sp; m = X[idx, c] <= t
        node.update({"feature": int(c), "threshold": int(t), "gain": gain})
        node["left"] = rec(idx[m], depth + 1); node["right"] = rec(idx[~m], depth + 1)
        return nid
    rec(np.arange(len(y)), 0)
    return nodes


def fit_cheap(X: np.ndarray, y: np.ndarray, g: np.ndarray, cfg: Dict[str, Any] = CHEAP_CFG) -> Dict[str, Any]:
    """TRAIN only. Fit on all 16 features; if more than 4 distinct features are used keep the top 4 by total SSE reduction (ties: lower index) and refit ONCE."""
    first = _grow(X, y, g, list(range(16)), cfg)
    gains: Dict[int, float] = {}
    for nd in first:
        if nd["feature"] >= 0: gains[nd["feature"]] = gains.get(nd["feature"], 0.0) + nd["gain"]
    used = sorted(gains)
    info = {"first_fit_features": [FEATURE_ORDER[c] for c in used], "first_fit_feature_gain": {FEATURE_ORDER[c]: gains[c] for c in used}, "first_fit_nodes": len(first), "refit": False}
    nodes = first
    if len(used) > cfg["max_distinct_features"]:
        keep = sorted(sorted(used, key=lambda c: (-gains[c], c))[:cfg["max_distinct_features"]])
        nodes = _grow(X, y, g, keep, cfg); info["refit"] = True; info["kept_features"] = [FEATURE_ORDER[c] for c in keep]
    fused = sorted({nd["feature"] for nd in nodes if nd["feature"] >= 0})
    leaves = sorted([nd for nd in nodes if nd["feature"] < 0], key=lambda nd: (nd["mean_y"], nd["id"]))
    rank = {nd["id"]: r for r, nd in enumerate(leaves)}
    feat_pos = {c: i for i, c in enumerate(fused)}
    tree = {"class": "CART", "max_depth": cfg["max_depth"], "features_used": [FEATURE_ORDER[c] for c in fused], "feature_order_index": fused,
            "feature": [feat_pos[nd["feature"]] if nd["feature"] >= 0 else -1 for nd in nodes], "threshold": [int(nd["threshold"]) for nd in nodes],
            "left": [nd["left"] for nd in nodes], "right": [nd["right"] for nd in nodes], "leaf_rank": [rank.get(nd["id"], -1) for nd in nodes],
            "node_stats": [{k: nd[k] for k in ("id", "depth", "n_rows", "n_decisions", "mean_y", "gain")} for nd in nodes], "fit_info": info, "config": dict(cfg)}
    tree["hash"] = sha_bytes(canon({k: v for k, v in tree.items() if k != "hash"}))
    return tree


def cheap_rule_text(tree: Dict[str, Any]) -> str:
    names = tree["features_used"]; lines = []

    def rec(n, ind):
        if tree["feature"][n] < 0:
            lines.append("  " * ind + f"-> leaf rank {tree['leaf_rank'][n]}  (mean log2(1+next-use distance) = {tree['node_stats'][n]['mean_y']:.3f}, rows {tree['node_stats'][n]['n_rows']})"); return
        lines.append("  " * ind + f"if {names[tree['feature'][n]]} <= {tree['threshold'][n]}:"); rec(tree["left"][n], ind + 1)
        lines.append("  " * ind + "else:"); rec(tree["right"][n], ind + 1)
    rec(0, 0)
    return "\n".join(lines)


# ====================================================================== RICH: GBRT (histogram, depth-wise)
RICH_CFG = {"rounds": 200, "learning_rate": 0.1, "max_depth": 6, "min_samples_leaf": 200, "max_bins": 255, "random_seed": 0}


def make_bins(X: np.ndarray, max_bins: int = 255) -> List[np.ndarray]:
    cuts = []
    for f in range(X.shape[1]):
        col = X[:, f]; u = np.unique(col)
        if len(u) <= max_bins: cuts.append(u.astype(np.float64))
        else: cuts.append(np.unique(np.quantile(col.astype(np.float64), np.arange(1, max_bins + 1) / (max_bins + 1), method="lower")))
    return cuts


def fit_rich(X: np.ndarray, y: np.ndarray, cfg: Dict[str, Any] = RICH_CFG, log=None, ckpt: Optional[str] = None, time_budget: float = 1e12) -> Optional["RichModel"]:
    """Deterministic; optional checkpoint/resume (pred and the finished trees are stored exactly) so the fit can be split over short executions. Returns None if the time budget ran out."""
    import time as _t
    t0 = _t.time(); n, F = X.shape; cuts = make_bins(X, cfg["max_bins"])
    B = np.stack([np.searchsorted(cuts[f], X[:, f].astype(np.float64), side="left") for f in range(F)], axis=1).astype(np.uint8)
    init = float(y.mean()); pred = np.full(n, init); lr = cfg["learning_rate"]; msl = cfg["min_samples_leaf"]; NB = 256
    trees = []; r0 = 0
    if ckpt and os.path.exists(ckpt):
        z = np.load(ckpt); pred = z["pred"]; r0 = int(z["done"]); trees = [(z["feat"][i], z["thr"][i], z["left"][i], z["right"][i], z["val"][i]) for i in range(r0)]
    for r in range(r0, cfg["rounds"]):
        if _t.time() - t0 > time_budget:
            return None
        res = y - pred
        feat = np.full(NODES, -1, np.int32); thr = np.zeros(NODES); left = np.zeros(NODES, np.int32); right = np.zeros(NODES, np.int32); val = np.zeros(NODES)
        assign = np.zeros(n, np.int32)       # tree node id per row
        level_nodes = [0]; nxt_id = 1
        for depth in range(cfg["max_depth"]):
            m = len(level_nodes); lut = np.full(NODES, -1, np.int32); lut[level_nodes] = np.arange(m, dtype=np.int32)
            lid = lut[assign]; act = lid >= 0
            ridx = np.flatnonzero(act) if not act.all() else None
            lid_a = lid if ridx is None else lid[ridx]; res_a = res if ridx is None else res[ridx]
            best = [None] * m   # (gain, f, b)
            for f in range(F):
                col = B[:, f] if ridx is None else B[ridx, f]
                key = lid_a.astype(np.int64) * NB + col
                cnt = np.bincount(key, minlength=m * NB).reshape(m, NB).astype(np.float64); sm = np.bincount(key, weights=res_a, minlength=m * NB).reshape(m, NB)
                cl = np.cumsum(cnt, axis=1); sl = np.cumsum(sm, axis=1); ct = cl[:, -1:]; st = sl[:, -1:]; cr = ct - cl; sr = st - sl
                with np.errstate(divide="ignore", invalid="ignore"):
                    gain = sl * sl / cl + sr * sr / cr - st * st / ct
                gain = np.where((cl >= msl) & (cr >= msl), gain, -np.inf)
                bb = np.argmax(gain, axis=1)
                for k in range(m):
                    gv = gain[k, bb[k]]
                    if gv > 0 and (best[k] is None or gv > best[k][0]): best[k] = (float(gv), f, int(bb[k]))
            new_level = []
            for k, nid in enumerate(level_nodes):
                if best[k] is None: continue
                _, f, b = best[k]; l_id, r_id = nxt_id, nxt_id + 1; nxt_id += 2
                feat[nid] = f; thr[nid] = cuts[f][b] if b < len(cuts[f]) else np.inf; left[nid] = l_id; right[nid] = r_id
                rows = np.flatnonzero(assign == nid); goleft = B[rows, f] <= b
                assign[rows[goleft]] = l_id; assign[rows[~goleft]] = r_id
                new_level += [l_id, r_id]
            level_nodes = new_level
            if not level_nodes: break
        for nid in range(nxt_id):
            if feat[nid] < 0:
                rows = assign == nid
                if rows.any(): val[nid] = float(res[rows].mean())
        pred = pred + lr * val[assign]
        trees.append((feat, thr, left, right, val))
        if ckpt and ((r + 1) % 5 == 0 or r == cfg["rounds"] - 1):
            np.savez(ckpt + ".tmp.npz", pred=pred, done=r + 1, feat=np.stack([x[0] for x in trees]), thr=np.stack([x[1] for x in trees]), left=np.stack([x[2] for x in trees]), right=np.stack([x[3] for x in trees]), val=np.stack([x[4] for x in trees]))
            os.replace(ckpt + ".tmp.npz", ckpt)
        if log and (r % 10 == 0 or r == cfg["rounds"] - 1): log(f"round {r + 1}/{cfg['rounds']} train_rmse={float(np.sqrt(np.mean((y - pred) ** 2))):.6f}")
    return RichModel(init, lr, trees, cuts, cfg)


class RichModel:
    def __init__(self, init: float, lr: float, trees, cuts, cfg):
        self.init, self.lr, self.cfg = float(init), float(lr), dict(cfg)
        self.feat = np.stack([t[0] for t in trees]).astype(np.int32); self.thr = np.stack([t[1] for t in trees]).astype(np.float64)
        self.left = np.stack([t[2] for t in trees]).astype(np.int32); self.right = np.stack([t[3] for t in trees]).astype(np.int32); self.val = np.stack([t[4] for t in trees]).astype(np.float64)
        self.cuts = cuts; self._lib = None; self.T = len(trees)

    def predict_float_numpy(self, X: np.ndarray) -> np.ndarray:
        Xf = X.astype(np.float64); n = len(Xf); p = np.full(n, self.init); rows = np.arange(n)
        for t in range(self.T):
            node = np.zeros(n, np.int64)
            for _ in range(self.cfg["max_depth"]):
                f = self.feat[t][node]; internal = f >= 0
                if not internal.any(): break
                xv = Xf[rows, np.where(internal, f, 0)]
                node = np.where(internal, np.where(xv <= self.thr[t][node], self.left[t][node], self.right[t][node]), node)
            p = p + self.lr * self.val[t][node]
        return p

    def predict_q_numpy(self, X: np.ndarray) -> np.ndarray:
        return np.rint(self.predict_float_numpy(X) * 65536.0).astype(np.int64)

    def _load_lib(self):
        if self._lib is not None: return self._lib
        try:
            d = Path(tempfile.gettempdir()) / "argine_r4_rich"; d.mkdir(exist_ok=True); so = d / ("librich_%s.so" % sha_bytes((HERE / "_rich_infer.c").read_bytes())[:12])
            if not so.exists():
                subprocess.run(["cc", "-O2", "-ffp-contract=off", "-fno-fast-math", "-shared", "-fPIC", "-o", str(so), str(HERE / "_rich_infer.c"), "-lm"], check=True, capture_output=True)
            lib = ctypes.CDLL(str(so)); self._lib = lib
        except Exception as exc:
            import sys as _s
            print(f"WARNING: C inference helper unavailable ({type(exc).__name__}); using the bit-identical numpy reference path", file=_s.stderr)
            self._lib = False
        return self._lib

    @property
    def backend(self) -> str:
        return "c" if self._load_lib() else "numpy"

    def predict_q(self, X: np.ndarray) -> np.ndarray:
        lib = self._load_lib()
        if not lib: return self.predict_q_numpy(X)
        Xf = np.ascontiguousarray(X, dtype=np.float64); n = len(Xf); out = np.empty(n, np.int64)
        P = lambda a: a.ctypes.data_as(ctypes.c_void_p)
        lib.rich_predict(ctypes.c_int(n), P(Xf), ctypes.c_int(self.T), P(self.feat), P(self.thr), P(self.left), P(self.right), P(self.val), ctypes.c_double(self.init), ctypes.c_double(self.lr), P(out))
        return out

    def to_json(self) -> Dict[str, Any]:
        import base64
        enc = lambda a, dt: base64.b64encode(np.ascontiguousarray(a, dtype=dt).tobytes()).decode()
        return {"class": "GBRT", "config": self.cfg, "init_value": self.init, "learning_rate": self.lr, "rounds": self.T, "nodes_per_tree_padded": NODES, "feature_order": FEATURE_ORDER,
                "encoding": "base64 little-endian arrays of shape [rounds, 127]; feat<0 marks a leaf; split rule: x[feat] <= thr goes left",
                "feat_i32": enc(self.feat, "<i4"), "thr_f64": enc(self.thr, "<f8"), "left_i32": enc(self.left, "<i4"), "right_i32": enc(self.right, "<i4"), "val_f64": enc(self.val, "<f8"),
                "bin_cuts_per_feature_counts": [len(c) for c in self.cuts]}

    def digest(self) -> str:
        h = hashlib.sha256()
        for a in (self.feat, self.thr, self.left, self.right, self.val): h.update(np.ascontiguousarray(a).tobytes())
        h.update(np.float64(self.init).tobytes()); h.update(np.float64(self.lr).tobytes()); h.update(canon(self.cfg))
        return h.hexdigest()

    @staticmethod
    def from_json(d: Dict[str, Any]) -> "RichModel":
        import base64
        T = d["rounds"]; dec = lambda s, dt: np.frombuffer(base64.b64decode(s), dtype=dt).reshape(T, NODES).copy()
        feat, thr, left, right, val = dec(d["feat_i32"], "<i4"), dec(d["thr_f64"], "<f8"), dec(d["left_i32"], "<i4"), dec(d["right_i32"], "<i4"), dec(d["val_f64"], "<f8")
        return RichModel(d["init_value"], d["learning_rate"], [(feat[t], thr[t], left[t], right[t], val[t]) for t in range(T)], [], d["config"])
