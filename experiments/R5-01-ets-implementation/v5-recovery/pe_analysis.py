"""PE identity analysis for the llama-research-v5 recovery (Research 5 Phase 1a).

  python3 pe_analysis.py fields A.exe                 PE header fields (TimeDateStamp, CheckSum, offsets, size)
  python3 pe_analysis.py diff A.exe B.exe             every differing byte range, mapped to PE fields
  python3 pe_analysis.py search A.exe SHA T0 T1       try every TimeDateStamp in [T0, T1] (unix s) with the matching
                                                      PE CheckSum; report whether any variant has raw SHA-256 SHA
"""
import hashlib, json, struct, sys
from concurrent.futures import ProcessPoolExecutor


def fields(b: bytes) -> dict:
    pe = struct.unpack_from("<I", b, 0x3C)[0]
    assert b[pe:pe + 4] == b"PE\0\0"
    opt = pe + 24
    magic = struct.unpack_from("<H", b, opt)[0]
    assert magic == 0x20B, "PE32+ expected"
    return {"pe_offset": pe, "timedatestamp_offset": pe + 8, "checksum_offset": opt + 64,
            "TimeDateStamp": struct.unpack_from("<I", b, pe + 8)[0], "CheckSum": struct.unpack_from("<I", b, opt + 64)[0],
            "size": len(b), "n_sections": struct.unpack_from("<H", b, pe + 6)[0]}


def ranges(a: bytes, b: bytes):
    if len(a) != len(b):
        return None
    out, i, n = [], 0, len(a)
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            out.append([i, j])
            i = j
        else:
            i += 1
    return out


def fold(s: int) -> int:
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return s


def word_sum(b: bytes) -> int:
    if len(b) % 2:
        b += b"\0"
    return fold(sum(struct.unpack("<%dH" % (len(b) // 2), b)))


def pe_checksum(b: bytes) -> int:
    f = fields(b)
    z = bytearray(b); z[f["checksum_offset"]:f["checksum_offset"] + 4] = b"\0\0\0\0"
    return fold(word_sum(bytes(z))) + len(b)


def _worker(args):
    path, target, ts_list, base_sum, tdo, cko = args
    b = bytearray(open(path, "rb").read())
    for t in ts_list:
        cs = fold(base_sum + (t & 0xFFFF) + (t >> 16)) + len(b)
        struct.pack_into("<I", b, tdo, t); struct.pack_into("<I", b, cko, cs)
        if hashlib.sha256(b).hexdigest() == target:
            return t
    return None


def search(path, target, t0, t1, workers=8):
    b = open(path, "rb").read()
    f = fields(b)
    z = bytearray(b)
    for off in (f["checksum_offset"], f["timedatestamp_offset"]):
        z[off:off + 4] = b"\0\0\0\0"
    base = word_sum(bytes(z))
    assert fold(base + (f["TimeDateStamp"] & 0xFFFF) + (f["TimeDateStamp"] >> 16)) + len(b) == f["CheckSum"], \
        "incremental checksum model does not reproduce the file's own CheckSum"
    ts = list(range(t0, t1 + 1))
    chunks = [ts[i::workers] for i in range(workers)]
    with ProcessPoolExecutor(workers) as ex:
        for r in ex.map(_worker, [(path, target, c, base, f["timedatestamp_offset"], f["checksum_offset"]) for c in chunks]):
            if r is not None:
                return {"match": True, "TimeDateStamp": r, "candidates": len(ts)}
    return {"match": False, "candidates": len(ts), "checksum_model_verified_on_own_file": True}


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "fields":
        b = open(sys.argv[2], "rb").read(); f = fields(b); f["CheckSum_recomputed"] = pe_checksum(b)
        print(json.dumps(f))
    elif cmd == "diff":
        a, b = open(sys.argv[2], "rb").read(), open(sys.argv[3], "rb").read()
        fa, fb = fields(a), fields(b)
        r = ranges(a, b)
        named = {}
        for s, e in (r or []):
            if fa["timedatestamp_offset"] <= s and e <= fa["timedatestamp_offset"] + 4:
                named[f"{s}-{e}"] = "TimeDateStamp"
            elif fa["checksum_offset"] <= s and e <= fa["checksum_offset"] + 4:
                named[f"{s}-{e}"] = "CheckSum"
            else:
                named[f"{s}-{e}"] = "OTHER"
        print(json.dumps({"size_a": len(a), "size_b": len(b), "ranges": r, "fields": named,
                          "a": {k: fa[k] for k in ("TimeDateStamp", "CheckSum")}, "b": {k: fb[k] for k in ("TimeDateStamp", "CheckSum")},
                          "only_pe_metadata": r is not None and all(v != "OTHER" for v in named.values())}))
    elif cmd == "search":
        print(json.dumps(search(sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]))))
