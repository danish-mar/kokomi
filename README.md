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

### 📊 Workflow Canvas & Visualization
*   **Live Mermaid.js Rendering**: Click on any workflow log in the WebUI to open a full-screen interactive graph.
*   **Traceability**: Each node represents a distinct action (User Message → Trigger → Deployment → Tool Call → Final Response).
*   **PNG Export**: High-resolution export of your AI's decision trees for auditing or archiving.

### 📚 RAG & Knowledge Spaces
*   **Vector Orchestration**: Documents are automatically chunked and vectorized using `gemini-embedding-2` and stored in **Qdrant**.
*   **Smart Retrieval**: Characters proactively query their assigned "Spaces" using semantic search to provide grounded, fact-based answers.
*   **Multi-File Support**: Handles PDFs, Markdowns, TXT, and Word documents with automated extraction.

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
