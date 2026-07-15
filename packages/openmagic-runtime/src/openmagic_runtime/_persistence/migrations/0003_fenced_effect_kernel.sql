ALTER TABLE openmagic_runtime.steps
    ADD COLUMN deferred_attempt_id uuid REFERENCES openmagic_runtime.attempts(attempt_id);

CREATE INDEX deferred_steps_by_attempt
    ON openmagic_runtime.steps(deferred_attempt_id)
    WHERE deferred_attempt_id IS NOT NULL;
