"""프로필 링크 페이지: 상품 번호 관리, index.html 갱신, (선택) Netlify 자동 배포."""
import io
import json
import os
import re
import zipfile

import requests

from .common import ROOT, load_state, log, notify, save_state

DIR = ROOT / "linkpage"
ITEMS_FILE = DIR / "items.json"
PAGE = DIR / "index.html"
ITEMS_RE = re.compile(r"const ITEMS = \[.*?\];", re.S)


def load_items():
    if ITEMS_FILE.exists():
        return json.loads(ITEMS_FILE.read_text(encoding="utf-8"))
    return []


def peek_number():
    state = load_state()
    return max([i["no"] for i in load_items()] + [state.get("last_item_no", 0)]) + 1


def reserve_number():
    """다음 상품 번호를 예약한다. 검수에서 떨어진 번호는 건너뛴 채로 남는다."""
    state = load_state()
    used = max([i["no"] for i in load_items()] + [state.get("last_item_no", 0)])
    state["last_item_no"] = used + 1
    save_state(state)
    return used + 1


def render():
    """items.json 내용으로 index.html의 상품 목록을 다시 쓴다."""
    items = sorted(load_items(), key=lambda i: i["no"])
    rows = ",\n".join(
        "    " + json.dumps({k: i[k] for k in ("no", "name", "desc", "url")}, ensure_ascii=False)
        for i in items
    )
    html = PAGE.read_text(encoding="utf-8")
    html, n = ITEMS_RE.subn(lambda _: f"const ITEMS = [\n{rows},\n  ];", html, count=1)
    if n != 1:
        raise RuntimeError("linkpage/index.html에서 상품 목록(const ITEMS)을 찾지 못했어요")
    PAGE.write_text(html, encoding="utf-8")


def add_item(no, name, url, desc="", config=None):
    items = [i for i in load_items() if i["no"] != no]
    items.append({"no": int(no), "name": name, "desc": desc, "url": url})
    ITEMS_FILE.write_text(json.dumps(sorted(items, key=lambda i: i["no"]), ensure_ascii=False, indent=2),
                          encoding="utf-8")
    render()
    log.info("링크 페이지에 %s번 %s 추가", no, name)
    if config is not None:
        publish(config)


def publish(config):
    """NETLIFY_AUTH_TOKEN과 NETLIFY_SITE_ID가 있으면 linkpage 폴더를 그대로 배포한다."""
    token, site = os.environ.get("NETLIFY_AUTH_TOKEN"), os.environ.get("NETLIFY_SITE_ID")
    url = config.get("linkpage", {}).get("url", "")
    if not (token and site):
        notify(f"🔗 링크 페이지 파일을 갱신했어요. Netlify에 linkpage 폴더를 다시 올려 주세요. ({url})")
        return False
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(PAGE, "index.html")
    r = requests.post(
        f"https://api.netlify.com/api/v1/sites/{site}/deploys",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/zip"},
        data=buf.getvalue(), timeout=120,
    )
    r.raise_for_status()
    log.info("링크 페이지 배포 완료: %s", url)
    return True


def page_url(config):
    return config.get("linkpage", {}).get("url", "").rstrip("/")
