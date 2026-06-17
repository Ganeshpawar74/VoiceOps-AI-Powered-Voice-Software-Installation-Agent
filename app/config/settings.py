"""
VoiceOps — Configuration
All env vars loaded via pydantic-settings.

FIXES IN THIS VERSION:
  All prior fixes retained.
  NEW: Added download_to_desktop feature flag and desktop_dir to AppSettings.
  NEW: Added ChatGPT / OpenAI app to all registries.
  NEW: Added DuckDuckGo search fallback URL for browser agent.
"""
from __future__ import annotations
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Literal, List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DB_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
    )
    pool_size: int = 20
    max_overflow: int = 40
    echo: bool = False


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REDIS_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    url: str = Field(default="redis://localhost:6379/0")
    celery_broker: str = "redis://localhost:6379/1"
    celery_backend: str = "redis://localhost:6379/2"
    ttl_short: int = 3600
    ttl_session: int = 86400
    socket_keepalive: bool = True
    socket_connect_timeout: int = 5


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LLM_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    provider: Literal["mistral"] = "mistral"
    mistral_api_key: str = Field(default="")
    mistral_base_url: str = "https://api.mistral.ai/v1"
    embedding_model: str = "mistral-embed"

    @property
    def base_url(self) -> str:
        return self.mistral_base_url

    intent_model: str = "mistral-small-latest"
    planner_model: str = "mistral-small-latest"
    tool_model: str = "mistral-small-latest"
    temperature: float = 0.1
    max_tokens: int = 2048
    timeout: int = 30


class STTSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="STT_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    provider: Literal["whisper", "sarvam"] = "whisper"
    whisper_model_size: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    sarvam_api_key: str = ""
    sarvam_base_url: str = "https://api.sarvam.ai"
    supported_languages: List[str] = ["en", "hi", "hi-en"]
    sample_rate: int = 16000


class TTSSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TTS_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    provider: Literal["sarvam", "none"] = "sarvam"
    sarvam_api_key: str = ""
    sarvam_base_url: str = "https://api.sarvam.ai"
    target_language_code: str = "en-IN"
    speaker: str = "meera"
    model: str = "bulbul:v1"
    enable: bool = True


class VectorDBSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VECTOR_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    provider: Literal["qdrant", "chromadb", "faiss"] = "qdrant"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "voiceops_docs"
    embedding_dim: int = 1024
    top_k: int = 5


class BrowserSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BROWSER_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    headless: bool = True
    timeout_ms: int = 30000
    navigation_timeout_ms: int = 60000

    trusted_domains: list[str] = [
        "microsoft.com", "github.com", "python.org",
        "code.visualstudio.com", "docker.com", "postman.com",
        "google.com", "mozilla.org", "jetbrains.com",
        "nodejs.org", "npmjs.com", "apple.com", "7-zip.org",
        "git-scm.com", "zoom.us", "slack.com", "rust-lang.org",
        "go.dev", "videolan.org", "gimp.org", "blender.org",
        "obsproject.com", "discord.com", "sublimetext.com",
        "developer.android.com", "inkscape.org", "telegram.org",
        "openai.com", "apps.microsoft.com",
    ]

    blocked_domains: list[str] = [
        "filehippo.com", "softonic.com", "soft32.com", "soft112.com",
        "download.cnet.com", "cnet.com", "uptodown.com", "softpedia.com",
        "apponic.com", "filehorse.com", "getintopc.com", "filecroco.com",
        "freedownloadmanager.org", "brothersoft.com", "fileplanet.com",
        "downloadastro.com", "malavida.com",
    ]

    trust_mode: Literal["allowlist", "heuristic"] = "heuristic"

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )


class SecuritySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SEC_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    jwt_secret: str = "CHANGE_IN_PRODUCTION_USE_LONG_RANDOM_STRING"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    verify_checksums: bool = True
    verify_publisher: bool = True
    require_https: bool = True
    max_file_size_mb: int = 4096


class ObservabilitySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OBS_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    jaeger_endpoint: str = "http://localhost:14268/api/traces"
    prometheus_port: int = 9090
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    enable_tracing: bool = True
    enable_metrics: bool = True


class FeatureFlagSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FEATURE_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    rag_enabled: bool = False
    ocr_fallback: bool = True
    vision_fallback: bool = False
    human_in_loop: bool = False
    tts_enabled: bool = True
    # NEW: save installer to Desktop before running it
    download_to_desktop: bool = True


class SoftwareRegistrySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REGISTRY_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    software_aliases: dict[str, str] = Field(default_factory=lambda: {
        "vscode": "Visual Studio Code", "vs code": "Visual Studio Code",
        "visual studio code": "Visual Studio Code", "code": "Visual Studio Code",
        "python": "Python", "python3": "Python", "py": "Python",
        "docker": "Docker Desktop", "docker desktop": "Docker Desktop",
        "postman": "Postman", "chrome": "Google Chrome",
        "google chrome": "Google Chrome", "firefox": "Mozilla Firefox",
        "node": "Node.js", "nodejs": "Node.js", "node.js": "Node.js",
        "git": "Git", "java": "Java JDK", "jdk": "Java JDK",
        "slack": "Slack", "zoom": "Zoom", "vlc": "VLC Media Player",
        "7zip": "7-Zip", "7-zip": "7-Zip", "notepad++": "Notepad++",
        "notepad plus": "Notepad++", "intellij": "IntelliJ IDEA",
        "pycharm": "PyCharm", "rust": "Rust", "golang": "Go",
        "go": "Go",
        "sublime": "Sublime Text", "sublime text": "Sublime Text",
        "android studio": "Android Studio", "discord": "Discord",
        "telegram": "Telegram", "obs": "OBS Studio", "obs studio": "OBS Studio",
        "gimp": "GIMP", "inkscape": "Inkscape", "blender": "Blender",
        "github": "GitHub Desktop", "github desktop": "GitHub Desktop",
        "git hub": "GitHub Desktop", "githubdesktop": "GitHub Desktop",
        "github cli": "GitHub CLI", "gh cli": "GitHub CLI", "gh": "GitHub CLI",
        "notion": "Notion", "spotify": "Spotify", "figma": "Figma",
        "anaconda": "Anaconda", "winrar": "WinRAR", "steam": "Steam",
        "whatsapp": "WhatsApp Desktop", "teams": "Microsoft Teams",
        "microsoft teams": "Microsoft Teams", "skype": "Skype",
        "putty": "PuTTY", "filezilla": "FileZilla", "audacity": "Audacity",
        "handbrake": "HandBrake", "wireshark": "Wireshark",
        # ChatGPT / OpenAI — many mis-transcription forms
        "chatgpt": "ChatGPT", "chat gpt": "ChatGPT", "chat g p t": "ChatGPT",
        "chatgbt": "ChatGPT", "chat gbt": "ChatGPT",
        "openai": "ChatGPT", "open ai": "ChatGPT",
        "chad gpt": "ChatGPT", "chat jpt": "ChatGPT",
    })

    hinglish_keywords: list[str] = Field(default_factory=lambda: [
        "karo", "chahiye", "lagao", "install karo", "download karo", "chahta"
    ])

    official_download_urls: dict[str, str] = Field(default_factory=lambda: {
        "Visual Studio Code": "https://code.visualstudio.com/Download",
        "Python": "https://www.python.org/downloads/",
        "Docker Desktop": "https://www.docker.com/products/docker-desktop/",
        "Postman": "https://www.postman.com/downloads/",
        "Google Chrome": "https://www.google.com/chrome/",
        "Mozilla Firefox": "https://www.mozilla.org/en-US/firefox/new/",
        "Node.js": "https://nodejs.org/en/download/",
        "Git": "https://git-scm.com/downloads",
        "Slack": "https://slack.com/downloads/",
        "Zoom": "https://zoom.us/download",
        "7-Zip": "https://www.7-zip.org/download.html",
        "IntelliJ IDEA": "https://www.jetbrains.com/idea/download/",
        "PyCharm": "https://www.jetbrains.com/pycharm/download/",
        "Rust": "https://www.rust-lang.org/tools/install",
        "Go": "https://go.dev/dl/",
        "Sublime Text": "https://www.sublimetext.com/download",
        "Android Studio": "https://developer.android.com/studio",
        "Discord": "https://discord.com/download",
        "Telegram": "https://desktop.telegram.org/",
        "OBS Studio": "https://obsproject.com/download",
        "GIMP": "https://www.gimp.org/downloads/",
        "Inkscape": "https://inkscape.org/release/",
        "Blender": "https://www.blender.org/download/",
        "VLC Media Player": "https://www.videolan.org/vlc/",
        "Notepad++": "https://notepad-plus-plus.org/downloads/",
        "Java JDK": "https://adoptium.net/temurin/releases/",
        "GitHub Desktop": "https://desktop.github.com/",
        "GitHub CLI": "https://cli.github.com/",
        # ChatGPT uses Microsoft Store on Windows
        "ChatGPT": "https://apps.microsoft.com/detail/9nt1r1c2hh7j",
    })

    winget_packages: dict[str, str] = Field(default_factory=lambda: {
        "Visual Studio Code":  "Microsoft.VisualStudioCode",
        "Python":              "Python.Python.3.12",
        "Google Chrome":       "Google.Chrome",
        "Mozilla Firefox":     "Mozilla.Firefox",
        "Node.js":             "OpenJS.NodeJS.LTS",
        "Git":                 "Git.Git",
        "Docker Desktop":      "Docker.DockerDesktop",
        "Postman":             "Postman.Postman",
        "Slack":               "SlackTechnologies.Slack",
        "Zoom":                "Zoom.Zoom",
        "7-Zip":               "7zip.7zip",
        "Notepad++":           "Notepad++.Notepad++",
        "Discord":             "Discord.Discord",
        "VLC Media Player":    "VideoLAN.VLC",
        "OBS Studio":          "OBSProject.OBSStudio",
        "GIMP":                "GIMP.GIMP",
        "Blender":             "BlenderFoundation.Blender",
        "Telegram":            "Telegram.TelegramDesktop",
        "IntelliJ IDEA":       "JetBrains.IntelliJIDEA.Community",
        "PyCharm":             "JetBrains.PyCharm.Community",
        "GitHub Desktop":      "GitHub.GitHubDesktop",
        "GitHub CLI":          "GitHub.cli",
        "Spotify":             "Spotify.Spotify",
        "Notion":              "Notion.Notion",
        "Figma":               "Figma.Figma",
        "Steam":               "Valve.Steam",
        "Audacity":            "Audacity.Audacity",
        "HandBrake":           "HandBrake.HandBrake",
        "WinRAR":              "RARLab.WinRAR",
        "PuTTY":               "PuTTY.PuTTY",
        "Wireshark":           "WiresharkFoundation.Wireshark",
        # ChatGPT — winget id for the Microsoft Store app
        "ChatGPT":             "9NT1R1C2HH7J",
    })

    brew_packages: dict[str, str] = Field(default_factory=lambda: {
        "Visual Studio Code":  "visual-studio-code",
        "Google Chrome":       "google-chrome",
        "Mozilla Firefox":     "firefox",
        "Node.js":             "node",
        "Git":                 "git",
        "Docker Desktop":      "docker",
        "Postman":             "postman",
        "Slack":               "slack",
        "Zoom":                "zoom",
        "7-Zip":               "p7zip",
        "Discord":             "discord",
        "VLC Media Player":    "vlc",
        "OBS Studio":          "obs",
        "GIMP":                "gimp",
        "Blender":             "blender",
        "Telegram":            "telegram",
        "Python":              "python@3.12",
        "GitHub Desktop":      "github",
        "GitHub CLI":          "gh",
        "Spotify":             "spotify",
    })

    apt_packages: dict[str, str] = Field(default_factory=lambda: {
        "Git":              "git",
        "Node.js":          "nodejs",
        "Python":           "python3",
        "VLC Media Player": "vlc",
        "GIMP":             "gimp",
        "Inkscape":         "inkscape",
        "Blender":          "blender",
        "GitHub CLI":       "gh",
    })

    snap_packages: dict[str, str] = Field(default_factory=lambda: {
        "Visual Studio Code": "code",
        "Discord":            "discord",
        "Slack":              "slack",
        "Zoom":               "zoom-client",
        "Postman":            "postman",
        "OBS Studio":         "obs-studio",
        "Telegram":           "telegram-desktop",
        "IntelliJ IDEA":      "intellij-idea-community",
        "PyCharm":            "pycharm-community",
        "Android Studio":     "android-studio",
    })


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    app_name: str = "VoiceOps AI Agent System"
    app_version: str = "1.0.0"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    celery_task_soft_time_limit: int = 600
    celery_task_time_limit: int = 900
    celery_max_retries: int = 3
    celery_retry_backoff: int = 60

    temp_dir: str = ""

    database:   DatabaseSettings    = Field(default_factory=DatabaseSettings)
    redis:      RedisSettings       = Field(default_factory=RedisSettings)
    llm:        LLMSettings         = Field(default_factory=LLMSettings)
    stt:        STTSettings         = Field(default_factory=STTSettings)
    tts:        TTSSettings         = Field(default_factory=TTSSettings)
    vector_db:  VectorDBSettings    = Field(default_factory=VectorDBSettings)
    browser:    BrowserSettings     = Field(default_factory=BrowserSettings)
    security:   SecuritySettings    = Field(default_factory=SecuritySettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    features:   FeatureFlagSettings = Field(default_factory=FeatureFlagSettings)
    registry:   SoftwareRegistrySettings = Field(default_factory=SoftwareRegistrySettings)

    @property
    def downloads_dir(self) -> str:
        d = Path(self.temp_dir) if self.temp_dir else (
            Path(tempfile.gettempdir()) / "voiceops_downloads"
        )
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    @property
    def desktop_dir(self) -> str:
        """User's Desktop directory — where downloaded installers are placed."""
        desktop = Path.home() / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        return str(desktop)


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()
