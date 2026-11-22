import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collections import OrderedDict
from argine.research.policies import Context
from argine.research2.r2_09a.policies import TwoQ, LRUK, AgedFrequency, GlobalLRU, LFU

def ctx_uniform(layers, n_experts, size, budget, top_k=1):
    return Context(layers=list(layers), n_experts=n_experts, size={l:size for l in layers}, budget=budget, top_k=top_k)

def test_twoq_basic_a1in_a1out_am():
    # small byte budget: 2 objects per partition logic via sizes
    c = ctx_uniform([0], 8, 10, 20, top_k=1)  # M=20
    p = TwoQ(a1in_fraction=0.5, a1out_slots_factor=1)  # M_a1in=10, M_am=10, 1 obj each
    p.start(c)
    # access 0 -> miss, no ghost -> A1in
    assert p.access(0,0,[0]) == [0]
    assert (0,0) in p.a1in and (0,0) not in p.am
    # access 1 -> miss, admit A1in evicting 0 to ghost
    assert p.access(1,0,[1]) == [1]
    assert (0,0) not in p.a1in and (0,0) in p.a1out and (0,1) in p.a1in
    # access 0 again -> ghost hit, admit to Am
    assert p.access(2,0,[0]) == [0]
    assert (0,0) in p.am and (0,0) not in p.a1out
    # A1in hit no promotion
    assert p.access(3,0,[1]) == []
    assert (0,1) in p.a1in and (0,1) not in p.am
    assert p.resident_bytes <= 20

def test_twoq_determinism():
    c = ctx_uniform([0,1], 16, 10, 60, top_k=2)
    seq = [([1,3],0),([2,4],1),([1,5],0),([3,6],1),([1,2],0)]
    def run():
        p=TwoQ(0.25,2); p.start(c)
        out=[]
        for i,(ex,l) in enumerate(seq):
            out.append(tuple(p.access(i,l,ex)))
        return out, p.evictions, p.resident_bytes, list(p.a1in.keys()), list(p.am.keys()), list(p.a1out.keys())
    assert run()==run()

def test_lruk_insufficient_history_evictable_and_determinism():
    c = ctx_uniform([0], 8, 10, 20, top_k=1)
    p = LRUK(K=2, correlated_reference_period_objects=0); p.start(c)
    assert p.access(0,0,[0]) == [0]
    assert p.access(1,0,[1]) == [1]
    # both have <K refs; victim should be chosen deterministically (oldest kth sentinel -> by last ref then stable key)
    assert p.access(2,0,[2]) == [2]
    assert p.resident_bytes <= 20
    # determinism
    p2 = LRUK(2,0); p2.start(c)
    seq = [0,1,2,0,3,1]
    r1=[]; r2=[]
    q = LRUK(2,0); q.start(c)
    for i,e in enumerate(seq): r1.append(tuple(q.access(i,0,[e])))
    q2 = LRUK(2,0); q2.start(c)
    for i,e in enumerate(seq): r2.append(tuple(q2.access(i,0,[e])))
    assert r1==r2

def test_aged_frequency_decay_aging_and_determinism():
    c = ctx_uniform([0], 8, 10, 20, top_k=1)
    p = AgedFrequency(decay_factor=0.5, aging_interval_steps=2); p.start(c)
    # t=1: aging due? 1%2!=0 -> +1 score for 0
    assert p.access(0,0,[0]) == [0]
    assert p.scores[(0,0)] == 1.0
    # t=2: interval boundary -> decay all resident before access: 1*0.5 then +1 => 1.5
    assert p.access(1,0,[0]) == []
    assert abs(p.scores[(0,0)] - 1.5) < 1e-9
    # determinism
    seq=[0,0,1,0,2,1]
    def run():
        q=AgedFrequency(0.9,1); q.start(c); out=[]
        for i,e in enumerate(seq): out.append(tuple(q.access(i,0,[e])))
        return out, q.evictions, q.resident_bytes
    assert run()==run()

def test_lru_lfu_causal_and_ties_basic():
    c = ctx_uniform([0], 8, 10, 20, top_k=1)
    g=GlobalLRU(); g.start(c); assert g.access(0,0,[1])==[1]; assert g.access(1,0,[1])==[]
    l=LFU(); l.start(c); assert l.access(0,0,[1])==[1]; assert l.access(1,0,[1])==[]
    assert getattr(g,'causal',True) and getattr(l,'causal',True)
