CREATE TABLE example_insurance.renewal_workflows (
    workflow_id uuid PRIMARY KEY,
    start_command_id uuid NOT NULL UNIQUE,
    instance_id uuid NOT NULL UNIQUE REFERENCES openmagic_runtime.instances(instance_id),
    thread_id uuid NOT NULL REFERENCES openmagic_runtime.threads(thread_id),
    policy_id uuid NOT NULL,
    policy_number text NOT NULL,
    policyholder_name text NOT NULL,
    renewal_date date NOT NULL,
    expiring_premium_cents bigint NOT NULL CHECK (expiring_premium_cents > 0),
    lifecycle text NOT NULL CHECK (lifecycle IN ('active', 'completed', 'cancelled')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE example_insurance.domain_events (
    event_id uuid PRIMARY KEY,
    event_type text NOT NULL,
    schema_version integer NOT NULL CHECK (schema_version > 0),
    workflow_id uuid REFERENCES example_insurance.renewal_workflows(workflow_id),
    actor jsonb NOT NULL,
    cause jsonb NOT NULL,
    payload jsonb NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE example_insurance.renewal_drafts (
    draft_id uuid PRIMARY KEY,
    workflow_id uuid NOT NULL UNIQUE REFERENCES example_insurance.renewal_workflows(workflow_id),
    step_id uuid NOT NULL UNIQUE REFERENCES openmagic_runtime.steps(step_id),
    agent_run_id uuid NOT NULL UNIQUE REFERENCES openmagic_runtime.agent_runs(agent_run_id),
    subject text NOT NULL,
    body text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
