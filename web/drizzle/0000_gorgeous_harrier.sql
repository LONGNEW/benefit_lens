CREATE TABLE `opportunity_registry` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`organization` text NOT NULL,
	`support` text NOT NULL,
	`category` text NOT NULL,
	`rules_json` text NOT NULL,
	`caveat` text,
	`source_url` text NOT NULL,
	`source_updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `parking_registry` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`address` text NOT NULL,
	`available` integer NOT NULL,
	`occupied` integer NOT NULL,
	`capacity` integer NOT NULL,
	`occupancy_rate` real NOT NULL,
	`latitude` real NOT NULL,
	`longitude` real NOT NULL,
	`collected_at` text NOT NULL,
	`quality_status` text NOT NULL
);
