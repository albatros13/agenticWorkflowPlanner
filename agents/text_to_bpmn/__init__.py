"""Text->BPMN agent: a thin wrapper that exposes the staged pipeline (``pipeline/``) as an agent.

The pipeline stages themselves are untouched; this package only translates between the agent
contract and the existing :class:`src.pipeline.runner.Pipeline` calls, and optionally serves
the agent as a standalone HTTP service.
"""
from .agent import TextToBpmnHandler

__all__ = ["TextToBpmnHandler"]
