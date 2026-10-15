ALTER TABLE `opportunity_registry` ADD `rights_status` text DEFAULT 'review_required' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `production_allowed` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `contract_version` text DEFAULT 'opportunity-legacy' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `condition_sections_json` text DEFAULT '{}' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `required_documents_json` text DEFAULT '[]' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `application_prompts_json` text DEFAULT '[]' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `structured_benefits_json` text DEFAULT '[]' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `opportunity_version_id` text DEFAULT '' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `rights_status` text DEFAULT 'review_required' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `production_allowed` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `contract_version` text DEFAULT 'opportunity-legacy' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `condition_sections_json` text DEFAULT '{}' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `required_documents_json` text DEFAULT '[]' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `application_prompts_json` text DEFAULT '[]' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `structured_benefits_json` text DEFAULT '[]' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `opportunity_version_id` text DEFAULT '' NOT NULL;