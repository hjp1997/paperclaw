import json
import tempfile
import threading
import unittest
from pathlib import Path

from paperclaw.core import CrawlError, SafeClient, normalize
from paperclaw.server import Application


class PublisherTests(unittest.TestCase):
    def test_classify_response_and_preserve_retry(self):
        cases = [
            (302, {"Location": "/user/login?destination=conference"}, b"", "usenix", "login"),
            (200, {}, b"<title>Sign in | USENIX</title>", "usenix", "login"),
            (200, {}, b'<form><input type="password"></form>', "dblp", "login"),
            (401, {}, b"Unauthorized", "ieee", "api_key"),
            (403, {}, b"Forbidden", "crossref", "denied"),
            (403, {}, b"<title>Just a moment</title>", "crossref", "challenge"),
            (429, {}, b"Too many requests", "ieee", None),
        ]
        for status, headers, raw, source, kind in cases:
            with self.subTest(status=status, kind=kind), tempfile.TemporaryDirectory() as directory:
                client = SafeClient(directory, transport=lambda url: (status, headers, raw), interval=0)
                with self.assertRaises(CrawlError) as result:
                    client.get(source, {"year": 2025}, "scope", threading.Event())
                self.assertEqual(getattr(result.exception, "kind", None), kind)
                self.assertFalse(list(Path(directory).glob("cache/*.json")))

    def test_group_identifies_actual_blocked_source_in_auto_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Application(directory)
            app.state["groups"] = [{"status":"pending", "conference":"NSDI", "year":2025}]
            app.client.transport = lambda url: (302, {"Location":"/user/login"}, b"")
            app.run({"source":"auto", "include_workshops":False}, "")
            group = app.snapshot()["groups"][0]
            self.assertEqual(group["status"], "failed")
            self.assertEqual(group["access_issue"], {"source":"usenix", "kind":"login"})

    def test_citation_import_preserves_catalog_and_handles_three_publishers(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Application(directory)
            options = {"source":"publisher", "years":[2025], "conferences":["MobiCom", "ICDE", "NSDI"]}
            bib = """
@inproceedings{aa,title={A {Mobile} Paper},booktitle={ACM MobiCom},doi={10.1145/1.2},year=2025}
@inproceedings{bb,title={Data},booktitle={IEEE ICDE},doi={10.1109/icde.123},year="2025"}
@inproceedings {cc,title={Networks},booktitle={NSDI 25},year={2025},
url={https://www.usenix.org/conference/nsdi25/presentation/cc}}
@inproceedings{old,title={Earlier},booktitle={ACM MobiCom},year=2024}
"""
            self.assertEqual(app.import_data({**options, "data":bib}), {"imported":3, "source_records":4})
            papers = {p["conference"]:p for p in app.state["papers"]}
            self.assertEqual(papers["MobiCom"]["publisher_url"], "https://dl.acm.org/doi/10.1145/1.2")
            self.assertEqual(papers["ICDE"]["publisher_url"], "https://doi.org/10.1109/icde.123")
            nsdi = papers["NSDI"]
            self.assertEqual(nsdi["publisher_url"], nsdi["url"])
            nsdi.update(source="usenix", abstract="Official abstract", abstract_source="usenix", ai_summary="Saved analysis")
            app.import_data({**options, "data":bib})
            preserved = next(p for p in app.state["papers"] if p["id"] == nsdi["id"])
            self.assertEqual(len(app.state["papers"]), 3)
            self.assertEqual((preserved["source"], preserved["abstract"], preserved["ai_summary"]),
                             ("usenix", "Official abstract", "Saved analysis"))
            with self.assertRaises(ValueError):
                app.import_data({**options, "data":bib + "@inproceedings{incomplete,title={Open"})

    def test_prefer_publisher_among_dblp_links_and_restore_legacy_records(self):
        data = {"title":"Sample", "venue":"SIGCOMM", "year":"2025", "type":"Conference and Workshop Papers",
                "doi":"10.1145/1.2", "ee":["https://example.com/other", "https://dl.acm.org/doi/10.1145/1.2"]}
        paper = normalize(data, "dblp", "SIGCOMM", 2025)
        expected = paper.pop("publisher_url")
        self.assertEqual(expected, data["ee"][1])
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "results.json").write_text(json.dumps({"papers":[paper]}), encoding="utf-8")
            self.assertEqual(Application(directory).state["papers"][0]["publisher_url"], expected)
