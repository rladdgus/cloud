"""쇼핑 쇼츠: 상품 고르기 → 대본·검수 → 영상 (Topview 또는 자체 제작) → 광고 표시."""
import json

import re

from . import coupang, llm, media, studio, topview
from .common import ROOT, log, notify

QUEUE_FILE = ROOT / "products.txt"

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "hook_title": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "narration": {"type": "string"},
                    "caption": {"type": "string"},
                    "motion_prompt": {"type": "string"},
                },
                "required": ["narration", "caption", "motion_prompt"],
                "additionalProperties": False,
            },
        },
        "comment": {"type": "string"},
    },
    "required": ["title", "hook_title", "description", "tags", "scenes", "comment"],
    "additionalProperties": False,
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "policy_ok": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "fix_instructions": {"type": "string"},
    },
    "required": ["score", "policy_ok", "issues", "fix_instructions"],
    "additionalProperties": False,
}

PICK_SCHEMA = {
    "type": "object",
    "properties": {"index": {"type": "integer"}, "reason": {"type": "string"}},
    "required": ["index", "reason"],
    "additionalProperties": False,
}

WRITER_SYSTEM = """당신은 한국어 쇼핑 쇼츠(제품 소개 숏폼) 전문 작가입니다.
- 길이: 낭독 25~40초 (전체 내레이션 150~230자), 장면 4~6개.
- 구조: ① 3초 훅(공감되는 불편함이나 "이거 아직도 모르세요?") ② 제품이 해결하는 방식 ③ 핵심 장점 2~3개 ④ 짧은 마무리 + "링크는 설명란" 안내.
- 반드시 지킬 것 (표시광고법·YouTube 정책):
  · 직접 써 본 척하는 가짜 후기("제가 써봤는데", "한 달 써보니") 금지. "~라고 해요", "~할 수 있어요"처럼 소개하는 말투로.
  · 확인할 수 없는 수치, "최저가", "1위", "완벽", 질병 치료·예방이나 다이어트 효과 같은 의학적 주장 금지.
  · 가격은 바뀔 수 있으니 구체적 금액을 말하지 말 것.
- hook_title: 영상 내내 위쪽에 고정되는 8~16자 제목 (예: "좁은 싱크대 필수템").
- caption: 해당 장면의 핵심 8~16자 문구 (자막이 없을 때 대체용).
- motion_prompt: 제품 사진을 짧은 영상으로 만들 때 쓸 영어 카메라 연출 1문장 (예: "Slow push-in on the product on a clean kitchen counter"). 제품 모양·색은 바꾸지 말 것.
- title: 35자 이내, 궁금증을 주되 거짓 없이, 끝에 #shorts.
- description: 2~3문장 제품 소개 (링크와 광고 문구는 프로그램이 따로 붙임).
- comment: 채널이 영상에 다는 첫 댓글 1~2문장 (구매 링크는 프로그램이 붙임)."""

REVIEW_SYSTEM = """당신은 쇼핑 쇼츠 광고 심의 담당자입니다. 대본을 평가하세요.
1) 표시광고법 위반 소지: 가짜 사용 후기, 근거 없는 수치·"최저가"·"1위", 의학적 효능, 과장
2) YouTube 커뮤니티 가이드·광고 정책 위반
3) 훅의 힘, 흐름, 길이(25~40초), 제품 정보와의 일치
policy_ok는 1)·2)에 문제가 전혀 없을 때만 true. score는 10점 만점 전체 품질."""

EXCLUDED_WORDS = ("건강기능식품", "의약", "의료기기", "주류", "담배", "전자담배", "성인", "다이어트", "무기")


def _queue():
    if not QUEUE_FILE.exists():
        return []
    items = []
    for line in QUEUE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        items.append({"id": parts[0], "link": parts[0], "name": parts[1] if len(parts) > 1 else "",
                      "image": parts[2] if len(parts) > 2 else "", "category": "직접 추가"})
    return items


def hd_image(url):
    """쿠팡 썸네일 주소의 크기 부분을 큰 이미지로 바꾼다 (실패하면 원래 주소 사용)."""
    return re.sub(r"/thumbnails/remote/\d+x\d+ex/", "/thumbnails/remote/492x492ex/", url or "")


def pick_product(config, state):
    used = set(state.get("used_products", []))
    queued = [p for p in _queue() if p["id"] not in used]
    if queued:
        return queued[0]  # 직접 넣은 링크가 가장 우선
    if not coupang.available():
        return None

    candidates = []
    for category_id in config["shopping"]["coupang_categories"]:
        try:
            candidates += coupang.best_products(category_id, limit=20)
        except Exception as e:
            log.warning("쿠팡 카테고리 %s 조회 실패: %s", category_id, e)
    candidates = [p for p in candidates if p["id"] not in used
                  and not any(w in p["name"] + p["category"] for w in EXCLUDED_WORDS)]
    if not candidates:
        return None
    candidates = candidates[:60]

    stats = {}
    for v in state["videos"]:
        if v.get("id") and v.get("category"):
            stats.setdefault(v["category"], []).append(v.get("views", 0))
    perf = {k: sum(x) // len(x) for k, x in stats.items()}
    listing = "\n".join(f"{i}. [{p['category']}] {p['name']}" for i, p in enumerate(candidates))
    choice = llm.ask_json(
        config["claude"]["model"],
        "당신은 쇼핑 쇼츠 채널의 상품 기획자입니다. 30초 영상으로 보여 주기 좋고, 한눈에 쓰임새가 보이며, "
        "충동구매가 일어나기 쉬운 생활 밀착형 상품을 고르세요. 브랜드 의존도가 높은 상품, 의료·건강 효능이 핵심인 상품은 피하세요.",
        f"카테고리별 지금까지 평균 조회수: {json.dumps(perf, ensure_ascii=False) or '아직 없음'}\n\n후보:\n{listing}",
        PICK_SCHEMA, effort="low",
    )
    product = candidates[max(0, min(choice["index"], len(candidates) - 1))]
    log.info("상품 선택: %s (%s)", product["name"], choice["reason"])
    return product


def write_and_review(config, product):
    model = config["claude"]["model"]
    info = f"상품명: {product.get('name') or '(링크에서 확인)'}\n카테고리: {product.get('category', '')}\n링크: {product['link']}"
    if product.get("rocket"):
        info += "\n로켓배송 상품"
    prompt = f"{info}\n\n이 상품의 쇼핑 쇼츠 대본을 쓰세요."
    script = llm.ask_json(model, WRITER_SYSTEM, prompt, SCRIPT_SCHEMA)
    for attempt in range(config["quality"]["max_rewrites"] + 1):
        review = llm.ask_json(model, REVIEW_SYSTEM, f"{info}\n\n대본:\n{json.dumps(script, ensure_ascii=False)}",
                              REVIEW_SCHEMA, effort="low")
        log.info("검수 %d회차: 점수 %s, 문제 %s", attempt + 1, review["score"], review["issues"])
        if review["policy_ok"] and review["score"] >= config["quality"]["min_score"]:
            script["review"] = review
            return script
        if attempt < config["quality"]["max_rewrites"]:
            script = llm.ask_json(
                model, WRITER_SYSTEM,
                f"{prompt}\n\n이전 대본:\n{json.dumps(script, ensure_ascii=False)}\n\n검수 의견을 반영해 다시 쓰세요:\n"
                f"{review['fix_instructions'] or '; '.join(review['issues'])}",
                SCRIPT_SCHEMA,
            )
    return None


def produce(config, state, workdir):
    """(script, video_path, record)를 돌려준다. 만들 수 없으면 None."""
    shop = config["shopping"]
    product = pick_product(config, state)
    if product is None:
        notify("⚠️ 올릴 상품이 없어요. products.txt에 쿠팡 파트너스 링크를 추가해 주세요.")
        return None

    script = write_and_review(config, product)
    if script is None:
        notify(f"⚠️ [{product.get('name') or product['link']}] 대본이 검수를 통과하지 못해 건너뛰었어요.")
        return None

    final = workdir / "final.mp4"
    engine = shop.get("engine", "studio")
    made = False
    if engine == "topview" and topview.available():
        raw = workdir / "raw.mp4"
        try:
            topview.make_video(product, " ".join(s["narration"] for s in script["scenes"]), config, raw)
            media.add_notice(raw, final, shop["disclosure_short"], config)
            raw.unlink()
            made = True
        except Exception as e:
            log.warning("Topview 실패, 자체 스튜디오로 전환: %s", e)
    if not made:
        product["image_hd"] = hd_image(product.get("image"))
        image = None
        for url in (product["image_hd"], product.get("image")):
            image = media.load_image(url) if url else None
            if image is not None:
                break
        if image is None:
            notify(f"⚠️ [{product['link']}] 제품 사진을 받을 수 없어요. "
                   "products.txt에 '링크 | 상품명 | 이미지주소' 형식으로 넣어 주세요.")
            return None
        studio.build(script, product, image, workdir, config).replace(final)

    footer = config["channel"].get("description_footer", "")
    script["full_description"] = (
        f"{script['description']}\n\n👉 제품 보러 가기: {product['link']}\n\n{shop['disclosure']}\n\n{footer}"
    )
    script["first_comment"] = f"{script['comment']}\n👉 {product['link']}\n({shop['disclosure_short']})"
    record = {"series": "shopping", "topic": product.get("name") or product["link"],
              "product_id": product["id"], "category": product.get("category", "")}
    return script, final, record
