-- Cover foreign keys introduced by the Vorquel Knowledge Store.
-- These indexes keep approval/retrieval joins cheap as the store grows.

create index knowledge_items_candidate_source_idx
  on vorquel_knowledge.knowledge_items (candidate_id, source_id);

create index knowledge_items_source_idx
  on vorquel_knowledge.knowledge_items (source_id);

create index knowledge_reviews_candidate_idx
  on vorquel_knowledge.knowledge_reviews (candidate_id);

create index knowledge_sources_candidate_source_idx
  on vorquel_knowledge.knowledge_sources (candidate_id, source_id);

create index knowledge_sources_item_source_idx
  on vorquel_knowledge.knowledge_sources (knowledge_id, source_id);

create index knowledge_sources_transcript_idx
  on vorquel_knowledge.knowledge_sources (transcript_id);

create index knowledge_sources_segment_idx
  on vorquel_knowledge.knowledge_sources (segment_id);

create index knowledge_sources_screen_observation_idx
  on vorquel_knowledge.knowledge_sources (screen_observation_id);
