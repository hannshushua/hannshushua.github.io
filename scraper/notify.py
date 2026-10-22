"""把 fetch_news.py 找到的新公告推播到 Telegram 和 Discord。

需要的環境變數（在 GitHub repo 的 Settings → Secrets and variables → Actions 設定）：
  TELEGRAM_BOT_TOKEN   Telegram bot 的 token（跟 @BotFather 拿）
  TELEGRAM_CHAT_ID     你的 Telegram 使用者 ID（跟 @userinfobot 拿）
  DISCORD_WEBHOOK_URL  Discord 頻道的 webhook 網址
沒設定的平台會自動跳過。

用法：
  python scraper/notify.py          發送 new_items.json 裡的新公告
  python scraper/notify.py --test   發一則測試訊息（每間學校最新一則），確認設定正確
"""

import html
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEW_ITEMS = ROOT / "new_items.json"
NEWS = ROOT / "data" / "news.json"
SITE = "https://hannshushua.github.io/"

SHORT = {
    "ntu-bime": "台大生機所",
    "nycu-ibe": "交大智慧醫電所",
    "nycu-bme": "交大醫工所",
    "nthu-bmes": "清大醫環所",
    "ncku-imi": "成大醫資所",
    "nsysu-imst": "中山醫科所",
    "ccu-csie": "中正資工所",
    "nycu-ece": "交大電機學院",
    "nycu-aimc": "交大人工智慧醫學計算所",
}


def env(name):
    """讀取環境變數並去掉前後空白／換行（從網頁或指令貼上時常會多帶）。"""
    return (os.environ.get(name) or "").strip().lstrip("\ufeff")  # 也去掉 Windows 管線可能加上的 BOM


def group_by_school(items):
    groups = {}
    for it in items:
        groups.setdefault(it["school_id"], []).append(it)
    return groups


def chunks(blocks, limit):
    """把多個區塊組成不超過平台字數上限的訊息。"""
    msg = ""
    for b in blocks:
        if msg and len(msg) + len(b) + 2 > limit:
            yield msg
            msg = ""
        msg = f"{msg}\n\n{b}" if msg else b
    if msg:
        yield msg


def post_json(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "hannshushua-news-bot/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


def send_telegram(items, title):
    token, chat_id = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        print("[skip] Telegram：沒有設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return True

    blocks = [f"📢 <b>{html.escape(title)}</b>"]
    for sid, its in group_by_school(items).items():
        lines = [f"<b>{html.escape(SHORT.get(sid, its[0]['dept']))}</b>"]
        for it in its:
            date = f"（{it['date']}）" if it.get("date") else ""
            lines.append(f"• <a href=\"{html.escape(it['url'])}\">{html.escape(it['title'])}</a>{date}")
        blocks.append("\n".join(lines))
    blocks.append(f"🔗 {SITE}")

    for text in chunks(blocks, 3800):  # Telegram 上限 4096 字
        post_json(f"https://api.telegram.org/bot{token}/sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })
    print("[OK]   Telegram 已發送")
    return True


def send_discord(items, title):
    url = env("DISCORD_WEBHOOK_URL")
    if not url:
        print("[skip] Discord：沒有設定 DISCORD_WEBHOOK_URL")
        return True

    md = lambda s: s.replace("[", "［").replace("]", "］")  # 避免標題裡的中括號破壞連結語法
    blocks = [f"📢 **{title}**"]
    for sid, its in group_by_school(items).items():
        lines = [f"**{SHORT.get(sid, its[0]['dept'])}**"]
        for it in its:
            date = f"（{it['date']}）" if it.get("date") else ""
            lines.append(f"• [{md(it['title'])}](<{it['url']}>){date}")  # <> 讓 Discord 不展開預覽
        blocks.append("\n".join(lines))
    blocks.append(f"🔗 <{SITE}>")

    for text in chunks(blocks, 1900):  # Discord 上限 2000 字
        post_json(url, {"content": text, "allowed_mentions": {"parse": []}})
    print("[OK]   Discord 已發送")
    return True


def main():
    if "--test" in sys.argv:
        data = json.loads(NEWS.read_text(encoding="utf-8"))
        items = [{**s["items"][0], "school_id": s["id"], "dept": s["dept"]} for s in data["schools"] if s["items"]]
        title = "測試通知：通知設定成功！以下是各系所目前最新一則"
    else:
        items = json.loads(NEW_ITEMS.read_text(encoding="utf-8")) if NEW_ITEMS.exists() else []
        title = f"申請學校有 {len(items)} 則新公告"

    if not items:
        print("沒有新公告，不發通知")
        return 0

    ok = True
    for sender in (send_telegram, send_discord):
        try:
            sender(items, title)
        except urllib.error.HTTPError as e:  # 一個平台失敗不影響另一個
            ok = False
            print(f"[FAIL] {sender.__name__}: HTTP {e.code} {e.read()[:200]!r}", file=sys.stderr)
        except Exception as e:
            ok = False
            print(f"[FAIL] {sender.__name__}: {e}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
