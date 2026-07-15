ALTER TABLE example_insurance.policy_renewal_facts
    ADD COLUMN policyholder_email text NOT NULL;

ALTER TABLE example_insurance.renewal_workflows
    ADD COLUMN authorized_actor_kind text NOT NULL,
    ADD COLUMN authorized_actor_id text NOT NULL,
    ADD COLUMN authority_revoked_at timestamptz;

ALTER TABLE example_insurance.renewal_drafts
    ADD COLUMN policyholder_email text NOT NULL,
    ADD COLUMN presentation_fingerprint text NOT NULL;

CREATE TABLE example_insurance.renewal_decisions (
    decision_id uuid PRIMARY KEY,
    command_id uuid NOT NULL UNIQUE,
    workflow_id uuid NOT NULL REFERENCES example_insurance.renewal_workflows(workflow_id),
    wait_id uuid NOT NULL UNIQUE REFERENCES openmagic_runtime.waits(wait_id),
    draft_id uuid NOT NULL REFERENCES example_insurance.renewal_drafts(draft_id),
    presented_message_id uuid NOT NULL REFERENCES openmagic_runtime.messages(message_id),
    thread_sequence integer NOT NULL CHECK (thread_sequence > 0),
    message_fingerprint text NOT NULL,
    decision_kind text NOT NULL CHECK (decision_kind IN ('approve', 'request_revision')),
    actor jsonb NOT NULL,
    cause jsonb NOT NULL,
    presentation_fingerprint text NOT NULL,
    proposed_effect jsonb NOT NULL,
    revision_instruction text,
    signal_id uuid NOT NULL UNIQUE REFERENCES openmagic_runtime.signals(signal_id),
    decided_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (
        (decision_kind = 'approve' AND revision_instruction IS NULL)
        OR (decision_kind = 'request_revision' AND revision_instruction IS NOT NULL)
    )
);

CREATE TABLE example_insurance.approval_grants (
    approval_grant_id uuid PRIMARY KEY,
    decision_id uuid NOT NULL UNIQUE REFERENCES example_insurance.renewal_decisions(decision_id),
    workflow_id uuid NOT NULL REFERENCES example_insurance.renewal_workflows(workflow_id),
    step_id uuid NOT NULL UNIQUE REFERENCES openmagic_runtime.steps(step_id),
    effect_fingerprint text NOT NULL,
    actor jsonb NOT NULL,
    cause jsonb NOT NULL,
    invalidated_at timestamptz,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (invalidated_at IS NULL OR consumed_at IS NULL)
);

CREATE TABLE example_insurance.external_effects (
    logical_effect_id uuid PRIMARY KEY,
    workflow_id uuid NOT NULL REFERENCES example_insurance.renewal_workflows(workflow_id),
    step_id uuid NOT NULL UNIQUE REFERENCES openmagic_runtime.steps(step_id),
    approval_grant_id uuid NOT NULL UNIQUE
        REFERENCES example_insurance.approval_grants(approval_grant_id),
    effect_fingerprint text NOT NULL,
    provider_idempotency_key text NOT NULL UNIQUE,
    dispatch_attempt_id uuid NOT NULL REFERENCES openmagic_runtime.attempts(attempt_id),
    dispatch_attempt_number integer NOT NULL CHECK (dispatch_attempt_number > 0),
    certainty text NOT NULL
        CHECK (certainty IN ('dispatching', 'applied', 'not_applied', 'uncertain')),
    fenced_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE example_insurance.external_effect_evidence (
    evidence_id uuid PRIMARY KEY,
    logical_effect_id uuid NOT NULL
        REFERENCES example_insurance.external_effects(logical_effect_id),
    attempt_id uuid NOT NULL REFERENCES openmagic_runtime.attempts(attempt_id),
    classification text NOT NULL
        CHECK (classification IN ('applied', 'not_applied', 'uncertain')),
    source text NOT NULL CHECK (
        source IN ('provider_response', 'provider_lookup', 'worker_loss_after_fence')
    ),
    provider_request_id text,
    details jsonb NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX external_effect_evidence_by_effect
    ON example_insurance.external_effect_evidence(logical_effect_id, observed_at, evidence_id);
