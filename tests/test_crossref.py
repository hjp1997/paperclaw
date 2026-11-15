import json
import tempfile
import threading
import unittest

from paperclaw.core import CrawlError, SafeClient, crawl_group, normalize, validate_payload
from paperclaw.server import validate_options


MAIN = "IEEE INFOCOM 2025 - IEEE Conference on Computer Communications"
WORKSHOP = MAIN + " Workshops (INFOCOM WKSHPS)"


def work(doi="10.1109/infocom.2025.1", venue=MAIN, kind="proceedings-article", year=2025):
    return {"DOI": doi, "title": ["Efficient Routing" if kind == "proceedings-article" else venue],
            "type": kind, "published": {"date-parts": [[year]]}, "container-title": [venue],
            "author": [{"given": "Alice", "family": "Li"}, {"name": "Research Team"}],
            "resource": {"primary": {"URL": "https://ieeexplore.ieee.org/document/123/"}},
            "page": "1-10", "abstract": "<jats:p>A <jats:italic>routing</jats:italic> method.</jats:p>"}


def response(records, total=None):
    return {"status": "ok", "message-type": "work-list", "message": {
        "total-results": len(records) if total is None else total, "items": records, "next-cursor": "server-cursor"}}


class CrossrefTests(unittest.TestCase):
    def collect(self, responses, include=False, conference="INFOCOM"):
        calls, rows, counts = [], [], []
        class Client:
            def get_cached_query(self, source, params): return None
            def cache_query(self, source, params, data): pass
            def get(self, source, params, scope, cancel, progress, *, cache=True):
                self.assert_source = source
                if cache: raise AssertionError("Cursor responses must not be cached")
                calls.append(params)
                return responses.pop(0)
        def on_page(batch, total, fetched):
            rows.extend(batch)
            counts.append((total, fetched))
        crawl_group(Client(), "crossref", conference, 2025, include, "", threading.Event(), on_page, lambda m: None)
        return calls, rows, counts

    def test_exact_container_filter_ignores_unrelated_infocom_and_workshops(self):
        discovery = [work(kind="proceedings"), work("10.1109/workshops.2025", WORKSHOP, "proceedings"),
                     work("10.1109/icrito.2025", "2025 International Conference on Reliability, Infocom Technologies and Optimization (ICRITO)", "proceedings"),
                     work("10.1109/old.2024", MAIN, "proceedings", 2024)]
        calls, rows, counts = self.collect([response(discovery), response([work()])])
        self.assertEqual(len(calls), 2)
        self.assertIn("container-title:" + MAIN, calls[1]["filter"])
        self.assertNotIn("query.container-title", calls[1])
        self.assertNotIn("sort", calls[1])  # Publication-date sorting is incompatible with cursors.
        self.assertEqual(rows[0]["source"], "crossref")
        self.assertEqual(counts, [(1, 1)])

    def test_short_pages_and_workshop_totals(self):
        discovery = [work(kind="proceedings"), work("10.1109/workshops.2025", WORKSHOP, "proceedings")]
        calls, rows, counts = self.collect([response(discovery), response([work()], 2),
                    response([work("10.1109/infocom.2025.2")], 2), response([work("10.1109/workshops.2025.1", WORKSHOP)])], True)
        self.assertEqual([c["cursor"] for c in calls], ["*", "*", "server-cursor", "*"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(counts, [(2,1), (2,2), (3,3)])
        self.assertEqual(rows[-1]["track"], "workshop")

    def test_no_proceedings_is_reported_as_unavailable(self):
        with self.assertRaises(CrawlError): self.collect([response([])])

    def test_icde_uses_specific_acronym_and_separate_workshop_search(self):
        venue = "2025 IEEE 41st International Conference on Data Engineering (ICDE)"
        workshop = "2025 IEEE International Conference on Data Engineering Workshops (ICDEW)"
        calls, rows, _ = self.collect([
            response([work("10.1109/icde.2025",venue,"proceedings")]),
            response([work("10.1109/icdew.2025",workshop,"proceedings")]),
            response([work("10.1109/icde.2025.1",venue)]),
            response([work("10.1109/icdew.2025.1",workshop)])],True,"ICDE")
        self.assertEqual([calls[0]["query.title"],calls[1]["query.title"]],["ICDE","ICDEW"])
        self.assertEqual(len(rows),2)

    def test_empty_or_duplicate_page_is_incomplete(self):
        for page in (response([], 2), response([work()], 2)):
            with self.subTest(page=page), self.assertRaises(CrawlError):
                self.collect([response([work(kind="proceedings")]), response([work()], 2), page])

    def test_wrong_container_is_rejected(self):
        with self.assertRaises(CrawlError):
            self.collect([response([work(kind="proceedings")]), response([work(venue=WORKSHOP)])])

    def test_missing_cursor_is_incomplete(self):
        first = response([work()], 2)
        del first["message"]["next-cursor"]
        with self.assertRaises(CrawlError):
            self.collect([response([work(kind="proceedings")]), first])

    def test_normalization_strips_jats_and_keeps_landing_link(self):
        raw = work()
        raw["link"] = [{"URL":"http://xplorestaging.ieee.org/private.pdf"}]
        paper = normalize(raw, "crossref", "INFOCOM", 2025)
        self.assertEqual(paper["abstract"], "A routing method.")
        self.assertEqual(paper["authors"], ["Alice Li", "Research Team"])
        self.assertEqual(paper["pages"], "1-10")
        self.assertEqual(paper["source_record_id"], raw["DOI"])
        self.assertEqual(paper["url"], "https://ieeexplore.ieee.org/document/123/")
        self.assertEqual(paper["pdf_url"], "")  # Do not expose publisher staging links.
        self.assertEqual(paper["keyword_source"], "local_title_abstract")

    def test_type_year_prefix_checks_and_cross_provider_dedup(self):
        raw = work()
        for changed in ({"type":"proceedings"}, {"DOI":"10.1234/not-ieee"}, {"published":{"date-parts":[[2024]]}}):
            self.assertIsNone(normalize({**raw,**changed}, "crossref", "INFOCOM", 2025))
        ieee = {"doi":raw["DOI"].upper(),"title":"Efficient Routing","publication_title":MAIN,"publication_year":2025}
        self.assertEqual(normalize(raw,"crossref","INFOCOM",2025)["id"], normalize(ieee,"ieee","INFOCOM",2025)["id"])

    def test_response_validation_and_default_source(self):
        self.assertEqual(validate_options({"conferences":["INFOCOM"],"years":[2025]})["source"], "crossref")
        for bad in ({"status":"failed"}, {"status":"ok","message-type":"work-list","message":{"total-results":1,"items":[{}]}}):
            with self.assertRaises((ValueError,KeyError)): validate_payload("crossref",bad)

    def test_cursor_pages_are_not_cached_but_complete_queries_survive_restart(self):
        replies = [response([work(kind="proceedings")]), response([work()], 3),
                   response([work("10.1109/infocom.2025.2")], 3),
                   response([work("10.1109/infocom.2025.3")], 3)]
        calls = []
        def transport(url):
            calls.append(url)
            return 200, {}, json.dumps(replies.pop(0)).encode()
        with tempfile.TemporaryDirectory() as directory:
            for attempt in range(2):
                client = SafeClient(directory, transport=transport, interval=0)
                rows = []
                crawl_group(client,"crossref","INFOCOM",2025,False,"",threading.Event(),
                            lambda batch,*args:rows.extend(batch),lambda message:None)
                self.assertEqual(len(rows),3)
                self.assertEqual(len(calls),4)
            # Server may return the same opaque cursor for consecutive pages.
            self.assertEqual(calls[-1],calls[-2])


if __name__ == "__main__": unittest.main()
