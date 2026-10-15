--
-- PostgreSQL database dump
--

\restrict omLIscpvPF0gFBkbfYHT89Wv1cK7aLrgKkODjaNZn3bLKWsZ9hKtRyeMEUSfBb5

-- Dumped from database version 16.15
-- Dumped by pg_dump version 16.15

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: benefitlens; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA benefitlens;


--
-- Name: platform; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA platform;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: application_prompt; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.application_prompt (
    application_prompt_id uuid NOT NULL,
    opportunity_version_id uuid NOT NULL,
    prompt_key text NOT NULL,
    prompt_text text NOT NULL,
    character_limit integer,
    required_elements jsonb DEFAULT '[]'::jsonb NOT NULL,
    evidence_span_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    source_url text,
    CONSTRAINT application_prompt_character_limit_check CHECK ((character_limit > 0))
);


--
-- Name: benefit; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.benefit (
    benefit_id bigint NOT NULL,
    opportunity_version_id uuid NOT NULL,
    benefit_type text NOT NULL,
    description text NOT NULL,
    amount_min numeric,
    amount_max numeric,
    amount_unit text,
    recurrence text,
    repayable boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    currency text,
    amount_qualifier text,
    CONSTRAINT benefit_benefit_type_check CHECK ((benefit_type = ANY (ARRAY['cash'::text, 'education_cost'::text, 'housing_finance'::text, 'goods'::text, 'service'::text, 'opportunity'::text, 'unknown'::text])))
);


--
-- Name: benefit_benefit_id_seq; Type: SEQUENCE; Schema: benefitlens; Owner: -
--

CREATE SEQUENCE benefitlens.benefit_benefit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: benefit_benefit_id_seq; Type: SEQUENCE OWNED BY; Schema: benefitlens; Owner: -
--

ALTER SEQUENCE benefitlens.benefit_benefit_id_seq OWNED BY benefitlens.benefit.benefit_id;


--
-- Name: claim_provenance; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.claim_provenance (
    claim_provenance_id bigint NOT NULL,
    draft_claim_id bigint NOT NULL,
    provenance_kind text NOT NULL,
    evidence_span_id bigint,
    experience_fact_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT claim_provenance_check CHECK ((((provenance_kind = 'official_requirement'::text) AND (evidence_span_id IS NOT NULL) AND (experience_fact_id IS NULL)) OR ((provenance_kind = 'experience_fact'::text) AND (evidence_span_id IS NULL) AND (experience_fact_id IS NOT NULL)) OR ((provenance_kind = 'needs_confirmation'::text) AND (evidence_span_id IS NULL) AND (experience_fact_id IS NULL)))),
    CONSTRAINT claim_provenance_provenance_kind_check CHECK ((provenance_kind = ANY (ARRAY['official_requirement'::text, 'experience_fact'::text, 'needs_confirmation'::text])))
);


--
-- Name: claim_provenance_claim_provenance_id_seq; Type: SEQUENCE; Schema: benefitlens; Owner: -
--

CREATE SEQUENCE benefitlens.claim_provenance_claim_provenance_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: claim_provenance_claim_provenance_id_seq; Type: SEQUENCE OWNED BY; Schema: benefitlens; Owner: -
--

ALTER SEQUENCE benefitlens.claim_provenance_claim_provenance_id_seq OWNED BY benefitlens.claim_provenance.claim_provenance_id;


--
-- Name: criteria_definition; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.criteria_definition (
    criterion_code text NOT NULL,
    display_name text NOT NULL,
    profile_field text NOT NULL,
    value_type text NOT NULL,
    canonical_unit text,
    allowed_operators jsonb NOT NULL,
    enum_values jsonb,
    criteria_version text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    condition_kind text DEFAULT 'eligibility'::text NOT NULL,
    CONSTRAINT criteria_definition_condition_kind_check CHECK ((condition_kind = ANY (ARRAY['eligibility'::text, 'requirement'::text]))),
    CONSTRAINT criteria_definition_value_type_check CHECK ((value_type = ANY (ARRAY['string'::text, 'integer'::text, 'number'::text, 'boolean'::text, 'enum'::text, 'date'::text])))
);


--
-- Name: discovery_hit; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.discovery_hit (
    discovery_hit_id bigint NOT NULL,
    query_text text NOT NULL,
    result_url text NOT NULL,
    discovered_at timestamp with time zone NOT NULL,
    official_source_id text,
    verification_status text NOT NULL,
    CONSTRAINT discovery_hit_verification_status_check CHECK ((verification_status = ANY (ARRAY['unverified'::text, 'official_confirmed'::text, 'rejected'::text])))
);


--
-- Name: discovery_hit_discovery_hit_id_seq; Type: SEQUENCE; Schema: benefitlens; Owner: -
--

CREATE SEQUENCE benefitlens.discovery_hit_discovery_hit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: discovery_hit_discovery_hit_id_seq; Type: SEQUENCE OWNED BY; Schema: benefitlens; Owner: -
--

ALTER SEQUENCE benefitlens.discovery_hit_discovery_hit_id_seq OWNED BY benefitlens.discovery_hit.discovery_hit_id;


--
-- Name: draft_claim; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.draft_claim (
    draft_claim_id bigint NOT NULL,
    draft_version_id uuid NOT NULL,
    sentence_index integer NOT NULL,
    claim_text text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT draft_claim_sentence_index_check CHECK ((sentence_index >= 0))
);


--
-- Name: draft_claim_draft_claim_id_seq; Type: SEQUENCE; Schema: benefitlens; Owner: -
--

CREATE SEQUENCE benefitlens.draft_claim_draft_claim_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: draft_claim_draft_claim_id_seq; Type: SEQUENCE OWNED BY; Schema: benefitlens; Owner: -
--

ALTER SEQUENCE benefitlens.draft_claim_draft_claim_id_seq OWNED BY benefitlens.draft_claim.draft_claim_id;


--
-- Name: draft_version; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.draft_version (
    draft_version_id uuid NOT NULL,
    application_prompt_id uuid NOT NULL,
    writing_style_profile_id uuid,
    generator_kind text NOT NULL,
    generator_name text NOT NULL,
    draft_text text NOT NULL,
    user_confirmed boolean DEFAULT false NOT NULL,
    retention_until timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT draft_version_check CHECK ((retention_until > created_at)),
    CONSTRAINT draft_version_generator_kind_check CHECK ((generator_kind = ANY (ARRAY['deterministic_baseline'::text, 'llm'::text])))
);


--
-- Name: eligibility_rule; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.eligibility_rule (
    eligibility_rule_id bigint NOT NULL,
    opportunity_version_id uuid NOT NULL,
    rule_type text NOT NULL,
    operator text,
    value_json jsonb,
    rule_text text NOT NULL,
    machine_evaluable boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    rule_key text,
    rule_group_id uuid,
    criterion_code text,
    profile_field text,
    value_type text,
    unit text,
    normalization_status text DEFAULT 'unresolved'::text NOT NULL,
    "position" integer DEFAULT 0 NOT NULL,
    display_label text,
    context_json jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: opportunity_version; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.opportunity_version (
    opportunity_version_id uuid NOT NULL,
    opportunity_id uuid NOT NULL,
    run_id uuid NOT NULL,
    document_version_id bigint NOT NULL,
    content_fingerprint_sha256 character(64) NOT NULL,
    title text NOT NULL,
    summary text,
    official_url text NOT NULL,
    application_url text,
    opens_at timestamp with time zone,
    closes_at timestamp with time zone,
    lifecycle_status text NOT NULL,
    publication_status text DEFAULT 'needs_review'::text NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_to timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    criteria_version text DEFAULT 'eligibility-1.0.1'::text NOT NULL,
    record_kind text DEFAULT 'notice'::text NOT NULL,
    presentation_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    evidence_sources_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    contract_version text DEFAULT 'opportunity-legacy'::text NOT NULL,
    condition_sections_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT opportunity_version_check CHECK (((valid_to IS NULL) OR (valid_to > valid_from))),
    CONSTRAINT opportunity_version_lifecycle_status_check CHECK ((lifecycle_status = ANY (ARRAY['upcoming'::text, 'open'::text, 'closed'::text, 'rolling'::text, 'unknown'::text]))),
    CONSTRAINT opportunity_version_publication_status_check CHECK ((publication_status = ANY (ARRAY['needs_review'::text, 'reviewed'::text, 'rejected'::text, 'quarantined'::text]))),
    CONSTRAINT opportunity_version_record_kind_check CHECK ((record_kind = ANY (ARRAY['notice'::text, 'policy'::text])))
);


--
-- Name: eligibility_coverage; Type: VIEW; Schema: benefitlens; Owner: -
--

CREATE VIEW benefitlens.eligibility_coverage AS
 SELECT v.opportunity_version_id,
    v.criteria_version,
    count(r.eligibility_rule_id) FILTER (WHERE (r.machine_evaluable AND (d.condition_kind = 'eligibility'::text))) AS structured_rule_count,
    count(r.eligibility_rule_id) FILTER (WHERE ((NOT r.machine_evaluable) AND (r.normalization_status = 'unresolved'::text))) AS unresolved_rule_count,
        CASE
            WHEN (count(r.eligibility_rule_id) FILTER (WHERE r.machine_evaluable) = 0) THEN 'review_required'::text
            WHEN (count(r.eligibility_rule_id) FILTER (WHERE ((NOT r.machine_evaluable) AND (r.normalization_status = 'unresolved'::text))) > 0) THEN 'partial'::text
            ELSE 'structured'::text
        END AS match_mode
   FROM ((benefitlens.opportunity_version v
     LEFT JOIN benefitlens.eligibility_rule r USING (opportunity_version_id))
     LEFT JOIN benefitlens.criteria_definition d USING (criterion_code))
  GROUP BY v.opportunity_version_id, v.criteria_version;


--
-- Name: eligibility_rule_eligibility_rule_id_seq; Type: SEQUENCE; Schema: benefitlens; Owner: -
--

CREATE SEQUENCE benefitlens.eligibility_rule_eligibility_rule_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: eligibility_rule_eligibility_rule_id_seq; Type: SEQUENCE OWNED BY; Schema: benefitlens; Owner: -
--

ALTER SEQUENCE benefitlens.eligibility_rule_eligibility_rule_id_seq OWNED BY benefitlens.eligibility_rule.eligibility_rule_id;


--
-- Name: eligibility_rule_group; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.eligibility_rule_group (
    rule_group_id uuid NOT NULL,
    opportunity_version_id uuid NOT NULL,
    group_key text NOT NULL,
    parent_rule_group_id uuid,
    combinator text NOT NULL,
    label text NOT NULL,
    "position" integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT eligibility_rule_group_combinator_check CHECK ((combinator = ANY (ARRAY['and'::text, 'or'::text, 'not'::text]))),
    CONSTRAINT eligibility_rule_group_position_check CHECK (("position" >= 0))
);


--
-- Name: evaluation; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.evaluation (
    evaluation_id uuid NOT NULL,
    profile_snapshot_id uuid NOT NULL,
    opportunity_version_id uuid NOT NULL,
    evaluator_version text NOT NULL,
    eligibility_status text NOT NULL,
    completeness_status text NOT NULL,
    evaluated_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT evaluation_completeness_status_check CHECK ((completeness_status = ANY (ARRAY['complete'::text, 'partial'::text, 'unavailable'::text]))),
    CONSTRAINT evaluation_eligibility_status_check CHECK ((eligibility_status = ANY (ARRAY['possible'::text, 'check'::text, 'unmet'::text])))
);


--
-- Name: evaluation_rule_result; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.evaluation_rule_result (
    evaluation_rule_result_id bigint NOT NULL,
    evaluation_id uuid NOT NULL,
    eligibility_rule_id bigint NOT NULL,
    result text NOT NULL,
    actual_value_json jsonb,
    reason_code text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT evaluation_rule_result_result_check CHECK ((result = ANY (ARRAY['pass'::text, 'fail'::text, 'unknown'::text, 'not_evaluated'::text])))
);


--
-- Name: evaluation_rule_result_evaluation_rule_result_id_seq; Type: SEQUENCE; Schema: benefitlens; Owner: -
--

CREATE SEQUENCE benefitlens.evaluation_rule_result_evaluation_rule_result_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: evaluation_rule_result_evaluation_rule_result_id_seq; Type: SEQUENCE OWNED BY; Schema: benefitlens; Owner: -
--

ALTER SEQUENCE benefitlens.evaluation_rule_result_evaluation_rule_result_id_seq OWNED BY benefitlens.evaluation_rule_result.evaluation_rule_result_id;


--
-- Name: evidence_span; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.evidence_span (
    evidence_span_id bigint NOT NULL,
    opportunity_version_id uuid NOT NULL,
    raw_document_id bigint NOT NULL,
    field_name text NOT NULL,
    evidence_text text NOT NULL,
    char_start integer,
    char_end integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT evidence_span_char_end_check CHECK ((char_end >= 0)),
    CONSTRAINT evidence_span_char_start_check CHECK ((char_start >= 0)),
    CONSTRAINT evidence_span_check CHECK (((char_start IS NULL) OR (char_end IS NULL) OR (char_end >= char_start)))
);


--
-- Name: evidence_span_evidence_span_id_seq; Type: SEQUENCE; Schema: benefitlens; Owner: -
--

CREATE SEQUENCE benefitlens.evidence_span_evidence_span_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: evidence_span_evidence_span_id_seq; Type: SEQUENCE OWNED BY; Schema: benefitlens; Owner: -
--

ALTER SEQUENCE benefitlens.evidence_span_evidence_span_id_seq OWNED BY benefitlens.evidence_span.evidence_span_id;


--
-- Name: experience_discovery_case; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.experience_discovery_case (
    case_key text NOT NULL,
    program_id text NOT NULL,
    person_name text,
    application_round text,
    outcome_claim text NOT NULL,
    evidence_status text NOT NULL,
    source_url text NOT NULL,
    payload jsonb NOT NULL,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: experience_fact; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.experience_fact (
    experience_fact_id uuid NOT NULL,
    subject_key text NOT NULL,
    fact_type text NOT NULL,
    fact_text text NOT NULL,
    source_kind text NOT NULL,
    user_confirmed boolean DEFAULT false NOT NULL,
    confirmed_at timestamp with time zone,
    retention_until timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT experience_fact_check CHECK (((NOT user_confirmed) OR (confirmed_at IS NOT NULL))),
    CONSTRAINT experience_fact_check1 CHECK ((retention_until > created_at)),
    CONSTRAINT experience_fact_source_kind_check CHECK ((source_kind = ANY (ARRAY['user_input'::text, 'uploaded_document'::text])))
);


--
-- Name: exploration_run; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.exploration_run (
    run_id text NOT NULL,
    payload jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: exploration_search_batch; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.exploration_search_batch (
    batch_key text NOT NULL,
    run_id text NOT NULL,
    wave integer NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: exploration_url; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.exploration_url (
    run_id text NOT NULL,
    url text NOT NULL,
    lane text NOT NULL,
    status text NOT NULL,
    payload jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    url_hash text GENERATED ALWAYS AS (md5(url)) STORED NOT NULL
);


--
-- Name: extraction_candidate; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.extraction_candidate (
    extraction_candidate_id bigint NOT NULL,
    opportunity_version_id uuid NOT NULL,
    raw_document_id bigint NOT NULL,
    field_kind text NOT NULL,
    proposed_value jsonb NOT NULL,
    source_url text NOT NULL,
    source_kind text NOT NULL,
    source_quote text NOT NULL,
    confidence numeric(5,4) NOT NULL,
    extractor_kind text DEFAULT 'local_codex'::text NOT NULL,
    model_name text NOT NULL,
    prompt_version text NOT NULL,
    review_status text DEFAULT 'pending'::text NOT NULL,
    candidate_fingerprint_sha256 character(64) NOT NULL,
    reviewed_at timestamp with time zone,
    reviewed_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT extraction_candidate_check CHECK ((((review_status = 'pending'::text) AND (reviewed_at IS NULL)) OR ((review_status = ANY (ARRAY['accepted'::text, 'rejected'::text])) AND (reviewed_at IS NOT NULL)))),
    CONSTRAINT extraction_candidate_confidence_check CHECK (((confidence >= (0)::numeric) AND (confidence <= (1)::numeric))),
    CONSTRAINT extraction_candidate_extractor_kind_check CHECK ((extractor_kind = ANY (ARRAY['local_codex'::text, 'llm_api'::text]))),
    CONSTRAINT extraction_candidate_field_kind_check CHECK ((field_kind = ANY (ARRAY['benefit'::text, 'eligibility'::text, 'required_document'::text, 'application_prompt'::text]))),
    CONSTRAINT extraction_candidate_review_status_check CHECK ((review_status = ANY (ARRAY['pending'::text, 'accepted'::text, 'rejected'::text]))),
    CONSTRAINT extraction_candidate_source_kind_check CHECK ((source_kind = ANY (ARRAY['api'::text, 'html'::text, 'pdf'::text, 'ocr'::text, 'hwp'::text, 'hwpx'::text])))
);


--
-- Name: extraction_candidate_extraction_candidate_id_seq; Type: SEQUENCE; Schema: benefitlens; Owner: -
--

CREATE SEQUENCE benefitlens.extraction_candidate_extraction_candidate_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: extraction_candidate_extraction_candidate_id_seq; Type: SEQUENCE OWNED BY; Schema: benefitlens; Owner: -
--

ALTER SEQUENCE benefitlens.extraction_candidate_extraction_candidate_id_seq OWNED BY benefitlens.extraction_candidate.extraction_candidate_id;


--
-- Name: funding_classification; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.funding_classification (
    opportunity_version_id uuid NOT NULL,
    classifier_version text NOT NULL,
    tags_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    evidence_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    classification_status text NOT NULL,
    reason text NOT NULL,
    classified_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT funding_classification_classification_status_check CHECK ((classification_status = ANY (ARRAY['targeted'::text, 'unconfirmed'::text]))),
    CONSTRAINT funding_classification_evidence_json_check CHECK ((jsonb_typeof(evidence_json) = 'array'::text)),
    CONSTRAINT funding_classification_tags_json_check CHECK ((jsonb_typeof(tags_json) = 'array'::text))
);


--
-- Name: opportunity; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.opportunity (
    opportunity_id uuid NOT NULL,
    provider_id uuid NOT NULL,
    canonical_key text NOT NULL,
    first_seen_at timestamp with time zone NOT NULL,
    retired_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: provider; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.provider (
    provider_id uuid NOT NULL,
    canonical_name text NOT NULL,
    provider_kind text NOT NULL,
    official_url text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT provider_provider_kind_check CHECK ((provider_kind = ANY (ARRAY['central_government'::text, 'local_government'::text, 'public_agency'::text, 'university'::text, 'company'::text, 'corporate_foundation'::text, 'public_foundation'::text, 'association_nonprofit'::text])))
);


--
-- Name: collection_run; Type: TABLE; Schema: platform; Owner: -
--

CREATE TABLE platform.collection_run (
    run_id uuid NOT NULL,
    source_id text NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    run_status text NOT NULL,
    http_status integer,
    request_url_redacted text,
    final_url text,
    content_type text,
    raw_record_count integer,
    normalized_record_count integer,
    raw_payload_sha256 character(64),
    parser_name text,
    parser_version text,
    error_class text,
    error_message_redacted text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT collection_run_normalized_record_count_check CHECK ((normalized_record_count >= 0)),
    CONSTRAINT collection_run_raw_record_count_check CHECK ((raw_record_count >= 0)),
    CONSTRAINT collection_run_run_status_check CHECK ((run_status = ANY (ARRAY['running'::text, 'succeeded'::text, 'quarantined'::text, 'failed'::text, 'blocked'::text])))
);


--
-- Name: data_source; Type: TABLE; Schema: platform; Owner: -
--

CREATE TABLE platform.data_source (
    source_id text NOT NULL,
    provider_name text NOT NULL,
    provider_kind text NOT NULL,
    source_group text NOT NULL,
    source_kind text NOT NULL,
    canonical_url text NOT NULL,
    approval_status text DEFAULT 'not_required'::text NOT NULL,
    rights_status text DEFAULT 'review_required'::text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    schedule_allowed boolean DEFAULT false NOT NULL,
    production_allowed boolean DEFAULT false NOT NULL,
    poll_interval_seconds integer,
    contract_version text DEFAULT '1'::text NOT NULL,
    last_rights_reviewed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT data_source_approval_status_check CHECK ((approval_status = ANY (ARRAY['not_required'::text, 'not_applied'::text, 'applied'::text, 'approved'::text, 'rejected'::text]))),
    CONSTRAINT data_source_poll_interval_seconds_check CHECK ((poll_interval_seconds > 0)),
    CONSTRAINT data_source_rights_status_check CHECK ((rights_status = ANY (ARRAY['review_required'::text, 'approved'::text, 'restricted'::text, 'rejected'::text]))),
    CONSTRAINT data_source_source_group_check CHECK ((source_group = ANY (ARRAY['school'::text, 'foundation'::text, 'company'::text, 'national'::text, 'other'::text]))),
    CONSTRAINT data_source_source_kind_check CHECK ((source_kind = ANY (ARRAY['official_api'::text, 'official_page'::text, 'internal_web_endpoint'::text, 'standard_master'::text, 'public_reference'::text])))
);


--
-- Name: latest_candidate_opportunity; Type: VIEW; Schema: benefitlens; Owner: -
--

CREATE VIEW benefitlens.latest_candidate_opportunity AS
 SELECT DISTINCT ON (o.opportunity_id) o.opportunity_id,
    p.canonical_name AS provider_name,
    p.provider_kind,
    v.opportunity_version_id,
    v.title,
    v.summary,
    v.official_url,
    v.application_url,
    v.opens_at,
    v.closes_at,
    v.lifecycle_status,
    v.publication_status,
    v.valid_from,
    r.source_id,
    s.source_group,
    r.finished_at AS last_collected_at
   FROM ((((benefitlens.opportunity o
     JOIN benefitlens.provider p USING (provider_id))
     JOIN benefitlens.opportunity_version v USING (opportunity_id))
     JOIN platform.collection_run r USING (run_id))
     JOIN platform.data_source s USING (source_id))
  WHERE ((v.valid_to IS NULL) AND (o.retired_at IS NULL))
  ORDER BY o.opportunity_id, v.valid_from DESC;


--
-- Name: latest_publishable_opportunity; Type: VIEW; Schema: benefitlens; Owner: -
--

CREATE VIEW benefitlens.latest_publishable_opportunity AS
 SELECT c.opportunity_id,
    c.provider_name,
    c.provider_kind,
    c.opportunity_version_id,
    c.title,
    c.summary,
    c.official_url,
    c.application_url,
    c.opens_at,
    c.closes_at,
    c.lifecycle_status,
    c.publication_status,
    c.valid_from,
    c.source_id,
    c.source_group,
    c.last_collected_at
   FROM (benefitlens.latest_candidate_opportunity c
     JOIN platform.data_source s USING (source_id))
  WHERE ((c.publication_status = 'reviewed'::text) AND (s.rights_status = 'approved'::text) AND (s.production_allowed = true));


--
-- Name: opportunities; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.opportunities (
    opportunity_id text NOT NULL,
    program_id text NOT NULL,
    round_id text,
    title text NOT NULL,
    category text NOT NULL,
    recipient_type text NOT NULL,
    issuer jsonb NOT NULL,
    eligibility jsonb,
    additional_checks jsonb DEFAULT '[]'::jsonb NOT NULL,
    benefits jsonb DEFAULT '[]'::jsonb NOT NULL,
    application jsonb DEFAULT '{}'::jsonb NOT NULL,
    requirements jsonb DEFAULT '[]'::jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    explanation text NOT NULL,
    extraction_status text NOT NULL,
    resolution_status text NOT NULL,
    extractor text NOT NULL,
    version_hash text NOT NULL,
    payload jsonb NOT NULL,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: opportunity_revision; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.opportunity_revision (
    opportunity_id text NOT NULL,
    version_hash text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pipeline_document; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.pipeline_document (
    document_id text NOT NULL,
    canonical_url text NOT NULL,
    content_sha256 text NOT NULL,
    text_sha256 text NOT NULL,
    kind text NOT NULL,
    raw_path text NOT NULL,
    text_path text NOT NULL,
    fetched_at timestamp with time zone NOT NULL,
    metadata jsonb NOT NULL,
    canonical_url_hash text GENERATED ALWAYS AS (md5(canonical_url)) STORED
);


--
-- Name: pipeline_search_wave; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.pipeline_search_wave (
    run_id text NOT NULL,
    wave integer NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: pipeline_work_item; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.pipeline_work_item (
    candidate_key text NOT NULL,
    source_family text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    reason text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: profile_attribute; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.profile_attribute (
    profile_attribute_id bigint NOT NULL,
    profile_snapshot_id uuid NOT NULL,
    criterion_code text NOT NULL,
    value_json jsonb NOT NULL,
    value_type text NOT NULL,
    unit text,
    user_confirmed boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: profile_attribute_profile_attribute_id_seq; Type: SEQUENCE; Schema: benefitlens; Owner: -
--

CREATE SEQUENCE benefitlens.profile_attribute_profile_attribute_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: profile_attribute_profile_attribute_id_seq; Type: SEQUENCE OWNED BY; Schema: benefitlens; Owner: -
--

ALTER SEQUENCE benefitlens.profile_attribute_profile_attribute_id_seq OWNED BY benefitlens.profile_attribute.profile_attribute_id;


--
-- Name: profile_snapshot; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.profile_snapshot (
    profile_snapshot_id uuid NOT NULL,
    subject_key text NOT NULL,
    criteria_version text NOT NULL,
    consent_version text NOT NULL,
    consented_at timestamp with time zone NOT NULL,
    retention_until timestamp with time zone NOT NULL,
    deleted_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT profile_snapshot_check CHECK ((retention_until > consented_at))
);


--
-- Name: program_knowledge; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.program_knowledge (
    program_id text NOT NULL,
    experience_cases jsonb NOT NULL,
    writing_pattern jsonb NOT NULL,
    version_hash text NOT NULL,
    payload jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: required_document; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.required_document (
    required_document_id bigint NOT NULL,
    opportunity_version_id uuid NOT NULL,
    document_name text NOT NULL,
    requirement_text text NOT NULL,
    evidence_span_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: required_document_required_document_id_seq; Type: SEQUENCE; Schema: benefitlens; Owner: -
--

CREATE SEQUENCE benefitlens.required_document_required_document_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: required_document_required_document_id_seq; Type: SEQUENCE OWNED BY; Schema: benefitlens; Owner: -
--

ALTER SEQUENCE benefitlens.required_document_required_document_id_seq OWNED BY benefitlens.required_document.required_document_id;


--
-- Name: requirement_coverage; Type: VIEW; Schema: benefitlens; Owner: -
--

CREATE VIEW benefitlens.requirement_coverage AS
 SELECT v.opportunity_version_id,
    count(r.eligibility_rule_id) FILTER (WHERE (r.machine_evaluable AND (d.condition_kind = 'requirement'::text))) AS requirement_count
   FROM ((benefitlens.opportunity_version v
     LEFT JOIN benefitlens.eligibility_rule r USING (opportunity_version_id))
     LEFT JOIN benefitlens.criteria_definition d USING (criterion_code))
  GROUP BY v.opportunity_version_id;


--
-- Name: review_event; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.review_event (
    review_event_id bigint NOT NULL,
    opportunity_version_id uuid NOT NULL,
    extraction_candidate_id bigint,
    action text NOT NULL,
    reviewer text NOT NULL,
    reason text NOT NULL,
    packet_sha256 character(64) NOT NULL,
    detail_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT review_event_action_check CHECK ((action = ANY (ARRAY['promote'::text, 'reject'::text, 'reviewed'::text])))
);


--
-- Name: review_event_review_event_id_seq; Type: SEQUENCE; Schema: benefitlens; Owner: -
--

CREATE SEQUENCE benefitlens.review_event_review_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: review_event_review_event_id_seq; Type: SEQUENCE OWNED BY; Schema: benefitlens; Owner: -
--

ALTER SEQUENCE benefitlens.review_event_review_event_id_seq OWNED BY benefitlens.review_event.review_event_id;


--
-- Name: support_discovery_batch; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.support_discovery_batch (
    batch_id text NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    manifest jsonb NOT NULL
);


--
-- Name: support_discovery_observation; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.support_discovery_observation (
    observation_key text NOT NULL,
    candidate_key text NOT NULL,
    source_family text NOT NULL,
    listing_url text NOT NULL,
    evidence_sha256 text,
    payload jsonb NOT NULL,
    observed_at timestamp with time zone NOT NULL
);


--
-- Name: support_discovery_record; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.support_discovery_record (
    candidate_key text NOT NULL,
    source_url text NOT NULL,
    title text NOT NULL,
    title_basis text NOT NULL,
    source_family text NOT NULL,
    source_record_id text,
    record_type text NOT NULL,
    capture_status text NOT NULL,
    issuer_name text,
    issuer_kind text DEFAULT 'unknown'::text NOT NULL,
    source_country text,
    funding_hints jsonb DEFAULT '[]'::jsonb NOT NULL,
    detail_text text,
    payload jsonb NOT NULL,
    first_seen_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    rights_status text DEFAULT 'review_required'::text NOT NULL,
    eligibility_status text DEFAULT 'not_evaluated'::text NOT NULL
);


--
-- Name: writing_style_profile; Type: TABLE; Schema: benefitlens; Owner: -
--

CREATE TABLE benefitlens.writing_style_profile (
    writing_style_profile_id uuid NOT NULL,
    subject_key text NOT NULL,
    style_label text NOT NULL,
    sample_sha256 character(64),
    derived_features jsonb DEFAULT '{}'::jsonb NOT NULL,
    consented_at timestamp with time zone NOT NULL,
    retention_until timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT writing_style_profile_check CHECK ((retention_until > consented_at))
);


--
-- Name: document_version; Type: TABLE; Schema: platform; Owner: -
--

CREATE TABLE platform.document_version (
    document_version_id bigint NOT NULL,
    source_id text NOT NULL,
    canonical_url text NOT NULL,
    body_sha256 character(64) NOT NULL,
    raw_document_id bigint NOT NULL,
    first_seen_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    valid_to timestamp with time zone,
    is_current boolean DEFAULT true NOT NULL
);


--
-- Name: document_version_document_version_id_seq; Type: SEQUENCE; Schema: platform; Owner: -
--

CREATE SEQUENCE platform.document_version_document_version_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: document_version_document_version_id_seq; Type: SEQUENCE OWNED BY; Schema: platform; Owner: -
--

ALTER SEQUENCE platform.document_version_document_version_id_seq OWNED BY platform.document_version.document_version_id;


--
-- Name: quality_result; Type: TABLE; Schema: platform; Owner: -
--

CREATE TABLE platform.quality_result (
    quality_result_id bigint NOT NULL,
    run_id uuid NOT NULL,
    check_name text NOT NULL,
    check_scope text NOT NULL,
    check_status text NOT NULL,
    message text NOT NULL,
    observed_value jsonb,
    expected_value jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT quality_result_check_status_check CHECK ((check_status = ANY (ARRAY['pass'::text, 'warn'::text, 'fail'::text])))
);


--
-- Name: quality_result_quality_result_id_seq; Type: SEQUENCE; Schema: platform; Owner: -
--

CREATE SEQUENCE platform.quality_result_quality_result_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: quality_result_quality_result_id_seq; Type: SEQUENCE OWNED BY; Schema: platform; Owner: -
--

ALTER SEQUENCE platform.quality_result_quality_result_id_seq OWNED BY platform.quality_result.quality_result_id;


--
-- Name: raw_document; Type: TABLE; Schema: platform; Owner: -
--

CREATE TABLE platform.raw_document (
    raw_document_id bigint NOT NULL,
    run_id uuid NOT NULL,
    source_id text NOT NULL,
    source_url text NOT NULL,
    final_url text NOT NULL,
    fetched_at timestamp with time zone NOT NULL,
    http_status integer NOT NULL,
    content_type text,
    response_headers jsonb DEFAULT '{}'::jsonb NOT NULL,
    body bytea NOT NULL,
    body_size_bytes integer NOT NULL,
    body_sha256 character(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT raw_document_body_size_bytes_check CHECK ((body_size_bytes >= 0))
);


--
-- Name: raw_document_raw_document_id_seq; Type: SEQUENCE; Schema: platform; Owner: -
--

CREATE SEQUENCE platform.raw_document_raw_document_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: raw_document_raw_document_id_seq; Type: SEQUENCE OWNED BY; Schema: platform; Owner: -
--

ALTER SEQUENCE platform.raw_document_raw_document_id_seq OWNED BY platform.raw_document.raw_document_id;


--
-- Name: schema_migration; Type: TABLE; Schema: platform; Owner: -
--

CREATE TABLE platform.schema_migration (
    version text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: benefit benefit_id; Type: DEFAULT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.benefit ALTER COLUMN benefit_id SET DEFAULT nextval('benefitlens.benefit_benefit_id_seq'::regclass);


--
-- Name: claim_provenance claim_provenance_id; Type: DEFAULT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.claim_provenance ALTER COLUMN claim_provenance_id SET DEFAULT nextval('benefitlens.claim_provenance_claim_provenance_id_seq'::regclass);


--
-- Name: discovery_hit discovery_hit_id; Type: DEFAULT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.discovery_hit ALTER COLUMN discovery_hit_id SET DEFAULT nextval('benefitlens.discovery_hit_discovery_hit_id_seq'::regclass);


--
-- Name: draft_claim draft_claim_id; Type: DEFAULT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.draft_claim ALTER COLUMN draft_claim_id SET DEFAULT nextval('benefitlens.draft_claim_draft_claim_id_seq'::regclass);


--
-- Name: eligibility_rule eligibility_rule_id; Type: DEFAULT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.eligibility_rule ALTER COLUMN eligibility_rule_id SET DEFAULT nextval('benefitlens.eligibility_rule_eligibility_rule_id_seq'::regclass);


--
-- Name: evaluation_rule_result evaluation_rule_result_id; Type: DEFAULT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.evaluation_rule_result ALTER COLUMN evaluation_rule_result_id SET DEFAULT nextval('benefitlens.evaluation_rule_result_evaluation_rule_result_id_seq'::regclass);


--
-- Name: evidence_span evidence_span_id; Type: DEFAULT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.evidence_span ALTER COLUMN evidence_span_id SET DEFAULT nextval('benefitlens.evidence_span_evidence_span_id_seq'::regclass);


--
-- Name: extraction_candidate extraction_candidate_id; Type: DEFAULT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.extraction_candidate ALTER COLUMN extraction_candidate_id SET DEFAULT nextval('benefitlens.extraction_candidate_extraction_candidate_id_seq'::regclass);


--
-- Name: profile_attribute profile_attribute_id; Type: DEFAULT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.profile_attribute ALTER COLUMN profile_attribute_id SET DEFAULT nextval('benefitlens.profile_attribute_profile_attribute_id_seq'::regclass);


--
-- Name: required_document required_document_id; Type: DEFAULT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.required_document ALTER COLUMN required_document_id SET DEFAULT nextval('benefitlens.required_document_required_document_id_seq'::regclass);


--
-- Name: review_event review_event_id; Type: DEFAULT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.review_event ALTER COLUMN review_event_id SET DEFAULT nextval('benefitlens.review_event_review_event_id_seq'::regclass);


--
-- Name: document_version document_version_id; Type: DEFAULT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.document_version ALTER COLUMN document_version_id SET DEFAULT nextval('platform.document_version_document_version_id_seq'::regclass);


--
-- Name: quality_result quality_result_id; Type: DEFAULT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.quality_result ALTER COLUMN quality_result_id SET DEFAULT nextval('platform.quality_result_quality_result_id_seq'::regclass);


--
-- Name: raw_document raw_document_id; Type: DEFAULT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.raw_document ALTER COLUMN raw_document_id SET DEFAULT nextval('platform.raw_document_raw_document_id_seq'::regclass);


--
-- Name: application_prompt application_prompt_opportunity_version_id_prompt_key_key; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.application_prompt
    ADD CONSTRAINT application_prompt_opportunity_version_id_prompt_key_key UNIQUE (opportunity_version_id, prompt_key);


--
-- Name: application_prompt application_prompt_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.application_prompt
    ADD CONSTRAINT application_prompt_pkey PRIMARY KEY (application_prompt_id);


--
-- Name: benefit benefit_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.benefit
    ADD CONSTRAINT benefit_pkey PRIMARY KEY (benefit_id);


--
-- Name: claim_provenance claim_provenance_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.claim_provenance
    ADD CONSTRAINT claim_provenance_pkey PRIMARY KEY (claim_provenance_id);


--
-- Name: criteria_definition criteria_definition_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.criteria_definition
    ADD CONSTRAINT criteria_definition_pkey PRIMARY KEY (criterion_code);


--
-- Name: criteria_definition criteria_definition_profile_field_key; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.criteria_definition
    ADD CONSTRAINT criteria_definition_profile_field_key UNIQUE (profile_field);


--
-- Name: discovery_hit discovery_hit_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.discovery_hit
    ADD CONSTRAINT discovery_hit_pkey PRIMARY KEY (discovery_hit_id);


--
-- Name: discovery_hit discovery_hit_query_text_result_url_discovered_at_key; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.discovery_hit
    ADD CONSTRAINT discovery_hit_query_text_result_url_discovered_at_key UNIQUE (query_text, result_url, discovered_at);


--
-- Name: draft_claim draft_claim_draft_version_id_sentence_index_key; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.draft_claim
    ADD CONSTRAINT draft_claim_draft_version_id_sentence_index_key UNIQUE (draft_version_id, sentence_index);


--
-- Name: draft_claim draft_claim_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.draft_claim
    ADD CONSTRAINT draft_claim_pkey PRIMARY KEY (draft_claim_id);


--
-- Name: draft_version draft_version_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.draft_version
    ADD CONSTRAINT draft_version_pkey PRIMARY KEY (draft_version_id);


--
-- Name: eligibility_rule_group eligibility_rule_group_opportunity_version_id_group_key_key; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.eligibility_rule_group
    ADD CONSTRAINT eligibility_rule_group_opportunity_version_id_group_key_key UNIQUE (opportunity_version_id, group_key);


--
-- Name: eligibility_rule_group eligibility_rule_group_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.eligibility_rule_group
    ADD CONSTRAINT eligibility_rule_group_pkey PRIMARY KEY (rule_group_id);


--
-- Name: eligibility_rule eligibility_rule_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.eligibility_rule
    ADD CONSTRAINT eligibility_rule_pkey PRIMARY KEY (eligibility_rule_id);


--
-- Name: evaluation evaluation_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.evaluation
    ADD CONSTRAINT evaluation_pkey PRIMARY KEY (evaluation_id);


--
-- Name: evaluation_rule_result evaluation_rule_result_evaluation_id_eligibility_rule_id_key; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.evaluation_rule_result
    ADD CONSTRAINT evaluation_rule_result_evaluation_id_eligibility_rule_id_key UNIQUE (evaluation_id, eligibility_rule_id);


--
-- Name: evaluation_rule_result evaluation_rule_result_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.evaluation_rule_result
    ADD CONSTRAINT evaluation_rule_result_pkey PRIMARY KEY (evaluation_rule_result_id);


--
-- Name: evidence_span evidence_span_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.evidence_span
    ADD CONSTRAINT evidence_span_pkey PRIMARY KEY (evidence_span_id);


--
-- Name: experience_discovery_case experience_discovery_case_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.experience_discovery_case
    ADD CONSTRAINT experience_discovery_case_pkey PRIMARY KEY (case_key);


--
-- Name: experience_fact experience_fact_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.experience_fact
    ADD CONSTRAINT experience_fact_pkey PRIMARY KEY (experience_fact_id);


--
-- Name: exploration_run exploration_run_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.exploration_run
    ADD CONSTRAINT exploration_run_pkey PRIMARY KEY (run_id);


--
-- Name: exploration_search_batch exploration_search_batch_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.exploration_search_batch
    ADD CONSTRAINT exploration_search_batch_pkey PRIMARY KEY (batch_key);


--
-- Name: exploration_url exploration_url_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.exploration_url
    ADD CONSTRAINT exploration_url_pkey PRIMARY KEY (run_id, url_hash);


--
-- Name: extraction_candidate extraction_candidate_opportunity_version_id_candidate_finge_key; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.extraction_candidate
    ADD CONSTRAINT extraction_candidate_opportunity_version_id_candidate_finge_key UNIQUE (opportunity_version_id, candidate_fingerprint_sha256);


--
-- Name: extraction_candidate extraction_candidate_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.extraction_candidate
    ADD CONSTRAINT extraction_candidate_pkey PRIMARY KEY (extraction_candidate_id);


--
-- Name: funding_classification funding_classification_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.funding_classification
    ADD CONSTRAINT funding_classification_pkey PRIMARY KEY (opportunity_version_id);


--
-- Name: opportunities opportunities_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.opportunities
    ADD CONSTRAINT opportunities_pkey PRIMARY KEY (opportunity_id);


--
-- Name: opportunity opportunity_canonical_key_key; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.opportunity
    ADD CONSTRAINT opportunity_canonical_key_key UNIQUE (canonical_key);


--
-- Name: opportunity opportunity_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.opportunity
    ADD CONSTRAINT opportunity_pkey PRIMARY KEY (opportunity_id);


--
-- Name: opportunity_revision opportunity_revision_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.opportunity_revision
    ADD CONSTRAINT opportunity_revision_pkey PRIMARY KEY (opportunity_id, version_hash);


--
-- Name: opportunity_version opportunity_version_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.opportunity_version
    ADD CONSTRAINT opportunity_version_pkey PRIMARY KEY (opportunity_version_id);


--
-- Name: pipeline_document pipeline_document_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.pipeline_document
    ADD CONSTRAINT pipeline_document_pkey PRIMARY KEY (document_id);


--
-- Name: pipeline_search_wave pipeline_search_wave_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.pipeline_search_wave
    ADD CONSTRAINT pipeline_search_wave_pkey PRIMARY KEY (run_id, wave);


--
-- Name: pipeline_work_item pipeline_work_item_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.pipeline_work_item
    ADD CONSTRAINT pipeline_work_item_pkey PRIMARY KEY (candidate_key);


--
-- Name: profile_attribute profile_attribute_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.profile_attribute
    ADD CONSTRAINT profile_attribute_pkey PRIMARY KEY (profile_attribute_id);


--
-- Name: profile_attribute profile_attribute_profile_snapshot_id_criterion_code_key; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.profile_attribute
    ADD CONSTRAINT profile_attribute_profile_snapshot_id_criterion_code_key UNIQUE (profile_snapshot_id, criterion_code);


--
-- Name: profile_snapshot profile_snapshot_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.profile_snapshot
    ADD CONSTRAINT profile_snapshot_pkey PRIMARY KEY (profile_snapshot_id);


--
-- Name: program_knowledge program_knowledge_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.program_knowledge
    ADD CONSTRAINT program_knowledge_pkey PRIMARY KEY (program_id);


--
-- Name: provider provider_canonical_name_key; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.provider
    ADD CONSTRAINT provider_canonical_name_key UNIQUE (canonical_name);


--
-- Name: provider provider_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.provider
    ADD CONSTRAINT provider_pkey PRIMARY KEY (provider_id);


--
-- Name: required_document required_document_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.required_document
    ADD CONSTRAINT required_document_pkey PRIMARY KEY (required_document_id);


--
-- Name: review_event review_event_packet_sha256_key; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.review_event
    ADD CONSTRAINT review_event_packet_sha256_key UNIQUE (packet_sha256);


--
-- Name: review_event review_event_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.review_event
    ADD CONSTRAINT review_event_pkey PRIMARY KEY (review_event_id);


--
-- Name: support_discovery_batch support_discovery_batch_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.support_discovery_batch
    ADD CONSTRAINT support_discovery_batch_pkey PRIMARY KEY (batch_id);


--
-- Name: support_discovery_observation support_discovery_observation_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.support_discovery_observation
    ADD CONSTRAINT support_discovery_observation_pkey PRIMARY KEY (observation_key);


--
-- Name: support_discovery_record support_discovery_record_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.support_discovery_record
    ADD CONSTRAINT support_discovery_record_pkey PRIMARY KEY (candidate_key);


--
-- Name: writing_style_profile writing_style_profile_pkey; Type: CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.writing_style_profile
    ADD CONSTRAINT writing_style_profile_pkey PRIMARY KEY (writing_style_profile_id);


--
-- Name: collection_run collection_run_pkey; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.collection_run
    ADD CONSTRAINT collection_run_pkey PRIMARY KEY (run_id);


--
-- Name: data_source data_source_pkey; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.data_source
    ADD CONSTRAINT data_source_pkey PRIMARY KEY (source_id);


--
-- Name: document_version document_version_pkey; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.document_version
    ADD CONSTRAINT document_version_pkey PRIMARY KEY (document_version_id);


--
-- Name: document_version document_version_source_id_canonical_url_body_sha256_key; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.document_version
    ADD CONSTRAINT document_version_source_id_canonical_url_body_sha256_key UNIQUE (source_id, canonical_url, body_sha256);


--
-- Name: quality_result quality_result_pkey; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.quality_result
    ADD CONSTRAINT quality_result_pkey PRIMARY KEY (quality_result_id);


--
-- Name: quality_result quality_result_run_id_check_name_check_scope_key; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.quality_result
    ADD CONSTRAINT quality_result_run_id_check_name_check_scope_key UNIQUE (run_id, check_name, check_scope);


--
-- Name: raw_document raw_document_pkey; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.raw_document
    ADD CONSTRAINT raw_document_pkey PRIMARY KEY (raw_document_id);


--
-- Name: raw_document raw_document_run_id_body_sha256_key; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.raw_document
    ADD CONSTRAINT raw_document_run_id_body_sha256_key UNIQUE (run_id, body_sha256);


--
-- Name: schema_migration schema_migration_pkey; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.schema_migration
    ADD CONSTRAINT schema_migration_pkey PRIMARY KEY (version);


--
-- Name: eligibility_rule_criterion_idx; Type: INDEX; Schema: benefitlens; Owner: -
--

CREATE INDEX eligibility_rule_criterion_idx ON benefitlens.eligibility_rule USING btree (criterion_code) WHERE (machine_evaluable = true);


--
-- Name: eligibility_rule_version_key_idx; Type: INDEX; Schema: benefitlens; Owner: -
--

CREATE UNIQUE INDEX eligibility_rule_version_key_idx ON benefitlens.eligibility_rule USING btree (opportunity_version_id, rule_key) WHERE (rule_key IS NOT NULL);


--
-- Name: experience_discovery_program; Type: INDEX; Schema: benefitlens; Owner: -
--

CREATE INDEX experience_discovery_program ON benefitlens.experience_discovery_case USING btree (program_id);


--
-- Name: exploration_url_status; Type: INDEX; Schema: benefitlens; Owner: -
--

CREATE INDEX exploration_url_status ON benefitlens.exploration_url USING btree (run_id, status);


--
-- Name: extraction_candidate_review_idx; Type: INDEX; Schema: benefitlens; Owner: -
--

CREATE INDEX extraction_candidate_review_idx ON benefitlens.extraction_candidate USING btree (review_status, created_at DESC);


--
-- Name: idx_funding_classification_tags; Type: INDEX; Schema: benefitlens; Owner: -
--

CREATE INDEX idx_funding_classification_tags ON benefitlens.funding_classification USING gin (tags_json);


--
-- Name: one_current_opportunity_version_idx; Type: INDEX; Schema: benefitlens; Owner: -
--

CREATE UNIQUE INDEX one_current_opportunity_version_idx ON benefitlens.opportunity_version USING btree (opportunity_id) WHERE (valid_to IS NULL);


--
-- Name: opportunities_payload_idx; Type: INDEX; Schema: benefitlens; Owner: -
--

CREATE INDEX opportunities_payload_idx ON benefitlens.opportunities USING gin (payload jsonb_path_ops);


--
-- Name: opportunities_program_idx; Type: INDEX; Schema: benefitlens; Owner: -
--

CREATE INDEX opportunities_program_idx ON benefitlens.opportunities USING btree (program_id);


--
-- Name: opportunities_scope_idx; Type: INDEX; Schema: benefitlens; Owner: -
--

CREATE INDEX opportunities_scope_idx ON benefitlens.opportunities USING btree (recipient_type, category, extraction_status);


--
-- Name: opportunity_version_content_history_idx; Type: INDEX; Schema: benefitlens; Owner: -
--

CREATE INDEX opportunity_version_content_history_idx ON benefitlens.opportunity_version USING btree (opportunity_id, content_fingerprint_sha256);


--
-- Name: pipeline_document_urlhash_content_key; Type: INDEX; Schema: benefitlens; Owner: -
--

CREATE UNIQUE INDEX pipeline_document_urlhash_content_key ON benefitlens.pipeline_document USING btree (canonical_url_hash, content_sha256);


--
-- Name: pipeline_work_status_idx; Type: INDEX; Schema: benefitlens; Owner: -
--

CREATE INDEX pipeline_work_status_idx ON benefitlens.pipeline_work_item USING btree (status, source_family);


--
-- Name: support_discovery_capture_idx; Type: INDEX; Schema: benefitlens; Owner: -
--

CREATE INDEX support_discovery_capture_idx ON benefitlens.support_discovery_record USING btree (capture_status, record_type);


--
-- Name: support_discovery_family_idx; Type: INDEX; Schema: benefitlens; Owner: -
--

CREATE INDEX support_discovery_family_idx ON benefitlens.support_discovery_record USING btree (source_family);


--
-- Name: collection_run_source_started_idx; Type: INDEX; Schema: platform; Owner: -
--

CREATE INDEX collection_run_source_started_idx ON platform.collection_run USING btree (source_id, started_at DESC);


--
-- Name: one_current_document_version_idx; Type: INDEX; Schema: platform; Owner: -
--

CREATE UNIQUE INDEX one_current_document_version_idx ON platform.document_version USING btree (source_id, canonical_url) WHERE (is_current = true);


--
-- Name: raw_document_source_fetched_idx; Type: INDEX; Schema: platform; Owner: -
--

CREATE INDEX raw_document_source_fetched_idx ON platform.raw_document USING btree (source_id, fetched_at DESC);


--
-- Name: application_prompt application_prompt_evidence_span_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.application_prompt
    ADD CONSTRAINT application_prompt_evidence_span_id_fkey FOREIGN KEY (evidence_span_id) REFERENCES benefitlens.evidence_span(evidence_span_id);


--
-- Name: application_prompt application_prompt_opportunity_version_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.application_prompt
    ADD CONSTRAINT application_prompt_opportunity_version_id_fkey FOREIGN KEY (opportunity_version_id) REFERENCES benefitlens.opportunity_version(opportunity_version_id);


--
-- Name: benefit benefit_opportunity_version_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.benefit
    ADD CONSTRAINT benefit_opportunity_version_id_fkey FOREIGN KEY (opportunity_version_id) REFERENCES benefitlens.opportunity_version(opportunity_version_id);


--
-- Name: claim_provenance claim_provenance_draft_claim_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.claim_provenance
    ADD CONSTRAINT claim_provenance_draft_claim_id_fkey FOREIGN KEY (draft_claim_id) REFERENCES benefitlens.draft_claim(draft_claim_id);


--
-- Name: claim_provenance claim_provenance_evidence_span_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.claim_provenance
    ADD CONSTRAINT claim_provenance_evidence_span_id_fkey FOREIGN KEY (evidence_span_id) REFERENCES benefitlens.evidence_span(evidence_span_id);


--
-- Name: claim_provenance claim_provenance_experience_fact_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.claim_provenance
    ADD CONSTRAINT claim_provenance_experience_fact_id_fkey FOREIGN KEY (experience_fact_id) REFERENCES benefitlens.experience_fact(experience_fact_id);


--
-- Name: discovery_hit discovery_hit_official_source_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.discovery_hit
    ADD CONSTRAINT discovery_hit_official_source_id_fkey FOREIGN KEY (official_source_id) REFERENCES platform.data_source(source_id);


--
-- Name: draft_claim draft_claim_draft_version_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.draft_claim
    ADD CONSTRAINT draft_claim_draft_version_id_fkey FOREIGN KEY (draft_version_id) REFERENCES benefitlens.draft_version(draft_version_id);


--
-- Name: draft_version draft_version_application_prompt_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.draft_version
    ADD CONSTRAINT draft_version_application_prompt_id_fkey FOREIGN KEY (application_prompt_id) REFERENCES benefitlens.application_prompt(application_prompt_id);


--
-- Name: draft_version draft_version_writing_style_profile_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.draft_version
    ADD CONSTRAINT draft_version_writing_style_profile_id_fkey FOREIGN KEY (writing_style_profile_id) REFERENCES benefitlens.writing_style_profile(writing_style_profile_id);


--
-- Name: eligibility_rule eligibility_rule_criterion_code_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.eligibility_rule
    ADD CONSTRAINT eligibility_rule_criterion_code_fkey FOREIGN KEY (criterion_code) REFERENCES benefitlens.criteria_definition(criterion_code);


--
-- Name: eligibility_rule_group eligibility_rule_group_opportunity_version_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.eligibility_rule_group
    ADD CONSTRAINT eligibility_rule_group_opportunity_version_id_fkey FOREIGN KEY (opportunity_version_id) REFERENCES benefitlens.opportunity_version(opportunity_version_id);


--
-- Name: eligibility_rule_group eligibility_rule_group_parent_rule_group_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.eligibility_rule_group
    ADD CONSTRAINT eligibility_rule_group_parent_rule_group_id_fkey FOREIGN KEY (parent_rule_group_id) REFERENCES benefitlens.eligibility_rule_group(rule_group_id);


--
-- Name: eligibility_rule eligibility_rule_opportunity_version_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.eligibility_rule
    ADD CONSTRAINT eligibility_rule_opportunity_version_id_fkey FOREIGN KEY (opportunity_version_id) REFERENCES benefitlens.opportunity_version(opportunity_version_id);


--
-- Name: eligibility_rule eligibility_rule_rule_group_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.eligibility_rule
    ADD CONSTRAINT eligibility_rule_rule_group_id_fkey FOREIGN KEY (rule_group_id) REFERENCES benefitlens.eligibility_rule_group(rule_group_id);


--
-- Name: evaluation evaluation_opportunity_version_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.evaluation
    ADD CONSTRAINT evaluation_opportunity_version_id_fkey FOREIGN KEY (opportunity_version_id) REFERENCES benefitlens.opportunity_version(opportunity_version_id);


--
-- Name: evaluation evaluation_profile_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.evaluation
    ADD CONSTRAINT evaluation_profile_snapshot_id_fkey FOREIGN KEY (profile_snapshot_id) REFERENCES benefitlens.profile_snapshot(profile_snapshot_id);


--
-- Name: evaluation_rule_result evaluation_rule_result_eligibility_rule_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.evaluation_rule_result
    ADD CONSTRAINT evaluation_rule_result_eligibility_rule_id_fkey FOREIGN KEY (eligibility_rule_id) REFERENCES benefitlens.eligibility_rule(eligibility_rule_id);


--
-- Name: evaluation_rule_result evaluation_rule_result_evaluation_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.evaluation_rule_result
    ADD CONSTRAINT evaluation_rule_result_evaluation_id_fkey FOREIGN KEY (evaluation_id) REFERENCES benefitlens.evaluation(evaluation_id);


--
-- Name: evidence_span evidence_span_opportunity_version_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.evidence_span
    ADD CONSTRAINT evidence_span_opportunity_version_id_fkey FOREIGN KEY (opportunity_version_id) REFERENCES benefitlens.opportunity_version(opportunity_version_id);


--
-- Name: evidence_span evidence_span_raw_document_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.evidence_span
    ADD CONSTRAINT evidence_span_raw_document_id_fkey FOREIGN KEY (raw_document_id) REFERENCES platform.raw_document(raw_document_id);


--
-- Name: extraction_candidate extraction_candidate_opportunity_version_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.extraction_candidate
    ADD CONSTRAINT extraction_candidate_opportunity_version_id_fkey FOREIGN KEY (opportunity_version_id) REFERENCES benefitlens.opportunity_version(opportunity_version_id);


--
-- Name: extraction_candidate extraction_candidate_raw_document_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.extraction_candidate
    ADD CONSTRAINT extraction_candidate_raw_document_id_fkey FOREIGN KEY (raw_document_id) REFERENCES platform.raw_document(raw_document_id);


--
-- Name: funding_classification funding_classification_opportunity_version_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.funding_classification
    ADD CONSTRAINT funding_classification_opportunity_version_id_fkey FOREIGN KEY (opportunity_version_id) REFERENCES benefitlens.opportunity_version(opportunity_version_id);


--
-- Name: opportunity opportunity_provider_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.opportunity
    ADD CONSTRAINT opportunity_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES benefitlens.provider(provider_id);


--
-- Name: opportunity_revision opportunity_revision_opportunity_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.opportunity_revision
    ADD CONSTRAINT opportunity_revision_opportunity_id_fkey FOREIGN KEY (opportunity_id) REFERENCES benefitlens.opportunities(opportunity_id);


--
-- Name: opportunity_version opportunity_version_document_version_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.opportunity_version
    ADD CONSTRAINT opportunity_version_document_version_id_fkey FOREIGN KEY (document_version_id) REFERENCES platform.document_version(document_version_id);


--
-- Name: opportunity_version opportunity_version_opportunity_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.opportunity_version
    ADD CONSTRAINT opportunity_version_opportunity_id_fkey FOREIGN KEY (opportunity_id) REFERENCES benefitlens.opportunity(opportunity_id);


--
-- Name: opportunity_version opportunity_version_run_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.opportunity_version
    ADD CONSTRAINT opportunity_version_run_id_fkey FOREIGN KEY (run_id) REFERENCES platform.collection_run(run_id);


--
-- Name: pipeline_work_item pipeline_work_item_candidate_key_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.pipeline_work_item
    ADD CONSTRAINT pipeline_work_item_candidate_key_fkey FOREIGN KEY (candidate_key) REFERENCES benefitlens.support_discovery_record(candidate_key);


--
-- Name: profile_attribute profile_attribute_criterion_code_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.profile_attribute
    ADD CONSTRAINT profile_attribute_criterion_code_fkey FOREIGN KEY (criterion_code) REFERENCES benefitlens.criteria_definition(criterion_code);


--
-- Name: profile_attribute profile_attribute_profile_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.profile_attribute
    ADD CONSTRAINT profile_attribute_profile_snapshot_id_fkey FOREIGN KEY (profile_snapshot_id) REFERENCES benefitlens.profile_snapshot(profile_snapshot_id);


--
-- Name: required_document required_document_evidence_span_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.required_document
    ADD CONSTRAINT required_document_evidence_span_id_fkey FOREIGN KEY (evidence_span_id) REFERENCES benefitlens.evidence_span(evidence_span_id);


--
-- Name: required_document required_document_opportunity_version_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.required_document
    ADD CONSTRAINT required_document_opportunity_version_id_fkey FOREIGN KEY (opportunity_version_id) REFERENCES benefitlens.opportunity_version(opportunity_version_id);


--
-- Name: review_event review_event_extraction_candidate_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.review_event
    ADD CONSTRAINT review_event_extraction_candidate_id_fkey FOREIGN KEY (extraction_candidate_id) REFERENCES benefitlens.extraction_candidate(extraction_candidate_id);


--
-- Name: review_event review_event_opportunity_version_id_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.review_event
    ADD CONSTRAINT review_event_opportunity_version_id_fkey FOREIGN KEY (opportunity_version_id) REFERENCES benefitlens.opportunity_version(opportunity_version_id);


--
-- Name: support_discovery_observation support_discovery_observation_candidate_key_fkey; Type: FK CONSTRAINT; Schema: benefitlens; Owner: -
--

ALTER TABLE ONLY benefitlens.support_discovery_observation
    ADD CONSTRAINT support_discovery_observation_candidate_key_fkey FOREIGN KEY (candidate_key) REFERENCES benefitlens.support_discovery_record(candidate_key);


--
-- Name: collection_run collection_run_source_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.collection_run
    ADD CONSTRAINT collection_run_source_id_fkey FOREIGN KEY (source_id) REFERENCES platform.data_source(source_id);


--
-- Name: document_version document_version_raw_document_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.document_version
    ADD CONSTRAINT document_version_raw_document_id_fkey FOREIGN KEY (raw_document_id) REFERENCES platform.raw_document(raw_document_id);


--
-- Name: document_version document_version_source_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.document_version
    ADD CONSTRAINT document_version_source_id_fkey FOREIGN KEY (source_id) REFERENCES platform.data_source(source_id);


--
-- Name: quality_result quality_result_run_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.quality_result
    ADD CONSTRAINT quality_result_run_id_fkey FOREIGN KEY (run_id) REFERENCES platform.collection_run(run_id);


--
-- Name: raw_document raw_document_run_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.raw_document
    ADD CONSTRAINT raw_document_run_id_fkey FOREIGN KEY (run_id) REFERENCES platform.collection_run(run_id);


--
-- Name: raw_document raw_document_source_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.raw_document
    ADD CONSTRAINT raw_document_source_id_fkey FOREIGN KEY (source_id) REFERENCES platform.data_source(source_id);


--
-- PostgreSQL database dump complete
--

\unrestrict omLIscpvPF0gFBkbfYHT89Wv1cK7aLrgKkODjaNZn3bLKWsZ9hKtRyeMEUSfBb5

