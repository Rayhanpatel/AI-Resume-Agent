"""
The weekly model-lifecycle check reads Google's model-versions table correctly:
scheduled dates fail inside the window, "or later" / "No sooner than" / "Not before"
only warn, and a model missing from the page warns. Uses a saved excerpt of the page.
"""
import datetime
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "fixtures" / "vertex_model_versions.html"

spec = importlib.util.spec_from_file_location("model_lifecycle", ROOT / "monitoring" / "model_lifecycle.py")
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)

TODAY = datetime.date(2026, 10, 25)


def test_reads_the_table():
    cells = lifecycle.retirement_cells(FIXTURE.read_text())
    assert cells["gemini-3.5-flash"] == "May 19, 2027 or later"
    assert cells["gemini-2.0-flash"] == "June 1, 2026"
    assert cells["gemini-embedding-001"] == "No sooner than May 20, 2028"
    assert cells["gemini-3.8-flash-cyber"] == "No retirement date announced"


def test_parses_each_wording():
    assert lifecycle.parse_retirement("October 20, 2026") == (datetime.date(2026, 10, 20), "scheduled")
    assert lifecycle.parse_retirement("May 19, 2027 or later") == (datetime.date(2027, 5, 19), "earliest")
    assert lifecycle.parse_retirement("No sooner than May 20, 2028")[1] == "earliest"
    assert lifecycle.parse_retirement("Not before November 1, 2026")[1] == "earliest"
    assert lifecycle.parse_retirement("No retirement date announced") == (None, "none")
    assert lifecycle.parse_retirement("") == (None, "none")


def test_assessment_levels():
    cells = lifecycle.retirement_cells(FIXTURE.read_text())
    level = lambda model: lifecycle.assess(model, cells.get(model), TODAY)[0]
    assert level("gemini-3.5-flash") == "ok"             # earliest possible date is May 2027
    assert level("gemini-3.6-flash") == "fail"           # November 19, 2026: 25 days away
    assert level("gemini-2.0-flash") == "fail"           # already retired (the June 2026 outage)
    assert level("test-model-not-before") == "warn"      # could retire Nov 1, not scheduled yet
    assert level("gemini-3.8-flash-cyber") == "ok"       # no date announced
    assert level("gemini-9-imaginary") == "warn"         # not on the page at all


def test_reads_the_configured_models_and_exits_cleanly_today():
    models = lifecycle.configured_models(str(ROOT / "backend" / "core" / "config.py"))
    assert set(models) == {"gemini_model", "gemini_fallback_model", "gemini_fast_model"}
    code = lifecycle.main(["--config", str(ROOT / "backend" / "core" / "config.py"),
                           "--html", str(FIXTURE), "--today", TODAY.isoformat()])
    assert code == 0  # gemini-3.5-flash and -lite are both listed as "... 2027 or later"
