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
    "page_context_split",
    "consistency_check_failed",
    "parse_unreachable",
    "parse_http_failed",
    "parse_refused",
    "office_convert_failed",
    "category_item_not_in_summary",
    "unexpected_error",
]
EntityType = Literal["cob", "sub"]
FieldStatus = Literal[
    "present", "missing", "points_to_attachment", "unparsable", "low_confidence", "conflict",
    "unsupported"
]
AnnouncementType = Literal["winning_announcement", "deal_announcement", "unknown"]
PackageMode = Literal["single", "multi", "unclear"]
TableRole = Literal["cob_detail", "cob_summary", "sub_score", "winner", "agency_fee", "other"]
RowGrain = Literal["cob", "supplier", "project", "other"]
Readability = Literal["unknown", "text_extractable", "low_text", "parse_failed", "unsupported",
                 "needs_normalisation", "ocr_needed"]
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
    # Every other amount the model saw for this package but did not pick. Audit only: step 8
    # still receives exactly one package_amount. 22 of 49 packages in the corpus mention the
    # amount in more than one place, and the choice used to be invisible.
    amount_alternatives: list[Amount] = Field(default_factory=list)


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
    # 品目只写在公告概要里的公告（全量 1038 则里 375 则），正文表格与附件都没有这一列。
    # 这里存的是第 1 步模型从公告概要逐项抄下来的原文条目，供第 8 步做归属判断。
    announcement_categories: list[str] = Field(default_factory=list)
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
    page_no: int | None = None


class CandidateEvidence(Model):
    """Provenance below file level. Step 8 cannot recover any of it later.

    The design contract used to stop provenance at the file. Identity resolution between two
    rows needs to know which table and which line each value came from, and who owns a quote,
    so these are stamped at extraction time. They are evidence for the merge model and for
    audit; no rule branches on their wording.
    """

    table_index: int | None = None
    row_text: str | None = None
    # field -> the header text of the column it came from, e.g. {"unit_price": "单价(万元)"}.
    # Amounts are per-cell but the 万元/元 unit is often only written in the column header, and
    # step 7 needs that header to decide whether to multiply. Copied, never interpreted here.
    column_units: dict[str, str] = Field(default_factory=dict)
    quote_supplier: str | None = None     # file level: whose document this is
    bidder_supplier: str | None = None    # row level: which bidder this line belongs to
    winner_supplier: str | None = None    # the award supplier named in this material


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
    evidence: CandidateEvidence | None = None


class PackageAmountObservation(Model):
    """A package-level award amount copied verbatim from an attachment page."""

    package_no: str
    raw_text: str
    amount_yuan: float | None = None
    source_type: str = "pdf"
    file_id: str | None = None
    file_name: str | None = None
    page_no: int | None = None
    file_class: str | None = None


class CandidateFile(Model):
    run_id: str
    status: StepStatus
    candidates: list[Candidate] = Field(default_factory=list)
    failures: list[Failure] = Field(default_factory=list)
    page_quality: list[dict[str, Any]] | None = None
    package_amounts: list[PackageAmountObservation] = Field(default_factory=list)


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
    field_stats: dict[str, FieldStat] = Field(default_factory=list if False else dict)
    missing_fields: list[str] = Field(default_factory=list)
    needs: list[dict[str, str]] = Field(default_factory=list)
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
    fmt: str = "unknown"
    note: str = ""
    digest: str = ""
    native_readable: bool = False
    needs_normalisation: bool = False


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
    # Whose document the screen gate says this is, and whether the document itself is an award
    # notice. Step 6 only looks for a winner supplier when this left it empty.
    quote_supplier: str | None = None
    is_award_notice: bool | None = None


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
    provenance: dict[str, list[str | None]] | None = None


class MergedProjects(Model):
    run_id: str
    status: StepStatus
    projects: list[Project] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    unmatched_summary_rows: list[dict[str, str]] = Field(default_factory=list)
    unassigned_candidates: list[dict[str, str]] = Field(default_factory=list)
    list_sources: dict[str, str] = Field(default_factory=dict)
    amount_origins: dict[str, str] = Field(default_factory=dict)
    alternatives: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    repairs: list[dict[str, Any]] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[Failure] = Field(default_factory=list)
    # Step 8 audit trail: one entry per model delta, accepted or rejected, plus the arithmetic
    # the rules recomputed. Empty when the merge ran on the deterministic baseline.
    merge_decisions: list[dict[str, Any]] = Field(default_factory=list)
    amount_audit: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # One merge call per package. When thinking is on, the model's reasoning is kept here as a
    # truncated audit excerpt keyed by package_no. Nothing branches on it: it exists so a reviewer
    # can read why a delta was proposed. Empty when the package was not asked.
    merge_notes: dict[str, str] = Field(default_factory=dict)


class MergeEvidence(Model):
    """Read-only context assembled in the pipeline for step 8.

    Everything here already exists inside `run_announcement`'s scope; step 8 simply could not
    see it. Keys are strings so the structure survives a round trip through JSON.
    """

    file_names: dict[str, str] = Field(default_factory=dict)
    file_classes: dict[str, str] = Field(default_factory=dict)
    file_quote_suppliers: dict[str, str] = Field(default_factory=dict)
    # file_id -> whether the screen gate read the document itself as an award notice. The supplier
    # such a document names is a winner by definition, which is how a quote file earns the right to
    # supply prices when the HTML never marked anyone as the winner.
    file_award_notices: dict[str, bool] = Field(default_factory=dict)
    # str(table_index) -> {table_role, package_scope, section, headers}
    html_tables: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # "file_id:page_no" -> {file_name, text_head, table_headers}
    attachment_pages: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # package_no -> supplier names the award text names as winners
    winner_hints: dict[str, list[str]] = Field(default_factory=dict)
    # package_no -> every package-level amount observed, from any source
    amount_observations: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


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
    # in-memory only: excluded so 10_run_report.json stays exactly as the contract defines it
    projects: list[dict[str, Any]] | None = Field(default=None, exclude=True)
