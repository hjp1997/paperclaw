import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pages
from paperclaw import analysis
from paperclaw.core import CrawlError, normalize


CONTENT = {"topics": ["networking", "optimization"], "problem": "优化网络资源分配。",
           "method": "通过在线算法进行调度。", "results": "摘要未报告具体结果。"}


def paper(index=1):
    result = normalize({"key": str(index), "title": "Paper " + str(index), "year": "2025", "venue": "INFOCOM",
                        "type": "Conference and Workshop Papers"}, "dblp", "INFOCOM", 2025)
    result["abstract"] = "We study online scheduling for network resource allocation."
    return result


def transport(content=CONTENT, status=200, finish="stop"):
    return lambda *args, **kwargs: (status, {}, json.dumps({"choices": [
        {"finish_reason": finish, "message": {"content": json.dumps(content)}}]}).encode())


class AnalysisTests(unittest.TestCase):
    def test_request_limits_evidence_and_public_output_excludes_private_fields(self):
        original = {**paper(), "api_key": "PRIVATE", "ai_summary": "PRIVATE SUMMARY"}
        captured = {}

        def request(url, **kwargs):
            captured.update(url=url, **kwargs)
            return transport()()

        original["ai_analysis"] = analysis.analyze_paper(original, "secret-test-key", transport=request)
        payload = json.loads(captured["body"])
        self.assertEqual(captured["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(json.loads(payload["messages"][1]["content"]), {"title": original["title"], "abstract": original["abstract"]})
        self.assertNotIn("secret-test-key", str(payload))
        original["ai_analysis"]["api_key"] = "PRIVATE"
        result = pages.public_paper(original)
        self.assertEqual(result["ai_analysis"]["topics"], CONTENT["topics"])
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_invalid_truncated_and_error_responses_are_not_saved_or_echoed(self):
        for content, status, finish in [({**CONTENT, "topics": ["Oral"]}, 200, "stop"),
                                      ({**CONTENT, "topics": ["networking"] * 2}, 200, "stop"),
                                      ({**CONTENT, "results": ""}, 200, "stop"),
                                      ({**CONTENT, "method": "secret-test-key"}, 200, "stop"),
                                      (CONTENT, 200, "length"), (CONTENT, 429, "stop")]:
            with self.subTest(status=status, finish=finish, content=content):
                with self.assertRaises(CrawlError) as error:
                    analysis.analyze_paper(paper(), "secret-test-key", transport=transport(content, status, finish))
                self.assertNotIn("secret-test-key", str(error.exception))

    def test_changed_evidence_invalidates_analysis_but_metadata_merge_retains_valid_result(self):
        original = paper()
        original["ai_analysis"] = analysis.analyze_paper(original, "secret-test-key", transport=transport())
        self.assertIsNotNone(analysis.public_analysis(original))
        for changed in ({"abstract": "Changed abstract"}, {"title": "Changed title"}, {"abstract": ""}):
            self.assertNotIn("ai_analysis", pages.public_paper({**original, **changed}))
        refreshed = pages.public_paper({**paper(), "abstract": ""})
        pages.retain_metadata(refreshed, original)
        self.assertEqual(refreshed["ai_analysis"], original["ai_analysis"])
        refreshed = pages.public_paper({**paper(), "abstract": "New abstract"})
        pages.retain_metadata(refreshed, original)
        self.assertNotIn("ai_analysis", refreshed)

    def test_batch_checkpoints_resume_limit_and_build(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "data"
            missing = {**paper(3), "abstract": ""}
            with contextlib.redirect_stdout(io.StringIO()):
                pages.save_collections([paper(1), paper(2), missing], directory)
            args = SimpleNamespace(data_dir=directory, conferences="INFOCOM", years="2025", limit=2,
                                   force=False, dry_run=False, model=analysis.MODEL)
            path = directory / "infocom-2025.json"
            real = lambda p, key, model: analysis.analyze_paper(p, key, model, transport=transport())
            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "secret-test-key"}), contextlib.redirect_stdout(io.StringIO()):
                with patch.object(pages, "analyze_paper", side_effect=[real(paper(1), "secret-test-key", analysis.MODEL), CrawlError("Stopped")]):
                    with self.assertRaises(CrawlError):
                        pages.analyze(args)
                self.assertEqual(sum(bool(p.get("ai_analysis")) for p in pages.load_input(path)), 1)
                with patch.object(pages, "analyze_paper", side_effect=real) as run:
                    pages.analyze(args)
                    self.assertEqual(run.call_count, 1)
                    pages.analyze(args)
                    self.assertEqual(run.call_count, 1)
                args.force, args.limit = True, 1
                with patch.object(pages, "analyze_paper", side_effect=real) as run:
                    pages.analyze(args)
                    self.assertEqual(run.call_count, 1)
                pages.build(directory, Path(temp) / "output")
            manifest = json.loads((Path(temp) / "output/manifest.json").read_bytes())
            self.assertEqual(manifest["groups"][0]["analysis_count"], 2)
            self.assertIn("networking", manifest["topics"])
            self.assertIn("ai_results", manifest["export_fields"])
            self.assertNotIn("secret-test-key", path.read_text("utf-8"))

    def test_dry_run_and_missing_key_do_not_call_model_or_modify_data(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            directory = Path(temp)
            pages.save_collections([paper()], directory)
            path = directory / "infocom-2025.json"
            before = path.read_bytes()
            args = SimpleNamespace(data_dir=directory, conferences="INFOCOM", years="2025", limit=50,
                                   force=False, dry_run=True, model=analysis.MODEL)
            with patch.dict("os.environ", {}, clear=True), patch.object(pages, "analyze_paper") as run:
                pages.analyze(args)
                args.dry_run = False
                with self.assertRaises(ValueError):
                    pages.analyze(args)
                run.assert_not_called()
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
