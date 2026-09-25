"""Agent layer: coordinator + specialized sub-agents behind one transport-agnostic contract.

This package is deliberately a *service boundary* sibling to ``src/`` (the in-process
pipeline library). Sub-agents may run embedded (``InProcessAgent``) or as their own HTTP
services (``HttpAgent``); the coordinator talks to the same :class:`~agents.contract.base.Agent`
protocol either way. See ``todo/COORDINATOR_PLAN.md``.
"""
