CREATE TABLE openmagic_runtime.deployment_metadata (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    installed_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO openmagic_runtime.deployment_metadata (singleton) VALUES (true);
