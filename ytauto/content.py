"""주제 선정, 대본 작성, 검수."""
import math
import random

from . import llm
from .common import log

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "narration": {"type": "string"},
                    "caption": {"type": "string"},
                    "image_keyword": {"type": "string"},
                },
                "required": ["narration", "caption", "image_keyword"],
                "additionalProperties": False,
            },
        },
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["topic", "title", "description", "tags", "scenes", "sources"],
    "additionalProperties": False,
}

WRITER_SYSTEM = """당신은 한국어 유튜브 쇼츠 지식 채널의 작가입니다.
- 길이: 낭독 40~55초 (전체 내레이션 220~300자 내외), 장면 5~7개.
- 첫 장면은 3초 안에 궁금증을 만드는 훅. 마지막 장면은 짧은 마무리와 가벼운 질문으로 댓글 유도.
- 사실로 확인된 내용만 쓰고, 불확실하면 그 주제를 쓰지 마세요. 과장·낚시성 거짓 제목 금지.
- 의료·투자·법률 조언, 실존 인물 비방, 폭력·선정적 묘사, 저작권 캐릭터 금지.
- caption은 화면에 크게 띄울 12~22자 요약 문구. image_keyword는 배경 사진 검색용 영어 1~3단어.
- title은 40자 이내, 끝에 #shorts. description은 2~3문장 + 출처 요약.
- sources에는 근거가 되는 신뢰할 만한 출처(기관·논문·백과사전 이름)를 적으세요."""

REVIEW_SYSTEM = """당신은 유튜브 채널의 깐깐한 검수자입니다. 대본을 다음 기준으로 평가하세요.
1) 사실 정확성 - 필요하면 웹 검색으로 핵심 주장을 확인
2) YouTube 커뮤니티 가이드·광고주 친화 가이드 위반 여부
3) 훅의 힘, 흐름, 길이(40~55초), 제목의 매력(낚시성 거짓은 감점)
4) 최근 영상과의 중복 여부
답변 마지막에 아래 형식의 JSON 객체 하나만 출력하세요:
{"score": 0~10 정수, "factual_ok": true/false, "policy_ok": true/false, "issues": ["..."], "fix_instructions": "..."}"""


def pick_series(config, state):
    """시리즈별 평균 조회수를 기준으로 UCB 방식으로 선택 (잘 되는 주제를 더 자주, 가끔은 탐색)."""
    series = config["series"]
    videos = [v for v in state["videos"] if v.get("id")]  # 테스트 영상은 제외
    unseen = [s for s in series if not any(v["series"] == s["id"] for v in videos)]
    if unseen:
        return random.choice(unseen)
    total = len(videos)
    top = max(1, max(v.get("views", 0) for v in videos))
    best, best_score = None, -1.0
    for s in series:
        mine = [v for v in videos if v["series"] == s["id"]]
        mean = sum(v.get("views", 0) for v in mine) / len(mine) / top
        score = mean + 0.6 * math.sqrt(math.log(total + 1) / len(mine))
        if score > best_score:
            best, best_score = s, score
    return best


def write_script(config, series, recent_topics, feedback=None, previous=None):
    prompt = (
        f"채널: {config['channel']['name']}\n"
        f"시리즈: {series['name']}\n시리즈 방향: {series['brief']}\n"
        f"최근에 다룬 주제(겹치지 않게): {', '.join(recent_topics[-40:]) or '없음'}\n\n"
    )
    if previous and feedback:
        prompt += f"이전 대본:\n{previous}\n\n검수 의견을 반영해 다시 쓰세요:\n{feedback}\n"
    else:
        prompt += "이 시리즈에서 사람들이 끝까지 보고 공유하고 싶을 새 주제 하나를 골라 대본을 쓰세요."
    return llm.ask_json(config["claude"]["model"], WRITER_SYSTEM, prompt, SCRIPT_SCHEMA)


def review_script(config, script, recent_topics):
    import json

    prompt = (
        f"최근 주제: {', '.join(recent_topics[-40:]) or '없음'}\n\n"
        f"검수할 대본:\n{json.dumps(script, ensure_ascii=False, indent=2)}"
    )
    model = config["claude"]["model"]
    if config["quality"].get("fact_check_web", True):
        return llm.ask_with_web_json(model, REVIEW_SYSTEM, prompt)
    schema = {
        "type": "object",
        "properties": {
            "score": {"type": "integer"},
            "factual_ok": {"type": "boolean"},
            "policy_ok": {"type": "boolean"},
            "issues": {"type": "array", "items": {"type": "string"}},
            "fix_instructions": {"type": "string"},
        },
        "required": ["score", "factual_ok", "policy_ok", "issues", "fix_instructions"],
        "additionalProperties": False,
    }
    return llm.ask_json(model, REVIEW_SYSTEM, prompt, schema)


def passes(config, review):
    return (
        review.get("factual_ok")
        and review.get("policy_ok")
        and int(review.get("score", 0)) >= config["quality"]["min_score"]
    )


def create_approved_script(config, state):
    """검수를 통과한 대본을 돌려준다. 끝내 통과 못하면 None."""
    import json

    recent = [v["topic"] for v in state["videos"]]
    series = pick_series(config, state)
    log.info("시리즈 선택: %s", series["name"])
    script = write_script(config, series, recent)
    for attempt in range(config["quality"]["max_rewrites"] + 1):
        review = review_script(config, script, recent)
        log.info("검수 %d회차: 점수 %s, 문제 %s", attempt + 1, review.get("score"), review.get("issues"))
        if passes(config, review):
            script["series"] = series["id"]
            script["review"] = review
            return series, script
        if attempt < config["quality"]["max_rewrites"]:
            script = write_script(
                config, series, recent,
                feedback=review.get("fix_instructions") or "; ".join(review.get("issues", [])),
                previous=json.dumps(script, ensure_ascii=False),
            )
    return series, None
