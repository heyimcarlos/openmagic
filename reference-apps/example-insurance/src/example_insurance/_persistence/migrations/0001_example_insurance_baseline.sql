CREATE TABLE example_insurance.deployment_metadata (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    installed_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO example_insurance.deployment_metadata (singleton) VALUES (true);
