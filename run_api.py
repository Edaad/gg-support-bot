#!/usr/bin/env python
"""Entry point for the FastAPI dashboard server (for local development)."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("api.app:app", host="0.0.0.0", port=8000, reload=True)
