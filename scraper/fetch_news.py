"""抓取申請學校的最新消息，輸出到 data/news.json 供網頁讀取。

每間學校一個 parser，回傳 [{title, url, date}]，date 為 YYYY-MM-DD 或 None。
某間學校抓取失敗時，沿用上一次成功的資料，不會讓網頁變空白。
"""

import json
import re
import ssl
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "news.json"
NEW_ITEMS = ROOT / "new_items.json"  # 本次新出現的公告，給 notify.py 發通知用（不進 git）
MAX_ITEMS = 8  # 每間學校在網頁上顯示幾則
MAX_SEEN = 200  # 每間學校記住幾個看過的公告連結
TW = timezone(timedelta(hours=8))

class LenientX509Adapter(HTTPAdapter):
    """仍然驗證憑證，但關閉 Python 3.13+ 的 X509 strict 模式。
    陽明交大醫工系的憑證缺少 Subject Key Identifier，strict 模式下會被拒絕。"""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


session = requests.Session()
# 連線被重置或 5xx 時自動重試 3 次（間隔 2、4、8 秒）
retry = Retry(total=3, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
session.mount("https://", LenientX509Adapter(max_retries=retry))
session.headers["User-Agent"] = (
    "Mozilla/5.0 (compatible; hannshushua-news-bot/1.0; +https://hannshushua.github.io/)"
)


def get_soup(url):
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.content, "html.parser")  # 讓 bs4 依 <meta charset> 判斷編碼


def clean(text):
    return re.sub(r"\s+", " ", text).strip()


# ---------- 各校 parser ----------

def parse_ntu_bime(list_url):
    """台大生機系：表格，日期格式 2026-10-06，置頂項目會排在前面。"""
    soup = get_soup(list_url)
    items = []
    for tr in soup.select("tr[data-position]"):
        a = tr.select_one("a[href*='News_Content']")
        date = tr.select_one("td[data-title='上版日期']")
        if not a:
            continue
        items.append({
            "title": clean(a.get("title") or a.get_text()),
            "url": urljoin(list_url, a["href"]),
            "date": clean(date.get_text()) if date else None,
        })
    return items


def parse_nycu_ibe(list_url):
    """交大智慧醫電所：日期為民國年 115-10-22。"""
    soup = get_soup(list_url)
    items = []
    for a in soup.select("a[href*='/news/view']"):
        title = a.select_one("p")
        date = None
        for p in a.select(".info p"):
            m = re.search(r"(\d{2,3})-(\d{1,2})-(\d{1,2})", p.get_text())
            if m:
                y, mo, d = map(int, m.groups())
                date = f"{y + 1911:04d}-{mo:02d}-{d:02d}"
                break
        items.append({
            "title": clean(title.get_text() if title else a.get("title", "")),
            "url": urljoin(list_url, a["href"]),
            "date": date,
        })
    return items


def parse_nycu_bme(list_url):
    """陽明交大醫工系（WordPress）：日期格式 10 月 16, 2026。"""
    soup = get_soup(list_url)
    items = []
    for art in soup.select("article"):
        a = art.select_one(".entry-title a")
        pub = art.select_one(".published")
        if not a:
            continue
        date = None
        if pub:
            m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2}),\s*(\d{4})", pub.get_text())
            if m:
                mo, d, y = map(int, m.groups())
                date = f"{y:04d}-{mo:02d}-{d:02d}"
        items.append({"title": clean(a.get_text()), "url": a["href"], "date": date})
    return items


def parse_nthu_bmes(list_url):
    """清大醫環系：列表沒有日期，用文章編號（越大越新）排序。"""
    soup = get_soup(list_url)
    items = []
    for a in soup.select("div.d-item .mtitle a"):
        m = re.search(r"/p/40\d-1360-(\d+)", a["href"])
        items.append({
            "title": clean(a.get_text()),
            "url": urljoin(list_url, a["href"]),
            "date": None,
            "_order": int(m.group(1)) if m else 0,
        })
    return items


def parse_ncku_imi(list_url):
    """成大醫資所：日期格式 2026.09.09，置頂項目會排在前面。"""
    soup = get_soup(list_url)
    items = []
    for li in soup.select("li.li-title"):
        a = li.select_one("a[href*='/news/']")
        small = li.select_one("small")
        if not a:
            continue
        date = None
        if small:
            m = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", small.get_text())
            if m:
                y, mo, d = map(int, m.groups())
                date = f"{y:04d}-{mo:02d}-{d:02d}"
        items.append({"title": clean(a.get_text()), "url": urljoin(list_url, a["href"]), "date": date})
    return items


SCHOOLS = [
    {
        "id": "ntu-bime",
        "school": "國立臺灣大學",
        "dept": "生物機電工程學系（生機所）",
        "home": "https://www.bime.ntu.edu.tw/",
        "list": "https://www.bime.ntu.edu.tw/News_n_64602_sms_63988.html",
        "parser": parse_ntu_bime,
    },
    {
        "id": "nycu-ibe",
        "school": "國立陽明交通大學",
        "dept": "智慧醫電工程研究所",
        "home": "https://ibe.nycu.edu.tw/",
        "list": "https://ibe.nycu.edu.tw/ibe/ch/app/news/list?module=headnews&id=6262",
        "parser": parse_nycu_ibe,
    },
    {
        "id": "nycu-bme",
        "school": "國立陽明交通大學",
        "dept": "生物醫學工程學系（醫工所）",
        "home": "https://nycubme.nycu.edu.tw/",
        "list": "https://nycubme.nycu.edu.tw/archives/category/uncategorized",
        "parser": parse_nycu_bme,
    },
    {
        "id": "nthu-bmes",
        "school": "國立清華大學",
        "dept": "生醫工程與環境科學系（醫環所）",
        "home": "https://bmes.site.nthu.edu.tw/",
        "list": "https://bmes.site.nthu.edu.tw/p/403-1360-11-1.php?Lang=zh-tw",
        "parser": parse_nthu_bmes,
    },
    {
        "id": "ncku-imi",
        "school": "國立成功大學",
        "dept": "醫學資訊研究所（醫資所）",
        "home": "https://www.imi.ncku.edu.tw/zh-hant/",
        "list": "https://www.imi.ncku.edu.tw/zh-hant/news/",
        "parser": parse_ncku_imi,
    },
]


def newest_first(items):
    """依日期（或文章編號）由新到舊排序，並去除重複連結。"""
    seen, unique = set(), []
    for it in items:
        if it["title"] and it["url"] not in seen:
            seen.add(it["url"])
            unique.append(it)
    unique.sort(key=lambda it: (it.get("date") or "", it.get("_order", 0)), reverse=True)
    for it in unique:
        it.pop("_order", None)
    return unique[:MAX_ITEMS]


def load_previous():
    try:
        data = json.loads(OUTPUT.read_text(encoding="utf-8"))
        return {s["id"]: s for s in data.get("schools", [])}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def main():
    previous = load_previous()
    now = datetime.now(TW).isoformat(timespec="minutes")
    results, failures, new_items = [], 0, []

    for cfg in SCHOOLS:
        entry = {k: cfg[k] for k in ("id", "school", "dept", "home", "list")}
        old = previous.get(cfg["id"], {})
        # seen：看過的所有公告連結，用來判斷「新公告」，避免同一則重複通知
        seen = old.get("seen") or [it["url"] for it in old.get("items", [])]
        try:
            items = newest_first(cfg["parser"](cfg["list"]))
            if not items:
                raise ValueError("沒有解析到任何消息，網站版面可能改了")
            fresh = [it for it in items if it["url"] not in seen]
            first_time = not seen  # 第一次追蹤這間學校時不通知，免得一次湧入一堆舊公告
            if not first_time:
                new_items += [{**it, "school_id": cfg["id"], "school": cfg["school"], "dept": cfg["dept"]} for it in fresh]
            seen = ([it["url"] for it in fresh] + seen)[:MAX_SEEN]
            # 內容沒變就沿用舊時間，避免每次排程都產生 commit
            changed_at = old.get("fetched_at") if old.get("items") == items and old.get("fetched_at") else now
            entry.update(items=items, fetched_at=changed_at, error=None)
            print(f"[OK]   {cfg['id']}: {len(items)} 則" + ("（首次追蹤）" if first_time else f"，新公告 {len(fresh)} 則"))
        except Exception as e:  # 單一學校失敗不影響其他學校
            failures += 1
            entry.update(items=old.get("items", []), fetched_at=old.get("fetched_at"), error=str(e)[:200])
            print(f"[FAIL] {cfg['id']}: {e}", file=sys.stderr)
        entry["seen"] = seen
        results.append(entry)

    NEW_ITEMS.write_text(json.dumps(new_items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"本次新公告共 {len(new_items)} 則 → {NEW_ITEMS.name}")

    stamps = [s["fetched_at"] for s in results if s.get("fetched_at")]
    updated_at = max(stamps) if stamps else now
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps({"updated_at": updated_at, "schools": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"寫入 {OUTPUT}（{len(SCHOOLS) - failures}/{len(SCHOOLS)} 成功）")
    return 1 if failures == len(SCHOOLS) else 0


if __name__ == "__main__":
    sys.exit(main())
