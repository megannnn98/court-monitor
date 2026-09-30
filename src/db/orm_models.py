"""All ORM models, one import away: they live by domain in `db.models`.

Importing this module registers every table on `Base.metadata`, which Alembic reads.
"""

from db.models.base import Base
from db.models.entities import (
    EntityGroupChargeRecord,
    EntityGroupMentionRecord,
    EntityGroupNewsRecord,
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
    EntityGroupRfMatchRecord,
    EntityGroupRoleRecord,
    EntityGroupUnnamedMentionRecord,
    EntityNameNormalizationRecord,
    EntityNameOverrideRecord,
    EntityNewsAnswerRecord,
    EntityOfficialMarkRecord,
    EntityPairDecisionRecord,
    EntityPoliticsAnswerRecord,
    EntityPoliticsDecisionRecord,
    EntityRoleAnswerRecord,
    EntityRoleDecisionRecord,
    UnnamedAnswerRecord,
    UnnamedDecisionRecord,
    UnnamedFigurantRecord,
    UnnamedIdentityResolutionRecord,
)
from db.models.extraction import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    EventEntityMentionRecord,
    ExtractedEventRecord,
)
from db.models.monitoring import (
    JunkScreenHoldRecord,
    MonitoringFindingRecord,
    MonitoringRunItemRecord,
    MonitoringRunRecord,
    SourceMonitoringStateRecord,
)
from db.models.operations import (
    OperatorOperationRunRecord,
)
from db.models.persecution import (
    PersecutionClassificationRecord,
)
from db.models.persons import (
    PersonAliasRecord,
    PersonEventLinkRecord,
    PersonMergeRecord,
    PersonRecord,
    PersonResolutionAiReviewRecord,
    PersonResolutionDecisionRecord,
    ReviewRecordModel,
)
from db.models.reference import (
    AirtableKnownPersonRecord,
    ExcludedPersonRecord,
)
from db.models.rosfinmonitoring import (
    RosfinMatchRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from db.models.semantic import (
    SemanticDocumentRecord,
    SemanticIndexStateRecord,
    SemanticVectorCollectionRecord,
    SemanticVectorRecord,
)
from db.models.sources import (
    ArticleDigestAnswerRecord,
    ParsedArticleRecord,
    Source,
    SourceDocument,
)

__all__ = [
    "AirtableKnownPersonRecord",
    "ArticleDigestAnswerRecord",
    "ArticleExtractionRunRecord",
    "Base",
    "EntityGroupChargeRecord",
    "EntityGroupMentionRecord",
    "EntityGroupNewsRecord",
    "EntityGroupPoliticsRecord",
    "EntityGroupRecord",
    "EntityGroupRfMatchRecord",
    "EntityGroupRoleRecord",
    "EntityGroupUnnamedMentionRecord",
    "EntityMentionRecord",
    "EntityNameNormalizationRecord",
    "EntityNameOverrideRecord",
    "EntityNewsAnswerRecord",
    "EntityOfficialMarkRecord",
    "EntityPairDecisionRecord",
    "EntityPoliticsAnswerRecord",
    "EntityPoliticsDecisionRecord",
    "EntityRoleAnswerRecord",
    "EntityRoleDecisionRecord",
    "EventEntityMentionRecord",
    "ExcludedPersonRecord",
    "ExtractedEventRecord",
    "JunkScreenHoldRecord",
    "MonitoringFindingRecord",
    "MonitoringRunItemRecord",
    "MonitoringRunRecord",
    "OperatorOperationRunRecord",
    "ParsedArticleRecord",
    "PersecutionClassificationRecord",
    "PersonAliasRecord",
    "PersonEventLinkRecord",
    "PersonMergeRecord",
    "PersonRecord",
    "PersonResolutionAiReviewRecord",
    "PersonResolutionDecisionRecord",
    "ReviewRecordModel",
    "RosfinMatchRecord",
    "RosfinmonitoringEntryRecord",
    "RosfinmonitoringSnapshotRecord",
    "SemanticDocumentRecord",
    "SemanticIndexStateRecord",
    "SemanticVectorCollectionRecord",
    "SemanticVectorRecord",
    "Source",
    "SourceDocument",
    "SourceMonitoringStateRecord",
    "UnnamedAnswerRecord",
    "UnnamedDecisionRecord",
    "UnnamedFigurantRecord",
    "UnnamedIdentityResolutionRecord",
]
