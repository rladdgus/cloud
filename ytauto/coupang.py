"""쿠팡 파트너스 Open API (누적 판매 15만원 이상 달성 후 발급)."""
import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

import requests

DOMAIN = "https://api-gateway.coupang.com"
BASE = "/v2/providers/affiliate_open_api/apis/openapi"


def available():
    return bool(os.environ.get("COUPANG_ACCESS_KEY") and os.environ.get("COUPANG_SECRET_KEY"))


def _auth(method, url):
    path, *query = url.split("?")
    signed = time.strftime("%y%m%d", time.gmtime()) + "T" + time.strftime("%H%M%S", time.gmtime()) + "Z"
    message = signed + method + path + (query[0] if query else "")
    signature = hmac.new(os.environ["COUPANG_SECRET_KEY"].encode(), message.encode(), hashlib.sha256).hexdigest()
    return (f"CEA algorithm=HmacSHA256, access-key={os.environ['COUPANG_ACCESS_KEY']}, "
            f"signed-date={signed}, signature={signature}")


def _call(method, path, params=None, body=None):
    url = BASE + path + (f"?{urlencode(params)}" if params else "")
    r = requests.request(
        method, DOMAIN + url, json=body, timeout=30,
        headers={"Authorization": _auth(method, url), "Content-Type": "application/json;charset=UTF-8"},
    )
    r.raise_for_status()
    data = r.json()
    if str(data.get("rCode")) not in ("0", "None"):
        raise RuntimeError(f"쿠팡 API 오류: {data.get('rMessage')}")
    return data.get("data")


def _normalize(p):
    return {
        "id": str(p.get("productId")),
        "name": p.get("productName", ""),
        "price": p.get("productPrice"),
        "image": p.get("productImage"),
        "link": p.get("productUrl"),
        "category": p.get("categoryName", ""),
        "rocket": p.get("isRocket", False),
    }


def best_products(category_id, limit=20):
    return [_normalize(p) for p in _call("GET", f"/products/bestcategories/{category_id}", {"limit": limit}) or []]


def goldbox():
    return [_normalize(p) for p in _call("GET", "/products/goldbox") or []]


def search(keyword, limit=10):
    data = _call("GET", "/products/search", {"keyword": keyword, "limit": limit}) or {}
    return [_normalize(p) for p in data.get("productData", [])]


def deeplink(url):
    data = _call("POST", "/deeplink", body={"coupangUrls": [url]})
    return data[0]["shortenUrl"] if data else url
