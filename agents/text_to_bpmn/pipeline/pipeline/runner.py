"""Stage orchestration for Phases 1-4.

Each stage is independently callable and persists its artifact; each depends on
the previous via :meth:`ArtifactStore.require`, so a missing upstream artifact
raises instead of silently continuing (guideline Step 1). BPMN compilation
(Phase 5) plugs in after ``terminology`` in a later step.
"""
from __future__ import annotations

from pathlib import Path

from ..bpmn.compiler import BpmnCompiler, CompiledModel
from ..config import Settings
from ..evidence.extractor import EvidenceExtractor
from ..evidence.models import EvidenceSet
from ..ingestion.base import Document, DocumentLoader
from ..ingestion.chunker import Chunk, SemanticChunker
from ..ingestion.markdown_loader import MarkdownLoader
from ..ingestion.pdf_loader import PdfLoader
from ..ir.models import Process
from ..llm.base import LLMProvider
from ..logic.interpreter import ProcessLogicInterpreter
from ..retrieval.base import EvidenceIndex
from ..retrieval.in_memory import InMemoryEvidenceIndex
from ..terminology.resolver import TerminologyResolver
from .artifacts import ArtifactStore

_LOADERS: list[DocumentLoader] = [MarkdownLoader(), PdfLoader()]


class Pipeline:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        store: ArtifactStore,
        settings: Settings | None = None,
        index: EvidenceIndex | None = None,
        resolver: TerminologyResolver | None = None,
    ):
        self.provider = provider
        self.store = store
        self.settings = settings or Settings.from_env()
        self.index = index or InMemoryEvidenceIndex()
        self.resolver = resolver or TerminologyResolver.from_config()
        self._chunker = SemanticChunker()

    def _meta(self, extra: dict | None = None) -> dict:
        meta = {
            "provider": self.provider.name,
            "prompt_version": self.settings.prompt_version,
            "pipeline_version": self.settings.pipeline_version,
        }
        if extra:
            meta.update(extra)
        return meta

    # --- Stage 1: ingestion ----------------------------------------------------
    def ingest(self, path: Path) -> Document:
        path = Path(path)
        loader = next((ld for ld in _LOADERS if ld.can_load(path)), None)
        if loader is None:
            raise ValueError(f"No loader available for {path.suffix!r}")
        return self._ingest_document(loader.load(path))

    def ingest_text(self, text: str, *, source_document: str = "input.md", doc_id: str = "input") -> Document:
        """Ingest raw markdown/plain text (used by the API for pasted/uploaded text)."""
        document = MarkdownLoader().load_text(text, source_document=source_document, doc_id=doc_id)
        return self._ingest_document(document)

    def _ingest_document(self, document: Document) -> Document:
        self.store.save(
            "document", document,
            meta=self._meta({"document_hash": ArtifactStore.content_hash(document.text)}),
        )
        chunks = self._chunker.chunk(document)
        self.index.index(chunks)
        self.store.save("chunks", chunks, meta=self._meta({"n_chunks": len(chunks)}))
        return document

    # --- Stage 2: evidence (Pass A) -------------------------------------------
    def extract_evidence(self) -> EvidenceSet:
        document = Document.model_validate(self.store.require("document"))
        chunks = [Chunk.model_validate(c) for c in self.store.require("chunks")]
        evidence = EvidenceExtractor(self.provider, self.settings).extract(document, chunks)
        self.store.save("evidence", evidence, meta=self._meta({"n_items": len(evidence.items)}))
        return evidence

    # --- Stage 3: logic interpretation (Pass B) -------------------------------
    def interpret(self, *, process_name: str = "") -> Process:
        evidence = EvidenceSet.model_validate(self.store.require("evidence"))
        process = ProcessLogicInterpreter(self.provider, self.settings).interpret(
            evidence, process_name=process_name
        )
        self.store.save("ir", process, meta=self._meta({"n_elements": len(process.elements)}))
        return process

    # --- Stage 4: terminology grounding ---------------------------------------
    def ground_terminology(self) -> list:
        process = Process.model_validate(self.store.require("ir"))
        annotations = self.resolver.annotate_process(process)
        self.store.save("terminology", annotations, meta=self._meta({"n_annotations": len(annotations)}))
        return annotations

    # --- Stage 5: deterministic BPMN + DMN compilation ------------------------
    def compile_bpmn(self) -> CompiledModel:
        process = Process.model_validate(self.store.require("ir"))
        model = BpmnCompiler().compile(process)
        self.store.save(
            "bpmn", model.bpmn_xml,
            meta=self._meta({"n_nodes": len(model.graph.nodes), "n_flows": len(model.graph.flows)}),
        )
        if model.dmn_xml is not None:
            self.store.save("dmn", model.dmn_xml, meta=self._meta({"n_decisions": len(model.graph.decisions)}))
        return model

    # --- convenience -----------------------------------------------------------
    def run_through_terminology(self, path: Path, *, process_name: str = "") -> Process:
        self.ingest(path)
        self.extract_evidence()
        process = self.interpret(process_name=process_name)
        self.ground_terminology()
        return process

    def run_through_bpmn(self, path: Path, *, process_name: str = "") -> CompiledModel:
        self.run_through_terminology(path, process_name=process_name)
        return self.compile_bpmn()
