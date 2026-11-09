"""Regulatory text samples with a reference eFLINT formalisation (vendored in ``dmi/``).

A :class:`Sample` is a piece of regulatory text (the articles an upstream example scenario relies
on), the application context of that scenario, and the reference modules that formalise those
articles. It feeds both the demo ("Load sample") and the generation-quality evaluation
(:mod:`.qa`), which compares the generated specification with the reference.
"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import as_file, files
from pathlib import Path

from pydantic import BaseModel

DMI = files(__package__) / "dmi"

# Upstream provenance, also stated in dmi/README.md.
DMI_REPOSITORY = "https://gitlab.com/eflint/formalisations/dmi-afsprakenstelsel"
DMI_COMMIT = "f4c8ea358667ed8f495e5ba27ccc2baa19a1e15b"


class Article(BaseModel):
    id: str
    source: str
    module: str
    text: str
    verified_in_pdf: bool | None = None  # quote found verbatim in the published PDF (None: not in it)


class Sample(BaseModel):
    id: str
    title: str
    language: str
    articles: list[str]
    modules: list[str]
    example: str
    domain_context: str

    @property
    def text(self) -> str:
        """The regulatory text given to the generator: the cited articles, in order."""
        arts = articles()
        return "\n\n".join(f"{arts[a].source}\n{arts[a].text}" for a in self.articles)

    def reference_files(self) -> dict[str, str]:
        """``{relative path: eFLINT source}`` of the modules formalising the cited articles."""
        return {m: policy_path(m).read_text(encoding="utf-8") for m in self.modules}

    def example_source(self) -> str:
        return policy_path(self.example).read_text(encoding="utf-8")


def dmi_path(*parts: str) -> Path:
    with as_file(DMI.joinpath(*parts)) as p:
        return Path(p)


def policy_path(relative: str) -> Path:
    return dmi_path("policy", *relative.split("/"))


@lru_cache(maxsize=1)
def articles() -> dict[str, Article]:
    data = json.loads(dmi_path("articles.json").read_text(encoding="utf-8"))
    return {k: Article(id=k, **v) for k, v in data.items()}


@lru_cache(maxsize=1)
def _samples() -> tuple[Sample, ...]:
    data = json.loads(dmi_path("samples.json").read_text(encoding="utf-8"))
    return tuple(Sample.model_validate(s) for s in data["samples"])


def samples() -> list[Sample]:
    return list(_samples())


def get_sample(sample_id: str) -> Sample:
    for s in _samples():
        if s.id == sample_id:
            return s
    raise KeyError(f"no sample {sample_id!r}")
