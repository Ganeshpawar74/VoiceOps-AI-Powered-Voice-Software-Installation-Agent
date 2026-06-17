"""VoiceOps — Core Pydantic v2 domain models.

BUGS FIXED IN THIS FILE:
  BUG-SCHEMA-1: VoiceCommandRequest.user_id was required (str) but main.py does
                `req.user_id or user_id` implying it can be absent/empty.
                FIX: made Optional[str] = None.

  BUG-SCHEMA-2: TextCommandRequest.user_id same issue.
                FIX: made Optional[str] = None.

  BUG-SCHEMA-3: Task.current_step was Optional[str] but TaskStatusResponse
                declared it as Optional[str] too — both consistent, no change needed.
"""
from __future__ import annotations
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, HttpUrl


class Language(str, Enum):
    EN    = "en"
    HI    = "hi"
    HI_EN = "hi-en"

class Intent(str, Enum):
    INSTALL_SOFTWARE = "install_software"
    DOWNLOAD_ONLY    = "download_only"
    UNINSTALL        = "uninstall"
    CHECK_STATUS     = "check_status"
    OPEN_APP         = "open_app"
    UNKNOWN          = "unknown"

class OperatingSystem(str, Enum):
    WINDOWS = "windows"
    LINUX   = "linux"
    MACOS   = "macos"
    UNKNOWN = "unknown"

class TaskStatus(str, Enum):
    PENDING     = "pending"
    RUNNING     = "running"
    DOWNLOADING = "downloading"
    INSTALLING  = "installing"
    COMPLETED   = "completed"
    FAILED      = "failed"
    CANCELLED   = "cancelled"

class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE    = "done"
    FAILED  = "failed"
    SKIPPED = "skipped"

class VerificationResult(str, Enum):
    VERIFIED = "verified"
    FAILED   = "failed"
    SKIPPED  = "skipped"

# Speech
class AudioInput(BaseModel):
    audio_bytes: bytes
    sample_rate: int = 16000
    encoding: str = "wav"
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

class SpeechOutput(BaseModel):
    query: str
    language: Language
    confidence: float = Field(ge=0.0, le=1.0)
    raw_transcript: str
    session_id: str
    processing_time_ms: float

# Intent
class IntentOutput(BaseModel):
    intent: Intent
    software_name: str
    software_canonical: str
    operating_system: OperatingSystem
    extra_params: dict[str, Any] = {}
    confidence: float = Field(ge=0.0, le=1.0)
    raw_query: str

# Planner
class ExecutionStep(BaseModel):
    step_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str
    agent: str
    tool: Optional[str] = None
    params: dict[str, Any] = {}
    depends_on: list[str] = []
    retry_count: int = 0
    max_retries: int = 3
    status: StepStatus = StepStatus.PENDING
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

class ExecutionPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    intent: IntentOutput
    steps: list[ExecutionStep]
    estimated_duration_sec: int
    created_at: datetime = Field(default_factory=datetime.utcnow)

# Browser
class DownloadLink(BaseModel):
    url: HttpUrl
    source_domain: str
    is_official: bool
    file_name: Optional[str] = None
    file_size_bytes: Optional[int] = None
    checksum_sha256: Optional[str] = None
    publisher: Optional[str] = None
    discovered_at: datetime = Field(default_factory=datetime.utcnow)

class BrowserResult(BaseModel):
    success: bool
    download_links: list[DownloadLink] = []
    selected_link: Optional[DownloadLink] = None
    page_title: Optional[str] = None
    navigation_path: list[str] = []
    error: Optional[str] = None

# Download
class DownloadResult(BaseModel):
    success: bool
    local_path: Optional[str] = None
    file_name: Optional[str] = None
    file_size_bytes: Optional[int] = None
    download_duration_sec: float = 0
    checksum_sha256: Optional[str] = None
    verification: VerificationResult = VerificationResult.SKIPPED
    publisher_verified: bool = False
    error: Optional[str] = None

# Install
class InstallResult(BaseModel):
    success: bool
    install_method: str
    install_path: Optional[str] = None
    version_installed: Optional[str] = None
    install_duration_sec: float = 0
    logs: list[str] = []
    error: Optional[str] = None

# Task
class Task(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    session_id: str
    query: str
    plan: Optional[ExecutionPlan] = None
    status: TaskStatus = TaskStatus.PENDING
    progress_pct: int = 0
    current_step: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    speech_output: Optional[SpeechOutput] = None
    intent_output: Optional[IntentOutput] = None
    browser_result: Optional[BrowserResult] = None
    download_result: Optional[DownloadResult] = None
    install_result: Optional[InstallResult] = None

# API
# BUG-SCHEMA-1 & BUG-SCHEMA-2 FIX: user_id is Optional — callers may omit it
# and main.py falls back to the auth header user_id
class VoiceCommandRequest(BaseModel):
    user_id: Optional[str] = None       # FIX: was required str
    audio_base64: str
    session_id: Optional[str] = None

class TextCommandRequest(BaseModel):
    user_id: Optional[str] = None       # FIX: was required str
    query: str
    os_hint: Optional[OperatingSystem] = None
    session_id: Optional[str] = None

class TaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    message: str

class TaskStatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    progress_pct: int
    current_step: Optional[str]
    result: Optional[dict[str, Any]]
    error: Optional[str]
    created_at: datetime
    updated_at: datetime

class NotificationEvent(BaseModel):
    task_id: str
    event: str
    message: str
    data: dict[str, Any] = {}
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    components: dict[str, str] = {}