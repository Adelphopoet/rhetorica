import sys
from contextlib import contextmanager
from types import SimpleNamespace

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime import build_agent_run_config
from rhetorica_backend.infra import langfuse


def test_langfuse_is_disabled_without_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("LANGFUSE_CAPTURE_CONTENT", raising=False)

    runtime = langfuse.install_langfuse(
        enabled=False,
        service_version="test",
        environment="test",
    )

    assert runtime.enabled is False
    assert langfuse.langfuse_enabled() is False
    assert langfuse.langfuse_capture_content() is False
    assert build_agent_run_config().tracing_disabled is True


def test_langfuse_content_capture_requires_explicit_true(monkeypatch) -> None:
    monkeypatch.setenv("LANGFUSE_CAPTURE_CONTENT", "true")

    assert langfuse.langfuse_capture_content() is True

    monkeypatch.setenv("LANGFUSE_CAPTURE_CONTENT", "false")

    assert langfuse.langfuse_capture_content() is False


def test_real_openinference_policy_hides_all_content_fields_by_default() -> None:
    config = langfuse.content_minimizing_trace_config(capture_content=False)

    assert config.hide_inputs is True
    assert config.hide_outputs is True
    assert config.hide_input_messages is True
    assert config.hide_output_messages is True
    assert config.hide_input_text is True
    assert config.hide_output_text is True
    assert config.hide_llm_tools is True


def test_langfuse_replaces_default_agents_sdk_processor(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeLangfuse:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def shutdown(self) -> None:
            pass

    class FakeOpenAIAgentsInstrumentor:
        is_instrumented_by_opentelemetry = False

        def instrument(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://langfuse.test")
    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(Langfuse=FakeLangfuse))
    monkeypatch.setitem(
        sys.modules,
        "openinference.instrumentation.openai_agents",
        SimpleNamespace(OpenAIAgentsInstrumentor=FakeOpenAIAgentsInstrumentor),
    )

    runtime = langfuse.install_langfuse(
        enabled=True,
        service_version="test",
        environment="test",
    )

    assert runtime.enabled is True
    assert captured["exclusive_processor"] is True
    langfuse.install_langfuse(enabled=False, service_version="test", environment="test")


def test_failed_langfuse_initialization_disables_agents_sdk_tracing(monkeypatch) -> None:
    class FailingLangfuse:
        def __init__(self, **_kwargs: object) -> None:
            raise RuntimeError("test initialization failure")

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://langfuse.test")
    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(Langfuse=FailingLangfuse))

    runtime = langfuse.install_langfuse(
        enabled=True,
        service_version="test",
        environment="test",
    )

    assert runtime.enabled is False
    assert langfuse.langfuse_enabled() is False
    assert build_agent_run_config().tracing_disabled is True


def test_preexisting_openinference_instrumentation_disables_agents_sdk_tracing(monkeypatch) -> None:
    client_shutdown_called = False

    class FakeLangfuse:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def shutdown(self) -> None:
            nonlocal client_shutdown_called
            client_shutdown_called = True

    class FakeOpenAIAgentsInstrumentor:
        is_instrumented_by_opentelemetry = True

        def instrument(self, **_kwargs: object) -> None:
            raise AssertionError("instrument must not run twice")

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://langfuse.test")
    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(Langfuse=FakeLangfuse))
    monkeypatch.setitem(
        sys.modules,
        "openinference.instrumentation.openai_agents",
        SimpleNamespace(OpenAIAgentsInstrumentor=FakeOpenAIAgentsInstrumentor),
    )

    runtime = langfuse.install_langfuse(
        enabled=True,
        service_version="test",
        environment="test",
    )

    assert runtime.enabled is False
    assert client_shutdown_called is True
    assert build_agent_run_config().tracing_disabled is True


def test_shutdown_disables_the_runtime() -> None:
    class FakeLangfuse:
        def __init__(self) -> None:
            self.shutdown_called = False

        def shutdown(self) -> None:
            self.shutdown_called = True

    client = FakeLangfuse()
    runtime = langfuse.LangfuseRuntime(enabled=True, client=client)

    runtime.shutdown()

    assert client.shutdown_called is True
    assert runtime.enabled is False


def test_langfuse_root_trace_captures_question_and_final_answer_when_enabled(monkeypatch) -> None:
    observation_calls: list[dict[str, object]] = []
    propagated_attributes: list[dict[str, object]] = []

    class FakeObservation:
        def __init__(self) -> None:
            self.updates: list[dict[str, object]] = []

        def update(self, **kwargs: object) -> None:
            self.updates.append(kwargs)

    observation = FakeObservation()

    class FakeClient:
        @contextmanager
        def start_as_current_observation(self, **kwargs: object):
            observation_calls.append(kwargs)
            yield observation

    @contextmanager
    def fake_propagate_attributes(**kwargs: object):
        propagated_attributes.append(kwargs)
        yield

    monkeypatch.setenv("LANGFUSE_CAPTURE_CONTENT", "true")
    monkeypatch.setattr(langfuse, "_runtime", langfuse.LangfuseRuntime(True, FakeClient()))
    monkeypatch.setattr("langfuse.propagate_attributes", fake_propagate_attributes)

    with langfuse.langfuse_agent_turn(
        ChatAskRequest(question="Какие продажи за неделю?", conversation_id="conversation-1"),
        streamed=False,
    ) as turn:
        turn.record_final_output("Продажи составили 42 млн ₽.")

    assert observation_calls == [
        {
            "as_type": "agent",
            "name": "rhetorica.chat.turn",
            "input": "Какие продажи за неделю?",
        }
    ]
    assert observation.updates == [{"output": "Продажи составили 42 млн ₽."}]
    assert propagated_attributes[0]["session_id"] == "conversation-1"


def test_langfuse_root_trace_does_not_capture_content_when_disabled(monkeypatch) -> None:
    observation_calls: list[dict[str, object]] = []

    class FakeObservation:
        def update(self, **_kwargs: object) -> None:
            raise AssertionError("content must not be recorded")

    class FakeClient:
        @contextmanager
        def start_as_current_observation(self, **kwargs: object):
            observation_calls.append(kwargs)
            yield FakeObservation()

    @contextmanager
    def fake_propagate_attributes(**_kwargs: object):
        yield

    monkeypatch.setenv("LANGFUSE_CAPTURE_CONTENT", "false")
    monkeypatch.setattr(langfuse, "_runtime", langfuse.LangfuseRuntime(True, FakeClient()))
    monkeypatch.setattr("langfuse.propagate_attributes", fake_propagate_attributes)

    with langfuse.langfuse_agent_turn(
        ChatAskRequest(question="Не экспортируй этот вопрос"), streamed=False
    ) as turn:
        turn.record_final_output("Не экспортируй этот ответ")

    assert observation_calls[0]["input"] is None
