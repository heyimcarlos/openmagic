from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from example_insurance.renewals import (
    ExampleInsurance,
)
from openmagic_evals.harness import (
    TestDeployment,
    issue_verification_challenge,
)
from openmagic_runtime.kernel.inspection import KernelInspection
from openmagic_runtime.threads import ThreadStore


def test_process_termination_restart_reconstructs_verification_from_postgresql(
    tmp_path: Path,
) -> None:
    secret = "issue-70-separate-process-secret"
    with TestDeployment(
        working_directory=tmp_path,
        verification_code_secret=secret,
    ) as deployment:
        stopped_workflow = deployment.terminate_role("workflow-worker")
        stopped_delivery = deployment.terminate_role("delivery-worker")
        application = ExampleInsurance(
            database_url=deployment.database_url,
            verification_code_secret=secret.encode(),
        )
        application.prepare()
        threads = ThreadStore(database_url=deployment.database_url)
        scenario = issue_verification_challenge(
            application,
            threads,
            run_workflow=False,
        )
        renewal = scenario.renewal
        actor = scenario.actor
        protected = scenario.protected_command
        required = scenario.challenge_receipt
        assert required.result.challenge_id is not None
        assert required.result.verification_instance_id is not None

        restarted_workflow = deployment.restart_role("workflow-worker")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if (
                KernelInspection(database_url=deployment.database_url)
                .snapshot(required.result.verification_instance_id)
                .state
                == "closed"
            ):
                break
            time.sleep(0.02)
        else:
            raise AssertionError("Restarted Workflow Worker did not complete verification")

        restarted_delivery = deployment.restart_role("delivery-worker")
        deadline = time.monotonic() + 10
        code_match = None
        while time.monotonic() < deadline:
            messages = threads.read(scenario.identifier_thread_id).messages
            if messages:
                code_match = re.search(r"\b(\d{6})\b", messages[-1].content)
            if code_match is not None:
                break
            time.sleep(0.02)
        assert code_match is not None
        deployment.terminate_role("delivery-worker")

        payload = {
            "database_url": deployment.database_url,
            "secret": secret,
            "command_id": str(uuid4()),
            "party_id": actor.identifier,
            "cause_id": str(uuid4()),
            "challenge_id": str(required.result.challenge_id),
            "protected_command_id": str(protected.command_id),
            "workflow_id": str(renewal.input.workflow_id),
            "thread_id": str(renewal.input.thread_id),
            "code": code_match.group(1),
        }
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import json
import sys
from uuid import UUID
from example_insurance.renewals import ExampleInsurance, SubmitVerificationCode, SubmitVerificationCodeInput
from openmagic_runtime.commands import Actor, Cause

value = json.load(sys.stdin)
application = ExampleInsurance(
    database_url=value["database_url"],
    verification_code_secret=value["secret"].encode(),
)
application.prepare()
receipt = application.submit_verification_code(
    SubmitVerificationCode(
        command_id=UUID(value["command_id"]),
        actor=Actor("party", value["party_id"]),
        cause=Cause("message", value["cause_id"]),
        input=SubmitVerificationCodeInput(
            challenge_id=UUID(value["challenge_id"]),
            protected_command_id=UUID(value["protected_command_id"]),
            workflow_id=UUID(value["workflow_id"]),
            thread_id=UUID(value["thread_id"]),
            purpose="renewal.read_approved_details",
            code=value["code"],
        ),
    )
)
print(json.dumps({"outcome": receipt.result.verification_outcome}))
""",
            ],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=True,
            cwd=tmp_path,
            env={"PATH": os.defpath, "PYTHONNOUSERSITE": "1"},
        )
        child_result = json.loads(child.stdout)
        resumed_delivery = deployment.restart_role("delivery-worker")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            messages = threads.read(renewal.input.thread_id).messages
            if "Approved renewal details" in messages[-1].content:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("Restarted Delivery Worker did not resume exact Thread")

        assert restarted_workflow.pid != stopped_workflow.pid
        assert restarted_delivery.pid != stopped_delivery.pid
        assert resumed_delivery.pid != restarted_delivery.pid
        assert child_result == {"outcome": "verified"}
