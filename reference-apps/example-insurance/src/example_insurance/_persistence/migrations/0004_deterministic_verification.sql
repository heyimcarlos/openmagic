CREATE TABLE example_insurance.parties (
    party_id uuid PRIMARY KEY,
    party_kind text NOT NULL CHECK (party_kind IN ('person', 'organization')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE example_insurance.party_identifiers (
    identifier_id uuid PRIMARY KEY,
    party_id uuid NOT NULL REFERENCES example_insurance.parties(party_id),
    identifier_kind text NOT NULL CHECK (identifier_kind = 'email'),
    canonical_value text NOT NULL,
    delivery_thread_id uuid NOT NULL REFERENCES openmagic_runtime.threads(thread_id),
    verified_at timestamptz,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (verified_at IS NULL OR verified_at >= created_at),
    UNIQUE (identifier_id, delivery_thread_id, party_id)
);

CREATE UNIQUE INDEX one_current_party_identifier
    ON example_insurance.party_identifiers(party_id, identifier_kind)
    WHERE revoked_at IS NULL;

CREATE UNIQUE INDEX one_current_identifier_value
    ON example_insurance.party_identifiers(identifier_kind, canonical_value)
    WHERE revoked_at IS NULL;

CREATE TABLE example_insurance.organization_memberships (
    membership_id uuid PRIMARY KEY,
    party_id uuid NOT NULL REFERENCES example_insurance.parties(party_id),
    organization_party_id uuid NOT NULL REFERENCES example_insurance.parties(party_id),
    joined_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    revoked_at timestamptz,
    CHECK (party_id <> organization_party_id),
    UNIQUE (membership_id, party_id)
);

CREATE UNIQUE INDEX one_current_organization_membership
    ON example_insurance.organization_memberships(party_id, organization_party_id)
    WHERE revoked_at IS NULL;

CREATE TABLE example_insurance.workflow_participants (
    participant_id uuid PRIMARY KEY,
    workflow_id uuid NOT NULL REFERENCES example_insurance.renewal_workflows(workflow_id),
    party_id uuid NOT NULL REFERENCES example_insurance.parties(party_id),
    assigned_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (participant_id, party_id),
    UNIQUE (workflow_id, party_id)
);

CREATE TABLE example_insurance.workflow_role_assignments (
    role_assignment_id uuid PRIMARY KEY,
    participant_id uuid NOT NULL,
    party_id uuid NOT NULL,
    membership_id uuid,
    role text NOT NULL CHECK (role IN ('broker', 'reporter', 'policyholder', 'claimant')),
    assigned_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    revoked_at timestamptz,
    CHECK (
        (role = 'broker' AND membership_id IS NOT NULL)
        OR (role <> 'broker' AND membership_id IS NULL)
    ),
    FOREIGN KEY (participant_id, party_id)
        REFERENCES example_insurance.workflow_participants(participant_id, party_id),
    FOREIGN KEY (membership_id, party_id)
        REFERENCES example_insurance.organization_memberships(membership_id, party_id)
);

CREATE UNIQUE INDEX one_current_workflow_role
    ON example_insurance.workflow_role_assignments(participant_id, role)
    WHERE revoked_at IS NULL;

CREATE TABLE example_insurance.protected_commands (
    protected_command_id uuid PRIMARY KEY,
    workflow_id uuid NOT NULL REFERENCES example_insurance.renewal_workflows(workflow_id),
    thread_id uuid NOT NULL REFERENCES openmagic_runtime.threads(thread_id),
    party_id uuid NOT NULL REFERENCES example_insurance.parties(party_id),
    purpose text NOT NULL CHECK (purpose = 'renewal.read_approved_details'),
    approval_grant_id uuid NOT NULL REFERENCES example_insurance.approval_grants(approval_grant_id),
    state text NOT NULL CHECK (state IN ('waiting', 'authorized', 'rejected')),
    outcome text CHECK (
        outcome IN (
            'authorized', 'approval_required', 'authority_revoked',
            'identifier_revoked', 'workflow_closed', 'wrong_party',
            'wrong_purpose', 'wrong_thread', 'verification_expired',
            'verification_delivery_failed', 'verification_attempts_exhausted'
        )
    ),
    authorized_delivery_id uuid REFERENCES openmagic_runtime.deliveries(delivery_id),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    resolved_at timestamptz,
    CHECK (
        (
            state = 'waiting' AND outcome IS NULL
            AND authorized_delivery_id IS NULL AND resolved_at IS NULL
        )
        OR (
            state = 'authorized' AND outcome IS NOT NULL AND outcome = 'authorized'
            AND authorized_delivery_id IS NOT NULL AND resolved_at IS NOT NULL
        )
        OR (
            state = 'rejected' AND outcome IS NOT NULL AND outcome <> 'authorized'
            AND authorized_delivery_id IS NULL AND resolved_at IS NOT NULL
        )
    ),
    UNIQUE (protected_command_id, party_id, thread_id, workflow_id, purpose)
);

CREATE TABLE example_insurance.verification_challenges (
    challenge_id uuid PRIMARY KEY,
    protected_command_id uuid NOT NULL UNIQUE
        REFERENCES example_insurance.protected_commands(protected_command_id),
    party_id uuid NOT NULL REFERENCES example_insurance.parties(party_id),
    thread_id uuid NOT NULL REFERENCES openmagic_runtime.threads(thread_id),
    protected_workflow_id uuid NOT NULL
        REFERENCES example_insurance.renewal_workflows(workflow_id),
    purpose text NOT NULL CHECK (purpose = 'renewal.read_approved_details'),
    destination_identifier_id uuid NOT NULL
        REFERENCES example_insurance.party_identifiers(identifier_id),
    destination_thread_id uuid NOT NULL REFERENCES openmagic_runtime.threads(thread_id),
    delivery_workflow_id uuid NOT NULL UNIQUE,
    delivery_instance_id uuid NOT NULL UNIQUE
        REFERENCES openmagic_runtime.instances(instance_id),
    state text NOT NULL CHECK (
        state IN (
            'pending', 'accepted', 'expired', 'delivery_failed',
            'attempts_exhausted', 'rejected'
        )
    ),
    failed_attempts integer NOT NULL DEFAULT 0 CHECK (failed_attempts BETWEEN 0 AND 5),
    expires_at timestamptz NOT NULL,
    accepted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (
        (state = 'accepted' AND accepted_at IS NOT NULL)
        OR (state <> 'accepted' AND accepted_at IS NULL)
    ),
    CHECK (destination_thread_id <> thread_id),
    FOREIGN KEY (
        protected_command_id, party_id, thread_id, protected_workflow_id, purpose
    ) REFERENCES example_insurance.protected_commands (
        protected_command_id, party_id, thread_id, workflow_id, purpose
    ),
    FOREIGN KEY (destination_identifier_id, destination_thread_id, party_id)
        REFERENCES example_insurance.party_identifiers (
            identifier_id, delivery_thread_id, party_id
        ),
    UNIQUE (
        challenge_id, party_id, thread_id, destination_identifier_id, destination_thread_id
    ),
    UNIQUE (
        delivery_workflow_id, challenge_id, delivery_instance_id, protected_workflow_id
    )
);

CREATE TABLE example_insurance.verification_workflows (
    workflow_id uuid PRIMARY KEY,
    instance_id uuid NOT NULL UNIQUE REFERENCES openmagic_runtime.instances(instance_id),
    challenge_id uuid NOT NULL UNIQUE
        REFERENCES example_insurance.verification_challenges(challenge_id),
    protected_workflow_id uuid NOT NULL
        REFERENCES example_insurance.renewal_workflows(workflow_id),
    lifecycle text NOT NULL CHECK (lifecycle IN ('active', 'completed', 'failed')),
    delivery_event_id uuid UNIQUE,
    delivery_id uuid UNIQUE REFERENCES openmagic_runtime.deliveries(delivery_id),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    CHECK (
        (lifecycle = 'active' AND completed_at IS NULL)
        OR (lifecycle <> 'active' AND completed_at IS NOT NULL)
    ),
    UNIQUE (workflow_id, challenge_id, instance_id, protected_workflow_id)
);

CREATE TABLE example_insurance.verification_events (
    event_id uuid PRIMARY KEY,
    workflow_id uuid NOT NULL
        REFERENCES example_insurance.verification_workflows(workflow_id),
    event_type text NOT NULL CHECK (event_type = 'verification.challenge.delivery_ready'),
    schema_version integer NOT NULL CHECK (schema_version = 1),
    actor jsonb NOT NULL,
    cause jsonb NOT NULL,
    payload jsonb NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (event_id, workflow_id)
);

ALTER TABLE example_insurance.verification_workflows
    ADD CONSTRAINT exact_verification_delivery_event
    FOREIGN KEY (delivery_event_id, workflow_id)
        REFERENCES example_insurance.verification_events(event_id, workflow_id);

CREATE TABLE example_insurance.verification_sessions (
    session_id uuid PRIMARY KEY,
    submit_command_id uuid NOT NULL UNIQUE,
    challenge_id uuid NOT NULL UNIQUE
        REFERENCES example_insurance.verification_challenges(challenge_id),
    party_id uuid NOT NULL REFERENCES example_insurance.parties(party_id),
    thread_id uuid NOT NULL REFERENCES openmagic_runtime.threads(thread_id),
    identifier_id uuid NOT NULL REFERENCES example_insurance.party_identifiers(identifier_id),
    identifier_thread_id uuid NOT NULL REFERENCES openmagic_runtime.threads(thread_id),
    established_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    CHECK (expires_at > established_at),
    FOREIGN KEY (challenge_id, party_id, thread_id, identifier_id, identifier_thread_id)
        REFERENCES example_insurance.verification_challenges (
            challenge_id, party_id, thread_id, destination_identifier_id, destination_thread_id
        )
);

ALTER TABLE example_insurance.verification_challenges
    ADD CONSTRAINT exact_verification_workflow
    FOREIGN KEY (
        delivery_workflow_id, challenge_id, delivery_instance_id, protected_workflow_id
    ) REFERENCES example_insurance.verification_workflows (
        workflow_id, challenge_id, instance_id, protected_workflow_id
    ) DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX current_verification_session
    ON example_insurance.verification_sessions(party_id, thread_id, expires_at)
    WHERE revoked_at IS NULL;
