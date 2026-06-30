# Development Guide

This guide details how to work on the `book-translator` codebase.

## Backend Development

The backend is a Flask application running in python. 

### Local Setup

1. Create a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the development server:
   ```bash
   python3 server.py
   ```

### Running Tests

Run the test suite inside the virtual environment:
```bash
.venv/bin/python3 test_translation.py
```

## Frontend Development

The frontend consists of `static/translator.js` and `static/translator.css`.

### Syntax Validation

Validate JavaScript syntax using node.js:
```bash
node -c static/translator.js
```
