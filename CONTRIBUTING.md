# Contributing to Book Translator

Thank you for your interest in contributing to the Book Translator plugin for Calibre-Web-Automated! We welcome contributions from everyone.

## Development Setup

1. Fork and clone the repository.
2. Ensure you have Python 3.11+ installed.
3. Set up a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
4. Install a local LLM or obtain an API key for your preferred cloud provider.
5. Create a `.env` file to store your API keys.

## Pull Request Process

1. Create a descriptive branch name (e.g. `feat/add-new-provider` or `bugfix/fix-cache-lock`).
2. Write clean, self-documenting code.
3. Test your changes locally to ensure translation latency and DOM injection are stable.
4. If modifying the frontend (`translator.js`), test on various ebook formats and themes (Light, Dark, Sepia, Black).
5. Submit a pull request detailing the changes and the rationale.

## Code Style Guide

- **Python**: Follow PEP 8 guidelines. Use type hints (`typing`) wherever possible. Keep the logic contained to avoid importing unnecessary heavy dependencies.
- **JavaScript**: Use modern ES6+ features. Avoid external dependencies in the frontend script to keep the overlay lightweight. No jQuery. Keep DOM mutations isolated to prevent breaking EPUB.js.

## Reporting Bugs

Please use the provided issue templates when reporting a bug. Provide clear reproduction steps, logs from `/metrics`, and mention whether the issue occurs on the frontend (UI glitch) or backend (API failure).
