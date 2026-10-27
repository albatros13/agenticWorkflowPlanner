"""Text -> process-algebra IR, through the project's provider-agnostic structured-output LLM call.

The model only *classifies structure* (sequence / xor / parallel / loop) and records what the text
leaves open; it never emits places, transitions or XML. Its output must still pass Pydantic
validation and :func:`.ir.check_ir` before the deterministic compiler runs.
"""
from __future__ import annotations

from agents.text_to_bpmn.pipeline.llm.base import LLMProvider

from .ir import ProcessIR

TASK = "petri_net_ir_extraction"

SYSTEM = """You convert a natural-language process description into a structured process tree.
Return ONLY the JSON object required by the schema; never Petri-net or BPMN XML.

Constructs:
- activity: one atomic task. Give it a short stable id (letters, digits, _ . -) unique in the
  process, and a label worded as in the source.
- sequence: steps that happen one after another ("then", "after", "next").
- xor: exclusive choice, exactly one branch runs ("if/otherwise", "either/or"). Put each stated
  condition in the branch's "condition". Use a branch with "body": null for "do nothing".
- parallel: branches that all run concurrently and must all finish before continuing ("in
  parallel", "meanwhile", "both ... and ...").
- loop: run "body", then either exit or run "redo" (optional) and run "body" again. Use it for
  repetition such as "return for revision and validate again". Record the exit and continue
  conditions when the text states them.

Rules:
- Preserve the described order, alternatives, concurrency and repetition.
- Do not invent steps, conditions, actors or outcomes that are not in the text.
- Distinguish exclusive choice (xor) from concurrency (parallel); never use one for the other.
- If a branch condition or a loop's termination is unclear, do not fabricate it: add an entry to
  "ambiguities" with a targeted "question". Mark it "blocking" when the process structure itself
  is undecidable (e.g. you cannot tell whether two steps are alternatives or concurrent), and
  "non_blocking" when a reasonable structure exists but a detail is missing; in that case also
  state the structure you chose in "assumptions".
"""


def extract_ir(provider: LLMProvider, text: str, *, process_name: str = "") -> ProcessIR:
    prompt = f"Process name: {process_name or '(derive a short name from the text)'}\n\n" \
             f"Process description:\n<<<\n{text}\n>>>"
    result = provider.generate_json(
        task=TASK, system=SYSTEM, prompt=prompt, schema=ProcessIR.model_json_schema(), max_tokens=4096,
    )
    ir = ProcessIR.model_validate(result.data)
    if process_name and not ir.process_name:
        ir = ir.model_copy(update={"process_name": process_name})
    return ir
