"""Unit tests for J.A.R.V.I.S. Standalone Mode (Zero-OpenClaw dependency).

Verifies that the assistant works out of the box with only standard LLM keys
(Gemini 2.5 Flash, Groq, or Ollama) and gracefully degrades without crashes
when the optional OpenClaw engine is not installed on the host.
"""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "jarvis", Path(__file__).resolve().parent.parent / "jarvis.py"
)
jarvis = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(jarvis)


# ── Runtime detection ─────────────────────────────────────────────

def test_has_openclaw_runtime_returns_false_for_none():
    with patch.object(jarvis, "OPENCLAW_BIN", None):
        assert jarvis.has_openclaw_runtime() is False


def test_has_openclaw_runtime_returns_false_for_nonexistent_binary(tmp_path):
    fake_bin = tmp_path / "nonexistent_openclaw_bin_xyz"
    with patch.object(jarvis, "OPENCLAW_BIN", fake_bin):
        assert jarvis.has_openclaw_runtime() is False


def test_has_openclaw_runtime_returns_true_for_executable(tmp_path):
    fake_bin = tmp_path / "openclaw"
    fake_bin.write_text("#!/bin/sh\necho ok\n")
    fake_bin.chmod(0o755)
    with patch.object(jarvis, "OPENCLAW_BIN", fake_bin):
        assert jarvis.has_openclaw_runtime() is True


def test_has_openclaw_runtime_checks_system_path_when_command_name():
    with patch.object(jarvis, "OPENCLAW_BIN", "custom_openclaw_cmd"):
        with patch("shutil.which", return_value="/usr/local/bin/custom_openclaw_cmd"):
            assert jarvis.has_openclaw_runtime() is True

        with patch("shutil.which", return_value=None):
            assert jarvis.has_openclaw_runtime() is False


# ── Direct Gemini 2.5 Flash standalone turn ────────────────────────

def test_direct_gemini_turn_returns_none_without_api_key():
    with patch.object(jarvis, "get_gemini_api_key", return_value=None):
        result = jarvis.direct_gemini_turn("test prompt", stream=False)
        assert result is None


def test_direct_gemini_turn_success_parses_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "Hello from Gemini 2.5 Flash!"}]
                }
            }
        ]
    }
    with patch.object(jarvis, "get_gemini_api_key", return_value="fake_key"):
        with patch("requests.post", return_value=mock_resp):
            res = jarvis.direct_gemini_turn("ping", stream=False)
            assert res == "Hello from Gemini 2.5 Flash!"


# ── Graceful failover in agent turn callers ────────────────────────

def test_run_openclaw_agent_falls_back_when_runtime_missing():
    with patch.object(jarvis, "has_openclaw_runtime", return_value=False):
        with patch.object(jarvis, "fallback_llm_turn", return_value="Standalone reply") as mock_fallback:
            reply = jarvis.run_openclaw_agent("explain microservices")
            assert reply == "Standalone reply"
            mock_fallback.assert_called_once_with("explain microservices", stream=False)


def test_run_openclaw_agent_catches_filenotfound_and_falls_back():
    with patch.object(jarvis, "has_openclaw_runtime", return_value=True):
        with patch("subprocess.run", side_effect=FileNotFoundError("No openclaw")):
            with patch.object(jarvis, "fallback_llm_turn", return_value="Recovered reply") as mock_fallback:
                reply = jarvis.run_openclaw_agent("explain grpc")
                assert reply == "Recovered reply"
                mock_fallback.assert_called_once()


def test_stream_openclaw_turn_falls_back_when_runtime_missing():
    with patch.object(jarvis, "has_openclaw_runtime", return_value=False):
        with patch.object(jarvis, "fallback_llm_turn", return_value="Stream fallback") as mock_fallback:
            reply = jarvis.stream_openclaw_turn("summarize diff")
            assert reply == "Stream fallback"
            mock_fallback.assert_called_once_with("summarize diff", stream=True)


# ── Interactive TUI graceful guard ────────────────────────────────

def test_cmd_tui_guards_missing_runtime():
    with patch.object(jarvis, "has_openclaw_runtime", return_value=False):
        with patch.object(jarvis, "cmd_banner"):
            with patch("subprocess.run") as mock_run:
                jarvis.cmd_tui()
                mock_run.assert_not_called()


# ── 3-Tier Fallback chain ──────────────────────────────────────────

def test_fallback_llm_turn_all_offline_returns_friendly_error():
    with patch.object(jarvis, "direct_gemini_turn", return_value=None):
        with patch.object(jarvis, "get_groq_api_key", return_value=None):
            with patch("requests.post", side_effect=Exception("Connection refused")):
                msg = jarvis.fallback_llm_turn("test question", stream=False)
                assert "все провайдеры LLM временно недоступны" in msg
