BEGIN;
ALTER TABLE benefitlens.opportunity_version ADD COLUMN IF NOT EXISTS evidence_sources_json jsonb NOT NULL DEFAULT '[]';
INSERT INTO benefitlens.criteria_definition (criterion_code, display_name, profile_field, value_type, canonical_unit, allowed_operators, enum_values, criteria_version)
VALUES
('fulltime_student','전일제 대학원생','fullTimeStudent','boolean',NULL,'["eq"]',NULL,'eligibility-1.2.0'),
('online_learning_available','온라인 사전교육 참여 가능','onlineLearningAvailable','boolean',NULL,'["eq"]',NULL,'eligibility-1.2.0'),
('hackathon_available','해커톤 일정 참여 가능','hackathonAvailable','boolean',NULL,'["eq"]',NULL,'eligibility-1.2.0'),
('activity_attendance','전체 활동 일정 참여 가능','activityAttendance','boolean',NULL,'["eq"]',NULL,'eligibility-1.2.0'),
('training_card_eligible','국민내일배움카드 발급 가능','trainingCardEligible','boolean',NULL,'["eq"]',NULL,'eligibility-1.2.0'),
('fulltime_training_available','공고의 전일 교육 참여 가능','fullTimeTrainingAvailable','boolean',NULL,'["eq"]',NULL,'eligibility-1.2.0'),
('coding_study_interest','코딩 학습 참여 희망','codingStudyInterest','boolean',NULL,'["eq"]',NULL,'eligibility-1.2.0')
ON CONFLICT (criterion_code) DO UPDATE SET criteria_version=EXCLUDED.criteria_version;
UPDATE benefitlens.criteria_definition SET criteria_version='eligibility-1.2.0';
COMMIT;
