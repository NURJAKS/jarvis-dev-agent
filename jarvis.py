#!/usr/bin/env python3
"""
🦾 J.A.R.V.I.S. Developer Assistant & Execution Engine
Binds OpenClaw runtime with Main-brain knowledge base for Middle+/Senior developers.

Level 1: System Audio Filter (highpass=120Hz fan noise filter, volume=2.5x gain) + Gemini developer prompt
Level 2: OpenAI Whisper Large v3 Turbo (via Groq API, 100ms ultra-low latency, multi-lingual code terms)
Level 3: Semantic Command Alignment (Intent Matcher for /review, /triage, /focus, /trace, /task)
"""

import sys
import os
import subprocess
import json
import argparse
import base64
import re
import threading
import time
import uuid
import math
import struct
import shutil
from pathlib import Path
from typing import Optional, Tuple, List, Dict

import requests

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.markdown import Markdown
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.syntax import Syntax
    from rich.live import Live
    from rich import print as rprint
except ImportError:
    print("Error: 'rich' library is required. Install via: pip install rich")
    sys.exit(1)

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.formatted_text import HTML
except ImportError:
    print("Error: 'prompt_toolkit' is required. Install via: pip install prompt_toolkit")
    sys.exit(1)

console = Console()
MAIN_BRAIN_PATH = Path(os.getenv("JARVIS_KNOWLEDGE_BASE", str(Path.home() / "Dev" / "Main-brain")))
OPENCLAW_BIN = os.getenv("JARVIS_OPENCLAW_BIN") or shutil.which("openclaw") or str(Path.home() / ".local" / "bin" / "openclaw")
DEV_ROOT = Path(os.getenv("JARVIS_DEV_ROOT", str(Path.home() / "Dev")))
OPENCLAW_ENV_FILE = Path(os.getenv("JARVIS_OPENCLAW_ENV", str(Path.home() / "Dev" / "openclaw" / ".env")))
EXTRA_NODE_PATH = os.getenv("JARVIS_NODE_BIN", "")
HISTORY_FILE = Path.home() / ".jarvis_history"

# Global state for voice output (Default: False for quiet operation in class/library)
VOICE_OUTPUT_ENABLED = False


def get_gemini_api_key() -> str:
    """Retrieve Gemini API key from environment, openclaw .env or config."""
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if key:
        return key

    env_file = OPENCLAW_ENV_FILE
    if env_file.exists():
        try:
            with open(env_file, "r") as f:
                for line in f:
                    if line.startswith("GEMINI_API_KEY="):
                        return line.strip().split("=", 1)[1].strip()
        except Exception:
            pass

    cfg_file = Path.home() / ".openclaw" / "openclaw.json"
    if cfg_file.exists():
        try:
            with open(cfg_file, "r") as f:
                cfg = json.load(f)
                token = cfg.get("talk", {}).get("realtime", {}).get("providers", {}).get("google", {}).get("apiKey")
                if token:
                    return token
        except Exception:
            pass

    return ""


def get_groq_api_key() -> str:
    """Retrieve Groq API key from environment or openclaw .env."""
    key = os.getenv("GROQ_API_KEY")
    if key:
        return key

    env_file = OPENCLAW_ENV_FILE
    if env_file.exists():
        try:
            with open(env_file, "r") as f:
                for line in f:
                    if line.startswith("GROQ_API_KEY="):
                        return line.strip().split("=", 1)[1].strip()
        except Exception:
            pass
    return ""


def speak_charon(text: str):
    """Voice response using Gemini TTS Charon voice played through paplay."""
    if not VOICE_OUTPUT_ENABLED or not text or not text.strip():
        return

    # Check for Kazakh characters or language
    is_kazakh = bool(re.search(r"[әіңғүұқөһӘІҢҒҮҰҚӨҺ]", text))
    if is_kazakh:
        console.print("[dim cyan]🇰🇿 [Қазақ тіліндегі жауап экранға шығарылды][/]")
        play_earcon("done")
        return

    api_key = get_gemini_api_key()
    if not api_key:
        return

    try:
        clean = text.strip()
        clean = re.sub(r"```.*?```", "", clean, flags=re.DOTALL)
        clean = re.sub(r"https?://\S+", "", clean)
        clean = re.sub(r"[`*#_│╰╭─╮╯|]", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()

        parts = [p.strip() for p in re.split(r"[.!?\n]", clean) if p.strip()]
        if parts:
            spoken = parts[0]
            if len(parts) > 1 and len(spoken) < 70:
                spoken += ". " + parts[1]
        else:
            spoken = "Сэр, результат выведен на экран."

        if not spoken.endswith((".", "!", "?")):
            spoken += "."
        if len(spoken) > 180:
            spoken = spoken[:180] + "..."

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": spoken}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": "Charon"
                        }
                    }
                }
            }
        }
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code == 200:
            data = r.json()
            cands = data.get("candidates", [])
            if cands and "content" in cands[0] and "parts" in cands[0]["content"] and len(cands[0]["content"]["parts"]) > 0:
                raw_b64 = cands[0]["content"]["parts"][0].get("inlineData", {}).get("data")
                if raw_b64:
                    audio_bytes = base64.b64decode(raw_b64)
                    p = subprocess.Popen(
                        ["paplay", "--raw", "--rate=24000", "--channels=1", "--format=s16le"],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    p.communicate(input=audio_bytes)
                    return
        # Fallback if TTS produced no audio
        play_earcon("done")
    except Exception:
        pass


def play_earcon(kind: str = "mic"):
    """Play a short, subtle, soft earcon asynchronously via PulseAudio (paplay).
    kind: 'mic' (soft 800Hz blip, 60ms) or 'done' (soft two-tone ascending chime 587Hz -> 880Hz, 140ms).
    Honors VOICE_OUTPUT_ENABLED (strictly silent if voice/sound is disabled, e.g. in class/meetings).
    """
    if not VOICE_OUTPUT_ENABLED:
        return

    def _synth_and_play():
        try:
            sample_rate = 24000
            audio_data = bytearray()
            if kind == "mic":
                duration = 0.06
                total_samples = int(sample_rate * duration)
                for i in range(total_samples):
                    t = i / sample_rate
                    # Hann window envelope to prevent clicking
                    envelope = 0.5 * (1 - math.cos(2 * math.pi * i / total_samples))
                    val = int(32767 * 0.12 * envelope * math.sin(2 * math.pi * 800 * t))
                    audio_data.extend(struct.pack("<h", val))
            elif kind == "done":
                # Soft two-tone chime: D5 (587Hz, 60ms) -> A5 (880Hz, 80ms)
                for freq, dur in [(587, 0.06), (880, 0.08)]:
                    samples = int(sample_rate * dur)
                    for i in range(samples):
                        t = i / sample_rate
                        env = 0.5 * (1 - math.cos(2 * math.pi * i / samples))
                        val = int(32767 * 0.15 * env * math.sin(2 * math.pi * freq * t))
                        audio_data.extend(struct.pack("<h", val))

            p = subprocess.Popen(
                ["paplay", "--raw", f"--rate={sample_rate}", "--channels=1", "--format=s16le"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            p.communicate(input=bytes(audio_data))
        except Exception:
            pass

    threading.Thread(target=_synth_and_play, daemon=True).start()


def record_audio(duration: float = 6.0, output_file: str = "/tmp/jarvis_speech.wav") -> bool:
    """Level 1: Record audio with fan noise suppression and 2.5x volume gain."""
    try:
        if os.path.exists(output_file):
            try:
                os.remove(output_file)
            except Exception:
                pass

        # ffmpeg captures from PulseAudio default with highpass (fan filter) & volume boost
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "pulse",
            "-i", "default",
            "-t", str(int(duration)),
            "-af", "highpass=f=120,volume=2.5",
            "-ar", "16000",
            "-ac", "1",
            output_file
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=duration + 2)
        return os.path.exists(output_file) and os.path.getsize(output_file) > 2000
    except Exception:
        # Fallback to arecord
        try:
            cmd = ["arecord", "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", str(int(duration)), "-t", "wav", output_file]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=duration + 2)
            return os.path.exists(output_file) and os.path.getsize(output_file) > 2000
        except Exception:
            return False


def normalize_voice_terms(text: str) -> str:
    """Normalize common Whisper acoustic misrecognitions and speech developer slang."""
    if not text:
        return ""
    t = text

    # Common Whisper STT acoustic hallucinations for developer commands
    t = re.sub(r"\b(?:гид|гит)\s+div\b", "git diff", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:гит|гид)\s+(?:див|диф)\b", "git diff", t, flags=re.IGNORECASE)
    t = re.sub(r"\bgit\s+div\b", "git diff", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:пул[- ]?реквест\w*|pull[- ]?реквест\w*|pull[- ]?request\w*)\b", "PR", t, flags=re.IGNORECASE)
    t = re.sub(r"\bкод[- ]?р[еи]вью\b", "code review", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:докер|доккер)\b", "docker", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:кубер|кубернетис|к8с|k8s)\b", "kubernetes", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:сиай|си[- ]?ай)\b", "CI", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:пайплайн|пайплайны|пайплайна)\b", "pipeline", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:ворктри|ворк[- ]?три)\b", "worktree", t, flags=re.IGNORECASE)
    t = re.sub(r"^\s*(?:sir|с[эе]р)[,\s]+", "", t, flags=re.IGNORECASE)
    return t.strip()


def transcribe_audio(audio_path: str = "/tmp/jarvis_speech.wav") -> str:
    """Level 2: Ultra-accurate Whisper Large v3 (Groq API, 100ms) with Gemini Level 1 fallback."""
    if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 2000:
        return ""

    # LEVEL 2: OpenAI Whisper Large v3 Turbo on Groq (Auto-detects Russian, Kazakh, English, Korean)
    groq_key = get_groq_api_key()
    if groq_key:
        try:
            headers = {"Authorization": f"Bearer {groq_key}"}
            url = "https://api.groq.com/openai/v1/audio/transcriptions"
            with open(audio_path, "rb") as f:
                files = {"file": ("speech.wav", f, "audio/wav")}
                # Language omitted to enable auto-detection for RU, KK, EN, KO
                data = {
                    "model": "whisper-large-v3-turbo",
                    "prompt": "Multilingual software engineering assistant: Russian, Kazakh (қазақша), English, Korean (한국어), git, commit, PR, review, docker, diff, tests"
                }
                r = requests.post(url, headers=headers, files=files, data=data, timeout=20)
                if r.status_code == 200:
                    text = r.json().get("text", "").strip()
                    low = text.lower()
                    artifacts = ["продолжение следует", "субтитры", "редактор субтитров", "переводчик", "спасибо за просмотр", "thanks for watching", "시청해 주셔서 감사합니다", "..."]
                    if text and not any(a in low for a in artifacts):
                        return normalize_voice_terms(text)
        except Exception:
            pass

    # LEVEL 1 Fallback: Gemini 2.5 Flash with Multilingual Prompt Conditioning
    api_key = get_gemini_api_key()
    if api_key:
        try:
            with open(audio_path, "rb") as f:
                wav_b64 = base64.b64encode(f.read()).decode("utf-8")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            payload = {
                "contents": [{
                    "parts": [
                        {"inlineData": {"mimeType": "audio/wav", "data": wav_b64}},
                        {"text": "Transcribe the spoken audio accurately. The speaker may talk in Russian, Kazakh (қазақ тілі), English, or Korean (한국어) with developer terminology (git, commit, PR, review, docker, tests, etc.). Return ONLY the transcribed text in the spoken language without quotes or commentary. If silence, return an empty string."}
                    ]
                }]
            }
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 200:
                cand = r.json().get("candidates", [{}])[0]
                parts = cand.get("content", {}).get("parts", [{}])
                for p in parts:
                    t = p.get("text", "").strip()
                    if t and t.lower() not in ["silence", "empty", "none", "null"]:
                        return normalize_voice_terms(t)
        except Exception:
            pass

    return ""


def match_developer_intent(raw_text: str) -> Tuple[str, bool]:
    """Level 3: Match spoken fuzzy phrases in RU, KZ, EN, KO to developer slash commands.
    Returns (normalized_command, was_matched)
    """
    if not raw_text or not raw_text.strip():
        return "", False

    # 1. Acoustic normalization
    normalized = normalize_voice_terms(raw_text)
    t = normalized.strip()
    low = t.lower()

    # 1. Review intent: RU, KZ, EN, KO
    if (
        re.search(r"\b(ревью|ривью|префлайт|дифф|diff|тексер|диффті қара|кодты қара|review|code review|코드 리뷰)\b", low)
        or re.search(r"\b(проверь|посмотри|глянь|чекни|покажи|разбери|сделай|тексер|қара)\b.*?\b(код|дифф|diff|изменения|стейдж|ветк|пр|pr|пулреквест|пуллреквест|pull request)\b", low)
    ):
        return "/review", True

    # 2. Triage intent: RU, KZ, EN, KO
    if (
        re.search(r"\b(триаж|триадж|triage)\b", low)
        or re.search(r"\b(что|почему|куда|неге|не|what|why)\b.*?\b(упало|упал|сломалось|сбоит|ошибка|фейл|failed|fail|error|пайплайн|билд|тест|ci|actions|құлады|қате)\b", low)
        or re.search(r"\b(ci error|build error|pipeline error|test fail|tests failed|에러)\b", low)
    ):
        run_match = re.search(r"\b(\d{7,12})\b", low)
        return (f"/triage {run_match.group(1)}" if run_match else "/triage"), True

    # 3. Focus intent: RU, KZ, EN, KO
    if re.search(r"\b(фокус|утренний (дайджест|план)|план на (день|сегодня)|какие задачи|что на сегодня|задачи на сегодня|план работы|бүгінгі (фокус|жоспар|тапсырмалар)|жұмыс жоспары|focus|daily agenda|my tasks|what to do today|오늘 할 일)\b", low):
        return "/focus", True

    # 4. Voice control
    if re.search(r"\b(включи (звук|голос)|вруби звук|включить голос|дыбысты қос|voice on|sound on|소ри 켜)\b", low):
        return "/voice on", True
    if re.search(r"\b(выключи (звук|голос)|выключить звук|дыбысты өшір|тихий режим|без звука|voice off|mute|silent|소ри 꺼)\b", low):
        return "/voice off", True

    # 5. Trace intent: "где метод X", "где описан класс X", "кто трогал X", "where is X", "X қайда"
    trace_m = re.search(r"\b(?:найди|где|кто|где описан|где объявлен|где находится|кто трогал|кто менял|кто писал|who wrote|who changed|where is|trace|қайда|кім жазды)\s+(?:метод|функция|класс|структура|символ|файл|компонент|код)?\s*([a-zA-Z0-9_\-\.\$]+)", t, re.IGNORECASE)
    if trace_m:
        return f"/trace {trace_m.group(1)}", True

    # 6. Task intent: "запусти в фоне X", "автономно сделай X", "фонда орында X"
    task_m = re.search(r"\b(?:запусти в фоне|фоновая задача|автономно (?:сделай|выполни)|сделай в фоне|сделай в ворктри|автономная задача|фонда орында|автономды жаса)\s+(.+)", t, re.IGNORECASE)
    if task_m:
        return f"/task {task_m.group(1).strip()}", True

    return t, False


def get_git_info() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (repo_root, repo_name, current_branch) or None if not in a git repo."""
    try:
        root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True
        ).strip()
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"],
            stderr=subprocess.DEVNULL,
            text=True
        ).strip() or "HEAD (detached)"
        return root, Path(root).name, branch
    except Exception:
        return None, None, None


def interactive_diff_review(diff_text: str, title: str = "Предпросмотр изменений") -> str:
    """Renders a syntax-highlighted diff with stats and prompts for approval.
    Returns: 'accept', 'reject', or 'keep'
    """
    if not diff_text or not diff_text.strip():
        console.print("[dim]Нет изменений для отображения.[/]")
        return "accept"

    additions = sum(1 for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---"))
    files = len(set(re.findall(r"^diff --git a/(.*?) b/", diff_text, re.MULTILINE)))

    stats_str = f"[bold green]+{additions}[/] [bold red]-{deletions}[/] в [bold cyan]{files or 1}[/] файлах"

    syntax = Syntax(diff_text[:18000], "diff", theme="monokai", line_numbers=True, word_wrap=True)
    if len(diff_text) > 18000:
        panel_content = Panel(syntax, title=f"[bold cyan]🔍 {title} ({stats_str}) - фрагмент[/]", border_style="cyan")
    else:
        panel_content = Panel(syntax, title=f"[bold cyan]🔍 {title} ({stats_str})[/]", border_style="cyan")

    console.print()
    console.print(panel_content)

    choices_prompt = (
        "[bold white]Сэр, утвердить изменения?[/] "
        "[bold green][A]ccept (слить)[/] | "
        "[bold red][R]eject (отклонить и стереть)[/] | "
        "[bold yellow][K]eep (оставить отдельную ветку)[/] "
        "❯ "
    )

    try:
        while True:
            choice = console.input(choices_prompt).strip().lower()
            if choice in ["a", "accept", "да", "y", "yes", ""]:
                return "accept"
            elif choice in ["r", "reject", "нет", "n", "no"]:
                return "reject"
            elif choice in ["k", "keep", "оставить"]:
                return "keep"
            else:
                console.print("[yellow]Пожалуйста, введите A, R или K.[/]")
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Операция отменена. Ветка сохранена.[/]")
        return "keep"


def is_llm_quota_error(text: str) -> bool:
    """Detect if the LLM response is a 429 / Quota Exceeded error."""
    if not text:
        return False
    low = text.lower()
    quota_signals = [
        "resource_exhausted",
        "exceeded your current quota",
        "code=resource_exhausted",
        "cli_error",
        "rate-limits",
        "rate_limit",
        "429"
    ]
    return any(sig in low for sig in quota_signals)


def fallback_llm_turn(prompt: str, stream: bool = True) -> str:
    """Auto-failover to Groq LPU (Qwen 3.8 27B, 1s response) and Ollama when OpenClaw / Gemini hit 429 quota."""
    groq_key = get_groq_api_key()
    if groq_key:
        try:
            console.print("[bold yellow]⚡ Задействован резервный сверхбыстрый движок Groq LPU (Qwen 3.8 27B)...[/]\n")
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {groq_key}"}
            payload = {
                "model": "qwen/qwen3.8-27b",
                "messages": [
                    {
                        "role": "system",
                        "content": "Ты J.A.R.V.I.S. — высокоинтеллектуальный персональный инженерный ассистент и Tech Lead. Отвечай компетентно, четко, структурированно, без пустых клише. Код и списки форматируй в Markdown."
                    },
                    {"role": "user", "content": prompt}
                ],
                "stream": stream,
                "temperature": 0.3
            }
            if stream:
                r = requests.post(url, headers=headers, json=payload, stream=True, timeout=30)
                if r.status_code == 200:
                    chunks = []
                    for line in r.iter_lines():
                        if not line:
                            continue
                        line_str = line.decode("utf-8")
                        if line_str.startswith("data: "):
                            data_str = line_str[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk_json = json.loads(data_str)
                                delta = chunk_json["choices"][0]["delta"].get("content", "")
                                if delta:
                                    console.print(delta, end="")
                                    chunks.append(delta)
                            except Exception:
                                pass
                    console.print()
                    return "".join(chunks).strip()
            else:
                r = requests.post(url, headers=headers, json=payload, timeout=30)
                if r.status_code == 200:
                    ans = r.json()["choices"][0]["message"]["content"]
                    return ans.strip()
        except Exception:
            pass

    # Secondary fallback: local Ollama
    try:
        console.print("[dim yellow]🔄 Переключение на локальный Ollama (qwen2.5-coder:7b)...[/]\n")
        r = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": "qwen2.5-coder:7b", "prompt": prompt, "stream": False},
            timeout=45
        )
        if r.status_code == 200:
            ans = r.json().get("response", "").strip()
            if ans:
                if stream:
                    console.print(ans)
                return ans
    except Exception:
        pass

    return "❌ К сожалению, все провайдеры LLM временно недоступны."


def run_openclaw_agent(
    prompt: str,
    spinner_text: str = "Джарвис анализирует...",
    cwd: Optional[str] = None,
    session_id: Optional[str] = None
) -> str:
    """Run an OpenClaw agent turn with spinner and return the text reply."""
    with Progress(
        SpinnerColumn(spinner_name="dots"),
        TextColumn("[bold cyan]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(description=spinner_text, total=None)

        env = os.environ.copy()
        env["PATH"] = f"{EXTRA_NODE_PATH}:{env.get('PATH', '')}" if EXTRA_NODE_PATH else env.get("PATH", "")

        cmd = [
            OPENCLAW_BIN,
            "agent",
            "--json",
            "--message",
            prompt
        ]
        if session_id:
            cmd.extend(["--session-id", session_id])

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=env,
                cwd=cwd,
                timeout=180
            )
        except subprocess.TimeoutExpired:
            return "❌ Превышено время ожидания ответа от OpenClaw Gateway (180s)."

    if res.returncode != 0 and not res.stdout:
        if is_llm_quota_error(res.stderr):
            return fallback_llm_turn(prompt, stream=False)
        return f"❌ Ошибка вызова OpenClaw ({res.returncode}):\n{res.stderr.strip()}"

    reply_candidate = ""
    try:
        start = res.stdout.find("{")
        end = res.stdout.rfind("}")
        if start != -1 and end != -1:
            data = json.loads(res.stdout[start:end+1])
            payloads = data.get("result", {}).get("payloads", [])
            if payloads and isinstance(payloads, list) and len(payloads) > 0 and "text" in payloads[0]:
                reply_candidate = str(payloads[0]["text"]).strip()
            if not reply_candidate:
                reply = (
                    data.get("result", {})
                    .get("agentReply", {})
                    .get("terminalReply", {})
                    .get("text")
                )
                if not reply:
                    reply = (
                        data.get("result", {})
                        .get("agentReply", {})
                        .get("finalAssistantVisibleText")
                    )
                if reply:
                    reply_candidate = str(reply).strip()
    except Exception:
        pass

    if not reply_candidate:
        lines = [line for line in res.stdout.splitlines() if line.strip() and not line.startswith("│") and not line.startswith("◇")]
        reply_candidate = "\n".join(lines).strip() or res.stdout.strip()

    if is_llm_quota_error(reply_candidate):
        return fallback_llm_turn(prompt, stream=False)

    return reply_candidate


def stream_openclaw_turn(
    prompt: str,
    session_id: Optional[str] = None,
    cwd: Optional[str] = None,
    spinner_text: str = "⚡ Джарвис обрабатывает запрос..."
) -> str:
    """Streams OpenClaw agent output live to the terminal with token-by-token feel and Ctrl+C interrupt handling."""
    env = os.environ.copy()
    env["PATH"] = f"{EXTRA_NODE_PATH}:{env.get('PATH', '')}" if EXTRA_NODE_PATH else env.get("PATH", "")

    cmd = [OPENCLAW_BIN, "agent", "--message", prompt]
    if session_id:
        cmd.extend(["--session-id", session_id])

    collected_output: List[str] = []
    streaming_started = False

    noise_patterns = [
        r"^OpenClaw\s+\d+",
        r"^MEMORY\.md:",
        r"^[\s│◇◒◐◓◑…\.]+$",
        r"^Waiting for agent reply"
    ]

    def is_noise(line: str) -> bool:
        s = line.strip()
        if not s:
            return False
        return any(re.search(pat, s) for pat in noise_patterns)

    with console.status(f"[bold cyan]{spinner_text}[/]", spinner="dots") as status:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                cwd=cwd
            )

            while True:
                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break
                if not line:
                    continue

                if not streaming_started:
                    if is_noise(line):
                        continue
                    status.stop()
                    streaming_started = True

                console.print(line, end="")
                collected_output.append(line)

            proc.wait(timeout=5)

        except KeyboardInterrupt:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            console.print("\n[yellow]⚠️ Генерация прервана пользователем (Ctrl+C).[/]")
        except Exception as e:
            if not streaming_started:
                status.stop()
            console.print(f"\n[red]❌ Ошибка OpenClaw Gateway: {e}[/]")

    full_output = "".join(collected_output).strip()
    if is_llm_quota_error(full_output):
        console.print("\n[bold yellow]⚡ Обнаружено исчерпание квоты Google Gemini (429).[/]")
        return fallback_llm_turn(prompt, stream=True)

    return full_output


def cmd_banner():
    root, name, branch = get_git_info()
    voice_status = "[bold green]ВКЛ[/]" if VOICE_OUTPUT_ENABLED else "[dim red]ВЫКЛ (тихий режим)[/]"
    groq_status = "[bold green]Whisper Large v3 (Groq)[/]" if get_groq_api_key() else "[yellow]Gemini Flash STT[/]"
    status_text = (
        f"[bold white]🦾 J.A.R.V.I.S. Developer Terminal Engine[/]\n"
        f"[dim]Связка: OpenClaw Gateway + Main-brain (42 сервиса, 40 навыков)[/]\n\n"
        f"📂 [bold cyan]Проект:[/] {name or 'Вне репозитория'}  "
        f"🌿 [bold green]Ветка:[/] {branch or '-'}  "
        f"🎙️ [bold white]STT:[/] {groq_status}  "
        f"🔊 [bold white]Голос:[/] {voice_status}\n"
        f"📍 [dim]Путь:[/] {root or os.getcwd()}"
    )
    console.print(Panel(status_text, border_style="cyan", title="[bold cyan]J.A.R.V.I.S.[/]"))


def cmd_review(args=None):
    """Pillar 3: Pre-flight Code Review before PR."""
    root, name, branch = get_git_info()
    if not root:
        console.print("[red]❌ Ошибка: Текущая директория не является git-репозиторием.[/]")
        return

    cmd_banner()

    diff = subprocess.check_output(["git", "diff", "--cached"], text=True).strip()
    diff_type = "Staged (индексированные изменения)"

    if not diff:
        diff = subprocess.check_output(["git", "diff"], text=True).strip()
        diff_type = "Unstaged (рабочая копия)"

    if not diff:
        for base in ["origin/main", "origin/master", "main", "master", "HEAD~1"]:
            try:
                diff = subprocess.check_output(
                    ["git", "diff", f"{base}...HEAD"],
                    stderr=subprocess.DEVNULL,
                    text=True
                ).strip()
                if diff:
                    diff_type = f"Ветка {branch} относительно {base}"
                    break
            except Exception:
                continue

    if not diff:
        console.print("[green]✓ Нет изменений для ревью. Рабочая копия чиста.[/]")
        return

    diff_sample = diff[:30000]
    if len(diff) > 30000:
        diff_sample += f"\n... [дифф обрезан, всего {len(diff)} символов]"

    prompt = f"""
Ты выступаешь в роли Senior/Staff Architect и проводишь Pre-flight Code Review для проекта '{name}' (ветка: {branch}).
Тип диффа: {diff_type}

Вот diff изменений:
```diff
{diff_sample}
```

Проведи строгий и прикладной аудит:
1. 🛑 **Критические риски и архитектура**:
   - SQL & Базы данных: есть ли запросы в цикле (N+1), фильтрация без индексов, опасные миграции.
   - Concurrency & Async: гонки (race conditions), незакрытые ресурсы/коннекты, блокирующий I/O в event loop.
   - Edge Cases: null/nil-safety, пустые списки, таймауты сетевых вызовов.
2. 💡 **Оптимизации и чистота**:
   - Соблюдение типизации и стандартов чистой архитектуры.
3. 📝 **Готовый PR Description** (сгенерируй для GitHub):
   - **Summary**: Краткая суть изменений (2-3 пункта).
   - **Breaking Changes**: Есть ли ломающие изменения.
   - **How to test**: Конкретные шаги проверки.

Отвечай в стиле Джарвиса: четко, структурированно, без воды.
"""
    console.print(f"[cyan]🔍 Обнаружен diff:[/] {diff_type} ({len(diff.splitlines())} строк)")
    result = run_openclaw_agent(prompt, "Джарвис проводит сеньорский префлайт-аудит диффа...")

    console.print()
    console.print(Panel(
        Markdown(result),
        title=f"[bold green]🛡️ Pre-flight Review: {name} ({branch})[/]",
        border_style="green"
    ))

    if VOICE_OUTPUT_ENABLED:
        threading.Thread(target=speak_charon, args=(result,), daemon=True).start()


def cmd_triage(args=None):
    """Pillar 1: Incident & CI Triage."""
    root, name, branch = get_git_info()
    cmd_banner()

    run_id = getattr(args, "run_id", None) if args else None
    if not run_id:
        console.print("[cyan]🔍 Поиск последнего упавшего пайплайна в GitHub Actions...[/]")
        try:
            runs_json = subprocess.check_output([
                "gh", "run", "list",
                "--limit", "10",
                "--json", "databaseId,status,conclusion,name,headBranch,url"
            ], text=True)
            runs = json.loads(runs_json)
            failed_runs = [r for r in runs if r.get("conclusion") == "failure"]
            if not failed_runs:
                console.print("[green]✓ Упавших пайплайнов не найдено! Все последние проверки успешны.[/]")
                return
            latest = failed_runs[0]
            run_id = str(latest["databaseId"])
            console.print(f"[bold yellow]Найден упавший пайплайн:[/] '{latest['name']}' на ветке '{latest['headBranch']}' (ID: {run_id})")
            console.print(f"[dim]URL: {latest['url']}[/]\n")
        except Exception as e:
            console.print(f"[red]❌ Не удалось получить список пайплайнов через gh: {e}[/]")
            return

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[cyan]Выгрузка логов упавшего CI..."),
            console=console,
            transient=True
        ):
            log_output = subprocess.check_output(
                ["gh", "run", "view", run_id, "--log-failed"],
                stderr=subprocess.STDOUT,
                text=True
            )
    except subprocess.CalledProcessError as e:
        log_output = e.output

    if not log_output.strip():
        console.print(f"[yellow]Лог для run-id {run_id} пуст или еще генерируется.[/]")
        return

    log_sample = log_output[:35000]
    last_commit = ""
    try:
        last_commit = subprocess.check_output(
            ["git", "log", "-1", "--stat"],
            stderr=subprocess.DEVNULL,
            text=True
        )
    except Exception:
        pass

    prompt = f"""
Ты дежурный SRE/Tech Lead Джарвис. Упал пайплайн CI/CD (Run ID: {run_id}) в проекте '{name}'.
Последний коммит:
{last_commit}

Вот сырой лог ошибки из CI:
```
{log_sample}
```

Твоя задача:
1. 🎯 **Изолируй точную причину**: какой тест или шаг упал, в каком файле и на какой строке (стектрейс).
2. 🔍 **Корреляция с коммитом**: что именно в последних изменениях спровоцировало этот сбой.
3. 🛠️ **Готовое решение**: покажи точный diff или команду, которая исправит проблему.
Ответь кратко и сразу по делу.
"""
    result = run_openclaw_agent(prompt, f"Джарвис анализирует лог падения CI #{run_id}...")

    console.print()
    console.print(Panel(
        Markdown(result),
        title=f"[bold red]🚨 CI Incident Triage: Run #{run_id}[/]",
        border_style="red"
    ))

    if VOICE_OUTPUT_ENABLED:
        threading.Thread(target=speak_charon, args=(result,), daemon=True).start()


def cmd_trace(query: str):
    """Pillar 2: Deep Codebase Navigator & Git Pickaxe."""
    root, name, branch = get_git_info()
    if not root:
        console.print("[red]❌ Ошибка: Текущая директория не является git-репозиторием.[/]")
        return

    cmd_banner()
    console.print(f"[cyan]🔎 Поиск в истории коммитов и blame для:[/] [bold white]'{query}'[/]\n")

    try:
        pickaxe = subprocess.check_output(
            ["git", "log", "-S", query, "-p", "--max-count", "4"],
            stderr=subprocess.DEVNULL,
            text=True
        )
    except Exception:
        pickaxe = ""

    prompt = f"""
Ты исследователь кодовой базы Джарвис в проекте '{name}'.
Разработчик исследует историю и архитектурную причину появления кода/символа: '{query}'.

Вот выборка из git history (git log -S):
```diff
{pickaxe[:25000] if pickaxe else "Символ не найден через git pickaxe, возможно был переименован или новый."}
```

Объясни разработчику:
1. Кто, когда и в рамках какого коммита/тикета добавил или изменил этот фрагмент.
2. Какая бизнес- или архитектурная логика стояла за этим изменением.
3. Связи с другими сервисами или модулями (если применимо).
"""
    result = run_openclaw_agent(prompt, f"Джарвис исследует историю '{query}'...")

    console.print()
    console.print(Panel(
        Markdown(result),
        title=f"[bold blue]🧭 Deep Codebase Trace: '{query}'[/]",
        border_style="blue"
    ))

    if VOICE_OUTPUT_ENABLED:
        threading.Thread(target=speak_charon, args=(result,), daemon=True).start()


def cmd_task(task_desc: str):
    """Pillar 4: Autonomous Worktree Background Agent with full physical isolation."""
    root, name, branch = get_git_info()
    if not root:
        console.print("[red]❌ Ошибка: Текущая директория не является git-репозиторием.[/]")
        return

    cmd_banner()
    clean_desc = re.sub(r'[^a-zA-Z0-9_\-]', '', task_desc.replace(' ', '-'))[:15].lower() or "auto"
    task_id = f"task-{int(time.time()) % 100000}-{clean_desc}"
    worktree_branch = f"jarvis/{task_id}"
    worktree_dir = Path(f"/tmp/jarvis-worktrees/{name}-{task_id}")
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)

    console.print(f"[bold cyan]🤖 Создание изолированного Git Worktree:[/] [green]{worktree_branch}[/]")
    console.print(f"[dim]Изолированный путь: {worktree_dir}[/]")
    console.print(f"[dim]Инструкция: {task_desc}[/]\n")

    # 1. Create Git worktree
    try:
        subprocess.run(
            ["git", "worktree", "add", "-b", worktree_branch, str(worktree_dir)],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as e:
        console.print(f"[red]❌ Не удалось создать git worktree: {e.stderr.decode().strip()}[/]")
        return

    try:
        prompt = f"""
Ты автономный инженер Джарвис. Тебе поручена фоновая задача в ИЗОЛИРОВАННОМ окружении Git Worktree:
- Проект: {name}
- Ветка: {worktree_branch}
- Физический путь worktree: {worktree_dir}
- Исходная задача: "{task_desc}"

Инструкция:
1. Выполняй изменения ИСКЛЮЧИТЕЛЬНО внутри каталога: {worktree_dir}.
2. Внеси необходимые правки (используй edit/write).
3. Запусти тесты или проверку синтаксиса проекта в каталоге {worktree_dir}.
4. Сделай понятный git commit с результатами изменений.
5. Выдай финальный краткий отчет: список измененных файлов и статус тестов.
"""
        result = run_openclaw_agent(
            prompt,
            f"Джарвис автономно работает в изолированном worktree ({worktree_branch})...",
            cwd=str(worktree_dir)
        )

        console.print()
        console.print(Panel(
            Markdown(result),
            title=f"[bold magenta]🦾 Background Task Result: {worktree_branch}[/]",
            border_style="magenta"
        ))

        # Check for uncommitted changes inside the worktree
        try:
            status = subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=str(worktree_dir),
                text=True
            ).strip()
            if status:
                subprocess.run(["git", "add", "-A"], cwd=str(worktree_dir), check=True)
                subprocess.run(
                    ["git", "commit", "-m", f"jarvis: {task_desc[:50]}"],
                    cwd=str(worktree_dir),
                    check=True,
                    stdout=subprocess.DEVNULL
                )
        except Exception:
            pass

        # Check diff between current branch and worktree branch
        diff = ""
        try:
            diff = subprocess.check_output(
                ["git", "diff", f"{branch}...{worktree_branch}"],
                cwd=root,
                text=True
            ).strip()
        except Exception:
            try:
                diff = subprocess.check_output(
                    ["git", "diff", f"HEAD...{worktree_branch}"],
                    cwd=root,
                    text=True
                ).strip()
            except Exception:
                pass

        # Sound earcon & Desktop Notification
        play_earcon("done")
        try:
            subprocess.run([
                "notify-send",
                "-a", "Jarvis Assistant",
                f"🦾 Задача в {name} завершена",
                task_desc[:80]
            ], check=False)
        except Exception:
            pass

        if VOICE_OUTPUT_ENABLED:
            threading.Thread(target=speak_charon, args=(result,), daemon=True).start()

        # Task 4: Interactive Diff & Merge Approval
        if diff:
            action = interactive_diff_review(diff, title=f"Изменения из {worktree_branch} -> {branch}")
            if action == "accept":
                try:
                    subprocess.run(
                        ["git", "merge", worktree_branch, "-m", f"Merge {worktree_branch}: {task_desc[:50]}"],
                        cwd=root,
                        check=True
                    )
                    console.print(f"[bold green]✓ Изменения успешно влиты в ветку {branch}![/]")
                    # Delete the merged branch
                    subprocess.run(["git", "branch", "-d", worktree_branch], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except subprocess.CalledProcessError as me:
                    console.print(f"[red]❌ Конфликт слияния: {me}. Ветка {worktree_branch} сохранена для ручного разбора.[/]")
            elif action == "reject":
                console.print(f"[yellow]Изменения отклонены. Ветка {worktree_branch} удалена.[/]")
                try:
                    subprocess.run(["git", "branch", "-D", worktree_branch], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
            else:
                console.print(f"[cyan]ℹ️ Ветка {worktree_branch} сохранена. Вы можете проверить её вручную (`git checkout {worktree_branch}`).[/]")
        else:
            console.print("[dim]Изменений в кодовой базе не обнаружено.[/]")

    finally:
        # Safe cleanup of worktree folder
        try:
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree_dir)], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if worktree_dir.exists():
                shutil.rmtree(str(worktree_dir), ignore_errors=True)
            subprocess.run(["git", "worktree", "prune"], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def cmd_focus(args=None):
    """Pillar 5: Daily Developer Focus."""
    cmd_banner()
    console.print("[cyan]📊 Сборка утреннего дайджеста и фокуса разработчика...[/]\n")

    pr_info = ""
    try:
        pr_raw = subprocess.check_output([
            "gh", "search", "prs",
            "--review-requested=@me",
            "--state=open",
            "--json", "number,title,repository,url"
        ], stderr=subprocess.DEVNULL, text=True)
        prs = json.loads(pr_raw)
        if prs:
            pr_info = f"PRs awaiting your review ({len(prs)}):\n" + "\n".join(
                [f"- #{p['number']} '{p['title']}' ({p.get('repository', {}).get('nameWithOwner', '')}) -> {p['url']}" for p in prs]
            )
        else:
            pr_info = "Нет PR, ожидающих твоего ревью.\n"
    except Exception:
        pr_info = "GitHub PR review check: активных запросов не обнаружено.\n"

    try:
        my_prs_raw = subprocess.check_output([
            "gh", "search", "prs",
            "--author=@me",
            "--state=open",
            "--json", "number,title,repository,url"
        ], stderr=subprocess.DEVNULL, text=True)
        my_prs = json.loads(my_prs_raw)
        if my_prs:
            pr_info += f"\nYour active open PRs ({len(my_prs)}):\n" + "\n".join(
                [f"- #{p['number']} '{p['title']}' ({p.get('repository', {}).get('nameWithOwner', '')}) -> {p['url']}" for p in my_prs]
            )
    except Exception:
        pass

    ports_info = ""
    ports_file = MAIN_BRAIN_PATH / "PORTS.md"
    if ports_file.exists():
        ports_info = f"Карта портов зафиксирована в {ports_file}.\n"

    dirty_repos = []
    dev_dir = DEV_ROOT
    if dev_dir.exists():
        for d in dev_dir.iterdir():
            if d.is_dir() and (d / ".git").exists():
                try:
                    res = subprocess.check_output(
                        ["git", "status", "--porcelain"],
                        cwd=d,
                        stderr=subprocess.DEVNULL,
                        text=True
                    ).strip()
                    if res:
                        lines = len(res.splitlines())
                        dirty_repos.append(f"{d.name} ({lines} uncommitted files)")
                except Exception:
                    pass

    dirty_summary = "Репозитории с незакоммиченными изменениями:\n" + ("\n".join(f"- {r}" for r in dirty_repos[:6]) if dirty_repos else "- Все рабочие копии чисты.")

    prompt = f"""
Ты личный цифровой советник Джарвис. Составь фокус-дайджест на сегодня для Сэра (Нурбека, Tech Lead):
1. **GitHub PRs**:
{pr_info}

2. **Состояние проектов в ~/Dev**:
{dirty_summary}

3. **Инфраструктура**:
{ports_info}

Сформируй лаконичный, мотивирующий план фокуса из 3-4 ключевых пунктов на утро. Обращайся 'Сэр'.
"""
    result = run_openclaw_agent(prompt, "Джарвис формирует Daily Focus...")

    console.print(Panel(
        Markdown(result),
        title="[bold yellow]☀️ Daily Focus & Tech Lead Agenda[/]",
        border_style="yellow"
    ))

    if VOICE_OUTPUT_ENABLED:
        threading.Thread(target=speak_charon, args=(result,), daemon=True).start()


def cmd_ask(question: str):
    """Ask a question with full Main-brain context and real-time streaming."""
    root, name, branch = get_git_info()
    cmd_banner()

    prompt = f"""
Вопрос от Сэра (Tech Lead): "{question}"
Контекст:
- Текущий проект: {name or 'Вне проекта'} (ветка: {branch or '-'})
- База знаний: подключен Main-brain (JARVIS_KNOWLEDGE_BASE) с 42 проектами и 40 навыками.

Ответь точно, емко, с конкретным кодом или архитектурным решением.
"""
    console.print(f"\n[bold cyan]🦾 Ответ Джарвиса ({name or 'main'}):[/]\n")
    result = stream_openclaw_turn(prompt, cwd=root or os.getcwd())
    console.print()

    if VOICE_OUTPUT_ENABLED:
        threading.Thread(target=speak_charon, args=(result,), daemon=True).start()


def cmd_tui():
    """Launch interactive curses terminal UI from OpenClaw."""
    cmd_banner()
    console.print("[dim]Запуск интерактивного терминала OpenClaw TUI... (Ctrl+C для выхода)[/]\n")
    env = os.environ.copy()
    env["PATH"] = f"{EXTRA_NODE_PATH}:{env.get('PATH', '')}" if EXTRA_NODE_PATH else env.get("PATH", "")
    try:
        subprocess.run([OPENCLAW_BIN, "tui"], env=env)
    except KeyboardInterrupt:
        pass


def cmd_repl():
    """Interactive Claude Code-style developer shell with Voice, Tools, Streaming & Session Memory."""
    global VOICE_OUTPUT_ENABLED
    root, name, branch = get_git_info()

    # Session State: Unique session key and conversation history
    session_id = f"jarvis-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    conversation_history: List[Tuple[str, str]] = []

    console.clear()
    voice_label = "[bold green]ВКЛ[/]" if VOICE_OUTPUT_ENABLED else "[dim red]ВЫКЛ (тихий режим)[/]"
    engine_label = "[bold green]OpenAI Whisper v3 (Groq 100ms)[/]" if get_groq_api_key() else "[yellow]Gemini Flash STT[/]"

    welcome_panel = (
        f"[bold white]🦾 J.A.R.V.I.S. Developer Interactive Shell[/]\n"
        f"[dim]Ко-пилот уровня Senior | OpenClaw Gateway + Main-brain (Streaming + Memory)[/]\n\n"
        f"📂 [bold cyan]Проект:[/] {name or 'Вне репозитория'}  "
        f"🌿 [bold green]Ветка:[/] {branch or '-'}  "
        f"🎙️ [bold white]STT:[/] {engine_label}  "
        f"🔊 [bold white]Голос:[/] {voice_label}\n\n"
        f"[bold yellow]Голосовой ввод:[/] Нажмите [bold cyan][Ctrl+V][/], [bold cyan][Ctrl+R][/] или [bold cyan][F12][/] (диктовка с Whisper)\n"
        f"[bold yellow]Команды:[/] [cyan]/review[/], [cyan]/triage[/], [cyan]/focus[/], [cyan]/trace <q>[/], [cyan]/task <p>[/], [cyan]/voice [on|off][/], [cyan]/clear[/], [cyan]/exit[/]"
    )
    console.print(Panel(welcome_panel, border_style="cyan", title="[bold cyan]J.A.R.V.I.S. REPL[/]"))

    kb = KeyBindings()

    @kb.add('c-v')
    @kb.add('c-r')
    @kb.add('f12')
    def _voice_handler(event):
        """Level 1+2+3: Record audio, transcribe via Whisper, match intent and insert into prompt buffer."""
        play_earcon("mic")
        console.print("\n[bold red]🎙️ [Слушаю вас, сэр... Говорите в микрофон (6 сек)][/]")
        rec_ok = record_audio(duration=6.0)
        if rec_ok:
            console.print("[dim cyan]⚡ Распознаю речь через Whisper Large v3...[/]")
            text = transcribe_audio()
            if text:
                norm_text, is_intent = match_developer_intent(text)
                if is_intent:
                    console.print(f"[bold green]🎯 Распознано намерение:[/] [bold cyan]{norm_text}[/] [dim](из: '{text}')[/]")
                else:
                    console.print(f"[bold green]✓ Распознано:[/] [white]{norm_text}[/]")
                event.current_buffer.insert_text(norm_text)
            else:
                console.print("[yellow]Тишина или речь не разобрана, сэр.[/]")
        else:
            console.print("[red]Не удалось записать аудио с микрофона.[/]")

    session = PromptSession(
        history=FileHistory(str(HISTORY_FILE)),
        auto_suggest=AutoSuggestFromHistory(),
        key_bindings=kb
    )

    while True:
        try:
            _, current_name, current_branch = get_git_info()
            proj_label = current_name or "jarvis"
            branch_label = f":{current_branch}" if current_branch else ""
            prompt_html = f"<b><ansicyan>{proj_label}</ansicyan></b><ansigreen>{branch_label}</ansigreen> <b><ansimagenta>❯</ansimagenta></b> "

            user_input = session.prompt(HTML(prompt_html)).strip()

            if not user_input:
                continue

            # Check semantic intent if not already a slash command
            if not user_input.startswith("/"):
                matched_cmd, was_matched = match_developer_intent(user_input)
                if was_matched:
                    console.print(f"[dim cyan]🎯 Применено авто-намерение:[/] [bold cyan]{matched_cmd}[/]")
                    user_input = matched_cmd

            # Handle slash commands
            if user_input.lower() in ["/exit", "/quit", "exit", "quit", ":q"]:
                console.print("[cyan]Джарвис завершает сессию. Хорошего дня, сэр![/]")
                break

            elif user_input.lower() == "/clear":
                console.clear()
                continue

            elif user_input.lower() in ["/help", "/?"]:
                help_text = (
                    "**Доступные команды в сессии:**\n"
                    "- `/review` — Запустить сеньорский префлайт-аудит текущего git diff\n"
                    "- `/triage [id]` — Разобрать упавший CI/CD пайплайн в GitHub Actions\n"
                    "- `/focus` — Сформировать утренний дайджест задач и PR\n"
                    "- `/trace <символ>` — Глубокий поиск истории и архитектурного смысла в коде\n"
                    "- `/task <описание>` — Запустить автономную фоновую задачу в изолированном Git Worktree\n"
                    "- `/voice [on|off]` — Включить / выключить краткую озвучку ответов\n"
                    "- `/v` или `/mic` — Надиктовать голосовую команду через микрофон\n"
                    "- `/clear` — Очистить экран терминала\n"
                    "- `/exit` — Выйти из сессии\n\n"
                    "**Горячие клавиши:**\n"
                    "- `[Ctrl+V]` или `[F12]` — Голосовая диктовка прямо в поле ввода"
                )
                console.print(Panel(Markdown(help_text), title="Справка команд", border_style="blue"))
                continue

            elif user_input.lower() in ["/v", "/mic", "/voice-input"]:
                play_earcon("mic")
                console.print("[bold red]🎙️ [Слушаю вас, сэр... Говорите в микрофон (6 сек)][/]")
                if record_audio(duration=6.0):
                    text = transcribe_audio()
                    if text:
                        norm_text, is_intent = match_developer_intent(text)
                        if is_intent:
                            console.print(f"[bold green]🎯 Распознано намерение:[/] [bold cyan]{norm_text}[/] [dim](из: '{text}')[/]\n")
                        else:
                            console.print(f"[bold green]✓ Распознано:[/] [white]{norm_text}[/]\n")
                        user_input = norm_text
                    else:
                        console.print("[yellow]Тишина или речь не распознана.[/]\n")
                        continue
                else:
                    console.print("[red]Ошибка записи аудио с микрофона.[/]\n")
                    continue

            elif user_input.lower().startswith("/voice"):
                parts = user_input.split()
                if len(parts) > 1 and parts[1].lower() in ["off", "mute", "false", "0"]:
                    VOICE_OUTPUT_ENABLED = False
                    console.print("[yellow]🔊 Озвучка ответов отключена (полная тишина).[/]\n")
                elif len(parts) > 1 and parts[1].lower() in ["on", "true", "1"]:
                    VOICE_OUTPUT_ENABLED = True
                    console.print("[green]🔊 Озвучка ответов включена (краткий вердикт Charon вслух).[/]\n")
                else:
                    VOICE_OUTPUT_ENABLED = not VOICE_OUTPUT_ENABLED
                    status_str = "[green]включена[/]" if VOICE_OUTPUT_ENABLED else "[yellow]отключена[/]"
                    console.print(f"🔊 Озвучка ответов {status_str}.\n")
                continue

            elif user_input.lower() == "/review":
                cmd_review()
                continue

            elif user_input.lower().startswith("/triage"):
                p_arg = user_input[7:].strip() or None
                class DummyArgs:
                    run_id = p_arg
                cmd_triage(DummyArgs())
                continue

            elif user_input.lower() == "/focus":
                cmd_focus()
                continue

            elif user_input.lower().startswith("/trace"):
                q = user_input[6:].strip()
                if q:
                    cmd_trace(q)
                else:
                    console.print("[yellow]Укажите символ или метод: /trace <имя>[/]\n")
                continue

            elif user_input.lower().startswith("/task"):
                t = user_input[5:].strip()
                if t:
                    cmd_task(t)
                else:
                    console.print("[yellow]Укажите описание задачи: /task <описание>[/]\n")
                continue

            # Direct Shell Pass-through & Navigation (Instant 1ms execution, 0 tokens)
            COMMON_SHELL_COMMANDS = {
                "ls", "ll", "la", "pwd", "cat", "git", "docker", "docker-compose",
                "grep", "find", "mkdir", "rm", "cp", "mv", "touch", "tree",
                "npm", "pnpm", "yarn", "pip", "python", "python3", "node", "go", "cargo",
                "curl", "wget", "ps", "kill", "which", "echo", "df", "free",
                "head", "tail", "pytest", "tsc"
            }

            stripped_input = user_input.strip()
            first_word = stripped_input.split()[0].lower() if stripped_input else ""

            # Check directory navigation (cd)
            if first_word == "cd":
                parts = stripped_input.split(maxsplit=1)
                target_dir = os.path.expanduser(parts[1]) if len(parts) > 1 else str(Path.home())
                try:
                    os.chdir(target_dir)
                    _, new_name, new_branch = get_git_info()
                    branch_part = f" [green]({new_branch})[/]" if new_branch else ""
                    console.print(f"[dim cyan]📂 Директория переключена:[/] [bold white]{os.getcwd()}[/]{branch_part}\n")
                except Exception as e:
                    console.print(f"[red]❌ Ошибка перехода:[/] {e}\n")
                continue

            # Check if it's a direct terminal command (or explicitly prefixed with ! or $)
            is_explicit_shell = stripped_input.startswith(("!", "$"))
            clean_cmd = stripped_input[1:].strip() if is_explicit_shell else stripped_input
            clean_first = clean_cmd.split()[0].lower() if clean_cmd else ""

            question_markers = ["что", "как", "почему", "зачем", "где", "объясни", "подскажи", "помоги", "напиши", "сделай", "?"]
            is_question = any(q in stripped_input.lower() for q in question_markers)

            if is_explicit_shell or (clean_first in COMMON_SHELL_COMMANDS and not is_question):
                try:
                    subprocess.run(clean_cmd, shell=True)
                except Exception as e:
                    console.print(f"[red]❌ Ошибка выполнения:[/] {e}")
                console.print()
                continue

            # Standard Developer Command / Question with Streaming & Session Memory
            root, name, branch = get_git_info()

            # Compile compact history of previous 3 turns
            history_snippet = ""
            if conversation_history:
                history_snippet = "Контекст недавнего диалога в этой сессии:\n"
                for u, a in conversation_history[-3:]:
                    short_a = a.strip()
                    if len(short_a) > 250:
                        short_a = short_a[:250] + "..."
                    history_snippet += f"• Разработчик: {u}\n• Джарвис: {short_a}\n"
                history_snippet += "\n"

            prompt = f"""
Вопрос или инструкция от Сэра (Tech Lead): "{user_input}"
Контекст:
- Текущий проект: {name or 'Вне проекта'} (ветка: {branch or '-'}, директория: {root or os.getcwd()})
- База знаний: подключен командный центр Main-brain (JARVIS_KNOWLEDGE_BASE) с 42 проектами, PORTS.md и 40 навыками.

{history_snippet}Инструкция:
1. Отвечай как Джарвис: компетентно, четко, без пустых клише.
2. При необходимости используй инструменты (чтение файлов, git-статус, diff, запуск тестов).
3. Код и структурированные данные форматируй в Markdown.
"""
            console.print(f"\n[bold cyan]🦾 J.A.R.V.I.S. ({name or 'main'}):[/]\n")
            result = stream_openclaw_turn(
                prompt,
                session_id=session_id,
                cwd=root or os.getcwd(),
                spinner_text="⚡ Джарвис обрабатывает запрос..."
            )
            console.print()

            if result:
                conversation_history.append((user_input, result))

            if VOICE_OUTPUT_ENABLED and result:
                threading.Thread(target=speak_charon, args=(result,), daemon=True).start()
            console.print()

        except KeyboardInterrupt:
            console.print("\n[dim](Нажмите Ctrl+D или напишите /exit для выхода)[/]")
            continue
        except EOFError:
            console.print("\n[cyan]До свидания, сэр![/]")
            break


def main():
    global VOICE_OUTPUT_ENABLED
    parser = argparse.ArgumentParser(
        description="🦾 J.A.R.V.I.S. Developer Assistant & Execution Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--silent", "-s", action="store_true", help="Запустить в тихом режиме (без звука и озвучки)")
    parser.add_argument("--voice", "-v", action="store_true", help="Включить голосовую озвучку ответов Charon")

    subparsers = parser.add_subparsers(dest="command")

    # repl / default
    subparsers.add_parser("repl", help="Запустить интерактивную сессию (Claude Code style с потоковым выводом)")
    subparsers.add_parser("tui", help="Запустить curses TUI OpenClaw")
    subparsers.add_parser("chat", help="Запустить интерактивную сессию")

    # review
    subparsers.add_parser("review", help="Префлайт-ревьюер текущего diff перед PR")

    # triage
    p_triage = subparsers.add_parser("triage", help="Дежурный по триажу упавших CI пайплайнов")
    p_triage.add_argument("run_id", nargs="?", help="ID упавшего workflow run в GitHub Actions (опционально)")

    # trace
    p_trace = subparsers.add_parser("trace", help="Исследователь кодовой базы (git pickaxe, blame, тикеты)")
    p_trace.add_argument("query", help="Символ, имя функции или строка для глубокого поиска")

    # task
    p_task = subparsers.add_parser("task", help="Автономный фоновый агент в изолированном окружении Git Worktree")
    p_task.add_argument("prompt", help="Текст инженерной задачи")

    # focus
    subparsers.add_parser("focus", help="Персональный утренний дайджест (PRs, задачи, алерты)")

    # ask
    p_ask = subparsers.add_parser("ask", help="Задать быстрый инженерный или архитектурный вопрос")
    p_ask.add_argument("question", help="Текст вопроса")

    args = parser.parse_args()

    if args.silent:
        VOICE_OUTPUT_ENABLED = False
    elif args.voice:
        VOICE_OUTPUT_ENABLED = True

    if not args.command:
        cmd_repl()
    elif args.command in ["repl", "chat"]:
        cmd_repl()
    elif args.command == "tui":
        cmd_tui()
    elif args.command == "review":
        cmd_review(args)
    elif args.command == "triage":
        cmd_triage(args)
    elif args.command == "trace":
        cmd_trace(args.query)
    elif args.command == "task":
        cmd_task(args.prompt)
    elif args.command == "focus":
        cmd_focus(args)
    elif args.command == "ask":
        cmd_ask(args.question)


if __name__ == "__main__":
    main()
