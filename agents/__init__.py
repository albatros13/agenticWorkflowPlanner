"""Agent layer: coordinator + specialized sub-agents behind one transport-agnostic contract.

This package is deliberately a *service boundary* around the in-process
pipeline library). Sub-agents may run embedded (``InProcessAgent``) or as their own HTTP
services (``HttpAgent``); the coordinator talks to the same :class:`~agents.contract.base.Agent`
protocol either way. See ``docs/planning-instructions/coordinator/IMPLEMENTATION_PLAN.md``.
"""
