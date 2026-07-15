CREATE TABLE openmagic_runtime.workflow_definitions (
    definition_key text NOT NULL,
    definition_version integer NOT NULL CHECK (definition_version > 0),
    manifest jsonb NOT NULL,
    manifest_digest text NOT NULL,
    registered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (definition_key, definition_version)
);

CREATE TABLE openmagic_runtime.instances (
    instance_id uuid PRIMARY KEY,
    definition_key text NOT NULL,
    definition_version integer NOT NULL,
    input jsonb NOT NULL,
    input_digest text NOT NULL,
    state text NOT NULL CHECK (state IN ('open', 'closed')),
    last_trace_sequence bigint NOT NULL DEFAULT 0 CHECK (last_trace_sequence >= 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    closed_at timestamptz,
    FOREIGN KEY (definition_key, definition_version)
        REFERENCES openmagic_runtime.workflow_definitions(definition_key, definition_version)
);

CREATE TABLE openmagic_runtime.steps (
    step_id uuid PRIMARY KEY,
    instance_id uuid NOT NULL REFERENCES openmagic_runtime.instances(instance_id),
    template_key text NOT NULL,
    route_key text NOT NULL,
    activation_source_kind text NOT NULL,
    activation_source_id uuid NOT NULL,
    output_slot text NOT NULL,
    input jsonb NOT NULL,
    input_digest text NOT NULL,
    state text NOT NULL CHECK (state IN ('pending', 'succeeded', 'failed', 'cancelled')),
    claimable_at timestamptz,
    output jsonb,
    output_digest text,
    failure jsonb,
    failure_digest text,
    terminal_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (instance_id, route_key, activation_source_kind, activation_source_id, output_slot),
    UNIQUE (instance_id, step_id),
    CHECK ((state = 'pending') = (terminal_at IS NULL)),
    CHECK (
        (state = 'succeeded' AND output IS NOT NULL AND output_digest IS NOT NULL
            AND failure IS NULL AND failure_digest IS NULL)
        OR (state = 'failed' AND failure IS NOT NULL AND failure_digest IS NOT NULL
            AND output IS NULL AND output_digest IS NULL)
        OR (state IN ('pending', 'cancelled') AND output IS NULL AND output_digest IS NULL
            AND failure IS NULL AND failure_digest IS NULL)
    )
);

CREATE TABLE openmagic_runtime.step_dependencies (
    instance_id uuid NOT NULL REFERENCES openmagic_runtime.instances(instance_id),
    step_id uuid NOT NULL,
    prerequisite_step_id uuid NOT NULL,
    PRIMARY KEY (step_id, prerequisite_step_id),
    CHECK (step_id <> prerequisite_step_id),
    FOREIGN KEY (instance_id, step_id)
        REFERENCES openmagic_runtime.steps(instance_id, step_id),
    FOREIGN KEY (instance_id, prerequisite_step_id)
        REFERENCES openmagic_runtime.steps(instance_id, step_id)
);

CREATE TABLE openmagic_runtime.attempts (
    attempt_id uuid PRIMARY KEY,
    instance_id uuid NOT NULL REFERENCES openmagic_runtime.instances(instance_id),
    step_id uuid NOT NULL,
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    state text NOT NULL CHECK (state IN ('leased', 'completed', 'abandoned', 'cancelled')),
    worker_id text NOT NULL,
    lease_expires_at timestamptz NOT NULL,
    hard_deadline timestamptz NOT NULL,
    observation jsonb,
    observation_digest text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    UNIQUE (step_id, attempt_number),
    CHECK (
        (state = 'completed' AND observation IS NOT NULL AND observation_digest IS NOT NULL
            AND completed_at IS NOT NULL)
        OR (state = 'leased' AND observation IS NULL AND observation_digest IS NULL
            AND completed_at IS NULL)
        OR (state IN ('abandoned', 'cancelled') AND observation IS NULL
            AND observation_digest IS NULL AND completed_at IS NOT NULL)
    ),
    FOREIGN KEY (instance_id, step_id)
        REFERENCES openmagic_runtime.steps(instance_id, step_id)
);

CREATE UNIQUE INDEX one_leased_attempt_per_step
    ON openmagic_runtime.attempts(step_id) WHERE state = 'leased';

CREATE TABLE openmagic_runtime.waits (
    wait_id uuid PRIMARY KEY,
    instance_id uuid NOT NULL REFERENCES openmagic_runtime.instances(instance_id),
    template_key text NOT NULL,
    route_key text NOT NULL,
    activation_source_kind text NOT NULL,
    activation_source_id uuid NOT NULL,
    output_slot text NOT NULL,
    input jsonb NOT NULL,
    input_digest text NOT NULL,
    state text NOT NULL CHECK (state IN ('unsatisfied', 'satisfied', 'cancelled')),
    satisfying_signal_id uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    satisfied_at timestamptz,
    UNIQUE (instance_id, route_key, activation_source_kind, activation_source_id, output_slot),
    UNIQUE (instance_id, wait_id),
    UNIQUE (satisfying_signal_id)
);

CREATE TABLE openmagic_runtime.signals (
    signal_id uuid PRIMARY KEY,
    instance_id uuid NOT NULL REFERENCES openmagic_runtime.instances(instance_id),
    wait_id uuid NOT NULL UNIQUE,
    signal_type text NOT NULL,
    schema_version integer NOT NULL CHECK (schema_version > 0),
    payload jsonb NOT NULL,
    payload_digest text NOT NULL,
    accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (instance_id, wait_id)
        REFERENCES openmagic_runtime.waits(instance_id, wait_id)
);

CREATE TABLE openmagic_runtime.trace_events (
    trace_event_id uuid PRIMARY KEY,
    instance_id uuid NOT NULL REFERENCES openmagic_runtime.instances(instance_id),
    sequence bigint NOT NULL CHECK (sequence > 0),
    event_type text NOT NULL,
    schema_version integer NOT NULL CHECK (schema_version > 0),
    source_kind text NOT NULL,
    source_id uuid NOT NULL,
    input_digest text NOT NULL,
    receipt jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (instance_id, sequence),
    UNIQUE (source_kind, source_id)
);

CREATE TABLE openmagic_runtime.command_receipts (
    command_id uuid PRIMARY KEY,
    command_type text NOT NULL,
    schema_version integer NOT NULL CHECK (schema_version > 0),
    command_digest text NOT NULL,
    result jsonb NOT NULL,
    result_digest text NOT NULL,
    committed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE openmagic_runtime.threads (
    thread_id uuid PRIMARY KEY,
    channel_kind text NOT NULL,
    channel_reference text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (channel_kind, channel_reference)
);

CREATE TABLE openmagic_runtime.messages (
    message_id uuid PRIMARY KEY,
    thread_id uuid NOT NULL REFERENCES openmagic_runtime.threads(thread_id),
    sequence bigint NOT NULL CHECK (sequence > 0),
    author_kind text NOT NULL,
    author_id text NOT NULL,
    source_kind text NOT NULL CHECK (source_kind IN ('channel', 'delivery', 'agent_run', 'system')),
    source_id uuid NOT NULL,
    content text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (thread_id, sequence),
    UNIQUE (thread_id, source_kind, source_id)
);

CREATE TABLE openmagic_runtime.agent_runs (
    agent_run_id uuid PRIMARY KEY,
    attempt_id uuid NOT NULL UNIQUE REFERENCES openmagic_runtime.attempts(attempt_id),
    agent_key text NOT NULL,
    thread_id uuid NOT NULL REFERENCES openmagic_runtime.threads(thread_id),
    context_through_sequence bigint NOT NULL CHECK (context_through_sequence >= 0),
    input jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'abandoned')),
    result jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz
);

CREATE TABLE openmagic_runtime.deliveries (
    delivery_id uuid PRIMARY KEY,
    domain_event_id uuid NOT NULL,
    thread_id uuid NOT NULL REFERENCES openmagic_runtime.threads(thread_id),
    audience jsonb NOT NULL,
    message_author jsonb NOT NULL,
    content_mode text NOT NULL CHECK (content_mode IN ('template', 'agent')),
    content_descriptor jsonb NOT NULL,
    message_content text NOT NULL,
    retry_policy jsonb NOT NULL,
    context_through_sequence bigint NOT NULL CHECK (context_through_sequence >= 0),
    status text NOT NULL CHECK (status IN ('pending', 'delivered', 'failed', 'suppressed')),
    next_eligible_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    successful_attempt_id uuid,
    delivered_message_id uuid REFERENCES openmagic_runtime.messages(message_id),
    acknowledged_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE openmagic_runtime.delivery_attempts (
    delivery_attempt_id uuid PRIMARY KEY,
    claim_request_id uuid NOT NULL UNIQUE,
    delivery_id uuid NOT NULL REFERENCES openmagic_runtime.deliveries(delivery_id),
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    state text NOT NULL CHECK (state IN ('running', 'succeeded', 'failed', 'abandoned')),
    worker_id text NOT NULL,
    lease_expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    UNIQUE (delivery_id, attempt_number)
);

CREATE UNIQUE INDEX one_running_delivery_attempt
    ON openmagic_runtime.delivery_attempts(delivery_id) WHERE state = 'running';

ALTER TABLE openmagic_runtime.deliveries
    ADD CONSTRAINT deliveries_successful_attempt_fk
    FOREIGN KEY (successful_attempt_id)
    REFERENCES openmagic_runtime.delivery_attempts(delivery_attempt_id);
