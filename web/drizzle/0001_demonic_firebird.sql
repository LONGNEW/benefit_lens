CREATE TABLE `opportunity_registry_stage` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`organization` text NOT NULL,
	`support` text NOT NULL,
	`category` text NOT NULL,
	`rules_json` text NOT NULL,
	`caveat` text,
	`source_url` text NOT NULL,
	`source_updated_at` text NOT NULL,
	`summary` text DEFAULT '' NOT NULL,
	`application_url` text,
	`eligibility_json` text DEFAULT '[]' NOT NULL,
	`benefit_type` text DEFAULT 'unknown' NOT NULL,
	`lifecycle_status` text DEFAULT 'unknown' NOT NULL,
	`publication_status` text DEFAULT 'needs_review' NOT NULL,
	`source_id` text DEFAULT 'fixture' NOT NULL,
	`match_mode` text DEFAULT 'structured' NOT NULL
);
--> statement-breakpoint
CREATE TABLE `parking_registry_stage` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`address` text NOT NULL,
	`available` integer NOT NULL,
	`occupied` integer NOT NULL,
	`capacity` integer NOT NULL,
	`occupancy_rate` real NOT NULL,
	`latitude` real,
	`longitude` real,
	`collected_at` text NOT NULL,
	`quality_status` text NOT NULL,
	`city_code` text DEFAULT 'unknown' NOT NULL,
	`source_id` text DEFAULT 'fixture' NOT NULL,
	`freshness_status` text DEFAULT 'unknown' NOT NULL
);
--> statement-breakpoint
CREATE TABLE `sync_state` (
	`resource` text PRIMARY KEY NOT NULL,
	`dataset_version` text NOT NULL,
	`expected_count` integer NOT NULL,
	`loaded_count` integer DEFAULT 0 NOT NULL,
	`status` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
PRAGMA foreign_keys=OFF;--> statement-breakpoint
CREATE TABLE `__new_parking_registry` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`address` text NOT NULL,
	`available` integer NOT NULL,
	`occupied` integer NOT NULL,
	`capacity` integer NOT NULL,
	`occupancy_rate` real NOT NULL,
	`latitude` real,
	`longitude` real,
	`collected_at` text NOT NULL,
	`quality_status` text NOT NULL,
	`city_code` text DEFAULT 'unknown' NOT NULL,
	`source_id` text DEFAULT 'fixture' NOT NULL,
	`freshness_status` text DEFAULT 'unknown' NOT NULL
);
--> statement-breakpoint
INSERT INTO `__new_parking_registry`("id", "name", "address", "available", "occupied", "capacity", "occupancy_rate", "latitude", "longitude", "collected_at", "quality_status", "city_code", "source_id", "freshness_status") SELECT "id", "name", "address", "available", "occupied", "capacity", "occupancy_rate", "latitude", "longitude", "collected_at", "quality_status", 'unknown', 'fixture', 'unknown' FROM `parking_registry`;--> statement-breakpoint
DROP TABLE `parking_registry`;--> statement-breakpoint
ALTER TABLE `__new_parking_registry` RENAME TO `parking_registry`;--> statement-breakpoint
PRAGMA foreign_keys=ON;--> statement-breakpoint
CREATE INDEX `idx_parking_registry_city_code` ON `parking_registry` (`city_code`);--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `summary` text DEFAULT '' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `application_url` text;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `eligibility_json` text DEFAULT '[]' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `benefit_type` text DEFAULT 'unknown' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `lifecycle_status` text DEFAULT 'unknown' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `publication_status` text DEFAULT 'needs_review' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `source_id` text DEFAULT 'fixture' NOT NULL;--> statement-breakpoint
ALTER TABLE `opportunity_registry` ADD `match_mode` text DEFAULT 'structured' NOT NULL;--> statement-breakpoint
CREATE INDEX `idx_opportunity_registry_lifecycle` ON `opportunity_registry` (`lifecycle_status`);
--> statement-breakpoint
PRAGMA optimize;
