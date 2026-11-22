"""SHA-256 of a PE image with the COFF TimeDateStamp and the optional-header CheckSum zeroed (the only fields that differ between two clean builds)."""
import hashlib, struct, sys
def normalized(path):
    b = bytearray(open(path, "rb").read())
    pe = struct.unpack_from("<I", b, 0x3C)[0]
    assert b[pe:pe + 4] == b"PE\0\0"
    struct.pack_into("<I", b, pe + 8, 0)          # TimeDateStamp
    struct.pack_into("<I", b, pe + 24 + 64, 0)     # CheckSum (PE32+ optional header)
    return hashlib.sha256(b).hexdigest()
if __name__ == "__main__":
    for p in sys.argv[1:]: print(normalized(p), p)
