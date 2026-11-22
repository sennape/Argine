"""Locale-independent MSVC family check for the R3-03B microbenchmark driver.

Reads the captured `cl` banner and requires exactly one version triple NN.NN.NNNNN and that its major.minor is 19.44.
Works for English ("Version 19.44.35229 for x64") and localized ("versione 19.44.35229 per x64") banners.
Exit codes: 0 = 19.44 family; 1 = another family (fail closed); 2 = missing/unparseable/ambiguous (fail closed).
"""
import re
import sys

REQUIRED = (19, 44)
TRIPLE = re.compile(r"(?<![\d.])(\d{2})\.(\d{2})\.(\d{4,6})(?![\d.])")


def check(text):
    found = sorted({m.groups() for m in TRIPLE.finditer(text)})
    if len(found) != 1:
        return 2, "unparseable or ambiguous compiler version (%d candidates)" % len(found)
    major, minor, build = found[0]
    if (int(major), int(minor)) != REQUIRED:
        return 1, "compiler %s.%s.%s is not the %d.%d family" % (major, minor, build, *REQUIRED)
    return 0, "compiler %s.%s.%s is in the %d.%d family" % (major, minor, build, *REQUIRED)


def main(argv):
    if len(argv) != 2:
        print("usage: r3_03b_check_cl_version.py <cl_version.txt>", file=sys.stderr)
        return 2
    with open(argv[1], "rb") as f:
        text = f.read().decode("utf-8", errors="replace")
    code, msg = check(text)
    print(("OK: " if code == 0 else "FAILED: ") + msg)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
