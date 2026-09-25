"""YouTube Data API: 인증, 업로드, 댓글, 통계."""
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from .common import ROOT, log

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
TOKEN = ROOT / "token.json"


def _secret_file():
    files = sorted(ROOT.glob("client_secret*.json"))
    if not files:
        raise FileNotFoundError("client_secret.json이 없어요. README의 'YouTube 연결' 단계를 따라 주세요.")
    return files[0]


def service(interactive=False):
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES) if TOKEN.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not interactive:
            raise RuntimeError("YouTube 로그인이 필요해요. `python run.py auth`를 먼저 실행하세요.")
        flow = InstalledAppFlow.from_client_secrets_file(str(_secret_file()), SCOPES)
        creds = flow.run_local_server(port=0)
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def my_channel(yt):
    items = yt.channels().list(part="id,snippet", mine=True).execute().get("items", [])
    if not items:
        raise RuntimeError("이 Google 계정에 YouTube 채널이 없어요. 먼저 채널을 만들어 주세요.")
    return items[0]


def upload(yt, path, script, config):
    up = config["upload"]
    body = {
        "snippet": {
            "title": script["title"][:100],
            "description": script["full_description"][:4900],
            "tags": script["tags"][:15],
            "categoryId": up["category_id"],
            "defaultLanguage": config["channel"]["language"],
            "defaultAudioLanguage": config["channel"]["language"],
        },
        "status": {
            "privacyStatus": up["privacy"],
            "selfDeclaredMadeForKids": up["made_for_kids"],
            "containsSyntheticMedia": up["synthetic_media"],
        },
    }
    parts = "snippet,status"
    if config.get("mode") == "shopping":
        # "유료 PPL 포함" 표시 (제휴 수수료를 받는 영상)
        body["snippet"]["categoryId"] = config["shopping"]["category_id"]
        body["paidProductPlacementDetails"] = {"hasPaidProductPlacement": True}
        parts += ",paidProductPlacementDetails"
    request = yt.videos().insert(
        part=parts, body=body,
        media_body=MediaFileUpload(str(path), mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024),
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response["id"]


def comment(yt, video_id, text):
    """채널 이름으로 첫 댓글을 단다. (API로는 고정할 수 없어 YouTube Studio에서 직접 고정해야 함)"""
    try:
        yt.commentThreads().insert(part="snippet", body={"snippet": {
            "videoId": video_id, "topLevelComment": {"snippet": {"textOriginal": text}}}}).execute()
    except HttpError as e:
        log.warning("첫 댓글 작성 실패: %s", e)


def video_stats(yt, video_ids):
    stats = {}
    for i in range(0, len(video_ids), 50):
        res = yt.videos().list(part="statistics", id=",".join(video_ids[i:i + 50])).execute()
        for item in res.get("items", []):
            s = item["statistics"]
            stats[item["id"]] = {
                "views": int(s.get("viewCount", 0)),
                "likes": int(s.get("likeCount", 0)),
                "comments": int(s.get("commentCount", 0)),
            }
    return stats


def new_comments(yt, channel_id, skip_ids):
    res = yt.commentThreads().list(
        part="snippet", allThreadsRelatedToChannelId=channel_id,
        order="time", maxResults=50, textFormat="plainText",
    ).execute()
    out = []
    for item in res.get("items", []):
        top = item["snippet"]["topLevelComment"]
        if top["id"] in skip_ids or item["snippet"].get("totalReplyCount", 0) > 0:
            continue
        sn = top["snippet"]
        if sn.get("authorChannelId", {}).get("value") == channel_id:
            continue
        out.append({"id": top["id"], "video_id": item["snippet"].get("videoId"),
                    "author": sn.get("authorDisplayName"), "text": sn.get("textDisplay", "")})
    return out


def reply(yt, comment_id, text):
    yt.comments().insert(part="snippet", body={"snippet": {"parentId": comment_id, "textOriginal": text}}).execute()


def moderate(yt, comment_id, status):
    try:
        yt.comments().setModerationStatus(id=comment_id, moderationStatus=status).execute()
    except HttpError as e:
        log.warning("댓글 숨김 실패 %s: %s", comment_id, e)
