"""
Weekly model-lifecycle check (run by .github/workflows/model-lifecycle.yml).

For each model the backend is configured to use (gemini_model, gemini_fallback_model
and gemini_fast_model in backend/core/config.py), read Google's model-versions page
and fail if a retirement date within 60 days is listed. Google blocks new access to a
model one month before that date, and a retired model ID returns 404. (Jun 2026: the
bot answered every question with an apology for four months after gemini-2.0-flash
was retired, and nothing noticed.)

Retirement cells on that page read like:
  "October 20, 2026"                 scheduled: fail when within the window or past
  "May 19, 2027 or later",
  "No sooner than May 20, 2028",
  "Not before November 1, 2026"      earliest possible: a warning when within the window
  "No retirement date announced", "" nothing to act on
A configured model missing from the page is a warning (renamed page, or a preview model).

No credentials and no dependencies beyond the standard library. Exit code 1 = act now.

    python monitoring/model_lifecycle.py --config backend/core/config.py
"""
import argparse
import ast
import datetime
import os
import re
import sys
import urllib.request
from html.parser import HTMLParser

PAGE_URL = "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions"
MODEL_SETTINGS = ("gemini_model", "gemini_fallback_model", "gemini_fast_model")
DATE = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4})")
EARLIEST = re.compile(r"(or later|no sooner than|not before)", re.I)


def configured_models(config_path: str) -> dict:
    """{setting name: model ID} from the Settings class defaults, read without importing it"""
    tree = ast.parse(open(config_path, encoding="utf-8").read())
    models = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id in MODEL_SETTINGS and isinstance(node.value, ast.Constant):
            models[node.target.id] = node.value.value
    return models


class _Rows(HTMLParser):
    """Every table row as a list of cell texts"""

    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def retirement_cells(html: str) -> dict:
    """{model ID: retirement cell text}; the first table that lists a model wins"""
    parser = _Rows()
    parser.feed(html)
    cells = {}
    for row in parser.rows:
        if len(row) >= 3 and row[0] and row[0] != "Model ID":
            cells.setdefault(row[0].rstrip("*").strip(), row[2])
    return cells


def parse_retirement(text: str):
    """(date or None, "scheduled" | "earliest" | "none")"""
    match = DATE.search(text or "")
    if not match:
        return None, "none"
    when = datetime.datetime.strptime(match.group(1), "%B %d, %Y").date()
    return when, ("earliest" if EARLIEST.search(text) else "scheduled")


def assess(model: str, cell, today: datetime.date, days: int = 60):
    """("ok" | "warn" | "fail", message)"""
    if cell is None:
        return "warn", f"{model}: not listed on the model-versions page; check its lifecycle by hand"
    when, kind = parse_retirement(cell)
    if when is None:
        return "ok", f"{model}: {cell or 'no retirement date listed'}"
    left = (when - today).days
    if kind == "scheduled" and left <= days:
        state = "is past its retirement date" if left < 0 else f"retires in {left} days"
        return "fail", (f"{model} {state} ({when:%b %d, %Y}); new access stops a month before. "
                        f"Switch the model setting in backend/core/config.py and measure first.")
    if kind == "earliest" and left <= days:
        return "warn", f"{model}: could retire from {when:%b %d, %Y} ({cell}); watch for a fixed date"
    return "ok", f"{model}: {cell}"


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "rayhan-agent-model-lifecycle/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:  # follows the docs' redirects
        return response.read().decode("utf-8", errors="replace")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", default="backend/core/config.py")
    parser.add_argument("--url", default=PAGE_URL)
    parser.add_argument("--html", help="read a saved copy of the page instead of fetching it")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--today", help="YYYY-MM-DD, for testing")
    args = parser.parse_args(argv)

    today = datetime.date.fromisoformat(args.today) if args.today else datetime.date.today()
    models = configured_models(args.config)
    if not models:
        print(f"::error::no model settings found in {args.config}")
        return 1
    try:
        html = open(args.html, encoding="utf-8").read() if args.html else fetch(args.url)
    except Exception as e:
        print(f"::error::could not read the model-versions page ({type(e).__name__}: {e}); it may have moved")
        return 1
    cells = retirement_cells(html)
    if not cells:
        print("::error::no table rows found on the model-versions page; the page layout changed")
        return 1

    worst, lines = "ok", []
    for setting, model in models.items():
        level, message = assess(model, cells.get(model), today, args.days)
        lines.append(f"| `{setting}` | `{model}` | {level} | {message} |")
        if level == "fail":
            print(f"::error::{message}")
            worst = "fail"
        elif level == "warn":
            print(f"::warning::{message}")
            worst = "warn" if worst == "ok" else worst
        else:
            print(message)

    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### Model lifecycle ({today:%b %d, %Y}, window {args.days} days)\n\n"
                    "| Setting | Model | Result | Detail |\n|---|---|---|---|\n" + "\n".join(lines) + "\n")
    return 1 if worst == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
