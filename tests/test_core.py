import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from paperclaw.core import (Cancelled, CrawlError, SafeClient, crawl_group, export_papers,
                            normalize, validate_payload)


def record(key="a", venue="INFOCOM", year="2025", title="Reliable Federated Learning over Wireless Networks"):
    return {"key": "conf/infocom/" + key, "title": title, "venue": venue, "year": year,
            "type": "Conference and Workshop Papers", "doi": "10.1109/test." + key,
            "authors": {"author": [{"text": "Alice 0001"}, {"text": "Bob"}]},
            "url": "https://dblp.org/rec/conf/infocom/" + key,
            "ee": "https://doi.org/10.1109/test." + key}


def dblp(records, total=None):
    return {"result": {"status": {"@code": "200"}, "hits": {"@total": str(len(records) if total is None else total),
                        "hit": [{"info": r} for r in records]}}}


def ieee(number="1"):
    return {"article_number": number, "title": "Database Query Optimization", "publication_title": "2025 IEEE 41st International Conference on Data Engineering (ICDE)",
            "publication_year": 2025, "doi": "10.1109/ICDE." + number, "content_type": "Conferences",
            "authors": {"authors": [{"full_name": "Alice"}]}, "abstract": "An optimizer for database queries.",
            "index_terms": {"author_terms": {"terms": ["Query optimization", "Databases"]}},
            "pdf_url": "https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=" + number}


class NormalizationTests(unittest.TestCase):
    def test_dblp_single_author_entities_and_no_invented_pdf(self):
        raw = record(title="Alice&apos;s Graph &amp; Database")
        raw["authors"]["author"] = {"text": "Alice"}
        p = normalize(raw, "dblp", "INFOCOM", 2025)
        self.assertEqual(p["title"], "Alice's Graph & Database")
        self.assertEqual(p["authors"], ["Alice"])
        self.assertEqual(p["pdf_url"], "")
        self.assertEqual(p["abstract"], "")
        self.assertEqual(p["keyword_source"], "local_title")

    def test_filters_proceedings_workshops_year_and_unrelated_conferences(self):
        for raw in [record(venue="INFOCOM Workshops"), record(year="2024"), record(venue="ICDE"),
                    record(title="[Front matter]"), record(title="A Message from the Chairs."),
                    {**record(), "type": "Editorship"}]:
            self.assertIsNone(normalize(raw, "dblp", "INFOCOM", 2025))
        p = normalize(record(venue="INFOCOM Workshops"), "dblp", "INFOCOM", 2025, True)
        self.assertEqual(p["track"], "workshop")

    def test_icdew_is_workshop(self):
        raw = ieee()
        raw["publication_title"] = "2025 IEEE International Conference on Data Engineering Workshops (ICDEW)"
        self.assertIsNone(normalize(raw, "ieee", "ICDE", 2025))
        self.assertEqual(normalize(raw, "ieee", "ICDE", 2025, True)["track"], "workshop")

    def test_ieee_preserves_abstract_and_author_keywords(self):
        p = normalize(ieee(), "ieee", "ICDE", 2025)
        self.assertEqual(p["keyword_source"], "author_terms")
        self.assertEqual(p["keywords"], ["Query optimization", "Databases"])
        self.assertTrue(p["url"].endswith("/document/1"))
        self.assertTrue(p["abstract"])

    def test_ieee_terms_fallback_and_doi_identity(self):
        raw = ieee()
        raw["index_terms"] = {"ieee_terms": {"terms": ["Databases"]}}
        p = normalize(raw, "ieee", "ICDE", 2025)
        self.assertEqual(p["keyword_source"], "ieee_terms")
        d = record(venue="ICDE")
        d["doi"] = "https://doi.org/10.1109/ICDE.1"
        self.assertEqual(p["id"], normalize(d, "dblp", "ICDE", 2025)["id"])

    def test_unsafe_links_and_exports(self):
        p = normalize(record(title="=HYPERLINK(\"https://bad\")"), "dblp", "INFOCOM", 2025)
        p["ai_summary"] = "中文总结"
        content, _ = export_papers([p], "csv")
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        self.assertIn("'=HYPERLINK", content.decode("utf-8-sig"))
        self.assertIn("中文总结", export_papers([p], "md")[0].decode())
        self.assertEqual(json.loads(export_papers([p], "json")[0])[0]["title"], p["title"])
        raw = ieee()
        raw["pdf_url"] = "javascript:alert(1)"
        self.assertEqual(normalize(raw, "ieee", "ICDE", 2025)["pdf_url"], "")


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.now = 1000000
        self.calls = []
        self.response = (200, {}, json.dumps(dblp([record()])).encode())
        self.cancel = threading.Event()

    def tearDown(self):
        self.temp.cleanup()

    def transport(self, url):
        self.calls.append(url)
        return self.response

    def client(self, interval=0):
        return SafeClient(self.path, transport=self.transport, clock=lambda: self.now, interval=interval)

    def fetch(self, client=None, params=None, scope="dblp:INFOCOM:2025"):
        return (client or self.client()).get("dblp", params or {"q": "infocom"}, scope, self.cancel)

    def test_cache_survives_restart_no_key_on_disk(self):
        self.fetch(params={"q": "infocom", "apikey": "secret-key"})
        self.fetch(params={"q": "infocom", "apikey": "changed-key"})
        self.assertEqual(len(self.calls), 1)
        for path in self.path.rglob("*.json"):
            self.assertNotIn("secret-key", path.read_text())
            self.assertNotIn("changed-key", path.read_text())

    def test_cache_expiry(self):
        self.fetch()
        self.now += 86401
        self.fetch()
        self.assertEqual(len(self.calls), 2)

    def test_401_403_allow_manual_retry_without_cooldown(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.calls.clear()
                self.response = (status, {}, b"Forbidden")
                with self.assertRaises(CrawlError): self.fetch()
                self.assertEqual(len(self.calls), 1)  # No automatic retry.
                self.response = (200, {}, json.dumps(dblp([])).encode())
                self.fetch()
                self.assertEqual(len(self.calls), 2)
                for path in self.path.glob("cache/*.json"):
                    path.unlink()

    def test_202_challenge_allows_manual_retry_and_is_not_cached(self):
        self.response = (202, {}, b"<html>verify you are human</html>")
        with self.assertRaises(CrawlError): self.fetch()
        with self.assertRaises(CrawlError): self.fetch()
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(list(self.path.glob("cache/*.json")))
        self.response = (200, {}, json.dumps(dblp([])).encode())
        self.fetch(scope="dblp:ICDE:2025")
        self.assertEqual(len(self.calls), 3)

    def test_429_retry_after_is_advisory_and_restart_does_not_block(self):
        self.response = (429, {"retry-after": "7200"}, b"limited")
        with self.assertRaisesRegex(CrawlError, "Retry-After: 7200"):
            self.fetch()
        self.response = (200, {}, json.dumps(dblp([])).encode())
        self.fetch(self.client())
        self.assertEqual(len(self.calls), 2)

    def test_legacy_cooldowns_are_ignored_without_resetting_request_count(self):
        policy = {"requests": [self.now - 20],
                  "blocks": {"dblp": self.now + 86400, "dblp:INFOCOM:2025": self.now + 900},
                  "challenges": {"dblp": 5}}
        (self.path / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
        self.fetch()
        saved = json.loads((self.path / "policy.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, {"requests": [self.now - 20, self.now]})
        self.assertEqual(len(self.calls), 1)

    def test_valid_paper_about_captcha_is_not_challenge(self):
        self.response = (200, {}, json.dumps(dblp([record(title="CAPTCHA Security")])).encode())
        self.fetch()

    def test_bad_json_and_api_errors_are_not_zero_results(self):
        for raw in (b"<html>oops</html>", b'{"error":"Invalid API Key"}', b'{"result":{"hits":{}}}'):
            self.response = (200, {}, raw)
            with self.assertRaises(CrawlError): self.fetch()
        self.assertFalse(list(self.path.glob("cache/*.json")))

    def test_daily_budget_is_persistent(self):
        (self.path / "policy.json").write_text(json.dumps({"requests":[self.now]*300,"blocks":{},"challenges":{}}))
        with self.assertRaises(CrawlError): self.fetch()
        self.assertFalse(self.calls)

    def test_cancellation_and_no_auto_retry(self):
        self.cancel.set()
        with self.assertRaises(Cancelled): self.fetch()
        self.assertFalse(self.calls)
        self.cancel.clear()
        self.response = (500, {}, b"error")
        with self.assertRaises(CrawlError): self.fetch()
        self.assertEqual(len(self.calls), 1)

    def test_rate_spacing_can_be_cancelled(self):
        self.fetch()
        cancel = self.cancel
        class WaitSpy:
            def is_set(self): return False
            def wait(self, delay):
                self.delay = delay
                return True
        spy = WaitSpy()
        self.cancel = spy
        with self.assertRaises(Cancelled): self.fetch(self.client(interval=10), {"q": "second"})
        self.assertEqual(spy.delay, 10)
        self.assertEqual(len(self.calls), 1)
        self.cancel = cancel


class PagingTests(unittest.TestCase):
    def collect(self, responses, source="dblp", include=False):
        calls, batches = [], []
        class Client:
            def get(self, provider, params, scope, cancel, progress):
                calls.append(params)
                return responses.pop(0)
        crawl_group(Client(), source, "INFOCOM" if source == "dblp" else "ICDE", 2025,
                    include, "test-key", threading.Event(), lambda batch,*rest:batches.extend(batch), lambda m:None)
        return calls, batches

    def test_short_pages_are_not_assumed_complete(self):
        calls, rows = self.collect([dblp([record("a"),record("b")], 3), dblp([record("c")], 3)])
        self.assertEqual([c["f"] for c in calls], [0,2])
        self.assertEqual(len(rows), 3)

    def test_duplicate_pages_stop(self):
        with self.assertRaises(CrawlError): self.collect([dblp([record()], 2),dblp([record()], 2)])

    def test_empty_truncated_page_stops(self):
        with self.assertRaises(CrawlError): self.collect([dblp([record()], 2),dblp([], 2)])

    def test_ieee_one_based_paging(self):
        calls, rows = self.collect([{"total_records":2,"articles":[ieee("1")]}, {"total_records":2,"articles":[ieee("2")]}], "ieee")
        self.assertEqual([c["start_record"] for c in calls], [1,2])
        self.assertEqual(calls[0]["max_records"], 200)
        self.assertEqual(len(rows), 2)

    def test_zero_results_is_valid_only_with_structured_count(self):
        calls, rows = self.collect([dblp([])])
        self.assertEqual(len(calls), 1)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
