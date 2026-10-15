DROP TABLE `parking_registry`;--> statement-breakpoint
DROP TABLE `parking_registry_stage`;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `source_group` text DEFAULT 'national' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `criteria_json` text DEFAULT 'null' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `unresolved_criteria_json` text DEFAULT '[]' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `criteria_version` text DEFAULT 'eligibility-1.0.1' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `structured_rule_count` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `unresolved_rule_count` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `opens_at` text;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `closes_at` text;--> statement-breakpoint
CREATE INDEX `idx_opportunity_registry_source_group` ON `opportunity_registry` (`source_group`);--> statement-breakpoint
CREATE INDEX `idx_opportunity_registry_benefit_type` ON `opportunity_registry` (`benefit_type`);--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `source_group` text DEFAULT 'national' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `criteria_json` text DEFAULT 'null' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `unresolved_criteria_json` text DEFAULT '[]' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `criteria_version` text DEFAULT 'eligibility-1.0.1' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `structured_rule_count` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `unresolved_rule_count` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `opens_at` text;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `closes_at` text;