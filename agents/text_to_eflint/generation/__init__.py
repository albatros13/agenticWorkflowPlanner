"""LLM-assisted generation of executable eFLINT specifications from regulatory text.

Reuses the approach (and, as a few-shot reference and golden test, the files) of the
*data governance framework* eFLINT specification — see ``reference/README.md``. Pieces:

* :mod:`.spec` — the structured spec the LLM emits; :mod:`.prompts` — schema + instructions;
* :mod:`.syntax` — parser/printer for the supported eFLINT subset;
* :mod:`.simulator` — a small reference interpreter used to run scenarios;
* :mod:`.checks` — static checks, grounding and scenario execution (the repair feedback);
* :mod:`.render` — ``.eflint`` module output; :mod:`.generator` — the draft/check/repair loop.
"""
from .checks import Report, check_spec
from .generator import EflintGenerator, GenerationResult, parse_base_spec
from .render import render_files
from .spec import ActDecl, DutyDecl, EflintSpec, FactDecl, Finding, Scenario, ScenarioStep, Statement

__all__ = [
    "EflintGenerator", "GenerationResult", "parse_base_spec", "check_spec", "Report", "render_files",
    "EflintSpec", "FactDecl", "DutyDecl", "ActDecl", "Statement", "Scenario", "ScenarioStep", "Finding",
]
