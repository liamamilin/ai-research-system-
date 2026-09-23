# TECH STACK

## Language
Python 3.10+ (backend/CLI), TypeScript 5.6 (frontend)

## Framework
FastAPI 0.116 + Uvicorn (Web), React 18 + Vite 5 + TailwindCSS 3 (UI)

## Package Manager
pip (venv at ../.AI_research), npm (ui/)

## Install Command
`pip install -r requirements.txt && cd ui && npm install`

## Run Command
`python run.py <job>` (CLI) / `python run_web.py serve` (Web, port 8765)

## Test Command
`python -m pytest -q` / `cd ui && npm test` (vitest)

## Build Command
`cd ui && npm run build`

## Lint Command
`cd ui && npm run lint` / full gate: `bash scripts/check.sh`

## Storage
SQLite (state/users.db, state/reports.db FTS5, new: state/tracking.db, vectors)

## Key Libraries
PyJWT, bcrypt, ruamel.yaml, watchfiles, python-dotenv, zustand, monaco-editor, react-markdown
