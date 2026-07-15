from __future__ import annotations

import argparse
import json
from uuid import uuid4

from example_insurance.renewals import ExampleInsurance


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m openmagic_evals.harness.fence_once")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--email-provider-url", required=True)
    parser.add_argument("--worker-id", required=True)
    arguments = parser.parse_args()
    application = ExampleInsurance(
        database_url=arguments.database_url,
        email_provider_url=arguments.email_provider_url,
    )
    application.prepare()
    attempt = application.claim_workflow_attempt(
        worker_id=arguments.worker_id,
        claim_request_id=uuid4(),
    )
    if attempt is None or attempt.template_key != "send_renewal_email":
        raise RuntimeError("Fenced-effect helper did not claim the email Step")
    permit = application.authorize_email_dispatch(
        attempt=attempt,
        worker_id=arguments.worker_id,
    )
    print(
        json.dumps(
            {
                "attempt_id": str(attempt.attempt_id),
                "logical_effect_id": str(permit.logical_effect_id),
            }
        )
    )


if __name__ == "__main__":
    main()
