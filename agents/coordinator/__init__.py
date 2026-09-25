"""Coordinator agent: recognizes user intent, manages projects, and routes to sub-agents.

Delegation is deterministic (code-orchestrated); the LLM is used only to classify intent and
extract slots (structured output), never to decide control flow. See COORDINATOR_PLAN.
"""
