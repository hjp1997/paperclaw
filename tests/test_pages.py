import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pages
from paperclaw.core import atomic_json, normalize
from test_core import record


class PagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "site-data"
        self.paper = normalize(record(title=r"\mathcalR³: A test paper"),"dblp","INFOCOM",2025)

    def test_publication_excludes_keys_ai_and_saved_library(self):
        original = {**self.paper,"ai_summary":"PRIVATE ANALYSIS","api_key":"PRIVATE KEY","title_zh":"private"}
        source=self.root / "results.json"
        atomic_json(source,{"papers":[original],"saved_groups":{"private":[{"title":"Private collection"}]}})
        result=pages.load_input(source)
        encoded=json.dumps(result)
        for value in ("PRIVATE ANALYSIS","PRIVATE KEY","Private collection","title_zh"):
            self.assertNotIn(value,encoded)
        self.assertEqual(result[0]["title_text"],"ℛ³: A test paper")
        for item in result:
            item.pop("id")
            item.pop("doi")
        other={**result[0],"title":"Another paper"}
        self.assertNotEqual(pages.public_paper(result[0])["id"],pages.public_paper(other)["id"])

    def test_import_merge_keeps_a_richer_abstract(self):
        self.paper.update(abstract="Complete abstract",abstract_source="ieee")
        with contextlib.redirect_stdout(io.StringIO()):
            pages.save_collections([self.paper],self.data)
            pages.save_collections([{**self.paper,"abstract":"","abstract_source":""}],self.data)
        result=pages.load_input(self.data / "infocom-2025.json")
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]["abstract"],"Complete abstract")

    def test_static_build_manifest_and_subdirectory_assets(self):
        with contextlib.redirect_stdout(io.StringIO()):
            pages.save_collections([self.paper],self.data)
            pages.build(self.data,self.root / "output")
        output=self.root / "output"
        manifest=json.loads((output/"manifest.json").read_bytes())
        group=manifest["groups"][0]
        self.assertEqual(group["count"],1)
        self.assertTrue((output / group["file"]).is_file())
        self.assertNotIn("ai_summary",manifest["export_fields"])
        html=(output/"index.html").read_text("utf-8")
        self.assertNotIn('src="/',html)
        self.assertNotIn('href="/',html)
        self.assertNotIn("/api/",(output/"site.js").read_text("utf-8"))
        self.assertTrue((output/".nojekyll").exists())
        with self.assertRaises(ValueError):
            pages.build(self.root/"empty",self.root/"blank")

    def test_failed_collector_preserves_previously_published_data(self):
        with contextlib.redirect_stdout(io.StringIO()):
            pages.save_collections([self.paper],self.data)
        before=(self.data/"infocom-2025.json").read_bytes()
        result={"status":"partial","message":"Source failed","papers":[],
                "groups":[{"conference":"INFOCOM","year":2025,"status":"failed"}]}
        args=SimpleNamespace(conferences="INFOCOM",years="2025",source="auto",skip_abstracts=False,data_dir=self.data)
        with patch.object(pages,"ROOT",self.root), patch.object(pages,"Application") as application:
            application.return_value.snapshot.return_value=result
            with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(ValueError):
                pages.collect(args)
        self.assertEqual((self.data/"infocom-2025.json").read_bytes(),before)
