"""R2-11 workload corpus loads cleanly through the unmodified loader."""
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argine.research import workloads as wlmod

REPO_ROOT = Path(__file__).resolve().parents[1]
WLDIR = REPO_ROOT / "research2" / "workloads" / "r2_11"


def test_exactly_12_workload_files():
    files = sorted(WLDIR.glob("*.json"))
    assert len(files) == 12, files


def test_each_workload_loads_one_eval_item():
    files = sorted(WLDIR.glob("*.json"))
    for p in files:
        wl = wlmod.load(str(p))
        items = wl.split("eval")
        assert len(items) == 1, (p, items)


def test_category_counts_3_each():
    from collections import Counter
    files = sorted(WLDIR.glob("*.json"))
    cats = []
    for p in files:
        d = json.loads(p.read_text(encoding="utf-8"))
        cats.append(d["semantic_category"])
    counts = Counter(cats)
    assert len(counts) == 4
    assert all(v == 3 for v in counts.values()), counts


def test_unique_workload_and_item_ids():
    files = sorted(WLDIR.glob("*.json"))
    names = set()
    item_ids = set()
    for p in files:
        d = json.loads(p.read_text(encoding="utf-8"))
        names.add(d["name"])
        item_ids.add(d["items"][0]["id"])
    assert len(names) == 12
    assert len(item_ids) == 12


def test_provenance_split_identity_is_r2_11_eval():
    files = sorted(WLDIR.glob("*.json"))
    for p in files:
        d = json.loads(p.read_text(encoding="utf-8"))
        assert d["provenance_split_identity"] == "r2_11_eval", p


def test_summarization_source_files_exist_and_are_distinct():
    sources = sorted((WLDIR / "sources").glob("*.txt"))
    assert len(sources) == 3
    texts = [p.read_text(encoding="utf-8") for p in sources]
    assert len(set(texts)) == 3
