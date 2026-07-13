# Current issue 11 coverage

This is a coverage inventory, not a completion claim. It separates behavior
already proved on `main` from the missing harness work.

| Required evidence | Current proof | Remaining gap |
| --- | --- | --- |
| Near-duplicate Workflow retrieval | `test_search_workflows.py`, `test_workflow_retrieval_eval.py` | Run the same user cases through both Interaction profiles and report paired results |
| Retrieval rank and bounded context | Hit@1, Hit@3, MRR, bytes, and approximate tokens already recorded | Add packet count, full interaction prompt burden, and trial/build identity |
| Ambiguity and authorization safety | Interaction and retrieval tests assert clarification, no mutation, and no leakage | Include these as named paired scenarios rather than scattered evidence |
| One-Job and concurrent claims | `test_concurrent_claimers_create_one_run_and_count_one_attempt` | None for deterministic claim correctness |
| Worker loss before dispatch | `test_expired_pre_dispatch_run_is_abandoned_and_reclaimed` | Add explicit database disposal and fresh Control Plane restart boundary |
| Worker loss or uncertainty after dispatch | approved-email tests assert waiting and no retry | Package the cases into one visible recovery matrix |
| Approval, revision, cancellation, and dispatch races | approved-email and notification-presentation tests | Package the cases into one visible recovery matrix |
| Duplicate broker input | a second initial graph is rejected | No idempotent same-Cause Workflow proposal or end-to-end duplicate-message assertion |
| Notification loss, lease expiry, and duplicate acknowledgement | notification and correlated-reply tests cover retry, fencing, and reply deduplication | Add delayed and out-of-order multi-Notification cases plus fresh-process restart |
| Restart while awaiting approval | fresh Interaction Agent reloads a Workflow Packet | No explicit close and reopen of database/application boundaries before approval |
| Deterministic versus live Composio | deterministic adapter branches and opt-in exact recipient smoke both exist | Surface them together in one evidence report without running live failures |
| Local, model, and provider latency | live and pytest durations exist | No segmented monotonic timers or paired trial summary |

The missing work naturally forms three focused delivery slices after the
Wayfinder verdict boundary is approved:

1. A paired legacy-versus-Workflow renewal evaluator and evidence report.
2. A restart, duplicate-input, and recovery scenario matrix over real
   PostgreSQL.
3. A Notification fault-ordering matrix and combined V0 evidence summary.
