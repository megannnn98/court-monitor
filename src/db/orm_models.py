"""All ORM models, one import away: they live by domain in `db.models`.

Importing this module registers every table on `Base.metadata`, which Alembic reads.
"""

from db.models.base import Base
from db.models.entities import (
    EntityGroupChargeRecord,
    EntityGroupMentionRecord,
    EntityGroupRecord,
    EntityGroupRfMatchRecord,
    EntityGroupRoleRecord,
    EntityNameNormalizationRecord,
    EntityPairDecisionRecord,
    EntityRoleAnswerRecord,
)
from db.models.extraction import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    EventEntityMentionRecord,
    ExtractedEventRecord,
)
from db.models.monitoring import (
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
    ParsedArticleRecord,
    Source,
    SourceDocument,
)

__all__ = [
    "ArticleExtractionRunRecord",
    "Base",
    "EntityGroupChargeRecord",
    "EntityGroupMentionRecord",
    "EntityGroupRecord",
    "EntityGroupRfMatchRecord",
    "EntityGroupRoleRecord",
    "EntityMentionRecord",
    "EntityNameNormalizationRecord",
    "EntityPairDecisionRecord",
    "EntityRoleAnswerRecord",
    "EventEntityMentionRecord",
    "ExtractedEventRecord",
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
]
