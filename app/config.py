import json
import os
from argparse import ArgumentParser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import urlparse

from app.prompts import FAIRY_CORE_SYSTEM_PROMPT


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_BUNDLED_SERVER = BASE_DIR / "app" / "ai" / "llama-server.exe"
LOCAL_SECRETS_FILE = BASE_DIR / "config" / "local_secrets.json"


def _load_local_secrets() -> dict[str, str]:
    if not LOCAL_SECRETS_FILE.exists():
        return {}
    try:
        data = json.loads(LOCAL_SECRETS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {str(k): str(v) for k, v in data.items() if v is not None}


_LOCAL_SECRETS = _load_local_secrets()


def _get_secret(name: str, default: str = "") -> str:
    return os.getenv(name, _LOCAL_SECRETS.get(name, default))


@dataclass
class LLMConfig:
    # Runtime mode:
    # - "external_api": connect to an already running OpenAI-compatible server
    # - "local_server": start llama-server.exe from this app
    runtime_mode: str = "local_server"

    api_base: str = "http://127.0.0.1:12765"
    api_path: str = "/v1/chat/completions"
    api_key: str | None = None
    model: str = "Qwen3.5-9B-Q4_K_M"

    server_executable: Path = DEFAULT_BUNDLED_SERVER
    # Keep this relative so llama.cpp receives an ASCII path even when the repo
    # lives under a non-ASCII Windows directory.
    model_path: Path = Path("model") / "Qwen3.5-9B-Q4_K_M.gguf"
    mmproj_path: Optional[Path] = None
    ctx_size: int = 4096
    gpu_layers: int = 99
    threads: int = 12
    auto_start_server: bool = True
    startup_timeout_sec: int = 180
    reasoning_budget: int = 0
    reasoning_format: str = "none"

    request_timeout_sec: int = 120
    enable_multimodal: bool = True
    hidden_deliberation: bool = True
    hidden_text_analysis_tokens: int = 96
    hidden_vision_analysis_tokens: int = 80
    context_margin_tokens: int = 512
    approx_chars_per_token: float = 1.3
    image_token_cost: int = 256
    max_history_messages: int = 8
    max_memory_hint_chars: int = 900
    min_system_prompt_chars: int = 1000
    max_attachment_files: int = 6
    max_attachment_chars_per_file: int = 6000
    max_attachment_chars_total: int = 18000
    max_image_bytes: int = 8 * 1024 * 1024
    max_image_side: int = 896
    image_jpeg_quality: int = 82
    text_max_tokens: int = 512
    vision_max_tokens: int = 128
    enable_web_search: bool = True
    web_search_max_results: int = 5
    web_read_max_pages: int = 3
    web_page_max_chars: int = 2500
    web_context_total_chars: int = 6000
    web_decision_max_tokens: int = 8
    web_request_timeout_sec: int = 12
    search_backend: str = "auto"
    search_api_url: str = ""
    search_api_key: str = ""
    browser_automation_enabled: bool = True
    browser_navigation_timeout_sec: int = 15
    browser_post_load_wait_ms: int = 1200


@dataclass
class MemoryConfig:
    memory_file: Path = BASE_DIR / "data" / "memory.json"
    legacy_memory_file: Path = BASE_DIR / "data" / "memory.json"
    db_path: Path = BASE_DIR / "data" / "fairy_memory.db"
    max_items: int = 500
    semantic_dimension: int = 192
    max_semantic_items: int = 1200


@dataclass
class PersonalityConfig:
    name: str = "Fairy"
    base_traits: str = FAIRY_CORE_SYSTEM_PROMPT


@dataclass
class PersonaConfig:
    default_source: Path = BASE_DIR / "config" / "fairy_normal_persona.json"
    mode: str = "normal"
    enable_style_guard: bool = True
    enable_prompt_injection: bool = True
    max_recent_summary_chars: int = 280


@dataclass
class UIConfig:
    width: int = 260
    height: int = 260
    inner_radius_ratio: float = 0.38
    outer_radius_ratio: float = 0.48


@dataclass
class SystemConfig:
    state_file: Path = BASE_DIR / "config" / "system_state.json"
    startup_status_lines: tuple[str, ...] = (
        "系统启动中。",
        "正在加载核心模块。",
        "正在初始化控制接口。",
        "正在校准语音模块。",
        "视觉模块已激活。",
        "正在同步运行状态。",
        "核心链路运行正常。",
        "主人，系统已恢复在线。",
    )
    welcome_voice_text: str = (
        "系统启动完成。\n"
        "我是三型总序式集成泛用人工智能。\n"
        "开发代号 Fairy。\n\n"
        "你好，主人。"
    )


@dataclass
class VoiceConfig:
    enabled: bool = True
    backend: str = "cosyvoice2_service"
    voice_profile: str = "clone_mecha"
    voice_prompt_selection_mode: str = "auto"
    speak_responses: bool = True
    speak_system: bool = True
    speak_thinking_notice: bool = False
    stream_responses: bool = True
    speak_min_chars: int = 8
    thinking_notice_delay_sec: float = 1.8
    max_sentence_chars: int = 40
    max_response_sentences: int = 0
    max_response_chars: int = 0
    warmup_on_start: bool = True
    voice_prompt_dir: Path = BASE_DIR / "app" / "ai" / "voice"
    audio_sample_rate: int = 24000
    cosyvoice_model_repo: str = "FunAudioLLM/CosyVoice2-0.5B"
    cosyvoice_model_dir: Path = BASE_DIR / "models" / "CosyVoice2-0.5B"
    cosyvoice_device: str = "cuda"
    cosyvoice_speaker: str = "default"
    cosyvoice_zero_shot_spk_id: str = "fairy_zero_shot"
    cosyvoice_env_dir: Path = BASE_DIR / "cosyvoice_env"
    cosyvoice_python_path: Path = BASE_DIR / "cosyvoice_env" / "Scripts" / "python.exe"
    cosyvoice_service_host: str = "127.0.0.1"
    cosyvoice_service_port: int = 12970
    cosyvoice_service_start_timeout_sec: int = 180
    cosyvoice_request_timeout_sec: int = 180
    cosyvoice_text_frontend: bool = False
    prepared_prompt_dir: Path = BASE_DIR / "data" / "voice_prompt"
    prompt_min_sec: float = 4.0
    prompt_max_sec: float = 30.0
    prompt_target_sec: float = 28.0
    cosyvoice_startup_timeout_sec: int = 600
    timestamp_chars_per_second: float = 7.2

    def uses_clone_profile(self) -> bool:
        return self.voice_profile in {"clone_clean", "clone_mecha"}

    def uses_mecha_profile(self) -> bool:
        return self.voice_profile == "clone_mecha"


@dataclass
class AgentConfig:
    allowed_roots: tuple[Path, ...] = (
        BASE_DIR,
        Path.home() / "Documents",
        Path.home() / "Downloads",
    )
    default_document_write_mode: str = "write_new_copy"
    revised_copy_suffix: str = "_revised"
    screenshot_dir: Path = BASE_DIR / "data" / "screenshots"
    max_web_pages_per_request: int = 3
    max_search_results_per_query: int = 5
    allow_screen_control: bool = False
    command_timeout_sec: int = 120
    agent_max_steps: int = 6
    agent_file_read_limit: int = 4


@dataclass
class NewsConfig:
    db_path: Path = BASE_DIR / "data" / "fairy_news.db"
    ithome_rss_url: str = "https://www.ithome.com/rss/"
    request_timeout_sec: int = 15
    headline_limit: int = 50
    briefing_top_n: int = 5
    project_related_limit: int = 5
    full_content_relevance_threshold: float = 0.48
    full_content_project_threshold: float = 0.42
    full_content_observe_threshold: float = 0.55
    max_cached_articles: int = 1500
    preferred_tags: tuple[str, ...] = (
        "ai_llm",
        "agent",
        "multimodal",
        "voice_tts",
        "windows",
        "apple",
        "gpu",
        "pc_hardware",
        "developer_tools",
        "browser_tools",
    )
    project_topics: tuple[str, ...] = (
        "desktop assistant",
        "voice assistant",
        "realtime tts",
        "memory system",
        "tool use",
        "screen understanding",
        "document understanding",
        "browser automation",
        "local model",
        "provider abstraction",
    )
    skip_keywords: tuple[str, ...] = (
        "纯爆料",
        "娱乐化标题",
        "无实质参数",
        "爆料",
        "曝光",
        "传闻",
    )


llm_config = LLMConfig()
memory_config = MemoryConfig()
personality_config = PersonalityConfig()
persona_config = PersonaConfig()
ui_config = UIConfig()
system_config = SystemConfig()
voice_config = VoiceConfig()
agent_config = AgentConfig()
news_config = NewsConfig()


def apply_cli_overrides(argv: Sequence[str] | None = None) -> LLMConfig:
    parser = ArgumentParser(add_help=False)
    parser.add_argument("--runtime-mode", choices=["local_server", "external_api"])
    parser.add_argument("--api-base")
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--mmproj-path", type=Path)
    parser.add_argument(
        "--search-backend",
        choices=[
            "auto",
            "searxng",
            "ddgs",
            "browser",
        ],
    )
    parser.add_argument("--search-api-url")
    parser.add_argument("--disable-auto-start-server", action="store_true")
    args, _ = parser.parse_known_args(list(argv) if argv is not None else None)

    if args.runtime_mode:
        llm_config.runtime_mode = args.runtime_mode
    if args.api_base:
        llm_config.api_base = args.api_base
    if args.model_path:
        llm_config.model_path = args.model_path
    if args.mmproj_path:
        llm_config.mmproj_path = args.mmproj_path
    if args.search_backend:
        llm_config.search_backend = args.search_backend
    if args.search_api_url:
        llm_config.search_api_url = args.search_api_url
    if args.disable_auto_start_server:
        llm_config.auto_start_server = False
    return llm_config


def validate_llm_config(config: LLMConfig) -> None:
    errors: list[str] = []

    parsed = urlparse(config.api_base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        errors.append(f"Invalid api_base: {config.api_base}")

    if config.runtime_mode not in {"local_server", "external_api"}:
        errors.append(f"Invalid runtime_mode: {config.runtime_mode}")

    if config.runtime_mode == "local_server":
        if not config.server_executable.exists():
            errors.append(f"llama-server executable not found: {config.server_executable}")
        if not config.model_path.exists():
            errors.append(f"Model file not found: {config.model_path}")
        if config.mmproj_path is not None and not config.mmproj_path.exists():
            errors.append(f"mmproj file not found: {config.mmproj_path}")

    if config.search_backend == "searxng" and not config.search_api_url.strip():
        errors.append("search_backend is 'searxng' but search_api_url is empty.")

    if errors:
        raise ValueError("\n".join(errors))



