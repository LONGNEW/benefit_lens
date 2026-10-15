ALTER TABLE `opportunity_registry` ADD `operator_group` text DEFAULT 'national' NOT NULL;--> statement-breakpoint
CREATE INDEX `idx_opportunity_registry_operator_group` ON `opportunity_registry` (`operator_group`);--> statement-breakpoint
ALTER TABLE `opportunity_registry_stage` ADD `operator_group` text DEFAULT 'national' NOT NULL;