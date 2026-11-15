import csv
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from paperclaw.core import SafeClient, atomic_json, export_papers, normalize
from paperclaw.program import INDEX_LABEL, acl_schedule, apply_program, parse_award_index, parse_program
from paperclaw.server import Application
from paperclaw.titles import prepare_title
from test_core import dblp, record


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Application(self.temp.name)
        self.paper = normalize(record(title=r"\mathcalR³: \mathcalRectification"), "dblp", "INFOCOM", 2025)

    def test_math_title_and_exports_preserve_original(self):
        prepare_title(self.paper)
        self.assertEqual(self.paper["title_text"], "ℛ³: ℛectification")
        self.assertEqual(self.paper["title_parts"][0]["math"], r"\mathcal{R}")
        self.paper["title_zh"] = "校正"
        self.paper["categories"] = ["Oral"]
        self.paper["category_sources"] = {"Oral": "https://example.org/program"}
        item = json.loads(export_papers([self.paper], "json")[0])[0]
        self.assertEqual(item["title"], r"\mathcalR³: \mathcalRectification")
        csv_item = next(csv.DictReader(io.StringIO(export_papers([self.paper], "csv")[0].decode("utf-8-sig"))))
        self.assertEqual(csv_item["title_text"], item["title_text"])
        md = export_papers([self.paper], "md")[0].decode()
        self.assertNotIn("title_zh", item)
        self.assertNotIn("title_zh", csv_item)
        for value in ["ℛ³", "Oral", "https://example.org/program"]:
            self.assertIn(value, md)

    def test_only_favorites_survive_replacement_and_reset(self):
        self.app.merge([self.paper])
        group = {"conference": "INFOCOM", "year": 2025}
        self.app.favorite({**group, "keep": True})
        with patch("threading.Thread.start"):
            self.app.start({"source":"dblp", "conferences":["ICDE"], "years":[2025]})
        self.assertEqual(self.app.snapshot()["papers"], [])
        self.app.state["status"] = "stopped"
        self.app.import_data({"source":"dblp", "conferences":["ICDE"], "years":[2025],
                              "data": dblp([record("other", venue="ICDE")])})
        self.app = Application(self.temp.name)
        self.assertEqual([p["conference"] for p in self.app.snapshot()["papers"]], ["ICDE"])
        self.app.reset_current()
        self.app = Application(self.temp.name)
        self.assertEqual(self.app.snapshot()["papers"], [])
        self.assertEqual(self.app.snapshot(view="favorites")["papers"][0]["id"], self.paper["id"])
        self.app.favorite({**group, "keep": False})
        self.assertEqual(Application(self.temp.name).snapshot(view="favorites")["papers"], [])

    def test_legacy_snapshot_and_backup(self):
        path = Path(self.temp.name) / "results.json"
        legacy = {"groups":[{"conference":"INFOCOM","year":2025,"status":"completed"}],
                  "papers":[self.paper,normalize(record("other", venue="ICDE"), "dblp", "ICDE", 2025)],"status":"completed"}
        atomic_json(path, legacy)
        app = Application(self.temp.name)
        self.assertEqual(len(app.snapshot()["papers"]), 1)
        app.reset_current()
        self.assertEqual(json.loads(path.with_name("results.pre-favorites.json").read_bytes()), legacy)

    def test_complete_abstracts_returns_feedback_without_a_job(self):
        self.paper.update(abstract="Available abstract", abstract_status="available")
        self.app.merge([self.paper])
        with patch("threading.Thread.start") as start:
            response = self.app.start_abstracts({"ids":[self.paper["id"]]})
        self.assertFalse(response["started"])
        self.assertIn("摘要已齐全", response["message"])
        start.assert_not_called()

    def test_match_awards_and_session_without_guessing(self):
        raw = b'<collection><volume id="long"><paper id="1"><doi>10.1/Test</doi><award name="Best Paper"/></paper></volume></collection>'
        client = SafeClient(Path(self.temp.name), transport=lambda url:(200, {}, raw))
        awards = client.get("program", {"conference":"ACL", "year":2026}, "test", threading.Event())["annotations"]
        schedule = acl_schedule(b'Paper number,Title,Underline/Whova Session Name\n1,Example,Oral Session A\n2,Other,Virtual Presentations\n')
        papers = [{"title":"Example.", "doi":"10.1/TEST"}, {"title":"Other","doi":""}]
        apply_program(papers, awards + schedule)
        self.assertEqual(papers[0]["categories"], ["Best Paper","Oral"])
        self.assertNotIn("categories", papers[1])
        duplicates = [{"title":"Example","doi":""}, {"title":"Example.","doi":""}]
        apply_program(duplicates, schedule)
        self.assertTrue(all("categories" not in p for p in duplicates))

    def test_multi_label_virtual_program(self):
        event = {"name":"Example", "eventtype":"Poster", "decision":"Accept (Highlight)", "virtualsite_url":"/virtual/2025/poster/8"}
        rows = parse_program(json.dumps({"results":[event]}).encode(), "CVPR", 2025)["annotations"]
        self.assertEqual(set(rows[0]["labels"]), {"Highlight","Poster"})

    def test_award_index_handles_rowspan_and_separates_editions(self):
        raw = b"""<table id="2025"><tr><th class="category-name" rowspan="2"><a>KDD</a></th>
        <td><a>First</a></td></tr><tr><td><a>Second</a></td></tr></table>
        <table id="2024"><tr><th class="category-name"><a>STOC</a></th><td><a>Older</a></td></tr></table>"""
        self.assertEqual(parse_award_index(raw)["editions"], {"SIGKDD:2025":["First","Second"],"STOC:2024":["Older"]})
        p = {"title":"First", "doi":""}
        apply_program([p], [{"title":"First", "labels":["Best Paper"], "url":"https://conf.org/"},
                            {"title":"First", "labels":[INDEX_LABEL], "indexed":True, "url":"https://example.org/"}])
        self.assertEqual(p["categories"], ["Best Paper"])

    def test_official_title_and_abstract_recovery(self):
        raw = br'<collection><volume id="short"><paper id="2"><title>New <tex-math>\mathcal{R}^3</tex-math></title><abstract>A verified abstract.</abstract></paper></volume></collection>'
        p = {"title":r"New \mathcalR³.", "doi":"10.18653/v1/2026.acl-short.2"}
        apply_program([p], parse_program(raw, "ACL", 2026)["annotations"])
        self.assertEqual(p["title_text"], "New ℛ³")
        self.assertEqual(p["abstract_source"], "acl_anthology")
        self.assertIn("aclanthology.org/2026.acl-short.2/",p["abstract_url"])
