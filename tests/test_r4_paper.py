"""Mutation tests for the Research 4 manuscript verifier: a clean copy passes; drifted copies must fail."""
import shutil, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "paper" / "research4" / "scripts"))
import verify_paper  # noqa: E402

SRC = ROOT / "paper" / "research4"


class R4PaperVerifier(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r4p"))
        self.p = self.tmp / "research4"
        shutil.copytree(SRC, self.p, ignore=shutil.ignore_patterns("__pycache__", "scripts", "*.aux", "*.log", "*.out", "*.bbl", "*.blg", "*.fls", "*.fdb_latexmk"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _edit(self, rel, old, new, count=1):
        f = self.p / rel; s = f.read_text(encoding="utf-8"); self.assertIn(old, s); f.write_text(s.replace(old, new, count), encoding="utf-8", newline="\n")

    def _fails(self):
        fails, _ = verify_paper.verify(paper=self.p)
        # freeze checks compare against files in the copy; ignore them here so that each mutation is tested on its own merits
        return [f for f in fails if not f.startswith("PUBLICATION_FREEZE")]

    def test_clean_copy_passes(self):
        self.assertEqual(self._fails(), [])

    def test_changed_capture_fails(self):
        self._edit("research4.tex", "$15.45\\%$", "$15.46\\%$")
        self.assertTrue(any("15.46" in f for f in self._fails()))

    def test_changed_sign_fails(self):
        self._edit("research4.tex", "$-36.04\\%$", "$36.04\\%$")
        self.assertTrue(any("36.04" in f for f in self._fails()))

    def test_changed_miss_bytes_fails(self):
        self._edit("research4.tex", "1{,}226{,}910{,}965{,}760", "1{,}226{,}910{,}965{,}761")
        self.assertTrue(self._fails())

    def test_stray_percentage_fails(self):
        self._edit("research4.tex", "\\section{Conclusion}", "\\section{Conclusion}\nA new figure of $33.33\\%$ appears.")
        self.assertTrue(any("unregistered percentage" in f for f in self._fails()))

    def test_internal_id_in_body_fails(self):
        self._edit("research4.tex", "\\section{Conclusion}", "\\section{Conclusion}\nSee R4-02 for details.")
        self.assertTrue(any("forbidden token" in f for f in self._fails()))

    def test_changed_classification_label_fails(self):
        self._edit("research4.tex", "R4\\_CFDC\\_CAUSAL\\_STATE\\_INSUFFICIENT", "R4\\_CFDC\\_CHEAP\\_VIABLE")
        self.assertTrue(self._fails())

    def test_changed_identity_fails(self):
        f = self.p / "research4.tex"; s = f.read_text(encoding="utf-8")
        import re
        h = re.search(r"freeze \\hsh\{([0-9a-f]{64})\}", s).group(1)
        self._edit("research4.tex", h, h[:-1] + ("0" if h[-1] != "0" else "1"))
        self.assertTrue(self._fails())

    def test_edited_generated_table_fails(self):
        self._edit("tables/table2_missbytes.tex", "1{,}226{,}910{,}965{,}760", "1{,}226{,}910{,}965{,}000")
        self.assertTrue(any("table2" in f for f in self._fails()))

    def test_missing_citation_key_fails(self):
        self._edit("research4.tex", "\\cite{belady1966replacement}", "\\cite{nosuchkey}")
        self.assertTrue(any("nosuchkey" in f for f in self._fails()))

    def test_stale_source_map_fails(self):
        self._edit("SOURCE_MAP.md", "| cap_val_NUE_X | 7.42% |", "| cap_val_NUE_X | 7.43% |")
        self.assertTrue(any("SOURCE_MAP" in f for f in self._fails()))

    def test_title_has_no_internal_label(self):
        tex = (SRC / "research4.tex").read_text(encoding="utf-8")
        title = tex.split("\\title{")[1].split("\\author")[0]
        self.assertNotRegex(title, r"Research|\bR4\b")

    def test_verifier_is_check_only(self):
        before = {p: p.read_bytes() for p in SRC.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".aux", ".log")}
        verify_paper.verify(paper=SRC)
        after = {p: p.read_bytes() for p in before}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
