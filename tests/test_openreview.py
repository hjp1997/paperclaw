import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from paperclaw.core import AccessError, CrawlError, SafeClient, crawl_group
from paperclaw.openreview import crawl, enrich, find_venues, normalize_note
from paperclaw.server import Application, validate_options

VENUE = "ICLR.cc/2026/Conference"
GROUP = {"id":VENUE, "content":{"submission_name":{"value":"Submission"}}}


def note(key="n1", title="An accepted paper", **fields):
    content = {"title": title, "authors":["Alice","Bob"], "abstract":"Real abstract.", "pdf":"/pdf/test.pdf",
               "keywords":["Learning"], "venueid":VENUE, "venue":"ICLR 2026 Oral", **fields}
    return {"id":key, "forum":key, "content":{key:{"value":value} for key,value in content.items()}}


class OpenReviewTests(unittest.TestCase):
    def test_acceptance_requires_exact_venue_not_public_submission(self):
        for venueid in [VENUE+"/Rejected", VENUE+"/Submission", VENUE+"/Withdrawn", "Other/2026/Conference"]:
            self.assertIsNone(normalize_note(note(venueid=venueid),"ICLR",2026,VENUE,2))
        paper = normalize_note(note(),"ICLR",2026,VENUE,2)
        self.assertEqual(paper["abstract_source"],"openreview")
        self.assertEqual(paper["categories"],["Oral"])
        self.assertTrue(paper["url"].startswith("https://openreview.net/forum?id="))
        self.assertEqual(paper["authors"],["Alice","Bob"])

    def test_legacy_requires_official_acceptance_and_restores_authors(self):
        data = {"id":"blind", "forum":"blind", "content":{"title":"Legacy","authors":["Anonymous"]},
                "details":{"original":{"content":{"authors":["Alice"]}}, "directReplies":[
                    {"forum":"blind","invitation":VENUE+"/Paper1/-/Decision","content":{"decision":"Accept (Poster)"}}]}}
        paper = normalize_note(data,"ICLR",2026,VENUE,1)
        self.assertEqual(paper["authors"],["Alice"])
        self.assertEqual(paper["categories"],["Poster"])
        data["details"]["directReplies"].append({"forum":"blind","invitation":VENUE+"/Paper1/-/Decision",
                                                "mdate":20,"content":{"decision":"Reject"}})
        self.assertIsNone(normalize_note(data,"ICLR",2026,VENUE,1))
        data["details"]["directReplies"].pop()
        data["details"]["directReplies"][0]["invitation"] = "Other/Paper1/-/Decision"
        self.assertIsNone(normalize_note(data,"ICLR",2026,VENUE,1))

    def test_pagination_preserves_prior_page_on_failure(self):
        calls = []
        class Client:
            def get(self, source, params, *args):
                calls.append((source,params))
                if params["offset"] == 0:
                    return {"count":2, "notes":[note()]}
                raise CrawlError("403")
        papers = []
        with patch("paperclaw.openreview.PAGE_SIZE",1), self.assertRaises(CrawlError):
            crawl(Client(),GROUP,"ICLR",2026,threading.Event(),lambda batch,*_:papers.extend(batch),lambda m:None)
        self.assertEqual(len(papers),1)
        self.assertEqual(calls[1][1]["offset"],1)
        self.assertEqual(calls[0][1]["content.venueid"],VENUE)

    def test_auto_prioritizes_openreview(self):
        calls = []
        class Client:
            def get(self, source, params, *args):
                calls.append(source)
                return {"groups":[GROUP]} if source == "openreview_groups" else {"count":1,"notes":[note()]}
        papers = []
        crawl_group(Client(),"auto","ICLR",2026,False,"",threading.Event(),lambda batch,*_:papers.extend(batch),lambda m:None)
        self.assertEqual(calls,["openreview_groups","openreview"])
        self.assertEqual(papers[0]["source"],"openreview")

    def test_application_entry_point_collects_direct_abstracts(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Application(directory)
            calls = []
            def get(source, params, *args):
                calls.append(source)
                if source == "openreview_groups":
                    return {"groups":[GROUP]}
                if source == "openreview":
                    return {"count":1,"notes":[note()]}
                raise CrawlError("Optional classification unavailable")
            app.client.get = get
            app.start({"source":"auto","conferences":["ICLR"],"years":[2026]})
            deadline = time.monotonic() + 3
            while app.snapshot()["status"] == "running" and time.monotonic() < deadline:
                time.sleep(.01)
            state = app.snapshot()
            self.assertEqual(state["status"],"completed")
            self.assertEqual(state["papers"][0]["abstract_source"],"openreview")
            self.assertNotIn("dblp_rdf",calls)
            self.assertNotIn("openalex",calls)

    def test_find_main_tracks_excludes_workshops_and_chair_groups(self):
        prefix = "KDD.org/2026/"
        names = ["Research_Track_August","ADS_Track_Cycle_2","Datasets_and_Benchmark_Track_Cycle_2",
                 "Workshop/FedKDD","Research_Track_August/Publication_Chairs","Workshop_Proposal","Cup/UniRec"]
        class Client:
            def get(self, source, params, *args):
                self.prefix = params["prefix"]
                return {"count":len(names), "groups":[{"id":prefix+name} for name in names]}
        client=Client()
        groups = find_venues(client, "SIGKDD",2026,threading.Event(),lambda m:None)
        self.assertEqual(client.prefix,prefix)
        self.assertEqual(len(groups),3)

    def test_missing_venue_uses_catalog_but_access_denial_stops(self):
        class Client:
            def get(self, source, params, *args):
                return {"groups":[]}
        with patch("paperclaw.core.crawl_dblp_rdf") as fallback:
            crawl_group(Client(),"auto","ICLR",2026,False,"",threading.Event(),lambda *a:None,lambda m:None)
            fallback.assert_called_once()
        with tempfile.TemporaryDirectory() as directory:
            client = SafeClient(directory,transport=lambda u:(403,{},b'Forbidden'),interval=0)
            with patch("paperclaw.core.crawl_dblp_rdf") as fallback, self.assertRaises(AccessError):
                crawl_group(client,"auto","ICLR",2026,False,"",threading.Event(),lambda *a:None,lambda m:None)
            fallback.assert_not_called()

    def test_bad_group_response_is_not_missing_venue(self):
        with tempfile.TemporaryDirectory() as directory:
            client = SafeClient(directory,transport=lambda u:(404,{},b'{"name":"NotFoundError"}'),interval=0)
            self.assertEqual(client.get("openreview_groups",{"id":VENUE},"test",threading.Event()),{"groups":[]})
        with tempfile.TemporaryDirectory() as directory:
            client = SafeClient(directory,transport=lambda u:(404,{},b'{"name":"OtherError"}'),interval=0)
            with self.assertRaises(CrawlError):
                client.get("openreview_groups",{"id":VENUE},"test",threading.Event())

    def test_empty_or_duplicate_pages_are_incomplete(self):
        for second in ([],[note()]):
            class Client:
                def get(self, source, params, *args):
                    return {"count":2,"notes":[note()] if params["offset"] == 0 else second}
            with patch("paperclaw.openreview.PAGE_SIZE",1), self.assertRaises(CrawlError):
                crawl(Client(),GROUP,"ICLR",2026,threading.Event(),lambda *a:None,lambda m:None)

    def test_enrichment_accepts_unique_title_only(self):
        class Client:
            def get(self, source, params, *args):
                return {"groups":[GROUP]} if source == "openreview_groups" else {"count":2,"notes":[note(),note("other","Other title")]}
        rows = [{"id":"old","conference":"ICLR","year":2026,"title":"An accepted paper.","abstract":""},
                {"id":"unmatched","conference":"ICLR","year":2026,"title":"Some other paper","abstract":""}]
        updates = []
        enrich(Client(),rows,threading.Event(),updates.extend,lambda m:None)
        self.assertEqual(len(updates),1)
        self.assertEqual(updates[0]["id"],"old")
        self.assertEqual(updates[0]["abstract_source"],"openreview")
        self.assertEqual(rows[1]["abstract"],"")

    def test_source_validation(self):
        self.assertEqual(validate_options({"source":"openreview","conferences":["ICLR"],"years":[2026]})["source"],"openreview")
        with self.assertRaises(ValueError):
            validate_options({"source":"openreview","conferences":["RTSS"],"years":[2026]})

    def test_award_runner_up_keeps_the_exact_distinction(self):
        paper = normalize_note(note(award="Best Paper Runner-Up"),"ICLR",2026,VENUE,2)
        self.assertIn("Best Paper Runner-Up",paper["categories"])
        self.assertNotIn("Best Paper",paper["categories"])
