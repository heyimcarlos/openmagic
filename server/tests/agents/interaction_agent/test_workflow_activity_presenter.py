from server.agents.interaction_agent.activity_presenter import (
    present_activity_input,
    present_activity_result,
)
from server.agents.interaction_agent.toolbox import ToolResult
from server.workflows import InteractionActivityAction, InteractionActivityPresentation


def test_search_activity_exposes_bounded_query_and_ranked_results():
    input_summary = present_activity_input(
        InteractionActivityAction.SEARCH_WORKFLOWS,
        {
            "query": "John Smith renewal",
            "status": "active",
            "renewal_period": "2026",
            "cursor": "opaque-secret-cursor",
        },
    )
    result = present_activity_result(
        InteractionActivityAction.SEARCH_WORKFLOWS,
        ToolResult(
            success=True,
            payload={
                "total_matches": 3,
                "has_more": True,
                "results": [
                    {
                        "objective": "2026 renewal outreach for John Smith",
                        "status": "active",
                        "organization": "Acme Brokerage",
                        "match_reasons": ["exact participant match", "active renewal"],
                    },
                    {
                        "objective": "2025 renewal outreach for John Smith",
                        "status": "completed",
                        "organization": "Acme Brokerage",
                        "match_reasons": ["exact participant match"],
                    },
                ],
            },
        ),
    )

    assert input_summary == ('query "John Smith renewal" · status active · renewal period 2026')
    assert result == InteractionActivityPresentation(
        summary="3 authorized matches, showing 2",
        items=(
            "2026 renewal outreach for John Smith · active · Acme Brokerage · "
            "exact participant match, active renewal",
            "2025 renewal outreach for John Smith · completed · Acme Brokerage · "
            "exact participant match",
            "More authorized matches are available",
        ),
    )
    assert "opaque-secret-cursor" not in input_summary


def test_failed_activity_exposes_safe_error_code_without_raw_payload():
    result = present_activity_result(
        InteractionActivityAction.READ_WORKFLOW_PACKET,
        ToolResult(
            success=False,
            payload={
                "code": "workflow_resolution_required",
                "message": "internal database detail that must not be shown",
            },
        ),
    )

    assert result == InteractionActivityPresentation(
        summary="Failed: select one Workflow before reading its packet",
    )


def test_packet_activity_summarizes_loaded_context_without_dumping_packet():
    result = present_activity_result(
        InteractionActivityAction.READ_WORKFLOW_PACKET,
        ToolResult(
            success=True,
            payload={
                "workflow": {
                    "objective": "2026 renewal outreach for John Smith",
                    "status": "active",
                    "organization": "Acme Brokerage",
                },
                "jobs": [{"id": "draft"}, {"id": "send"}],
                "private_notes": "must not appear",
            },
        ),
    )

    assert result == InteractionActivityPresentation(
        summary="Loaded bounded Workflow Packet",
        items=("2026 renewal outreach for John Smith · active · Acme Brokerage · 2 Jobs",),
    )
