"""The vendored reference specification (see ``reference/README.md``) as an :class:`EflintSpec`."""
from __future__ import annotations

from functools import lru_cache
from importlib.resources import as_file, files
from pathlib import Path

from .convert import program_to_spec, scenario_from_program
from .spec import EflintSpec
from .syntax import parse_file

REFERENCE = files(__package__) / "reference"

# Scenarios shown to the LLM: the compliant path and a duty violation (others are golden tests).
FEW_SHOT_SCENARIOS = {
    1: ("Compliant path", "All parties hold consent and meet every condition; the full act chain runs."),
    3: ("Privacy technique not applied (GDPR Art. 5(1)(c))",
        "company-b collects data but never fulfils the privacy-technique duty: the duty is violated "
        "and company-b is blocked at compliance verification."),
}
SCENARIO_TITLES = {
    1: "Compliant path",
    2: "Consent withdrawn (GDPR Art. 7)",
    3: "Privacy technique not applied (GDPR Art. 5(1)(c))",
    4: "Unauthorized third party (Data Act Art. 5)",
    5: "Second training round",
}


def reference_path(*parts: str) -> Path:
    with as_file(REFERENCE.joinpath(*parts)) as p:
        return Path(p)


@lru_cache(maxsize=1)
def reference_spec(scenarios: tuple[int, ...] = tuple(FEW_SHOT_SCENARIOS)) -> EflintSpec:
    spec = program_to_spec(parse_file(reference_path("Init.eflint")),
                           title="Data governance framework for vertical federated learning "
                                 "(GDPR, Data Act, Electricity Directive)")
    for n in scenarios:
        path = reference_path("scenarios", f"Scenario-{n}.eflint").resolve()
        name, desc = FEW_SHOT_SCENARIOS.get(n, (SCENARIO_TITLES[n], ""))
        spec.scenarios.append(scenario_from_program(parse_file(path), str(path), name=name, description=desc,
                                                    simulate=True))
    return spec
