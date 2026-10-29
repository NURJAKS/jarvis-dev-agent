"""Unit tests for the deterministic (non-LLM) layers of J.A.R.V.I.S.

These cover the parts of the pipeline that must be predictable:
speech-term normalization, multilingual intent routing and the
quota detector that drives LLM provider failover.
"""
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "jarvis", Path(__file__).resolve().parent.parent / "jarvis.py"
)
jarvis = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(jarvis)


# ── Level 1.5: acoustic normalization ─────────────────────────────

@pytest.mark.parametrize(
    "raw, expected_fragment",
    [
        ("гит див", "git diff"),
        ("git div", "git diff"),
        ("докер", "docker"),
        ("кубер", "kubernetes"),
        ("ворктри", "worktree"),
    ],
)
def test_normalize_voice_terms_fixes_stt_misrecognitions(raw, expected_fragment):
    assert expected_fragment in jarvis.normalize_voice_terms(raw)


def test_normalize_voice_terms_handles_empty_input():
    assert jarvis.normalize_voice_terms("") == ""


# ── Level 3: multilingual intent routing ──────────────────────────

@pytest.mark.parametrize(
    "utterance",
    [
        "сделай ревью",                 # RU
        "code review please",           # EN
        "проверь изменения в ветке",    # RU, verb + object
        "кодты қара",                   # KZ
    ],
)
def test_review_intent_is_routed(utterance):
    command, matched = jarvis.match_developer_intent(utterance)
    assert matched is True
    assert command == "/review"


def test_triage_intent_extracts_run_id():
    command, matched = jarvis.match_developer_intent("почему упал пайплайн 39458369159")
    assert matched is True
    assert command == "/triage 39458369159"


def test_triage_intent_without_run_id():
    command, matched = jarvis.match_developer_intent("triage")
    assert matched is True
    assert command == "/triage"


@pytest.mark.parametrize("utterance", ["план на сегодня", "focus", "daily agenda"])
def test_focus_intent_is_routed(utterance):
    command, matched = jarvis.match_developer_intent(utterance)
    assert matched is True
    assert command == "/focus"


def test_trace_intent_extracts_symbol():
    command, matched = jarvis.match_developer_intent("where is deductCredits")
    assert matched is True
    assert command == "/trace deductCredits"


def test_task_intent_extracts_instruction():
    command, matched = jarvis.match_developer_intent("запусти в фоне добавить тесты для auth")
    assert matched is True
    assert command == "/task добавить тесты для auth"


def test_voice_toggle_intents():
    assert jarvis.match_developer_intent("voice on") == ("/voice on", True)
    assert jarvis.match_developer_intent("mute") == ("/voice off", True)


def test_free_form_question_falls_through_to_llm():
    command, matched = jarvis.match_developer_intent("explain the CAP theorem")
    assert matched is False
    assert command == "explain the CAP theorem"


def test_empty_utterance():
    assert jarvis.match_developer_intent("   ") == ("", False)


# ── Provider failover trigger ─────────────────────────────────────

@pytest.mark.parametrize(
    "message",
    [
        "Error 429: Too Many Requests",
        "RESOURCE_EXHAUSTED: You exceeded your current quota",
        "rate_limit reached for model",
    ],
)
def test_quota_errors_trigger_failover(message):
    assert jarvis.is_llm_quota_error(message) is True


@pytest.mark.parametrize("message", ["", "Here is your refactored function", None])
def test_normal_responses_do_not_trigger_failover(message):
    assert jarvis.is_llm_quota_error(message) is False
