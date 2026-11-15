import threading
import unittest
from unittest.mock import patch

from paperclaw import dblp_rdf
from paperclaw.catalog import CATALOG, CATEGORIES
from paperclaw.core import CrawlError, crawl_group
from paperclaw.server import validate_options


def binding(key, venue, title, author, position):
    fields = dict(p="https://dblp.org/rec/" + key, venue=venue, year="2024",
                  title=title, name=author, position=str(position))
    return {k: {"value": v} for k, v in fields.items()}


class ConferenceTreeTests(unittest.TestCase):
    def test_supported_areas_and_routing(self):
        self.assertEqual([sum(c["category"] == area for c in CATALOG.values()) for area in CATEGORIES],
                         [11, 4, 2, 7, 5, 5])
        self.assertEqual(CATALOG["NeurIPS"]["stream"], "nips")
        self.assertEqual(CATALOG["SIGKDD"]["stream"], "kdd")
        options = validate_options({"source":"auto", "conferences": list(CATALOG), "years":[2025]})
        self.assertEqual(len(options["conferences"]), 34)
        self.assertEqual(sum(c["provider"] == "dblp_rdf" for c in CATALOG.values()), 28)
        self.assertEqual(CATALOG["ACM SIGOPS ATC"]["stream"], "usenix")
        self.assertTrue(dblp_rdf.main_proceedings("USENIX ATC", "ACM SIGOPS ATC"))
        self.assertEqual(CATALOG["FAST"]["provider"], "usenix")

    def test_paginate_papers_before_authors_and_keep_edition(self):
        pages = [
            [{"n":{"value":"2"}}],
            [binding("conf/nips/a", "NeurIPS", "One", "Second", 2),
             binding("conf/nips/a", "NeurIPS", "One", "First", 1)],
            [binding("conf/nips/b", "NeurIPS Workshops", "Two", "Other", 1)],
        ]
        queries = []
        class Client:
            def get(self, source, params, *args):
                queries.append(params["query"])
                return {"results":{"bindings":pages.pop(0)}}
        papers = []
        with patch.object(dblp_rdf, "PAGE_SIZE", 1):
            crawl_group(Client(), "dblp", "NeurIPS", 2025, False, "", threading.Event(),
                        lambda rows,*args: papers.extend(rows), lambda message:None)
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["authors"], ["First","Second"])
        self.assertEqual((papers[0]["year"],papers[0]["publication_year"]), (2025,2024))
        self.assertIn("OFFSET 1", queries[-1])
        self.assertIn("streams/conf/nips", queries[-1])

    def test_link_journal_volumes_to_conference_year(self):
        self.assertIn('("2" "4")', dblp_rdf.edition("SIGMOD",2025))
        self.assertIn('publishedInJournalVolume "18"', dblp_rdf.edition("VLDB",2025))
        self.assertTrue(dblp_rdf.main_proceedings("Proc. VLDB Endow.", "VLDB"))
        self.assertTrue(dblp_rdf.main_proceedings("KDD (2)", "SIGKDD"))
        self.assertFalse(dblp_rdf.main_proceedings("TerraBytes@ICML", "ICML"))
        with self.assertRaises(CrawlError):
            crawl_group(None,"auto","SIGMOD",2027,False,"",threading.Event(),None,None)
