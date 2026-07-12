from uuid import uuid4

from server.tests.live.agentmail import AgentMailMessageDetail, AgentMailRecipient
from server.workflows import EmailSendEffectV1


def test_recipient_evidence_requires_one_complete_exact_effect() -> None:
    effect = EmailSendEffectV1(
        sender_mailbox_id=uuid4(),
        expected_sender_address="sender@example.com",
        to=("recipient@example.com",),
        subject="Exact subject",
        body="Exact body",
    )
    exact = AgentMailMessageDetail.model_validate(
        {
            "message_id": "message-1",
            "from": "Sender <sender@example.com>",
            "to": ["recipient@example.com"],
            "subject": "Exact subject",
            "text": "Exact body\n",
            "extracted_text": "Exact body",
        }
    )
    wrong_sender = exact.model_copy(update={"sender": "sender@example.com <wrong@example.com>"})
    wrong_recipient = exact.model_copy(update={"to": ("other@example.com",)})
    wrong_body = exact.model_copy(update={"extracted_text": "Different body"})
    html_body = exact.model_copy(update={"html": "<p>Exact body</p>"})

    assert AgentMailRecipient.contains_exactly_one_effect((exact,), effect)
    assert not AgentMailRecipient.contains_exactly_one_effect((exact, exact), effect)
    assert not AgentMailRecipient.contains_exactly_one_effect((wrong_sender,), effect)
    assert not AgentMailRecipient.contains_exactly_one_effect((wrong_recipient,), effect)
    assert not AgentMailRecipient.contains_exactly_one_effect((wrong_body,), effect)
    assert not AgentMailRecipient.contains_exactly_one_effect((html_body,), effect)
