"""The entity groups and their roles as the stages after processing read them (ADR 0031).

The tables are the processing's (`processing.storage`); another package reads them here,
in its own transaction, and gets values, never ORM records. Many ids at once: a page or
a step asks once for what it shows. The groups' publication evidence is
`processing.evidence.publications`."""

from .events import EventFact as EventFact
from .events import EventMention as EventMention
from .events import EventPerson as EventPerson
from .events import TargetEvent as TargetEvent
from .events import articles_with_events as articles_with_events
from .events import event_counts as event_counts
from .events import event_facts as event_facts
from .events import event_mentions as event_mentions
from .events import event_orgs as event_orgs
from .events import event_people as event_people
from .events import events_dated as events_dated
from .events import events_naming as events_naming
from .events import events_undated as events_undated
from .events import target_events as target_events
from .figurants_articles import ArticlePerson as ArticlePerson
from .figurants_articles import Charge as Charge
from .figurants_articles import FigurantMention as FigurantMention
from .figurants_articles import FigurantProfile as FigurantProfile
from .figurants_articles import charge_articles as charge_articles
from .figurants_articles import charges as charges
from .figurants_articles import digest_relevance as digest_relevance
from .figurants_articles import figurant_mentions as figurant_mentions
from .figurants_articles import figurant_profiles as figurant_profiles
from .figurants_articles import first_charge_quotes as first_charge_quotes
from .figurants_articles import held_article_count as held_article_count
from .figurants_articles import memorial_categories as memorial_categories
from .figurants_articles import people_in as people_in
from .figurants_articles import publication_charges as publication_charges
from .groups_roles import GROUP_ORDERS as GROUP_ORDERS
from .groups_roles import NAME_SEPARATORS as NAME_SEPARATORS
from .groups_roles import Group as Group
from .groups_roles import GroupHit as GroupHit
from .groups_roles import GroupIdentity as GroupIdentity
from .groups_roles import Role as Role
from .groups_roles import SelectionCounts as SelectionCounts
from .groups_roles import all_regions as all_regions
from .groups_roles import any_roles as any_roles
from .groups_roles import by_key as by_key
from .groups_roles import by_name as by_name
from .groups_roles import filtered_group_ids as filtered_group_ids
from .groups_roles import group_count as group_count
from .groups_roles import group_references as group_references
from .groups_roles import groups as groups
from .groups_roles import groups_by_key as groups_by_key
from .groups_roles import groups_named as groups_named
from .groups_roles import groups_named_like as groups_named_like
from .groups_roles import groups_with_role as groups_with_role
from .groups_roles import latest_first_order as latest_first_order
from .groups_roles import ordered_groups as ordered_groups
from .groups_roles import references_by_mentions as references_by_mentions
from .groups_roles import role_count as role_count
from .groups_roles import role_review as role_review
from .groups_roles import roles as roles
from .groups_roles import selection_counts as selection_counts
from .sentences import HiddenSentence as HiddenSentence
from .sentences import ShownSentence as ShownSentence
from .sentences import hidden_sentences as hidden_sentences
from .sentences import sentence_reading_count as sentence_reading_count
from .sentences import shown_sentences as shown_sentences
