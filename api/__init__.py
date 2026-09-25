"""Concrete LLM/service SDK clients (Anthropic, OpenAI, Qdrant).

This package is the **single place that talks to the real provider SDKs**. The
pipeline's provider-agnostic layer (``agents.text_to_bpmn.pipeline.llm``) delegates
its actual network calls here, so credentials, timeouts and SDK-shape details live in
one spot and can be swapped without touching agent/pipeline code.
"""
