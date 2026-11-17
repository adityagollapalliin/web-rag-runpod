import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.services.answering import NO_RESULTS_ANSWER
from tests.conftest import bearer, signup_and_login

PLANTS = (
    "Photosynthesis converts sunlight into chemical energy inside chloroplasts. "
    "Plants absorb carbon dioxide and release oxygen during this process."
)
BUDGET = (
    "The quarterly budget allocates funds to marketing and engineering. "
    "Invoices must be approved by the finance director before payment."
)


def upload(client: TestClient, token: str, name: str, text: str) -> str:
    response = client.post(
        "/api/documents", files={"file": (name, text.encode())}, headers=bearer(token)
    )
    assert response.status_code == 202 and response.json()["status"] == "ready", response.text
    document_id: str = response.json()["id"]
    return document_id


def ask(
    client: TestClient, token: str, question: str, conversation_id: str | None = None
) -> dict[str, Any]:
    """Call /api/ask and fold the event stream into {sources, answer, done, events}."""
    body: dict[str, Any] = {"question": question}
    if conversation_id:
        body["conversation_id"] = conversation_id
    response = client.post("/api/ask", json=body, headers=bearer(token))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in response.text.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    names = [name for name, _ in events]
    assert names[0] == "sources" and names[-1] == "done", names
    assert set(names[1:-1]) == {"token"}
    return {
        "sources": events[0][1]["sources"],
        "answer": "".join(data["text"] for name, data in events if name == "token").strip(),
        "done": events[-1][1],
        "token_events": names.count("token"),
    }


def test_answer_streams_with_sources_and_is_saved(client: TestClient) -> None:
    token = signup_and_login(client, "a@example.com")
    document_id = upload(client, token, "plants.txt", PLANTS)

    result = ask(client, token, "What happens inside chloroplasts?")

    assert result["token_events"] > 3  # streamed in pieces, not one blob
    assert "Photosynthesis converts sunlight" in result["answer"] and "[1]" in result["answer"]
    assert result["done"]["cached"] is False
    [source] = result["sources"]
    assert source["n"] == 1 and source["document_id"] == document_id
    assert source["filename"] == "plants.txt" and source["page_start"] == 1
    assert "chloroplasts" in source["snippet"] and "text" not in source

    conversation_id = result["done"]["conversation_id"]
    listed = client.get("/api/conversations", headers=bearer(token)).json()
    assert [c["id"] for c in listed] == [conversation_id]
    assert listed[0]["title"] == "What happens inside chloroplasts?"
    detail = client.get(f"/api/conversations/{conversation_id}", headers=bearer(token)).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["id"] == result["done"]["message_id"]
    assert detail["messages"][1]["content"] == result["answer"]
    assert detail["messages"][1]["citations"] == result["sources"]
    assert detail["messages"][0]["citations"] == []


def test_question_with_no_matching_documents(client: TestClient) -> None:
    token = signup_and_login(client, "a@example.com")
    result = ask(client, token, "What is the meaning of life?")
    assert result["sources"] == []
    assert result["answer"] == NO_RESULTS_ANSWER


def test_best_matching_chunk_is_ranked_first(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "chunk_max_chars", 120)
    monkeypatch.setattr(get_settings(), "chunk_overlap_chars", 0)
    monkeypatch.setattr(get_settings(), "context_chunks", 3)
    token = signup_and_login(client, "a@example.com")
    filler = " ".join(f"Filler sentence number {i} talks about nothing special." for i in range(30))
    upload(
        client, token, "long.txt", f"{filler} The zygomorphic orchid blooms at midnight. {filler}"
    )

    result = ask(client, token, "When does the zygomorphic orchid bloom?")

    assert [s["n"] for s in result["sources"]] == [1, 2, 3]  # capped at context_chunks
    assert "zygomorphic orchid blooms at midnight" in result["sources"][0]["snippet"]
    scores = [s["score"] for s in result["sources"]]
    assert scores == sorted(scores, reverse=True)
    assert "zygomorphic orchid blooms at midnight" in result["answer"]


def test_users_only_get_answers_from_their_own_documents(client: TestClient) -> None:
    alice = signup_and_login(client, "a@example.com")
    bob = signup_and_login(client, "b@example.com")
    alice_doc = upload(client, alice, "plants.txt", PLANTS)
    bob_doc = upload(client, bob, "budget.txt", BUDGET)

    # Bob asks about Alice's topic in her exact words: he gets only his own document back.
    result = ask(client, bob, "photosynthesis chloroplasts carbon dioxide oxygen")
    assert {s["document_id"] for s in result["sources"]} <= {bob_doc}
    assert "Photosynthesis" not in result["answer"]
    assert alice_doc not in json.dumps(result)

    result = ask(client, alice, "Who approves invoices in the quarterly budget?")
    assert {s["document_id"] for s in result["sources"]} <= {alice_doc}
    assert "finance director" not in result["answer"]


def test_repeat_question_is_cached_until_documents_change(client: TestClient) -> None:
    token = signup_and_login(client, "a@example.com")
    upload(client, token, "plants.txt", PLANTS)

    first = ask(client, token, "What do plants absorb?")
    second = ask(client, token, "  what do PLANTS absorb? ")  # same after normalising
    assert first["done"]["cached"] is False and second["done"]["cached"] is True
    assert second["answer"] == first["answer"] and second["sources"] == first["sources"]
    assert second["done"]["conversation_id"] != first["done"]["conversation_id"]

    # A new document changes the corpus, so the cached answer is no longer used.
    extra = upload(client, token, "more.txt", "Plants absorb water through their roots.")
    third = ask(client, token, "What do plants absorb?")
    assert third["done"]["cached"] is False
    assert extra in {s["document_id"] for s in third["sources"]}

    # Deleting it changes the corpus again.
    client.delete(f"/api/documents/{extra}", headers=bearer(token))
    fourth = ask(client, token, "What do plants absorb?")
    assert fourth["done"]["cached"] is False
    assert extra not in {s["document_id"] for s in fourth["sources"]}


def test_cache_is_not_shared_between_users(client: TestClient) -> None:
    alice = signup_and_login(client, "a@example.com")
    bob = signup_and_login(client, "b@example.com")
    upload(client, alice, "plants.txt", PLANTS)
    ask(client, alice, "What do plants absorb?")
    result = ask(client, bob, "What do plants absorb?")
    assert result["done"]["cached"] is False and result["sources"] == []


def test_follow_up_extends_the_conversation(client: TestClient) -> None:
    token = signup_and_login(client, "a@example.com")
    upload(client, token, "plants.txt", PLANTS)
    first = ask(client, token, "What do plants absorb?")
    conversation_id = first["done"]["conversation_id"]

    follow_up = ask(client, token, "What do plants absorb?", conversation_id)
    assert follow_up["done"]["conversation_id"] == conversation_id
    assert follow_up["done"]["cached"] is False  # follow-ups are never served from cache

    detail = client.get(f"/api/conversations/{conversation_id}", headers=bearer(token)).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    assert len(client.get("/api/conversations", headers=bearer(token)).json()) == 1


def test_conversations_are_private(client: TestClient) -> None:
    alice = signup_and_login(client, "a@example.com")
    bob = signup_and_login(client, "b@example.com")
    upload(client, alice, "plants.txt", PLANTS)
    conversation_id = ask(client, alice, "What do plants absorb?")["done"]["conversation_id"]
    url = f"/api/conversations/{conversation_id}"

    assert client.get("/api/conversations", headers=bearer(bob)).json() == []
    assert client.get(url, headers=bearer(bob)).status_code == 404
    assert client.delete(url, headers=bearer(bob)).status_code == 404
    hijack = client.post(
        "/api/ask",
        json={"question": "hello", "conversation_id": conversation_id},
        headers=bearer(bob),
    )
    assert hijack.status_code == 404
    assert (
        client.post(
            "/api/ask",
            json={"question": "hi", "conversation_id": str(uuid.uuid4())},
            headers=bearer(bob),
        ).status_code
        == 404
    )

    assert client.delete(url, headers=bearer(alice)).status_code == 204
    assert client.get(url, headers=bearer(alice)).status_code == 404


def test_ask_validation_auth_and_rate_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.post("/api/ask", json={"question": "hi"}).status_code == 401
    token = signup_and_login(client, "a@example.com")
    assert client.post("/api/ask", json={"question": ""}, headers=bearer(token)).status_code == 422
    too_long = {"question": "x" * 2001}
    assert client.post("/api/ask", json=too_long, headers=bearer(token)).status_code == 422

    monkeypatch.setattr(get_settings(), "ask_rate_limit_per_minute", 2)
    codes = [
        client.post("/api/ask", json={"question": f"q{i}"}, headers=bearer(token)).status_code
        for i in range(3)
    ]
    assert codes == [200, 200, 429]


def test_model_failure_is_reported_as_an_error_event(client: TestClient) -> None:
    token = signup_and_login(client, "a@example.com")
    upload(client, token, "plants.txt", PLANTS)

    class BrokenChat:
        def stream(self, messages: Any) -> Any:
            raise RuntimeError("model server is down")

        async def aclose(self) -> None:
            return None

    client.app.state.chat_model = BrokenChat()  # type: ignore[attr-defined]
    response = client.post(
        "/api/ask", json={"question": "What do plants absorb?"}, headers=bearer(token)
    )
    assert response.status_code == 200
    assert response.text.strip().split("\n\n")[-1].startswith("event: error")
    assert "model server is down" not in response.text  # internals are not leaked
    assert (
        client.get("/api/conversations", headers=bearer(token)).json() == []
    )  # nothing half-saved


def test_unreachable_model_reports_that_models_are_starting(client: TestClient) -> None:
    from app.api.ask import MODELS_STARTING_MESSAGE
    from app.services.chat import OpenAIChatModel

    token = signup_and_login(client, "starting@example.com")
    upload(client, token, "plants.txt", PLANTS)
    # Nothing listens on port 1: the same refusal as a model server that is still loading.
    app_state = client.app.state  # type: ignore[attr-defined]
    real_model = app_state.chat_model
    app_state.chat_model = OpenAIChatModel("http://127.0.0.1:1", "m", 10)
    try:
        response = client.post(
            "/api/ask",
            json={"question": "What happens inside chloroplasts?"},
            headers=bearer(token),
        )
    finally:
        app_state.chat_model = real_model
    events = [block.split("\n", 1) for block in response.text.strip().split("\n\n")]
    name, data = events[-1]
    assert name == "event: error"
    assert json.loads(data.removeprefix("data: "))["message"] == MODELS_STARTING_MESSAGE
