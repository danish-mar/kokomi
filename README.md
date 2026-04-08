# Kokomi: AI-Based Assistant

Initialized with `uv`, this project uses **LangChain** and **Groq** to build high-performance AI-driven features.

## Setup Instructions

1.  **Clone the repository.**
2.  **Ensure `uv` is installed.** If not, install with:
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```
3.  **Configure Environment Variables.** Create a `.env` file from the example:
    ```bash
    cp .env.example .env
    ```
    Add your [Groq API Key](https://console.groq.com/keys) to the `.env` file.
4.  **Install dependencies and run.** 
    With `uv`, dependency management is seamless:
    ```bash
    uv run python main.py
    ```

## Features

- **Groq Integration**: High-speed inference using LPUs.
- **LangChain Core**: Uses modern LangChain message schemas.
- **`uv` Powered**: Fast dependency management and project environments.
