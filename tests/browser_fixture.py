"""Build deterministic browser-test data; no network calls or real API keys."""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pages
from test_analysis import CONTENT, paper, transport
from paperclaw.analysis import analyze_paper
from paperclaw.core import atomic_json, normalize

root = Path(__file__).resolve().parents[1] / "test-results/pages-browser"
rows = []
for index in range(32):
    item = paper(index)
    item["categories"] = ["Oral"] if index < 3 else ["Poster"]
    item["category_sources"] = {item["categories"][0]: "https://example.org/program"}
    item["keywords"] = ["scheduling", "systems"]
    if index < 2:
        item["ai_analysis"] = analyze_paper(item, "fixture-only-key", transport=transport())
    rows.append(item)
rows.append(normalize({"key":"other", "title":"Another conference paper", "venue":"ICDE", "year":"2024",
                       "type":"Conference and Workshop Papers"}, "dblp", "ICDE", 2024))
with contextlib.redirect_stdout(io.StringIO()):
    pages.save_collections(rows, root / "data", replace=True)
    atomic_json(root/"data/status/collections.json", {"editions":[{"conference":"HPCA","year":2026,"status":"unavailable","reason":"尚未收录（测试）"}]})
    pages.build(root / "data", root / "paperclaw")
print("Browser fixtures built in test-results/pages-browser/paperclaw")
