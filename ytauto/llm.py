import json
import re

import anthropic

from .common import log

_client = None


def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=4)
    return _client


def _request(model, system, prompt, max_tokens, **extra):
    # 정책 분류기가 요청을 거절하면 서버가 권장 모델로 자동 재시도한다.
    return client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
        extra_body={"fallbacks": "default"},
        **extra,
    )


def _check(response):
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude가 요청을 거절했습니다")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("응답이 max_tokens에서 잘렸습니다")


def ask_json(model, system, prompt, schema, max_tokens=16000, effort="medium"):
    """JSON 스키마에 맞는 응답을 받아 dict로 돌려준다."""
    response = _request(
        model, system, prompt, max_tokens,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )
    _check(response)
    text = "".join(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def ask_with_web_json(model, system, prompt, max_tokens=16000):
    """웹 검색을 쓰게 한 뒤 마지막 텍스트에서 JSON 객체를 뽑는다."""
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}]
    for _ in range(4):
        response = client().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools,
            output_config={"effort": "medium"},
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
            extra_body={"fallbacks": "default"},
        )
        if response.stop_reason != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": response.content}]
    _check(response)
    text = "".join(b.text for b in response.content if b.type == "text")
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        log.warning("검수 응답에서 JSON을 찾지 못함: %s", text[:300])
        raise ValueError("검수 응답 형식 오류")
    return json.loads(match.group(0))
