"""text -> eFLINT agent.

* :mod:`.labelling` — FLINT semantic-role labelling of legal sentences into act frames (FlintFiller-SRL);
* :mod:`.generation` — LLM-assisted generation of executable eFLINT specifications (facts, duties,
  acts, scenarios), checked, scenario-tested and repaired; see ``generation/reference/README.md``;
* :mod:`.ontology` — the vendored FLINT ontology + SHACL shapes (see that package's README);
* :mod:`.agent` / :mod:`.service` — the agent contract handler and its HTTP service.
"""
