# VoiceOps — AI-Powered Voice Software Installation Agent

> **Speak a command → AI automates the entire software research, download, installation, and verification loop.**

```text
"Install VS Code"  →  🎤 STT  →  🧠 Intent  →  📋 Plan  →  🌐 Browse  →  ⬇ Download  →  ⚙ Install  →  🔔 Notify

```

VoiceOps is an intelligent, multi-agent operating assistant that converts natural language voice commands into fully automated, cross-platform software installations. Utilizing a centralized **FastAPI backend orchestration engine**, stateful workflow management, and specialized autonomous agents, VoiceOps handles structural parsing, official source aggregation, secure streaming downloads, silent automated execution loops, and natural-voice user validation.

---

## 🏗️ System Architecture

### High-Level End-to-End System Flow

The diagram below details the operational layout of the installation agent pipeline, guiding an incoming command through voice parsing, intent extraction, multi-agent coordination, and feedback loops.

### Detailed Core Architecture & Tech Stack Layers

This diagram captures the internal orchestration pipeline, asynchronous job boundaries, cross-cutting infrastructure concerns, and distinct execution inputs/outputs mapping to individual specialist agents.

---

## 🚀 Key Features

* **🎙️ 100% Voice-First Interaction:** Converts speech to structured actions seamlessly while native support processes standard English, Hindi, and Hinglish mixed expressions.
* **🤖 Multi-Agent Orchestration:** Coordinated state execution graph powered by a centralized FastAPI task runner ensuring strict context management and resilient error handling.
* **🌐 Automated Smart Sourcing:** Uses dynamic web engine queries and Playwright browser automation alongside direct API integrations to track official platform vendors.
* **💻 Cross-Platform Support:** Seamless host operating system abstraction utilizing native package manager bindings (`winget`, `brew`, `apt`) and custom shell executors.
* **🔒 Secure & Verified Installations:** Enforces background security checks, file integrity hashes (SHA-256 validation), privilege escalation rules, and error tracking telemetry.
* **📈 Real-Time Tracking & Notifications:** Leverages Redis Pub/Sub, WebSockets, and Server-Sent Events (SSE) to push instant interactive installation workflows to the front end.

---

## 🛠️ Technology Stack

| Layer | Technology | Purpose / Implementation |
| --- | --- | --- |
| **STT** | `faster-whisper` (Local) / OpenAI Whisper / Sarvam AI | Audio-to-text transcription with noise reduction processing. |
| **LLM / Reasoner** | Mistral API (`mistral-small-latest` / `mistral-large-latest`) | Semantic intent extraction, OS matching parameter mapping, and structural JSON parsing. |
| **Orchestration** | LangGraph / FastAPI Backend | Graph-based stateful workflow automation, task scheduling, and error-recovery loops. |
| **Task Queue** | Celery + Redis | Asynchronous background workers management and real-time execution distribution. |
| **Database & Cache** | PostgreSQL (SQLAlchemy async) / SQLite / Redis | User task queues tracking, structural historical logging, caching, and state management. |
| **Storage & Tracking** | ELK Stack / Prometheus / Local FS | Telemetry tracking, system health logs collection, configuration management, and audit archives. |
| **Browser Auto** | Playwright + OCR Fallbacks | Headless web interaction engine for locating, parsing, and extracting authentic target download links. |
| **Vector DB (RAG)** | Qdrant | Fast contextual lookup of command maps, script files, and dynamic runtime metadata. |
| **Containerization** | Docker / Docker Compose | Isolated application deployment configuration for repeatable cross-system local testing environments. |

---

## 🤖 Specialized Multi-Agent Breakdown

The system achieves structural separation of concerns across dedicated micro-agents:

1. **Speech to Text Agent (Whisper):** Intercepts `.wav`/`.mp3` audio tracks, eliminates environmental channel noises, and parses speech structures to clean user transcripts.
2. **Intent & Planning Agent (Mistral LLM):** Identifies specific actions and extracts context metrics to compile organized JSON payloads containing intent profiles, target operating systems, and step sequences.
3. **Search Agent:** Queries indexing engines and software package directories to discover verified web-source delivery networks.
4. **Download Agent:** Orchestrates non-blocking streaming data tasks, provides runtime download tracking, and executes checksum cryptographic integrity validations.
5. **Install Agent:** Detects setup payload format frameworks, establishes background privilege hooks (`sudo`/Administrator controls), and passes automated silent execution switches.
6. **Verification Agent:** Performs file path scans, registry verification checks, and active version command evaluations to confirm structural platform readiness.
7. **Response Agent (Mistral + TTS):** Crafts human-like, interactive natural text updates, running generated outputs directly through Sarvam TTS architectures to provide audio responses to users.

---

## 🎯 Primary Use Cases

* **Install Developer Tools:** Instant background setups for environments including VS Code, Python runtimes, Docker instances, Git, and IDE plugins.
* **Install Productivity Apps:** Hands-free installation of office utilities, browsers (Chrome/Firefox), media playback hubs, and communications tools (Slack/Teams).
* **Install System Utilities:** Clean setup management covering background system archiving applications, file management engines, and basic diagnostics.
* **Voice Controlled Automation:** Direct multi-step setup sequences chaining multiple dependent software applications inside an unmonitored shell script environment.

---

## 🗣️ Supported Commands

VoiceOps actively intercepts conversational language patterns, including direct, multi-step, and cross-lingual instructions:

* `"Install VS Code"`
* `"Download Python 3.12 for Windows"`
* `"Install Docker Desktop"`
* `"Install Postman and open it"`
* `"VS Code install karo"` *(Hinglish)*
* `"Python install chahiye"` *(Hindi)*

---

## 🔌 API Endpoints

| Method | Path | Description |
| --- | --- | --- |
| **POST** | `/api/v1/voice/command` | Submit voice commands (accepts base64 encoded audio payloads) |
| **POST** | `/api/v1/text/command` | Submit plaintext instruction commands |
| **GET** | `/api/v1/tasks/{id}` | Poll historical state or target specific task statuses |
| **GET** | `/api/v1/tasks/{id}/stream` | Attach directly to an active SSE progress stream |
| **GET** | `/api/v1/tasks` | Fetch general task history |
| **WS** | `/api/v1/ws/{user_id}` | Establish active live WebSocket channels for instant updates |
| **GET** | `/api/health` | Service health status check |
| **GET** | `/api/docs` | Interactive Swagger UI API playground |

---

## 🚀 Quick Start

### Prerequisites

* Python 3.10+
* Docker & Docker Compose
* Node.js (for Playwright system dependencies)

### Installation & Initialization

1. **Clone the Repository:**
```bash
git clone [https://github.com/yourname/voiceops](https://github.com/yourname/voiceops)
cd voiceops

```


2. **Configure Environment Variables:**
```bash
cp .env.example .env
# Open .env and populate your LLM_MISTRAL_API_KEY, SARVAM_API_KEY, and database credentials.

```


3. **Run Setup Infrastructure Script:**
```bash
bash scripts/setup.sh

```


4. **Launch the FastAPI Server:**
```bash
uvicorn app.api.main:app --reload

```



> 💡 For comprehensive deployment configuration instructions, environment parameters, and multi-platform manual setups, see the [Full Installation Guide](https://www.google.com/search?q=docs/INSTALLATION.md).

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.
"""

with open("README.md", "w", encoding="utf-8") as f:
f.write(readme_content)

print("File written successfully as README.md")

```
Your markdown document is ready to download.

[file-tag: code-generated-file-0-1781717734917608736]

### 📂 How to make images appear on GitHub
For the system architecture images to load natively on GitHub, make sure to save and commit your two images inside your repository using this exact directory layout:

```text
your-project-root/
│
├── docs/
│   └── images/
│       ├── Agent.png          <--- Save High-Level Architecture here
│       └── Architecture.png   <--- Save Core/Detailed Architecture here
│
└── README.md                  <--- Place this downloaded file here

```
