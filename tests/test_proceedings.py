import threading
import tempfile
import unittest

from paperclaw import proceedings
from paperclaw.catalog import parse_usenix
from paperclaw.core import SafeClient, normalize


class ProceedingsTests(unittest.TestCase):
    def test_official_lists_extract_papers_authors_and_explicit_awards(self):
        examples = [
            ("ACM SIGOPS ATC", '<p>The following 1 papers</p><ul class="paperlist"><li><strong>Systems paper</strong><em>Alice, Bob (University, Lab); Carol (Institute)</em></li></ul>', []),
            ("MobiCom", '<div class="acceptedpapers"><ul><li><b>Systems paper</b><div class="pauthors">Authors: Alice, Bob (University, Lab); Carol (Institute)</div></li></ul></div>', []),
            ("FOCS", '<table><tr><td><span class="paper-title">Systems paper</span><span class="paper-authors">Alice, Bob (University, Lab); Carol (Institute)</span></td></tr></table>', []),
            ("MICRO", '<div class="panel"><h4>Session 2C: Distinguished Papers</h4><div class="paper"><div class="paper-title">Systems paper</div><div class="paper-authors">Alice, Bob (University, Lab); Carol (Institute)</div></div></div><div class="panel"><h4>Session 0A: Opening Remarks</h4><div class="paper"><div class="paper-title">Welcome message</div><div class="paper-authors">Chair</div></div></div>', ["Distinguished Paper"]),
            ("RTSS", '<ul><li class="mb-2"><span>star</span>Systems paper<small>Outstanding Paper</small><small>Artifact Evaluated</small><small class="fw-light">Alice, Bob (University, Lab); Carol (Institute)</small></li></ul>', ["Outstanding Paper"]),
        ]
        for conference,html,labels in examples:
            with self.subTest(conference=conference):
                data = proceedings.parse(html.encode(),conference,2026)
                self.assertEqual(data["total_records"],1)
                row = data["articles"][0]
                self.assertEqual(row["title"],"Systems paper")
                self.assertEqual([p["full_name"] for p in row["authors"]["authors"]],["Alice","Bob","Carol"])
                self.assertEqual(row["program_labels"],labels)
                self.assertIsNotNone(normalize(row,"ieee",conference,2026))
        with self.assertRaises(ValueError):
            proceedings.parse(b'<p>The following 2 papers</p><ul class="paperlist"><li><strong>Only one</strong><em>Alice</em></li></ul>',"ACM SIGOPS ATC",2026)

    def test_sc_public_cache_only_reads_discovered_day_pages_and_technical_papers(self):
        page = "/wp-content/linklings_snippets/wp_program_view_all_2026-11-17.txt?v=1"
        index = f'<div source="{page}"></div>'
        self.assertEqual(proceedings.parse(index.encode(),"SC",2026)["pages"],[page])
        for bad in ("https://evil.example/data", "/wp-admin/users.php", "/wp-content/linklings_snippets/wp_program_view_all_2025-11-17.txt"):
            with self.assertRaises(ValueError): proceedings.url("SC",2026,bad)
        html = '<table><tr ssid="pap123"><td><a data-link-type="full-program.presentation" href="/?id=pap123">Real paper</a></td><td><div class="presenter-name">Alice</div></td></tr><tr ssid="bof12"><td>Non-paper event</td></tr></table>'
        rows = proceedings.parse(html.encode(),"SC",2026,page)["articles"]
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["title"],"Real paper")
        self.assertTrue(rows[0]["landing_url"].startswith("https://sc26.conference-program.com/"))

    def test_usenix_fast_and_historical_atc_share_public_paper_parser(self):
        for conference,slug in (("FAST","fast"),("ACM SIGOPS ATC","atc")):
            path=f"/conference/{slug}25/presentation/sample"
            html=f'<div class="node-paper"><h2><a href="{path}">Paper</a></h2><div class="field-name-field-paper-description-long">Official abstract.</div></div>'
            data=parse_usenix(html,2025,conference)
            self.assertEqual(data["articles"][0]["abstract"],"Official abstract.")
            self.assertEqual(data["articles"][0]["publication_title"],conference+" 2025")

    def test_safe_client_decodes_public_accepted_list(self):
        raw=b'<table><tr><td><span class="paper-title">Paper</span><span class="paper-authors">Alice</span></td></tr></table>'
        with tempfile.TemporaryDirectory() as directory:
            client=SafeClient(directory,transport=lambda url:(200,{},raw),interval=0)
            data=client.get("proceedings",{"conference":"FOCS","year":2026},"public",threading.Event())
        self.assertEqual(data["total_records"],1)


if __name__ == "__main__":
    unittest.main()
