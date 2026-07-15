from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

from example_insurance.renewals import (
    ExampleInsurance,
    SubmitVerificationCode,
    SubmitVerificationCodeInput,
)
from openmagic_evals.harness import (
    TestDeployment,
    issue_verification_challenge,
    renewal_context,
)
from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.kernel.inspection import KernelInspection
from openmagic_runtime.threads import ThreadStore

_SUBMISSION_SCRIPT = """
import json
import sys
import time
from uuid import UUID
from example_insurance.renewals import ExampleInsurance, SubmitVerificationCode, SubmitVerificationCodeInput
from openmagic_runtime.commands import Actor, Cause

value = json.load(sys.stdin)
start_at = value.get("start_at")
if start_at is not None:
    time.sleep(max(0, start_at - time.time()))
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
print(json.dumps({"command_id": value["command_id"], "outcome": receipt.result.verification_outcome}))
"""


def _run_conflicting_submissions(
    tmp_path: Path, payloads: tuple[dict[str, object], dict[str, object]]
) -> tuple[dict[str, str], dict[str, str]]:
    start_at = time.time() + 0.5
    processes = tuple(
        subprocess.Popen(
            [sys.executable, "-c", _SUBMISSION_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=tmp_path,
            env={"PATH": os.defpath, "PYTHONNOUSERSITE": "1"},
        )
        for _ in payloads
    )
    for process, payload in zip(processes, payloads, strict=True):
        assert process.stdin is not None
        process.stdin.write(json.dumps({**payload, "start_at": start_at}))
        process.stdin.close()
    results: list[dict[str, str]] = []
    for process in processes:
        assert process.stdout is not None
        assert process.stderr is not None
        stdout = process.stdout.read()
        stderr = process.stderr.read()
        assert process.wait(timeout=10) == 0, stderr
        results.append(json.loads(stdout))
    return results[0], results[1]


def _submission_payload(
    *,
    database_url: str,
    secret: str,
    party_id: str,
    challenge_id: str,
    protected_command_id: str,
    workflow_id: str,
    thread_id: str,
    code: str,
) -> dict[str, object]:
    return {
        "database_url": database_url,
        "secret": secret,
        "command_id": str(uuid4()),
        "party_id": party_id,
        "cause_id": str(uuid4()),
        "challenge_id": challenge_id,
        "protected_command_id": protected_command_id,
        "workflow_id": workflow_id,
        "thread_id": thread_id,
        "code": code,
    }


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
                _SUBMISSION_SCRIPT,
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
        assert child_result["outcome"] == "verified"


def test_separate_processes_serialize_single_use_code_acceptance(tmp_path: Path) -> None:
    secret = "issue-70-process-code-race"
    with renewal_context(verification_code_secret=secret.encode()) as (
        database_url,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads)
        challenge_id = scenario.challenge_receipt.result.challenge_id
        assert challenge_id is not None
        assert scenario.code is not None
        common = {
            "database_url": database_url,
            "secret": secret,
            "party_id": scenario.actor.identifier,
            "challenge_id": str(challenge_id),
            "protected_command_id": str(scenario.protected_command.command_id),
            "workflow_id": str(scenario.renewal.input.workflow_id),
            "thread_id": str(scenario.renewal.input.thread_id),
            "code": scenario.code,
        }
        payloads = (
            _submission_payload(**common),
            _submission_payload(**common),
        )

        results = _run_conflicting_submissions(tmp_path, payloads)
        winner = next(
            payload
            for payload, result in zip(payloads, results, strict=True)
            if result["outcome"] == "verified"
        )
        replay = application.submit_verification_code(
            SubmitVerificationCode(
                command_id=UUID(str(winner["command_id"])),
                actor=Actor("party", str(winner["party_id"])),
                cause=Cause("message", str(winner["cause_id"])),
                input=SubmitVerificationCodeInput(
                    challenge_id=UUID(str(winner["challenge_id"])),
                    protected_command_id=UUID(str(winner["protected_command_id"])),
                    workflow_id=UUID(str(winner["workflow_id"])),
                    thread_id=UUID(str(winner["thread_id"])),
                    purpose="renewal.read_approved_details",
                    code=str(winner["code"]),
                ),
            )
        )

        assert sorted(result["outcome"] for result in results) == ["already_used", "verified"]
        assert replay.result.verification_outcome == "verified"


def test_separate_processes_serialize_final_failed_attempts(tmp_path: Path) -> None:
    secret = "issue-70-process-attempt-race"
    with renewal_context(verification_code_secret=secret.encode()) as (
        database_url,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads)
        challenge_id = scenario.challenge_receipt.result.challenge_id
        assert challenge_id is not None
        assert scenario.code is not None
        wrong_code = "000000" if scenario.code != "000000" else "999999"

        def command(code: str) -> SubmitVerificationCode:
            return SubmitVerificationCode(
                command_id=uuid4(),
                actor=scenario.actor,
                cause=Cause("message", str(uuid4())),
                input=SubmitVerificationCodeInput(
                    challenge_id=challenge_id,
                    protected_command_id=scenario.protected_command.command_id,
                    workflow_id=scenario.renewal.input.workflow_id,
                    thread_id=scenario.renewal.input.thread_id,
                    purpose="renewal.read_approved_details",
                    code=code,
                ),
            )

        for _ in range(4):
            assert (
                application.submit_verification_code(
                    command(wrong_code)
                ).result.verification_outcome
                == "invalid_code"
            )
        common = {
            "database_url": database_url,
            "secret": secret,
            "party_id": scenario.actor.identifier,
            "challenge_id": str(challenge_id),
            "protected_command_id": str(scenario.protected_command.command_id),
            "workflow_id": str(scenario.renewal.input.workflow_id),
            "thread_id": str(scenario.renewal.input.thread_id),
            "code": wrong_code,
        }
        results = _run_conflicting_submissions(
            tmp_path,
            (_submission_payload(**common), _submission_payload(**common)),
        )
        later_correct = application.submit_verification_code(command(scenario.code))

        assert [result["outcome"] for result in results] == ["invalid_code", "invalid_code"]
        assert later_correct.result.verification_outcome == "invalid_code"
