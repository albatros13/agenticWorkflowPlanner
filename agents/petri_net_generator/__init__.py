"""Petri net generator agent: BPMN XML / IR / text -> ordinary workflow Petri net -> PNML.

Only the handler is exported here; it imports PM4Py lazily, so registering the agent does not
require the ``petri_net`` extra until a net is actually generated.
"""
from .agent import PetriNetGeneratorHandler

__all__ = ["PetriNetGeneratorHandler"]
