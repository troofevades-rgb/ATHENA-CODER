# Project: ocode

## Stack
- Python 3.10+, httpx, rich, prompt_toolkit
- Talks to local Ollama at $OLLAMA_HOST (default http://localhost:11434)

## Build/test
- `pip install -e .` (already done in .venv)
- No tests yet — would live under tests/

## Layout
- ocode/agent.py        agent loop
- ocode/ollama_client.py thin /api/chat wrapper
- ocode/tools/          built-in tools (read/write/edit/bash/glob/grep)
- ocode/mcp/            MCP stdio integration

## Conventions
- New built-in tools register via @tool() in ocode/tools/registry.py
- Keep tool descriptions terse but include hints about preferred use
