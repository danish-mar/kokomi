# 🌊 Kokomi AI: Divine Strategist OS

<p align="center">
  <img src="https://img.shields.io/badge/Powered%20By-Groq-orange?style=for-the-badge" alt="Groq">
  <img src="https://img.shields.io/badge/Architecture-FastAPI-009688?style=for-the-badge&logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Database-Qdrant-red?style=for-the-badge&logo=qdrant" alt="Qdrant">
  <img src="https://img.shields.io/badge/Integration-WhatsApp-25D366?style=for-the-badge&logo=whatsapp" alt="WhatsApp">
</p>

Kokomi AI is a high-fidelity, autonomous agentic platform designed to orchestrate complex AI interactions across multiple channels. From deep, context-aware WhatsApp conversations to multi-agent task execution, Kokomi provides a sophisticated environment for digital personas to live, learn, and act.

---

## 🚀 Core Features & Minute Details

### 👤 Advanced Character Engine
*   **Dynamic Personas**: Characters are defined by multi-layered system prompts that include core personality, speaking style, and goal-oriented behaviors.
*   **Case-Insensitive Multi-Agent Lookup**: Agents can refer to and deploy each other by name or ID (e.g., "Kokomi" vs "kokomi") without failures.
*   **Context Persistence**: Conversations are saved as structured JSON objects, preserving message history, role-play states, and internal AI thoughts.
*   **Autonomous Deployment**: A primary agent can trigger `deploy_agent` to create a child process for a secondary agent (like Nahida or Yae), who handles a sub-task and returns the result to the caller.

### 📱 Seamless WhatsApp Bridge
*   **Direct REST Architecture**: Unlike discovery-heavy protocols, Kokomi uses a direct `httpx` based REST pipeline to communicate with a dedicated WhatsApp-MCP bridge, reducing latency to milliseconds.
*   **Thinking Mode (Reasoning Visibility)**:
    *   Captured `<thought>` and `<think>` tags from models like Qwen-2.5-32B are processed separately.
    *   The bridge can be configured to either forward these thoughts to your phone or keep them purely in the WebUI.
*   **Secret Admin Commands**: Modify agent behavior on-the-fly directly from your WhatsApp chat:
    *   `thinking_show=true`: Enables transmission of the AI's internal reasoning process.
    *   `thinking_show=false`: Disables thoughts for a more immersion-focused conversation.
*   **Real-time Tool Feedback**: When an agent decides to use a tool or deploy a sub-agent, you get a "confirmation message" on WhatsApp immediately, so you aren't left waiting during long-running tasks.

### 🛠️ Advanced Tool Orchestration
*   **Invisible Browser Redirection**:
    *   The AI can now trigger `redirect_url` to programmatically open links, movie players, or music streams in new browser tabs.
    *   **Auto-Execution**: If you ask it to "play" something, it won't just give you a link; it will immediately open the tab for you.
*   **Natural Language Tool Status**:
    *   Say goodbye to cryptic function names like `search_and_play`. 
    *   The AI now generates human-readable status messages (e.g., *"Searching and playing All of us are dead..."*) which appear in the UI during execution.
*   **Custom MCP Server Icons**:
    *   Configure unique FontAwesome icons for each of your tool servers in the Integrations settings.
    *   The chat UI dynamically renders these icons in the tool pills, providing instant visual recognition.

### 📊 Workflow Canvas & Visualization
*   **Live Mermaid.js Rendering**: Click on any workflow log in the WebUI to open a full-screen interactive graph.
*   **Traceability**: Each node represents a distinct action (User Message → Trigger → Deployment → Tool Call → Final Response).
*   **PNG Export**: High-resolution export of your AI's decision trees for auditing or archiving.

### 📦 Premium Artifacts & Multimodal Attachments
*   **Inline Code Anchoring**: Artifacts are now dynamically injected into the conversation stream using a robust placeholder system, maintaining their exact context.
*   **Real-time Previews**: Artifact cards feature automatic syntax-highlighted previews and state-aware "generating" indicators.
*   **Multimodal File Engine (New)**: 
    *   **Vision Support**: Native integration for image attachments (`.jpg`, `.png`, `.webp`). Encodes images into Base64 for vision-capable models like Gemini to analyze.
    *   **PDF Extraction**: Automated text extraction from all pages of uploaded PDFs using `pypdf`, enabling deep document reasoning.
    *   **Smart Previews**: Visual thumbnails for images and clean metadata chips for text/code files.
    *   **Paste-to-Attach**: Support for `Ctrl+V` pasting of screenshots and files directly into the chat bar.
*   **Cinematic UI DNA**: Seamless cross-fade transitions between the Welcome Screen and active chats, powered by Alpine.js for a fluid OS-like experience.


### 📁 Multi-Agent Document & Slide Deck Exporter Suite (New)
*   **Universal Document Compiler**: Integrated tools (`pdf_export`, `docx_export`, `pptx_export`, `excel_export`) to compile rich research reports into publication-grade assets including PDFs, Word Documents, PowerPoint slide decks, and Excel spreadsheets.
*   **Apple HIG Inline Bold-Text Parser**: Converts raw double asterisks (`**bold**`) dynamically into native styled bold text runs inside PowerPoint and Word documents instead of dump-pasting raw markdown text decorators.
*   **Thread-Safe Workspace Isolation**: All compiled documents are saved dynamically inside the active workflow storage folder (`active_storage_dir`) for a clean project structure rather than general common uploads.
*   **Dynamic Allowed Tools UI**: Added a dynamically populated router system that decouples Alpine.js templates from hardcoded lists, automatically fetching active Model Context Protocol (MCP) server tools on load.

### 📚 RAG & Knowledge Spaces
*   **Vector Orchestration**: Documents are automatically chunked and vectorized using `gemini-embedding-2` and stored in **Qdrant**.
*   **Smart Retrieval**: Characters proactively query their assigned "Spaces" using semantic search to provide grounded, fact-based answers.
*   **Multi-File Support**: Handles PDFs, Markdowns, TXT, and Word documents with automated extraction.

### 🧠 Neural Memory Explorer & Long-Term Memory
*   **Perplexity-Style Context RAG**: Distills chats at the end of each conversation into concise, persistent "Memory Atoms" and vectorizes them into Qdrant.
*   **Sub-Second Parallel Vector Retrieval**: Concurrently queries memory points for all active group participants using `asyncio.gather` on session start.
*   **Gemini Embedding Cache**: Leverages a global model singleton cache to bypass cold-start model initialization latency, reducing RAG search times by 95%.
*   **Dedicated macOS-style Memory Explorer Page**: A full-featured `/memories` dashboard that displays stored memory points per character:
    *   **Live Text Search & Filtering**: Filters stored facts in real-time as you type.
    *   **Manual Fact Insertion Modal**: Feed custom memory atoms directly into Qdrant without waiting for conversational summarization.
    *   **Individual & Bulk Erase**: Forget specific details or completely wipe out a character's long-term memories in one click.
    *   **Incognito Memory Toggles**: Granular settings to disable/enable memory capabilities individually per AI character.

---

## 💻 Technical Architecture

### Tech Stack Breakdown
| Component | Technology | Detail |
| :--- | :--- | :--- |
| **Backend** | FastAPI | High-performance Python async framework. |
| **Inference** | Groq / LangChain | Utilizing Qwen-2.5 and Llama 3 for ultra-fast reasoning. |
| **Vector Store** | Qdrant | Used for RAG knowledge spaces and long-term memory retrieval. |
| **Frontend** | Alpine.js + Tailwind | Lightweight, reactive UI with premium Apple-inspired styling. |
| **Deployment** | Docker + UV | Containerized environment with Astral's `uv` for 10x faster builds. |
| **Communication** | REST / SSE | Real-time streaming to WebUI and RESTful bridge to WhatsApp. |

### Environment Configuration (`.env`)
| Variable | Description | Default |
| :--- | :--- | :--- |
| `GROQ_API_KEY` | Your Groq Cloud API key. | Required |
| `GOOGLE_API_KEY` | Used for Gemini Embeddings. | Required |
| `WHATSAPP_API_URL` | Endpoint for the WhatsApp bridge. | `http://localhost:3013` |
| `DATA_DIR` | Path to persistent storage. | `./data` |

---

## 📂 Project Structure
```text
kokomi/
├── app/                  # Main Backend Logic
│   ├── routers/          # API & Page Routes (Chat, WhatsApp, Prefs, etc.)
│   ├── llm.py            # LLM Factory & Model Providers
│   ├── storage.py        # Persistence Layer (JSON & Files)
│   └── mcp.py            # Tool & MCP Integration Logic
├── templates/            # Premium WebUI (Jinja2)
│   ├── index.html        # Main Chat Dashboard
│   ├── whatsapp.html     # WhatsApp & Workflow Canvas Dashboard
│   └── settings.html     # System & Character Configuration
├── data/                 # Persistent Data (Convos, Chars, Vectors)
├── Dockerfile            # Multi-stage optimized build
└── docker-compose.yml    # Full stack (App + Qdrant) orchestration
```

---

## 🛠️ Installation & Setup

### Option 1: Docker (Fastest)
```bash
docker compose up --build -d
```

### Option 2: Local Development
1. **Install uv**: `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. **Setup environment**: `uv sync`
3. **Configure keys**: Create `.env` with your API keys.
4. **Launch**: `uv run main.py`

---

## 🎨 Design Philosophy
Kokomi follows a "Premium Aesthetic" mantra. The UI is designed to feel like a high-end OS, utilizing:
- **Glassmorphism**: 20px blur with 180% saturation for a frosted-glass feel.
- **Squircle Geometry**: Continuous curves (not simple rounded corners) for all cards and modals.
- **Dark Mode DNA**: Deep indigo and obsidian gradients tailored for professional desktop environments.

---

<p align="center">
  <i>"A strategist does not just predict the future—she prepares for it."</i>
</p>
