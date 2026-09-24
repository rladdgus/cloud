# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python app that runs a Korean YouTube Shorts knowledge channel autonomously on the user's PC: Claude writes and fact-checks scripts, Edge TTS + Pillow + ffmpeg (bundled via `imageio-ffmpeg`) build the video, the YouTube Data API uploads it and handles comments. User-facing docs (`README.md`, `config.yaml` comments, log/notify messages) are in Korean.

## Commands

- Install: `pip install -r requirements.txt` (Python 3.10+)
- Run: `python run.py [auth|test|once|comments|stats|start]` — `test` builds a video without uploading
- Lint: `python -m pyflakes ytauto run.py`
- No test suite; to exercise the pipeline offline, monkeypatch `ytauto.llm.ask_json` / `ask_with_web_json` and `ytauto.media.synthesize` and call `jobs.make_and_upload(config, dry_run=True)`.

## Architecture

- `run.py` — CLI + scheduler loop (`start`), catches per-job failures so the loop never dies.
- `ytauto/jobs.py` — the three jobs: `make_and_upload`, `handle_comments`, `update_stats`.
- `ytauto/content.py` — series selection (UCB bandit over views), writer prompt + JSON schema, reviewer (web-search fact check) with rewrite loop.
- `ytauto/llm.py` — Anthropic SDK wrapper; structured outputs via `output_config.format`, server-side refusal fallbacks.
- `ytauto/media.py` — TTS per scene, scene PNG rendering, ffmpeg segment/concat/music mix.
- `ytauto/youtube.py` — OAuth (`client_secret*.json` → `token.json`), upload, comments, stats.
- State lives in `data/state.json` (videos, handled comment IDs, used upload slots); config in `config.yaml`; secrets in `.env`.
