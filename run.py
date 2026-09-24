"""유튜브 채널 자동화 실행 파일.

  python run.py auth              YouTube 계정 연결 (처음 한 번)
  python run.py test              영상 1개를 만들기만 하고 업로드는 안 함
  python run.py once              영상 1개를 만들어 바로 업로드
  python run.py comments          댓글 한 번 처리
  python run.py start             자동 운영 시작 (컴퓨터를 켜 두면 계속 동작)
"""
import logging
import sys
import time
import traceback
from datetime import datetime, timedelta

from ytauto import jobs
from ytauto.common import DATA, load_config, load_env, load_state, log, notify, save_state


def setup_logging():
    DATA.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(DATA / "ytauto.log", encoding="utf-8")],
    )


def keep_awake():
    """Windows에서 자동 절전 모드로 들어가지 않게 한다."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)


def safe(name, fn, *args):
    try:
        fn(*args)
    except Exception as e:  # 한 작업이 실패해도 전체 루프는 계속 돌아야 한다
        log.error("%s 실패: %s\n%s", name, e, traceback.format_exc())
        notify(f"❌ {name} 실패: {e}")


def start(config):
    keep_awake()
    sched = config["schedule"]
    next_comments = datetime.now()
    next_stats = datetime.now()
    notify(f"▶️ 자동 운영 시작 - 업로드 시각 {', '.join(sched['upload_times'])}")
    while True:
        now = datetime.now()
        slot = now.strftime("%Y-%m-%d ") + now.strftime("%H:%M")
        state = load_state()
        if now.strftime("%H:%M") in sched["upload_times"] and slot not in state["last_slots"]:
            state["last_slots"] = (state["last_slots"] + [slot])[-50:]
            save_state(state)
            safe("영상 제작/업로드", jobs.make_and_upload, config)
        if now >= next_comments:
            safe("댓글 관리", jobs.handle_comments, config)
            next_comments = now + timedelta(minutes=sched["comment_check_minutes"])
        if now >= next_stats:
            safe("통계 수집", jobs.update_stats, config)
            next_stats = now + timedelta(hours=sched["stats_check_hours"])
        time.sleep(20)


def main():
    load_env()
    setup_logging()
    config = load_config()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "auth":
        from ytauto import youtube

        channel = youtube.my_channel(youtube.service(interactive=True))
        print(f"연결 완료: {channel['snippet']['title']}")
    elif cmd == "test":
        jobs.make_and_upload(config, dry_run=True)
    elif cmd == "once":
        jobs.make_and_upload(config)
    elif cmd == "comments":
        jobs.handle_comments(config)
    elif cmd == "stats":
        jobs.update_stats(config)
    elif cmd == "start":
        start(config)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
