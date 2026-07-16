CREATE TABLE example_insurance.deployment_metadata (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    deployment_purpose text NOT NULL DEFAULT 'production'
        CHECK (deployment_purpose IN ('production', 'synthetic')),
    installed_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO example_insurance.deployment_metadata (singleton) VALUES (true);
