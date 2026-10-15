ALTER TABLE `opportunity_registry` ADD `record_kind` text DEFAULT 'notice' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `presentation_json` text DEFAULT '{}' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `record_kind` text DEFAULT 'notice' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `presentation_json` text DEFAULT '{}' NOT NULL;