"""Pydantic models matching the main-route contract. Field names stay as specified."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RunStatus = Literal["pending", "running", "success", "partial", "failed"]
StepStatus = Literal["pending", "running", "success", "partial", "failed", "skipped"]
FailureCode = Literal[
    "no_attachment",
    "attachment_index_miss",
    "unsupported_image",
    "scanned_or_low_text_pdf",
    "encrypted_pdf",
    "watermark_interference",
    "document_parse_failed",
    "package_structure_unclear",
    "llm_schema_invalid",
    "llm_call_failed",
    "no_text_extractable",
    "no_candidate_extracted",
    "field_normalization_failed",
    "merge_conflict_unresolved",
    "page_context_truncated",
    "consistency_check_failed",
    "unexpected_error",
]
EntityType = Literal["cob", "sub"]
FieldStatus = Literal[
    "present", "missing", "points_to_attachment", "low_confidence", "conflict", "unsupported"
]
AnnouncementType = Literal["winning_announcement", "deal_announcement", "unknown"]
PackageMode = Literal["single", "multi", "unclear"]
TableRole = Literal["cob_detail", "cob_summary", "sub_score", "winner", "agency_fee", "other"]
RowGrain = Literal["cob", "supplier", "project", "other"]
Readability = Literal["unknown", "text_extractable", "low_text", "parse_failed", "unsupported"]
FileClass = Literal[
    "award_detail",
    "bid_quote",
    "winner_detail",
    "tender_requirement",
    "qualification",
    "contract",
    "evaluation",
    "unrelated",
    "unknown",
]
ReadStrategy = Literal["skip", "target_pages", "unsupported"]
ExtractionMode = Literal["text", "table", "text_and_table", "unsupported"]
SourceType = Literal["html", "pdf", "docx", "doc", "xlsx"]
MatchType = Literal[
    "exact_code", "exact_path", "exact_name", "normalized_name", "parent_name", "fuzzy", "unmatched"
]


class Model(BaseModel):
    model_config = ConfigDict(extra="allow", protected_namespaces=())


class Failure(Model):
    failure_code: FailureCode
    failure_message: str | None = None
    location: str | None = None


class ModelMetadata(Model):
    model_name: str
    model_version: str | None = None
    prompt_version: str
    latency_ms: int


class Amount(Model):
    raw_text: str | None = None
    amount_yuan: float | None = None
    scope: Literal["package", "announcement"]
    confidence: float | None = None


class PackageUnderstanding(Model):
    package_no: str
    title: str | None = None
    project_id: str
    package_evidence_text: str
    package_amount: Amount | None = None


class AnnouncementUnderstanding(Model):
    run_id: str
    status: StepStatus
    project_name: str | None = None
    purchaser: str | None = None
    source_project_no: str | None = None
    announcement_type: AnnouncementType
    package_mode: PackageMode
    packages: list[PackageUnderstanding] = Field(default_factory=list)
    summary_amount: Amount | None = None
    unclear_reason: str | None = None
    model_metadata: ModelMetadata | None = None
    failures: list[Failure] = Field(default_factory=list)


class HtmlTable(Model):
    table_index: int
    table_role: TableRole
    package_scope: str
    row_grain: RowGrain
    column_mapping: dict[str, str] = Field(default_factory=dict)
    unmapped_columns: list[str] = Field(default_factory=list)
    confidence: float | None = None
    issues: list[str] = Field(default_factory=list)
    status: StepStatus


class HtmlTables(Model):
    run_id: str
    status: StepStatus
    tables: list[HtmlTable] = Field(default_factory=list)
    failures: list[Failure] = Field(default_factory=list)


class Source(Model):
    source_type: SourceType
    file_id: str | None = None


class FieldObservation(Model):
    raw_value: Any = None
    normalized_value: Any = None
    status: FieldStatus
    confidence: float | None = None
    normalization: dict[str, Any] | None = None


class Candidate(Model):
    candidate_id: str
    entity_type: EntityType
    project_id: str | None = None
    package_no: str | None = None
    source: Source
    source_priority: int
    fields: dict[str, FieldObservation]
    issues: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_class: str | None = None


class CandidateFile(Model):
    run_id: str
    status: StepStatus
    candidates: list[Candidate] = Field(default_factory=list)
    failures: list[Failure] = Field(default_factory=list)
    page_quality: list[dict[str, Any]] | None = None


class FieldStat(Model):
    total: int
    present: int
    points_to_attachment: int
    missing: int
    coverage: float


class ProjectPlan(Model):
    project_id: str
    package_no: str
    cob_candidate_ids: list[str] = Field(default_factory=list)
    sub_candidate_ids: list[str] = Field(default_factory=list)
    field_stats: dict[str, FieldStat] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    suspects: list[str] = Field(default_factory=list)
    has_attachment: bool
    needs_attachment: bool
    search_queries: list[str] = Field(default_factory=list)
    status: StepStatus


class ProjectPlans(Model):
    run_id: str
    status: StepStatus
    projects: list[ProjectPlan] = Field(default_factory=list)
    failures: list[Failure] = Field(default_factory=list)


class IndexedFile(Model):
    file_id: str
    display_name: str
    relative_path: str
    mime_type: str | None = None
    extension: str
    page_count: int | None = None
    text_density: float | None = None
    readability: Readability


class AttachmentIndex(Model):
    announcement_id: str
    attachment_directory: str | None = None
    files: list[IndexedFile] = Field(default_factory=list)


class FileDecision(Model):
    file_id: str
    file_class: FileClass
    expected_fields: list[str] = Field(default_factory=list)
    possible_packages: list[str] = Field(default_factory=list)
    priority: float
    read_strategy: ReadStrategy
    reason: str
    failure_code: FailureCode | None = None
    failure_message: str | None = None


class FileDecisions(Model):
    run_id: str
    status: StepStatus
    file_decisions: list[FileDecision] = Field(default_factory=list)
    failures: list[Failure] = Field(default_factory=list)


class PageDecision(Model):
    file_id: str
    page_no: int
    relevance: float
    expected_fields: list[str] = Field(default_factory=list)
    package_scope: str
    extraction_mode: ExtractionMode
    reason: str
    failure_code: FailureCode | None = None
    failure_message: str | None = None


class PageDecisions(Model):
    run_id: str
    status: StepStatus
    page_decisions: list[PageDecision] = Field(default_factory=list)
    failures: list[Failure] = Field(default_factory=list)


class Cob(Model):
    object_name: str
    category_code: str | None = None
    category_name: str | None = None
    category_type: str | None = None
    brand: str | None = None
    product_supplier: str | None = None
    spec_model: str | None = None
    unit_price: float | None = None
    quantity: float | None = None
    unit: str | None = None
    total_price: float | None = None


class Sub(Model):
    supplier_name: str
    score: float | None = None
    is_winner: bool
    cooperative_product_suppliers: list[str] = Field(default_factory=list)


class Project(Model):
    project_id: str
    source_project_no: str | None = None
    project_name: str
    package_no: str
    purchaser: str | None = None
    package_total_amount: float | None = None
    cobs: list[Cob] = Field(default_factory=list)
    subs: list[Sub] = Field(default_factory=list)
    provenance: dict[str, list[str]] | None = None


class MergedProjects(Model):
    run_id: str
    status: StepStatus
    projects: list[Project] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    unmatched_summary_rows: list[dict[str, str]] = Field(default_factory=list)
    unassigned_candidates: list[dict[str, str]] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[Failure] = Field(default_factory=list)


class RunStep(Model):
    step: str
    status: StepStatus
    output_file: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    failure_code: FailureCode | None = None
    failure_message: str | None = None


class RunState(Model):
    run_id: str
    announcement_id: str
    created_at: str
    updated_at: str
    status: RunStatus
    current_step: str
    steps: list[RunStep] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    review_required: bool = False


class RunReport(Model):
    run_id: str
    announcement_id: str
    status: RunStatus
    duration_ms: int
    counts: dict[str, int]
    llm_calls: dict[str, int]
    failure_summary: list[dict[str, Any]]
    review_required: bool
