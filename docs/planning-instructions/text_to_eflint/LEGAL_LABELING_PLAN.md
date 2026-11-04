## FlintFiller-SRL — Overview & Reuse Guide

> Purpose of this document: assess whether this repo is useful for building an
> **LLM assistant for legal interpretation** (discovering and explaining the
> implications of legislation as a workflow), and give a concrete plan for
> reusing **only the Generative LLM (GPT) approach** — no BERT fine-tuning, no
> rule-based method — in a way that is flexible across any/multiple LLMs.

---

## 1. What this tool actually is

**FlintFiller-SRL** (from TNO) automates one narrow, mechanical step in legal
formalization: **turning sentences of legislation into structured "FLINT act
frames"** by identifying the semantic roles in each sentence.

It is **not** a reasoning engine, a legal Q&A system, or an interpretation tool.
It is an **extraction / structuring** pipeline:

```
Legal text (Dutch law)  →  Semantic Role Labeling  →  FLINT frames (JSON)
   "De minister verleent    each word tagged as        {act, actor, action,
    de vreemdeling..."       ACTOR/V/OBJ/REC/O          object, recipient, ...}
```

**Semantic Role Labeling (SRL)** here means every word is classified into one of
five roles:

- `ACTOR` – who performs the action (the volitional causer)
- `V` (action) – the main verb + auxiliaries/modals
- `OBJ` – the direct object / entity affected
- `REC` – the recipient / beneficiary
- `O` – everything else (and everything in subordinate clauses)

**FLINT** is a formal legal-modeling language (TNO / Van Engers, the
Calculemus / eFLINT line of work) for expressing *normative systems* — who
**may** or **must** do what to whom. A FLINT model is made of **acts**,
**facts**, and **duties**. This tool auto-fills the *act* part from raw text so a
human modeler doesn't have to build every frame by hand.

## 2. What's in the repo

Three interchangeable backends that all produce the same role labeling:

| Approach | Files | How it works |
|---|---|---|
| **Fine-tuned BERTje** (main) | `src/label_text.py`, `src/main.py` | A Dutch BERT model fine-tuned on annotated law texts tags each token. |
| **Generative LLM (GPT)** | `src/label_text_gpt/` | Queries GPT-4 / GPT-3.5 via prompting, few-shot, or function-calling. |
| Rule-based | (separate repo `flintfiller-rlb`) | Not in this repo. |

`src/labels_to_frames.py` converts labeled sentences into FLINT JSON:

```json
{
  "acts": [
    {"act": "...", "actor": "...", "action": "...", "object": "...",
     "recipient": "...", "preconditions": {"expression": "LITERAL", "operand": true},
     "create": [], "terminate": [], "sources": [], "explanation": ""}
  ],
  "facts": [],
  "duties": []
}
```

The rest of the repo (~90% of files) is research scaffolding: annotation
datasets, fine-tuning scripts, evaluation notebooks, agreement metrics, and GPT
experiment result CSVs. It supports a published paper, not a product.

## 3. Is it useful for your goal?

Your goal — *"an LLM assistant that discovers and explains legislation
implications as a workflow"* — breaks into three parts:

| Your need | Does FlintFiller help? |
|---|---|
| **Structure legislation** into machine-readable form | ✅ **Yes, directly.** Extracting actor/action/object/recipient is a solid first step for any downstream legal reasoning, and FLINT frames are a principled target representation. |
| **Discover implications** (what follows from a norm) | ⚠️ **Not directly.** FlintFiller only *extracts* frames. Deriving implications needs a normative reasoning engine — this is what **eFLINT** does; FlintFiller produces eFLINT's *input*. |
| **Explain** the interpretation | ❌ **No.** The `explanation` field exists in the schema but is always left empty. No natural-language explanation is generated. |

**Bottom line:** useful as the **"parse law into normative structure" stage** of
your workflow. It is not a foundation for the interpretation or explanation
stages — you build those yourself (and eFLINT, or an LLM, handles
implication-derivation and explanation).

**Caveats:** Shallow FLINT output (only `acts` filled,
`facts`/`duties`/`preconditions`/`sources` are placeholders); the "act" is a
naive concatenation of action+object; one sentence at a time; no cross-reference,
condition, or exception handling — which is where most legal *implication* lives.

---

## 4. Reusing ONLY the Generative LLM (GPT) approach

Goal: keep the **valuable, reusable intellectual property** — the FLINT role
definitions, the prompt design, the few-shot examples, the output→frame
mapping — and drop everything tied to OpenAI-specific SDKs, BERT, evaluation,
and CSV plumbing. Make the LLM call **provider-agnostic** so you can swap or mix
models (OpenAI, Anthropic Claude, Gemini, local models, …).

### 4.1 What to copy vs. drop

**Copy (as reference — the real IP), from `src/label_text_gpt/`:**

| File | What to extract | Why |
|---|---|---|
| `label_text_function_call_gpt.py` | The `tag_function` JSON schema + `system_instructions_srl` | This is the cleanest, most structured formulation of the SRL task — the role definitions and the output schema. **This is the core asset.** |
| `label_text_funct_few_combi.py` | The few-shot example messages (`message_srl_en` / `message_srl_nl`) + combined system+few-shot prompt | The "function-call + few-shot combo" was the best-performing variant in their paper. The examples are gold-standard annotations. |
| `label_text_few_shot_gpt.py` | The English & Dutch few-shot example blocks | Additional labeled examples if you use plain few-shot instead of function calling. |
| `postprocessing_function_call.py` | `transform_to_dict` / mapping logic (concept only) | Only needed if you must realign roles back to a per-token label list (e.g. for evaluation). **Skip if you build frames directly from the role dict** — see 4.4. |

**Copy from `src/` (frame construction):**

| File | What to extract |
|---|---|
| `labels_to_frames.py` | The frame templates `create_empty_flint_format`, `create_empty_act_frame`, `create_empty_fact_frame`, and `write_flint_frames_to_json`. These define the FLINT JSON contract. Ignore the BERT-`##`-token merging (`remove_bert_separator`, `merge_*`) — not needed for LLM output. |

**Drop entirely:**

- `src/label_text.py`, `src/main.py`, `src/finetune_bertje/`, `src/resource/models/` — BERT.
- `src/evaluation/`, `test/`, all `resource/results_*` CSVs — research/eval.
- `label_text_prompt_gpt.py` — zero-shot; superseded by the few-shot/function-call variants.
- `preprocessing_few_shot.py`, `arrow_to_csv.py` — dataset-format tooling for their experiments.
- All raw-`requests`-to-`api.openai.com` code and `openai==0.27 ChatCompletion` calls — obsolete; you replace these with a provider-agnostic client (4.3).

### 4.2 The two reusable assets, verbatim

**(a) The role schema + instructions** (from `tag_function` / `system_instructions_srl`):

```python
FLINT_ROLE_SCHEMA = {
    "name": "get_sentence_tags",
    "description": (
        "Classify all words in a sentence as part of the action, actor, object, "
        "recipient, or other. Include determiners, adjectives, prepositions, "
        "complementisers, negations and phrasal verbs. Exclude adverbs. "
        "An action consists of the main verb of the sentence and its auxiliaries and modals."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action":    {"type": "array", "items": {"type": "string"},
                          "description": "Main verb of the sentence plus its auxiliaries and modals."},
            "actor":     {"type": "array", "items": {"type": "string"},
                          "description": "The volitional causer / main agent who interacts with the action."},
            "object":    {"type": "array", "items": {"type": "string"},
                          "description": "The direct object; the entity undergoing the effect of the action."},
            "recipient": {"type": "array", "items": {"type": "string"},
                          "description": "The benefactor of the action."},
            "other":     {"type": "array", "items": {"type": "string"},
                          "description": "All words not in the other categories."},
        },
        "required": ["action", "actor", "object", "recipient", "other"],
    },
}

SYSTEM_INSTRUCTIONS = (
    "You are an assistant that classifies all words in the main clause of a "
    "sentence to the roles 'action', 'actor', 'object', 'recipient', or 'other'. "
    "All words in a subordinate clause should be classified as 'other'. "
    "Do not leave words unclassified, and classify each word only once. "
    "Check that all words are classified before returning the result."
)
```

**(b) The few-shot examples** — copy the `message_srl_en` and `message_srl_nl`
lists (each is a sequence of `example_user` / `example_assistant` pairs) from
`label_text_funct_few_combi.py`. These are hand-annotated and are the main reason
the method works well; keep them as a data file (e.g. `few_shot_examples.py` or
JSON).

### 4.3 Provider-agnostic LLM layer (make it flexible across models)

The original code hard-wires OpenAI. Replace the transport with one thin
adapter so any/multiple LLMs can be used. Two viable strategies:

**Option A — one library that speaks to every provider (least code).**
Use a router such as `litellm`, which exposes a single `completion()` call and
supports OpenAI, Anthropic, Gemini, Azure, and local models, including their
tool/function-calling. You get multi-provider for free and only maintain one
call site.

**Option B — a small internal interface you implement per provider (most control).**
Define one method and write a short adapter per backend. All modern providers
(OpenAI, Anthropic Claude, Gemini) support **tool/function calling with a JSON
schema**, so the `FLINT_ROLE_SCHEMA` above maps onto each with minor shape
differences. If you prefer not to use tool-calling at all, you can instead ask
for **strict JSON output** and validate it — that works on every model including
ones without tool APIs.

```python
# llm_client.py — provider-agnostic interface (Option B sketch)
from typing import Protocol
import json

class LLMClient(Protocol):
    def tag_roles(self, sentence: str, examples: list, model: str) -> dict:
        """Return {'action': [...], 'actor': [...], 'object': [...],
                   'recipient': [...], 'other': [...]} for one sentence."""
        ...

# Example: a JSON-mode implementation that works with ANY chat model.
# (Swap the `_chat` body for litellm.completion, the OpenAI SDK, the Anthropic
#  SDK, etc. — the rest is provider-independent.)
class JsonModeClient:
    def __init__(self, chat_fn):
        self._chat = chat_fn  # chat_fn(messages, model) -> assistant text

    def tag_roles(self, sentence, examples, model):
        messages = (
            [{"role": "system", "content": SYSTEM_INSTRUCTIONS},
             {"role": "system", "content":
                 "Return ONLY a JSON object with keys action, actor, object, "
                 "recipient, other, each an array of strings."}]
            + examples
            + [{"role": "user", "content": sentence}]
        )
        raw = self._chat(messages, model)
        return json.loads(raw)
```

Notes carried over from the original code that you should **keep**:
- `temperature = 0` for deterministic labeling.
- A retry/backoff wrapper (the original retries on HTTP 429 with a 5s sleep).
- Defensive JSON parsing (their `process_function_call_gpt_mask` patches
  truncated JSON). Prefer setting a generous `max_tokens` and validating with a
  schema (e.g. `pydantic`) instead of the ad-hoc string patching they used.

### 4.4 From LLM output straight to FLINT frames (skip the per-token detour)

The BERT path needed per-token tuples because the model labels tokens. The LLM
returns role **lists directly**, so you can build the FLINT act frame without the
`labels_to_frames` token-merging code:

```python
# roles_to_frame.py
def role_dict_to_act_frame(roles: dict) -> dict:
    action    = " ".join(roles.get("action", [])).strip()
    obj       = " ".join(roles.get("object", [])).strip()
    return {
        "act":       (action + " " + obj).strip(),
        "actor":     " ".join(roles.get("actor", [])).strip(),
        "action":    action,
        "object":    obj,
        "recipient": " ".join(roles.get("recipient", [])).strip(),
        "preconditions": {"expression": "LITERAL", "operand": True},
        "create": [], "terminate": [], "sources": [],
        "explanation": "",   # <-- fill this with an LLM-generated explanation for YOUR use case
    }

def frames_from_sentences(sentences, client, examples, model):
    acts = [role_dict_to_act_frame(client.tag_roles(s, examples, model))
            for s in sentences]
    acts = [a for a in acts if a["action"]]           # drop empty acts (same rule as original)
    return {"acts": acts, "facts": [], "duties": []}
```

This reproduces the original end-to-end behavior (`main.py` →
`create_frames`) with ~30 lines and no OpenAI/BERT dependencies.

### 4.5 Minimal file set for your project

```
your_project/
├── flint/
│   ├── schema.py              # FLINT_ROLE_SCHEMA + SYSTEM_INSTRUCTIONS   (from 4.2a)
│   ├── few_shot_examples.py   # message_srl_en / message_srl_nl           (from 4.2b)
│   ├── llm_client.py          # provider-agnostic client                  (from 4.3)
│   ├── roles_to_frame.py      # role dict -> FLINT frame                  (from 4.4)
│   └── frame_templates.py     # (optional) empty act/fact/duty templates from labels_to_frames.py
└── requirements: your LLM SDK(s) or `litellm`; optionally `pydantic` for validation
```

You do **not** need: `torch`, `transformers`, `datasets`, `seqeval`,
`nervaluate`, the bundled BERTje models, or the `openai==0.27` pin.

### 4.6 Recommended enhancements for a legal-interpretation assistant

The original tool stops at extraction. To serve *your* goal, extend it:

1. **Fill `explanation`** — after extracting roles, make a second LLM call:
   "Explain in plain language what obligation/power this provision creates, for
   whom, and under what condition." This is the "explain" capability the repo lacks.
2. **Extract conditions/exceptions** — add `preconditions` extraction (the repo
   leaves it `LITERAL true`). This is where legal *implications* live.
3. **Populate `facts` and `duties`**, not just `acts`, to make the FLINT model
   usable by a reasoning engine (eFLINT) for implication discovery.
4. **Multi-model ensembling** — since the client is provider-agnostic, run two
   models and flag disagreements for human review (legal-grade reliability).
5. **Keep `sources`** — attach the article/citation to every frame so
   explanations are traceable to the statute.

