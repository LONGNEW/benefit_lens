import { index, integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

const opportunityColumns = () => ({
  id: text("id").primaryKey(),
  name: text("name").notNull(),
  organization: text("organization").notNull(),
  support: text("support").notNull(),
  category: text("category").notNull(),
  sourceGroup: text("source_group").notNull().default("national"),
  operatorGroup: text("operator_group").notNull().default("national"),
  rulesJson: text("rules_json").notNull(),
  criteriaJson: text("criteria_json").notNull().default("null"),
  unresolvedCriteriaJson: text("unresolved_criteria_json").notNull().default("[]"),
  caveat: text("caveat"),
  sourceUrl: text("source_url").notNull(),
  sourceUpdatedAt: text("source_updated_at").notNull(),
  summary: text("summary").notNull().default(""),
  applicationUrl: text("application_url"),
  eligibilityJson: text("eligibility_json").notNull().default("[]"),
  benefitType: text("benefit_type").notNull().default("unknown"),
  lifecycleStatus: text("lifecycle_status").notNull().default("unknown"),
  publicationStatus: text("publication_status").notNull().default("needs_review"),
  rightsStatus: text("rights_status").notNull().default("review_required"),
  productionAllowed: integer("production_allowed").notNull().default(0),
  contractVersion: text("contract_version").notNull().default("opportunity-legacy"),
  conditionSectionsJson: text("condition_sections_json").notNull().default("{}"),
  requiredDocumentsJson: text("required_documents_json").notNull().default("[]"),
  applicationPromptsJson: text("application_prompts_json").notNull().default("[]"),
  structuredBenefitsJson: text("structured_benefits_json").notNull().default("[]"),
  opportunityVersionId: text("opportunity_version_id").notNull().default(""),
  sourceId: text("source_id").notNull().default("fixture"),
  matchMode: text("match_mode").notNull().default("structured"),
  criteriaVersion: text("criteria_version").notNull().default("eligibility-1.0.1"),
  structuredRuleCount: integer("structured_rule_count").notNull().default(0),
  unresolvedRuleCount: integer("unresolved_rule_count").notNull().default(0),
  opensAt: text("opens_at"),
  closesAt: text("closes_at"),
  recordKind: text("record_kind").notNull().default("notice"),
  presentationJson: text("presentation_json").notNull().default("{}"),
  evidenceSourcesJson: text("evidence_sources_json").notNull().default("[]"),
  fundingTagsJson: text("funding_tags_json").notNull().default("[]"),
  fundingEvidenceJson: text("funding_evidence_json").notNull().default("[]"),
});

export const opportunityRegistry = sqliteTable("opportunity_registry", opportunityColumns(), (table) => [
  index("idx_opportunity_registry_lifecycle").on(table.lifecycleStatus),
  index("idx_opportunity_registry_source_group").on(table.sourceGroup),
  index("idx_opportunity_registry_operator_group").on(table.operatorGroup),
  index("idx_opportunity_registry_benefit_type").on(table.benefitType),
]);

export const opportunityRegistryStage = sqliteTable("opportunity_registry_stage", opportunityColumns());

export const syncState = sqliteTable("sync_state", {
  resource: text("resource").primaryKey(),
  datasetVersion: text("dataset_version").notNull(),
  expectedCount: integer("expected_count").notNull(),
  loadedCount: integer("loaded_count").notNull().default(0),
  status: text("status").notNull(),
  updatedAt: text("updated_at").notNull(),
});
