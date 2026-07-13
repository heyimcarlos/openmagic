from pathlib import Path

from server.services.conversation import ConversationSessionStore


def test_interaction_sessions_keep_phone_conversations_isolated(tmp_path: Path):
    store = ConversationSessionStore(tmp_path)
    john = store.get("sms-policyholder-demo")
    broker = store.get("sms-broker-demo")

    john.log.record_user_message("Show my renewal details")
    john.log.record_reply("Verification required")

    assert [message.content for message in john.log.to_chat_messages()] == [
        "Show my renewal details",
        "Verification required",
    ]
    assert broker.log.to_chat_messages() == []
    assert store.get("sms-policyholder-demo").log is john.log
