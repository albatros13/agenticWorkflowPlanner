"""eFLINT verifier agent: wraps the external eFLINT model-checking webservice (default
``http://127.0.0.1:8100``) to verify a Petri-net process against an eFLINT specification."""
from .agent import EflintVerifierHandler

__all__ = ["EflintVerifierHandler"]
