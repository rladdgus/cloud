"""스케줄러가 부르는 작업들: 영상 제작·업로드, 댓글 관리, 통계 수집."""
import json
import re
import shutil
from datetime import datetime

from . import content, llm, media
from .common import OUTPUT, load_state, log, notify, save_state

COMMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "action": {"type": "string", "enum": ["reply", "hold", "ignore"]},
                    "reply": {"type": "string"},
                },
                "required": ["id", "action", "reply"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["decisions"],
    "additionalProperties": False,
}

COMMENT_SYSTEM = """당신은 한국어 쇼츠 채널 운영자로서 댓글에 답합니다.
- 쇼핑 영상에서 구매처·링크를 물으면 "설명란과 댓글에 링크가 있어요"라고 안내. 가격·재고·배송은 약속하지 말고 링크에서 확인하도록.
- reply: 질문, 칭찬, 추가 정보, 건전한 반론. 1~2문장, 친근하고 정중하게. 모르는 건 모른다고.
- hold: 스팸, 광고, 링크 도배, 욕설·혐오, 개인정보 노출. (검토 대기로 숨김)
- ignore: 이모지만, 의미 없는 짧은 글, 논쟁 유도.
- 정치·종교 논쟁에 가담하지 말고, 의료·투자 조언은 하지 마세요. 절대 AI라고 거짓 부정하지 마세요.
hold/ignore일 때 reply는 빈 문자열."""


def _produce_knowledge(config, state, workdir):
    series, script = content.create_approved_script(config, state)
    if script is None:
        notify(f"⚠️ [{series['name']}] 대본이 검수를 통과하지 못해 이번 업로드를 건너뛰었어요.")
        return None
    video, _ = media.build_video(script, series, workdir, config)
    footer = config["channel"].get("description_footer", "")
    script["full_description"] = f"{script['description']}\n\n출처: {', '.join(script['sources'])}\n\n{footer}"
    return script, video, {"series": series["id"], "topic": script["topic"]}


def make_and_upload(config, dry_run=False):
    from . import shopping

    state = load_state()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    workdir = OUTPUT / stamp
    workdir.mkdir(parents=True, exist_ok=True)
    produce = shopping.produce if config.get("mode") == "shopping" else _produce_knowledge
    result = produce(config, state, workdir)
    if result is None:
        shutil.rmtree(workdir, ignore_errors=True)
        return None
    script, video, record = result
    (workdir / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    seconds = media.duration(video)
    log.info("영상 완성: %s (%.1f초)", video, seconds)

    video_id = None
    status = "test" if dry_run else "uploaded"
    if not dry_run and config["upload"].get("method", "api") == "cowork":
        write_upload_sheet(workdir, script, config)
        status = "awaiting_upload"
        notify(f"📦 검수·업로드 대기: {script['title']}\n{workdir}")
    elif not dry_run:
        from . import youtube

        yt = youtube.service()
        video_id = youtube.upload(yt, video, script, config)
        if script.get("first_comment"):
            youtube.comment(yt, video_id, script["first_comment"])
        notify(f"✅ 업로드 완료: {script['title']}\nhttps://youtube.com/shorts/{video_id}")
    else:
        notify(f"🧪 테스트 모드: 업로드 없이 영상만 만들었어요 → {video}")

    state = load_state()
    state["videos"].append({
        **record, "id": video_id, "title": script["title"], "created": stamp,
        "seconds": round(seconds, 1), "views": 0, "dry_run": dry_run, "status": status,
    })
    if record.get("product_id") and not dry_run:
        state.setdefault("used_products", []).append(record["product_id"])
    save_state(state)
    _cleanup(workdir)
    return video_id


KEEP_FILES = ("final.mp4", "script.json", "upload.json", "업로드정보.txt", "READY")


def _cleanup(workdir):
    """중간 파일은 지우고 최종 영상과 업로드 자료만 남긴다. 처리가 끝난 결과물은 최근 30개만 유지."""
    for f in workdir.iterdir():
        if f.name not in KEEP_FILES:
            f.unlink()
    finished = sorted(p for p in OUTPUT.iterdir() if p.is_dir() and not (p / "READY").exists())
    for old in finished[:-30]:
        shutil.rmtree(old, ignore_errors=True)


def write_upload_sheet(workdir, script, config):
    """Cowork가 YouTube Studio에서 그대로 옮겨 적을 업로드 정보를 남긴다."""
    shopping = config.get("mode") == "shopping"
    info = {
        "title": script["title"][:100],
        "description": script["full_description"][:4900],
        "tags": script["tags"][:15],
        "first_comment": script.get("first_comment", ""),
        "made_for_kids": config["upload"]["made_for_kids"],
        "paid_promotion": shopping,
        "altered_content": config["upload"]["synthetic_media"],
        "category": "노하우/스타일" if shopping else "교육",
        "visibility": {"public": "공개", "unlisted": "일부 공개", "private": "비공개"}[config["upload"]["privacy"]],
    }
    (workdir / "upload.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    yes = lambda b: "예" if b else "아니요"  # noqa: E731
    sheet = f"""[제목]
{info['title']}

[설명]
{info['description']}

[태그]
{', '.join(info['tags'])}

[설정]
- 아동용: {yes(info['made_for_kids'])}
- 유료 프로모션 포함: {yes(info['paid_promotion'])}
- 변경되거나 합성된 콘텐츠: {yes(info['altered_content'])}
- 카테고리: {info['category']}
- 공개 상태: {info['visibility']}

[첫 댓글 (올린 뒤 달고 고정)]
{info['first_comment']}
"""
    (workdir / "업로드정보.txt").write_text(sheet, encoding="utf-8")
    (workdir / "READY").write_text("검수·업로드 대기 중\n", encoding="utf-8")


def sync_manual_uploads():
    """Cowork가 남긴 uploaded.txt / rejected.txt를 읽어 상태에 반영한다."""
    state = load_state()
    changed = False
    for v in state["videos"]:
        if v.get("status") != "awaiting_upload":
            continue
        folder = OUTPUT / v["created"]
        uploaded, rejected = folder / "uploaded.txt", folder / "rejected.txt"
        if uploaded.exists():
            m = re.search(r"(?:shorts/|v=|youtu\.be/)([\w-]{11})", uploaded.read_text(encoding="utf-8"))
            if m:
                v["id"], v["status"] = m.group(1), "uploaded"
                changed = True
        elif rejected.exists():
            v["status"] = "rejected"
            v["reject_reason"] = rejected.read_text(encoding="utf-8").strip()[:500]
            changed = True
    if changed:
        save_state(state)


def handle_comments(config):
    if not config["comments"]["enabled"]:
        return
    from . import youtube

    yt = youtube.service()
    state = load_state()
    channel_id = state.get("channel_id") or youtube.my_channel(yt)["id"]
    state["channel_id"] = channel_id
    done = set(state["replied_comments"])
    comments = youtube.new_comments(yt, channel_id, done)[: config["comments"]["max_replies_per_run"]]
    if not comments:
        return
    titles = {v["id"]: v["title"] for v in state["videos"] if v.get("id")}
    for c in comments:
        c["video_title"] = titles.get(c["video_id"], "")
    result = llm.ask_json(
        config["claude"]["model"], COMMENT_SYSTEM,
        "다음 댓글들을 처리하세요:\n" + json.dumps(comments, ensure_ascii=False, indent=2),
        COMMENT_SCHEMA, effort="low",
    )
    valid = {c["id"] for c in comments}
    counts = {"reply": 0, "hold": 0, "ignore": 0}
    for d in result["decisions"]:
        if d["id"] not in valid:
            continue
        if d["action"] == "reply" and d["reply"].strip():
            youtube.reply(yt, d["id"], d["reply"].strip())
        elif d["action"] == "hold":
            youtube.moderate(yt, d["id"], "heldForReview")
        counts[d["action"]] += 1
        done.add(d["id"])
    state["replied_comments"] = list(done)[-5000:]
    save_state(state)
    log.info("댓글 처리: 답글 %d, 숨김 %d, 무시 %d", counts["reply"], counts["hold"], counts["ignore"])


def update_stats(config):
    from . import youtube

    sync_manual_uploads()
    state = load_state()
    ids = [v["id"] for v in state["videos"] if v.get("id")]
    if not ids:
        return
    stats = youtube.video_stats(youtube.service(), ids)
    for v in state["videos"]:
        if v.get("id") in stats:
            v.update(stats[v["id"]])
    save_state(state)
    by_series = {}
    for v in state["videos"]:
        if v.get("id"):
            by_series.setdefault(v["series"], []).append(v.get("views", 0))
    summary = ", ".join(f"{k}: 평균 {sum(x) // len(x)}회" for k, x in by_series.items())
    log.info("시리즈별 조회수 - %s", summary)
