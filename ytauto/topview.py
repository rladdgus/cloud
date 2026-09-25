"""Topview Avatar Marketing Video API: 제품 링크 → 광고 쇼츠 (내보내기 1회당 5크레딧)."""
import os
import time

import requests

from .common import log

API = "https://api.topview.ai"


def available():
    return bool(os.environ.get("TOPVIEW_API_KEY") and os.environ.get("TOPVIEW_UID"))


def _headers():
    return {
        "Authorization": f"Bearer {os.environ['TOPVIEW_API_KEY']}",
        "Topview-Uid": os.environ["TOPVIEW_UID"],
        "Content-Type": "application/json",
    }


def _check(r):
    r.raise_for_status()
    data = r.json()
    if str(data.get("code")) != "200":
        raise RuntimeError(f"Topview 오류: {data.get('message')}")
    return data["result"]


def make_video(product, narration, config, out_path, timeout_min=25):
    """대본을 넘겨 영상을 만들고 out_path에 저장한다."""
    tv = config["shopping"]["topview"]
    body = {
        "productLink": product["link"],
        "productName": product.get("name", ""),
        "aspectRatio": "9:16",
        "language": "ko",
        "videoLengthType": tv.get("video_length_type", 1),
        "isDiyScript": "true",
        "diyScriptDescription": narration,
        "preview": "false",
    }
    for key, field in (("voice_id", "voiceId"), ("avatar_id", "aiavatarId"), ("caption_id", "captionId")):
        if tv.get(key):
            body[field] = tv[key]
    task = _check(requests.post(f"{API}/v1/m2v/task/submit", json=body, headers=_headers(), timeout=60))
    task_id = task["taskId"]
    log.info("Topview 작업 시작: %s", task_id)

    deadline = time.time() + timeout_min * 60
    while time.time() < deadline:
        time.sleep(10)
        res = _check(requests.get(f"{API}/v1/m2v/task/query", params={"taskId": task_id},
                                  headers=_headers(), timeout=60))
        if res["status"] == "fail":
            raise RuntimeError(f"Topview 제작 실패: {res.get('errorMsg')}")
        videos = [v for v in res.get("exportVideos") or [] if v.get("status") == "success" and v.get("videoUrl")]
        if res["status"] == "success" and videos:
            with requests.get(videos[0]["videoUrl"], stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(out_path, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            return out_path
    raise TimeoutError(f"Topview 작업이 {timeout_min}분 안에 끝나지 않음: {task_id}")
