ALTER TABLE `opportunity_registry` ADD `funding_tags_json` text DEFAULT '[]' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `funding_evidence_json` text DEFAULT '[]' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `funding_tags_json` text DEFAULT '[]' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `funding_evidence_json` text DEFAULT '[]' NOT NULL;