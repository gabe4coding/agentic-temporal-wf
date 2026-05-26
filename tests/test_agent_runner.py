import json

from src.agent_runner.stream_codec import (
    encode_message,
    decode_messages,
)


def test_round_trip_assistant_message():
    line = encode_message({"type": "assistant", "content": "hi"})
    decoded = list(decode_messages([line]))
    assert decoded == [{"type": "assistant", "content": "hi"}]


def test_decode_skips_garbage_lines():
    lines = ["not json", json.dumps({"type": "result", "result": "ok"})]
    out = list(decode_messages(lines))
    assert out == [{"type": "result", "result": "ok"}]
