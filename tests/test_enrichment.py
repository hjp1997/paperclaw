import json
import tempfile
import threading
import unittest
from pathlib import Path
from paperclaw.abstracts import enrich_abstracts, enrich_titles, reconstruct, paper_abstract
from paperclaw.catalog import Document, plain, parse_catalog, parse_usenix
from paperclaw.core import CrawlError, SafeClient, crawl_group, normalize
from paperclaw.server import Application, validate_options


class EnrichmentTests(unittest.TestCase):
    ABSTRACT = "We study a verifiable systems problem and describe a concrete method with measurements and results from the paper."

    def test_source_quality_rejects_artifact_repository_and_tiny_fragments(self):
        index={}
        for i,w in enumerate(self.ABSTRACT.split()):index.setdefault(w,[]).append(i)
        self.assertEqual(paper_abstract({"abstract_inverted_index":index}),self.ABSTRACT)
        for extra in ({"doi":"https://doi.org/10.5281/zenodo.1"},{"type":"dataset"},
                      {"primary_location":{"landing_page_url":"https://figshare.com/articles/x"}}):
            self.assertEqual(paper_abstract({"abstract_inverted_index":index,**extra}),"")
        self.assertEqual(paper_abstract({"abstract_inverted_index":{"Artifact.":[0]}}),"")

    def test_title_search_requires_full_title_author_year_and_unique_record(self):
        paper = {"id":"original-id","title":"Unique Systems Paper","authors":["Alice Example 0001"],"year":2026,"abstract":"","doi":""}
        good = {"id":"https://openalex.org/W1","doi":"https://doi.org/10.1145/123","title":paper["title"],"publication_year":2026,
                "authorships":[{"author":{"display_name":"Alice Example"}}],"abstract_inverted_index":{}}
        for i,w in enumerate(self.ABSTRACT.split()):good["abstract_inverted_index"].setdefault(w,[]).append(i)
        class Client:
            rows = []
            def get(self,*args): return {"meta":{"count":len(self.rows)},"results":self.rows}
        client=Client()
        for rows,expected in [([good],True),([dict(good,title="Different title")],False),([dict(good,publication_year=2020)],False),
                              ([dict(good,authorships=[])],False),([good,dict(good,id="https://openalex.org/W2")],False)]:
            client.rows=rows;updated=[]
            enrich_titles(client,[paper],threading.Event(),updated.extend,lambda m:None)
            self.assertEqual(bool(updated[0]["abstract"]),expected)
            self.assertEqual(updated[0]["id"],paper["id"])

    def test_index_order_repetition_and_malformed_positions(self):
        self.assertEqual(reconstruct({"networks": [1, 3], "We": [0], "study": [2]}), "We networks study networks")
        for index in ({"a": [1]}, {"a": [0], "b": [0]}, {"a": [-1]}, {"a": [True]}, {"a": [50000]}, None):
            self.assertEqual(reconstruct(index), "")

    def test_exact_doi_missing_and_existing_abstract(self):
        index={}
        for i,w in enumerate(self.ABSTRACT.split()):index.setdefault(w,[]).append(i)
        papers = [{"id": "1", "doi": "10.1/ABC", "title": "Routing", "abstract": "", "keyword_source": "local_title"},
                  {"id": "2", "doi": "10.1/missing", "title": "Routing", "abstract": ""},
                  {"id": "3", "doi": "10.1/has", "title": "Existing", "abstract": "Original"}]
        class Client:
            def get(self, source, params, *args):
                assert source == "openalex"
                assert "10.1/has" not in params["filter"]
                return {"meta": {"count": 2}, "results": [
                    {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/abc", "abstract_inverted_index": index},
                    {"id": "https://openalex.org/W2", "doi": "https://doi.org/10.1/unrelated", "title": "Routing", "abstract_inverted_index": {"Wrong": [0]}}]}
        rows = []
        enrich_abstracts(Client(), papers, threading.Event(), rows.extend, lambda m: None)
        self.assertEqual(rows[0]["abstract"], self.ABSTRACT)
        self.assertEqual(rows[0]["abstract_source"], "openalex")
        self.assertEqual(rows[1]["abstract_status"], "unavailable")
        self.assertEqual(len(rows), 2)
        self.assertEqual(papers[0]["abstract"], "")

    def test_batching_and_key_never_cached(self):
        calls, updated = [], []
        def transport(url):
            calls.append(url)
            return 200, {}, json.dumps({"meta": {"count": 0}, "results": []}).encode()
        with tempfile.TemporaryDirectory() as directory:
            client = SafeClient(directory, transport=transport, interval=0)
            papers = [{"id": str(i), "doi": f"10.1/{i}", "abstract": ""} for i in range(101)]
            for key in ("secret-one", "secret-two"):
                enrich_abstracts(client, papers, threading.Event(), updated.extend, lambda m: None, key)
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(updated), 202)
            self.assertTrue(all("secret" not in p.read_text() for p in Path(directory).rglob("*.json")))

    def test_failed_enrichment_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Application(directory)
            paper = normalize({"title": "Title", "publication_title": "NSDI 2025", "publication_year": 2025,
                               "doi": "10.1/test", "abstract_url": "https://www.usenix.org/conference/nsdi25/presentation/test"}, "usenix", "NSDI", 2025)
            app.merge([paper])
            def fail(*args, **kwargs): raise CrawlError("429")
            app.client.get = fail
            app.run_abstracts([paper], "")
            self.assertEqual(app.snapshot()["status"], "partial")
            self.assertEqual(len(app.snapshot()["papers"]), 1)
            self.assertEqual(app.snapshot()["papers"][0]["abstract_status"], "not_checked")


class CatalogTests(unittest.TestCase):
    def test_html_math_superscripts_are_not_flattened_into_wrong_numbers(self):
        self.assertEqual(plain(Document('Up to 10<sup>6</sup>X; x<sub>i</sub>.').root), 'Up to 10^(6)X; x_(i).')

    def test_catalog_filters_category_rank_and_host(self):
        def row(name, rank="A", category="计算机网络", url="https://dblp.org/db/conf/nsdi/"):
            return f'<tr><td>{name}</td><td><a href="{url}">Title</a></td><td>{rank}</td><td>会议</td><td>{category}</td></tr>'
        data = parse_catalog(row("NSDI") + row("Other", "B") + row("ICDE", category="数据库") + row("Unsafe", url="https://other.example/"))
        self.assertEqual([r["name"] for r in data["entries"]], ["NSDI"])
        with self.assertRaises(ValueError): parse_catalog("<html>Verify you are human</html>")

    def test_usenix_abstract_authors_links_and_duplicates(self):
        article = '<article class="node node-paper"><h2><a href="/conference/nsdi25/presentation/test">A Paper</a></h2><div class="field-name-field-paper-people-text"><p>Alice, <em>University;</em> Bob and Carol, <em>Company</em></p></div><div class="field-name-field-paper-description-long"><p>First paragraph.</p><p>Second paragraph.</p></div><a href="/system/files/test.pdf">PDF</a></article>'
        data = parse_usenix(article * 2, 2025)
        self.assertEqual(data["total_records"], 1)
        p = normalize(data["articles"][0], "usenix", "NSDI", 2025)
        self.assertEqual(p["authors"], ["Alice", "Bob", "Carol"])
        self.assertEqual(p["abstract"], "First paragraph. Second paragraph.")
        self.assertEqual(p["abstract_source"], "usenix")
        self.assertEqual(p["pdf_url"], "https://www.usenix.org/system/files/test.pdf")
        with self.assertRaises(ValueError): parse_usenix(article, 2024)

    def test_acm_conferences_and_publisher_checks(self):
        for c, venue in [("MobiCom", "Proceedings of the 31st Annual International Conference on Mobile Computing and Networking"),
                         ("SIGCOMM", "Proceedings of the ACM SIGCOMM 2025 Conference")]:
            raw = {"DOI": "10.1145/123.456", "type": "proceedings-article", "title": ["A Paper"],
                   "container-title": [venue], "published": {"date-parts": [[2025]]}, "abstract": "<p>Actual abstract.</p>"}
            self.assertIsNotNone(normalize(raw, "crossref", c, 2025))
            self.assertEqual(normalize(raw, "crossref", c, 2025)["abstract_source"], "crossref")
            self.assertIsNone(normalize({**raw, "DOI": "10.1109/incorrect"}, "crossref", c, 2025))

    def test_auto_routes_nsdi_without_doi(self):
        class Client:
            def get(self, source, params, *args):
                assert source == "usenix"
                return {"total_records": 1, "articles": [{"title": "Paper", "publication_title": "NSDI 2025", "publication_year": 2025}]}
        rows = []
        crawl_group(Client(), "auto", "NSDI", 2025, False, "", threading.Event(), lambda b,*a: rows.extend(b), lambda m: None)
        self.assertEqual(rows[0]["source"], "usenix")
        self.assertEqual(rows[0]["doi"], "")
        for source, cs in [("crossref", ["NSDI"]), ("ieee", ["MobiCom"]), ("usenix", ["INFOCOM"])]:
            with self.assertRaises(ValueError): validate_options({"source": source, "conferences": cs, "years": [2025]})


if __name__ == "__main__": unittest.main()
