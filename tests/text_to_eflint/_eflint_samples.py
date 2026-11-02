"""A small GDPR consent specification (cf. the reference Consent.eflint) used across the tests."""
from __future__ import annotations

import copy

TEXT = (
    "Article 6(1)(a). Processing shall be lawful only if and to the extent that the data subject has "
    "given consent to the processing of his or her personal data for one or more specific purposes.\n"
    "Article 7(3). The data subject shall have the right to withdraw his or her consent at any time."
)

SPEC = {
    "title": "GDPR consent",
    "facts": [
        {"name": "controller", "identified_by": ["String"], "source": "GDPR Art. 4(7)"},
        {"name": "data-subject", "identified_by": ["String"], "source": "GDPR Art. 4(1)"},
        {"name": "purpose", "identified_by": ["String"], "source": "GDPR Art. 6(1)(a)"},
        {"name": "consent", "identified_by": ["data-subject", "controller", "purpose"],
         "source": "GDPR Art. 6(1)(a)",
         "source_quote": "the data subject has given consent to the processing of his or her personal data"},
        {"name": "processed", "identified_by": ["controller", "data-subject", "purpose"], "source": "domain model"},
    ],
    "duties": [],
    "acts": [
        {"name": "give-consent", "actor": "data-subject", "recipient": "controller", "related_to": ["purpose"],
         "holds_when": "!consent()", "creates": ["consent()"], "source": "GDPR Art. 6(1)(a)",
         "source_quote": "the data subject has given consent"},
        {"name": "withdraw-consent", "actor": "data-subject", "recipient": "controller", "related_to": ["purpose"],
         "holds_when": "consent()", "terminates": ["consent()"], "source": "GDPR Art. 7(3)",
         "source_quote": "The data subject shall have the right to withdraw his or her consent at any time."},
        {"name": "process-personal-data", "actor": "controller", "recipient": "data-subject", "related_to": ["purpose"],
         "holds_when": "consent(data-subject, controller, purpose)",
         "creates": ["processed(controller, data-subject, purpose)"], "source": "GDPR Art. 6(1)(a)",
         "source_quote": "Processing shall be lawful only if and to the extent that the data subject has given consent"},
    ],
    "instances": [
        {"term": 'controller("acme")'}, {"term": 'data-subject("alice")'}, {"term": 'purpose("marketing")'},
    ],
    "initial_state": [],
    "scenarios": [
        {"name": "Consent given then withdrawn", "steps": [
            {"kind": "perform", "term": 'give-consent("alice", "acme", "marketing")'},
            {"kind": "query", "term": 'consent("alice", "acme", "marketing")', "query": "holds", "expect": True},
            {"kind": "perform", "term": 'process-personal-data("acme", "alice", "marketing")'},
            {"kind": "perform", "term": 'withdraw-consent("alice", "acme", "marketing")'},
            {"kind": "query", "term": 'process-personal-data("acme", "alice", "marketing")', "query": "enabled", "expect": False},
            {"kind": "perform", "term": 'process-personal-data("acme", "alice", "marketing")', "expect": False},
        ]},
    ],
}


def spec(**overrides) -> dict:
    s = copy.deepcopy(SPEC)
    s.update(overrides)
    return s
