import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from paperclaw.core import CrawlError, SafeClient, crawl_group
from paperclaw.official import allowed_page, crawl, parse_page, update_paper
from paperclaw.program import parse_program


class OfficialTests(unittest.TestCase):
    def test_separate_official_abstract_index_matches_event_ids_and_skips_session_heading(self):
        from paperclaw.official import records
        from paperclaw.program import program_url
        item = {"id":42, "name":"Real paper", "eventtype":"Poster", "authors":[], "virtualsite_url":"/virtual/2026/poster/42"}
        raw = json.dumps({"count":2,"results":[item,{**item,"id":99,"name":"Oral Presentations","eventtype":"Oral"}]}).encode()
        def transport(url):
            return 200, {}, json.dumps({"42":"Complete official abstract.","43":"Another paper."}).encode() if url.endswith("-abstracts.json") else raw
        with tempfile.TemporaryDirectory() as directory:
            client = SafeClient(directory, transport=transport, interval=0)
            rows = records(client,"ICML",2026,threading.Event(),lambda m:None)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["abstract"],"Complete official abstract.")
        self.assertTrue(program_url("ICML",2026,"abstracts").endswith("icml-2026-abstracts.json"))
        with tempfile.TemporaryDirectory() as directory:
            client = SafeClient(directory, transport=lambda url: (404,{},b'{}') if url.endswith('-abstracts.json') else (200,{},raw), interval=0)
            fallback = records(client,"ICML",2026,threading.Event(),lambda m:None)
        self.assertEqual(fallback[0]["title"],"Real paper")
        self.assertEqual(fallback[0]["abstract"],"")
        with self.assertRaises(ValueError):
            parse_program(b'{"42":{"abstract":"Unexpected shape"}}',"ICML",2026,"abstracts")

    def test_landing_page_reads_the_entire_abstract(self):
        html = b"""<html><h1>Some paper</h1><div id="abstractText" class="abstract-text collapsed">
        <p>First paragraph.</p><ul><li>Result one.</li><li>Result two.</li></ul><p>The final sentence.</p>
        </div></html>"""
        data = parse_page(html,"https://iclr.cc/virtual/2026/poster/1")
        self.assertEqual(data["title"],"Some paper")
        self.assertIn("Result two.",data["abstract"])
        self.assertTrue(data["abstract"].endswith("The final sentence."))

    def test_website_catalog_has_abstracts_and_merges_oral_poster_entries(self):
        item = {"name":"Some paper","abstract":"Full abstract.","authors":[{"fullname":"Alice"}],
                "eventtype":"Poster","virtualsite_url":"/virtual/2025/poster/1"}
        raw = json.dumps({"count":2,"results":[item,{**item,"eventtype":"Oral"}]}).encode()
        data = parse_program(raw,"ICLR",2025)
        class Client:
            def get(self, *args):
                return data
        papers = []
        crawl(Client(),"ICLR",2025,threading.Event(),lambda batch,*_:papers.extend(batch),lambda m:None)
        self.assertEqual(len(papers),1)
        self.assertEqual(papers[0]["source"],"conference_site")
        self.assertEqual(papers[0]["abstract"],"Full abstract.")
        self.assertEqual(set(papers[0]["categories"]),{"Poster","Oral"})
        self.assertEqual(papers[0]["authors"],["Alice"])

    def test_openreview_challenge_uses_official_site_in_auto_mode(self):
        def transport(url):
            if "openreview" in url:
                return 403,{},b'{"name":"ChallengeRequiredError","message":"Challenge verification required"}'
            return 200,{},json.dumps({"count":1,"results":[{"name":"Some paper","authors":[],
                "abstract":"Official abstract.","eventtype":"Poster","virtualsite_url":"/virtual/2025/poster/1"}]}).encode()
        with tempfile.TemporaryDirectory() as directory:
            client = SafeClient(directory,transport=transport,interval=0)
            papers=[]
            crawl_group(client,"auto","ICLR",2025,False,"",threading.Event(),lambda batch,*_:papers.extend(batch),lambda m:None)
            self.assertEqual(papers[0]["abstract_source"],"conference_site")
        with tempfile.TemporaryDirectory() as directory:
            client=SafeClient(directory,transport=transport,interval=0)
            with self.assertRaises(CrawlError):
                crawl_group(client,"openreview","ICLR",2025,False,"",threading.Event(),lambda *a:None,lambda m:None)

    def test_partial_openreview_results_are_preserved_without_mixing_catalogs(self):
        from paperclaw.core import AccessError
        class Client:
            def get(self,*args):
                return {"groups":[{"id":"ICLR.cc/2025/Conference","content":{"submission_name":{"value":"Submission"}}}]}
        def partial(client, groups, conference, year, cancel, on_page, progress):
            on_page([{"id":"kept"}],2,1)
            raise AccessError("403","openreview","challenge")
        with patch("paperclaw.openreview.crawl_venues",partial), patch("paperclaw.official.crawl") as official_crawl:
            with self.assertRaises(CrawlError):
                crawl_group(Client(),"auto","ICLR",2025,False,"",threading.Event(),lambda *a:None,lambda m:None)
        official_crawl.assert_not_called()

    def test_source_update_keeps_original_on_wrong_paper(self):
        paper={"title":"Expected paper","doi":"10.1/correct","conference":"STOC","year":2026,
               "abstract":"Existing abstract","publisher_url":"https://arxiv.org/abs/1234.5678"}
        class Client:
            def get(self,*args):
                return {"title":"Wrong paper","abstract":"Wrong abstract","url":"https://arxiv.org/abs/1234.5678"}
        with self.assertRaises(CrawlError):
            update_paper(Client(),paper,threading.Event(),lambda m:None,url=paper["publisher_url"])
        self.assertEqual(paper["abstract"],"Existing abstract")

    def test_source_update_replaces_openalex_excerpt(self):
        paper={"title":"Expected paper","doi":"","conference":"STOC","year":2026,"abstract":"First sentence",
               "abstract_source":"openalex","publisher_url":"https://arxiv.org/abs/1234.5678"}
        class Client:
            def get(self,*args):
                return {"title":"Expected paper","abstract":"First sentence. Further results.","url":"https://arxiv.org/abs/1234.5678"}
        result=update_paper(Client(),paper,threading.Event(),lambda m:None)
        self.assertEqual(result["abstract"],"First sentence. Further results.")
        self.assertEqual(result["abstract_source"],"arxiv")

    def test_only_supported_public_paper_hosts(self):
        for url in ["http://arxiv.org/abs/1","https://localhost/","https://127.0.0.1/","https://arxiv.org.evil.example/",
                    "https://user:pass@arxiv.org/abs/1"]:
            self.assertFalse(allowed_page(url))
        self.assertTrue(allowed_page("https://iclr.cc/virtual/2026/poster/1"))

    def test_refresh_preserves_concurrent_ai_result(self):
        from paperclaw.server import Application
        from paperclaw.core import normalize
        with tempfile.TemporaryDirectory() as directory:
            app=Application(directory)
            paper=normalize({"title":"Some paper","venue":"STOC","year":"2026",
                             "type":"Conference and Workshop Papers","key":"one"},"dblp","STOC",2026)
            app.merge([paper])
            def update(client, snapshot, *args):
                app.state["papers"][0]["ai_summary"]="New analysis"
                snapshot.update(abstract="Full updated abstract.",abstract_source="arxiv")
                return snapshot
            with patch("paperclaw.official.update_paper", update):
                app.refresh_paper({"id":paper["id"]})
            self.assertEqual(app.snapshot()["papers"][0]["ai_summary"],"New analysis")
            self.assertEqual(app.snapshot()["papers"][0]["abstract"],"Full updated abstract.")
