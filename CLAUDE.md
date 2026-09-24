# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python app that runs a Korean YouTube Shorts channel autonomously (`mode: shopping` = Coupang Partners affiliate product shorts, `mode: knowledge` = trivia shorts) on the user's PC: Claude writes and fact-checks scripts, Edge TTS + Pillow + ffmpeg (bundled via `imageio-ffmpeg`) build the video, the YouTube Data API uploads it and handles comments. User-facing docs (`README.md`, `config.yaml` comments, log/notify messages) are in Korean.

## Commands

- Install: `pip install -r requirements.txt` (Python 3.10+)
- Run: `python run.py [auth|test|once|comments|stats|start]` — `test` builds a video without uploading
- Lint: `python -m pyflakes ytauto run.py`
- No test suite; to exercise the pipeline offline, monkeypatch `ytauto.llm.ask_json` / `ask_with_web_json`, `ytauto.media.synthesize` (and `media.load_image` in shopping mode) and call `jobs.make_and_upload(config, dry_run=True)`.

## Architecture

- `run.py` — CLI + scheduler loop (`start`), catches per-job failures so the loop never dies.
- `ytauto/jobs.py` — the three jobs: `make_and_upload` (dispatches to `shopping.produce` or `_produce_knowledge`, each returning `(script, video, record)`), `handle_comments`, `update_stats`.
- `ytauto/shopping.py` — product pick (`products.txt` queue first, then Coupang best-sellers + Claude pick), script + ad-law review, Topview video with local fallback, disclosure overlay.
- `ytauto/studio.py` — default shopping video engine: per-scene TTS with word timings (Edge `WordBoundary` or ElevenLabs `with-timestamps`), optional Kling clips via fal.ai queue API, base frame + timed caption PNG overlays composited with ffmpeg.
- `ytauto/channel.py` — `run.py channel`: generates profile/banner art, sets description/keywords/banner via API.
- `ytauto/topview.py` / `ytauto/coupang.py` — thin REST clients (Topview m2v submit/poll; Coupang Partners HMAC auth).
- `ytauto/content.py` — series selection (UCB bandit over views), writer prompt + JSON schema, reviewer (web-search fact check) with rewrite loop.
- `ytauto/llm.py` — Anthropic SDK wrapper; structured outputs via `output_config.format`, server-side refusal fallbacks.
- `ytauto/media.py` — TTS per scene, scene PNG rendering, ffmpeg segment/concat/music mix.
- `ytauto/youtube.py` — OAuth (`client_secret*.json` → `token.json`), upload, comments, stats.
- `cowork/` — Korean instructions for Claude Cowork scheduled tasks. With `upload.method: cowork`, `make_and_upload` writes `upload.json` / `업로드정보.txt` / `READY` into the output folder instead of uploading; Cowork inspects and uploads via YouTube Studio, then writes `uploaded.txt` (link) or `rejected.txt`, which `jobs.sync_manual_uploads` reads back. Keep these files in sync with that contract.
- `docs/PLAN.md` is the (Korean) operating plan: tool comparison, costs, roadmap.
- State lives in `data/state.json` (videos, handled comment IDs, used upload slots); config in `config.yaml`; secrets in `.env`.
