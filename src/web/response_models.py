"""Pydantic response models of the REST API."""

from datetime import date, datetime

from pydantic import BaseModel


class RosfinmonitoringImportResponse(BaseModel):
    """What importing a published-list file did.

    `status` is `imported` (a new snapshot), `unchanged` (the file is the list already
    in the database, so nothing was written) or `error` (the file is not a list, or
    nothing was parsed out of it).
    """

    status: str
    snapshot_id: int | None = None
    snapshot_date: str | None = None
    entries: int = 0
    source_url: str
    detail: str | None = None
    # A new snapshot has no match rows yet, so the candidate query would find nothing
    # until the RF stage runs. The operator is told rather than left to notice.
    rematch_required: bool = False


class AirtableTableSyncResponse(BaseModel):
    """One list's outcome. `error` is set only when that list itself failed, so the
    other three still report what they did; `status` is `skipped` when the operator
    exported no file for it, which leaves the list alone rather than emptying it."""

    created: int
    updated: int
    unchanged: int
    errors: int
    received: int = 0
    status: str = "success"
    error: str | None = None
    # Rows the list no longer has, removed: it is a copy of an Airtable view, and this is
    # how many. Said out loud because a list that quietly shrank is one nobody trusts.
    removed: int = 0
    # Why nothing was removed, when the export was not believable enough to act on. The
    # operator needs this more than the count: it is the case where the button declined.
    removed_blocked: str | None = None


class AirtableSyncResponse(BaseModel):
    """The whole sync: success, partial (some list failed) or failed. `mode` says
    whether the records came from the Airtable API or from exported files."""

    status: str
    mode: str = "api"
    started_at: str
    finished_at: str | None = None
    duration_seconds: float = 0.0
    tables: dict[str, AirtableTableSyncResponse]


# Pydantic models for API responses
class PersonResponse(BaseModel):
    """Person response model."""

    id: int
    canonical_name: str
    normalized_name: str
    matching_key: str
    status: str
    merged_into_id: int | None = None


class PersonAliasResponse(BaseModel):
    """Person alias response model."""

    id: int
    person_id: int
    surface_text: str
    normalized_text: str
    matching_key: str
    origin: str
    confidence: float


class PersecutionClassificationResponse(BaseModel):
    """Persecution classification response model."""

    id: int
    person_id: int
    status: str
    confidence: float
    reasons: list[str]
    evidence_types: list[str]
    classifier_name: str
    classifier_version: str


class CandidateResponse(BaseModel):
    """Candidate response model."""

    person_id: int
    canonical_name: str
    normalized_name: str
    persecution_status: str
    persecution_confidence: float
    persecution_reasons: list[str]
    rosfinmonitoring_status: str
    rosfinmonitoring_match_confidence: float | None = None
    event_count: int = 0
    alias_count: int = 0


class ArticleResponse(BaseModel):
    id: int
    source_name: str
    external_id: str
    title: str
    published_at: str | None
    url: str
    text: str


class EvidenceSpanResponse(BaseModel):
    article_id: int
    source_name: str
    title: str
    url: str
    start_offset: int
    end_offset: int
    text: str


class PersonEventResponse(BaseModel):
    id: int
    event_type: str
    event_date: str | None
    role: str
    confidence: float
    evidence: EvidenceSpanResponse


class LatestRosfinMatchResponse(BaseModel):
    snapshot_id: int
    status: str
    confidence: float
    matched_entry_id: int | None
    matched_entry_name: str | None
    reasons: list[str]


class PersonDetailResponse(BaseModel):
    person: PersonResponse
    aliases: list[PersonAliasResponse]
    persecution: PersecutionClassificationResponse | None
    rosfinmonitoring: LatestRosfinMatchResponse | None
    events: list[PersonEventResponse]


class RosfinmonitoringSnapshotResponse(BaseModel):
    """Rosfinmonitoring snapshot response model."""

    id: int
    snapshot_date: str
    source_url: str
    content_hash: str
    entry_count: int


class RosfinmonitoringEntryResponse(BaseModel):
    """Rosfinmonitoring entry response model."""

    id: int
    snapshot_id: int
    full_name: str
    normalized_name: str
    matching_key: str
    birth_date: str | None = None
    inclusion_reason: str | None = None


class ReviewResponse(BaseModel):
    """Review response model."""

    id: int
    subject_type: str
    subject_id: int
    decision: str | None = None
    confidence: float | None = None
    reason: str | None = None
    reviewer_note: str | None = None
    created_at: str
    reviewed_at: str | None = None


class OperationRunResponse(BaseModel):
    id: int
    operation: str
    title: str
    parameters: dict[str, object]
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    duration_seconds: float | None
    command: list[str]
    return_code: int | None
    stdout: str
    stderr: str
    error: str | None


class AboutResponse(BaseModel):
    """«О системе»: the build stamp, the totals behind the status strip, and the last
    successful operator run. The same data the legacy `/ui/about` page shows."""

    version: str
    tag: str
    commit: str
    built_at: str
    articles: int
    people: int
    last_successful_run_at: datetime | None
    checked_at: datetime


class QueueResponse(BaseModel):
    """What waits for the operator, by queue; `total` is the menu's «Работа» count."""

    total: int
    pairs: int
    unclear_roles: int
    unclear_verdicts: int
    unnamed: int
    junk_holds: int


class LiveOperationResponse(BaseModel):
    """The step running now: its mode and the title the console gives it."""

    mode: str | None
    title: str


class StatusResponse(BaseModel):
    """The legacy status strip and menu counters: the same numbers on every page."""

    articles: int
    people: int
    result: int
    queue: QueueResponse
    latest_monitoring_status: str | None
    live_operation: LiveOperationResponse | None
    next_action: str


class OptionResponse(BaseModel):
    """One choice of a filter: its value, the console's words and, where the list counts
    it, how many rows it would show."""

    value: str
    label: str
    count: int | None = None


class EventCountResponse(BaseModel):
    kind: str
    label: str
    count: int


class EntityArticleResponse(BaseModel):
    """A Criminal Code article of a person; `shared` when every event naming it accuses
    other people too."""

    article: str
    shared: bool


class EntityRowResponse(BaseModel):
    id: int
    key: str
    # Surname first, as the legacy list writes it.
    name: str
    # «ИИ» or «исправлено» when a model gave the name or a person corrected it.
    name_source_label: str | None
    rf_level: str | None
    rf_label: str | None
    role: str | None
    role_label: str | None
    verdict: str | None
    verdict_label: str | None
    regions: list[str]
    articles: list[EntityArticleResponse]
    events: list[EventCountResponse]
    mention_count: int
    article_count: int
    last_published_at: datetime | None
    variants: list[str]
    dossier_url: str


class EntityListResponse(BaseModel):
    """One page of «Все люди» under the filters, with the counts the legacy page shows."""

    items: list[EntityRowResponse]
    total: int
    page: int
    page_size: int
    hidden_in_list: int
    hidden_maybe_listed: int
    roles_known: bool
    roles: list[OptionResponse]
    verdicts: list[OptionResponse]
    regions: list[str]


class PersonLinkResponse(BaseModel):
    key: str
    # Surname first.
    name: str
    dossier_url: str


class PublicationRowResponse(BaseModel):
    id: int
    title: str
    published_at: datetime | None
    source: str
    url: str
    people: list[PersonLinkResponse]
    # People beyond the ones shown.
    more_people: int
    events: list[EventCountResponse]


class SourceCountResponse(BaseModel):
    id: int
    name: str
    count: int


class PublicationListResponse(BaseModel):
    items: list[PublicationRowResponse]
    total: int
    page: int
    page_size: int
    sources: list[SourceCountResponse]


class ArticleMentionsResponse(BaseModel):
    """The people an article names, most mentioned first, and the events found in it."""

    people: list[PersonLinkResponse]
    events: list[EventCountResponse]


class KnownResponse(BaseModel):
    """What the operator's base says of a person."""

    level: str
    label: str
    names: list[str]


class PoliticalArticleResponse(BaseModel):
    article: str
    # On the list of political articles: the legacy page writes it bold.
    political: bool


class PublicationLinkResponse(BaseModel):
    title: str
    url: str
    source: str


class PoliticalRowResponse(BaseModel):
    key: str
    # Surname first for a person, as told for an unnamed figurant.
    name: str
    # The dossier, or the figurant's sentences on «Безымянные» (legacy pages).
    url: str
    unnamed: bool
    done: bool
    news_kind: str | None
    news_label: str | None
    news_reason: str
    # None while the operator's base is not loaded: then nobody is «not in the base».
    known: KnownResponse | None
    not_in_base: bool
    regions: str
    rf_level: str | None
    rf_label: str | None
    rf_entry: str
    rf_included: str | None
    listing: str | None
    awaited: bool
    articles: list[PoliticalArticleResponse]
    basis: str
    basis_quote: str
    memorial: str | None
    first_published_at: datetime | None
    last_published_at: datetime | None
    links: list[PublicationLinkResponse]


class PoliticalListResponse(BaseModel):
    """«Результат» under the filters: one page of rows and every count the filters show."""

    items: list[PoliticalRowResponse]
    total: int
    page: int
    page_size: int
    done_total: int
    awaited: int
    base_loaded: bool
    # The legacy Excel file of exactly these filters, every row, built on the server.
    export_url: str
    periods: list[OptionResponse]
    # Asked with a `queue`: every queue with its count in the period; else empty.
    queues: list[OptionResponse]
    news: list[OptionResponse]
    known: list[OptionResponse]
    who: list[OptionResponse]
    rfm: list[OptionResponse]


class InvestigationFoundResponse(BaseModel):
    key: str
    # Surname first.
    name: str
    role_label: str | None
    article_count: int
    last_published_at: datetime | None


class InvestigationSearchResponse(BaseModel):
    """«Найти человека»: by any form of the name, or the latest political cases."""

    heading: str
    items: list[InvestigationFoundResponse]


class NameFormResponse(BaseModel):
    form: str
    count: int


class RfEntryResponse(BaseModel):
    level: str
    # «ФИО с отчеством» or «имя и фамилия».
    level_label: str
    text: str


class DossierKnownResponse(BaseModel):
    """What the operator's base says, by the name alone; `loaded` False: no base to ask."""

    loaded: bool
    not_in_base: bool
    label: str | None
    names: list[str]
    more: int


class DossierNewsResponse(BaseModel):
    kind: str
    label: str
    reason: str


class ChargeResponse(BaseModel):
    article: str
    parts: list[str]
    shared: bool
    political: bool
    publications: int


class QuoteResponse(BaseModel):
    """An excerpt with the mention or the event's trigger at [start, end); the article
    text offsets open the full text with the same mark."""

    article_id: int
    title: str
    source: str
    published_at: datetime | None
    quote: str
    start: int
    end: int
    text_start: int
    text_end: int


class OrgResponse(BaseModel):
    name: str
    role_label: str


class TimelineItemResponse(BaseModel):
    event_type: str
    label: str
    day: datetime | None
    # False: the text names another year, `day` is the publication's.
    dated: bool
    confidence: float | None
    extractor_label: str
    articles: list[str]
    orgs: list[OrgResponse]
    sources: list[QuoteResponse]


class EvidenceResponse(QuoteResponse):
    url: str
    identification: str | None
    events: list[str]
    articles: list[str]
    others: list[PersonLinkResponse]


class RelatedPersonResponse(BaseModel):
    key: str
    name: str
    shared: int


class DossierResponse(BaseModel):
    """The legacy dossier: who, the system's decisions with their reasons and quotes, the
    list, the charges, the timeline and the publications behind each conclusion."""

    key: str
    name: str
    role: str | None
    role_label: str | None
    role_method_label: str | None
    role_reason: str | None
    role_quote: str | None
    verdict: str | None
    verdict_label: str | None
    verdict_method_label: str | None
    verdict_reason: str | None
    verdict_quote: str | None
    verdict_source_article_id: int | None
    rf_label: str
    rf_maybe: bool
    rf_entries: list[RfEntryResponse]
    rf_expected: str | None
    snapshot_date: datetime | None
    inclusion_attribution: str
    disputes: list[str]
    variants: list[NameFormResponse]
    regions: list[str]
    article_count: int
    mention_count: int
    first_published_at: datetime | None
    last_published_at: datetime | None
    events: list[EventCountResponse]
    name_source_label: str
    gender_label: str | None
    news: DossierNewsResponse | None
    known: DossierKnownResponse
    warnings: list[str]
    charges: list[ChargeResponse]
    timeline: list[TimelineItemResponse]
    timeline_capped: bool
    timeline_limit: int
    publications: list[EvidenceResponse]
    related: list[RelatedPersonResponse]
    graph_url: str


class SentenceRowResponse(BaseModel):
    """One case of «Приговоры» (several rows of one sentence told by several sources), or
    one row a person took out of the counts."""

    row_ids: list[int]
    person: str
    region: str | None
    kind_label: str
    in_absentia: bool
    # «2 г. 6 мес.», «штраф 35 000 ₽» or a dash, as the legacy page writes it.
    term: str
    sentenced_on: str | None
    reason_label: str
    reason_text: str
    article_id: int
    title: str
    # The source the case is shown by; None for a hidden row.
    source: str | None
    more_publications: int
    quote: str


class SentenceListResponse(BaseModel):
    """«Приговоры» under the filters, or (`view=hidden`) what was taken out."""

    view: str
    reason: str
    region: str
    items: list[SentenceRowResponse]
    total: int
    page: int
    page_size: int
    read: int
    cases: int
    hidden: int
    reasons: list[OptionResponse]
    regions: list[str]


class DoneRequest(BaseModel):
    """«обработано» on a person of «Результат»: ticked or not."""

    key: str
    done: bool


class DoneResponse(BaseModel):
    key: str
    done: bool


class ReviewItemResponse(BaseModel):
    key: str
    # Surname first.
    name: str
    # Why the step could not decide.
    reason: str


class ReviewListResponse(BaseModel):
    """A review station: the people a step could not decide, and the operator's choices."""

    title: str
    explanation: str
    choices: list[OptionResponse]
    items: list[ReviewItemResponse]


class RoleDecisionRequest(BaseModel):
    key: str
    role: str


class VerdictDecisionRequest(BaseModel):
    key: str
    verdict: str


class DecisionResponse(BaseModel):
    key: str
    decision: str


class PairSideResponse(BaseModel):
    key: str
    name: str
    role_label: str | None
    rf_label: str | None
    variants: list[NameFormResponse]
    mention_count: int
    article_count: int
    regions: list[str]
    articles: list[EntityArticleResponse]


class PairResponse(BaseModel):
    kind: str
    hint: str
    # Why the check did not merge it itself, when a side could be several people.
    note: str | None
    left: PairSideResponse
    right: PairSideResponse


class PairListResponse(BaseModel):
    """«Пары»: two entities that may be one person, for the operator to decide."""

    kinds: list[OptionResponse]
    open_pairs: int
    items: list[PairResponse]
    total: int
    page: int
    page_size: int
    decided: dict[str, int]


class PairDecisionRequest(BaseModel):
    key_a: str
    key_b: str
    decision: str


class CycleTaskResponse(BaseModel):
    """A manual queue of «Работа»: what waits for the operator and where."""

    key: str
    title: str
    count: int
    # The legacy page of the queue.
    href: str
    description: str


class CycleStepResponse(BaseModel):
    stage: str
    number: int
    label: str
    # done, running, ready or waiting.
    status: str


class LiveRunResponse(BaseModel):
    run_id: int
    title: str


class CycleResponse(BaseModel):
    """«Работа»: the one review to do first, the other queues, and the automatic steps
    with the one press that runs what is left of the round."""

    attention: CycleTaskResponse | None
    tasks: list[CycleTaskResponse]
    steps: list[CycleStepResponse]
    current_stage: str
    live: LiveRunResponse | None
    # The latest run this answer was read for: «Сделать всё» names it, so an old press
    # sent again cannot start a second round.
    latest_run_id: int
    chain_span: str
    # What to ask before «Сделать всё»: an unfinished review, what is deleted, the cost.
    chain_question: str
    chain_note: str
    chain_stopped: str


class CycleStartRequest(BaseModel):
    """«Сделать всё»: the steps from the current one to the end of the round."""

    after: int


class CycleStartResponse(BaseModel):
    run_id: int
    stage: str


class StopResponse(BaseModel):
    run_id: int
    # False when the run had already ended: nothing was changed.
    stopped: bool


class CandidateRowResponse(BaseModel):
    """A row of the candidates' table, as the legacy page and its Excel file have it."""

    person_id: int
    # Surname first.
    name: str
    # The Moscow day of the news the row is about.
    news_day: date | None
    category: str | None
    persecution_confidence: float
    event_count: int
    rosfinmonitoring_status: str
    reasons: list[str]


class CandidateTableResponse(BaseModel):
    """The candidates under the legacy page's filters, in its order; the Excel file of the
    same filters holds every one of `total`."""

    snapshot_id: int
    # The first news day shown; None: every day.
    period_start: date | None
    total: int
    items: list[CandidateRowResponse]
    # The legacy Excel file of exactly these filters, built with them on the server.
    export_url: str
