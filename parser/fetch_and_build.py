"""
Проверяет volpi.ru/timetable, скачивает актуальный PDF, парсит и пишет JSON для приложения.

  python parser/fetch_and_build.py                  # обычный запуск (GitHub Actions)
  python parser/fetch_and_build.py --pdf file.pdf   # локально, без сети

Код выхода != 0, если что-то пошло не так. Тогда GitHub пришлёт письмо о неудачном запуске,
а старый JSON останется на месте (приложение продолжит показывать прошлое расписание).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.request
from urllib.parse import urljoin

sys.path.insert(0, os.path.dirname(__file__))
from timetable_parser import parse_pdf  # noqa: E402

PAGE_URL = "https://volpi.ru/timetable"
GROUP = os.environ.get("TT_GROUP", "ВИП-408")
# какой файл искать на странице: факультет ФЭИ, 4 курс
LINK_RE = re.compile(os.environ.get("TT_LINK_RE", r'href=["\']([^"\']*timetable_fei/4_kurs_\d+\.pdf)'))
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
OUT_FILE = os.path.join(OUT_DIR, "vip-408.json")
STATUS_FILE = os.path.join(OUT_DIR, "status.json")
MSK = dt.timezone(dt.timedelta(hours=3))
UA = "Mozilla/5.0 (timetable-bot; personal schedule app)"


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def find_pdf_link(html: str) -> tuple[str, str | None]:
    m = LINK_RE.search(html)
    if not m:
        raise RuntimeError("ссылка на PDF не найдена на странице — возможно, поменялась вёрстка сайта")
    url = urljoin(PAGE_URL, m.group(1))
    tail = html[m.end(): m.end() + 400]
    upd = re.search(r"обновлено:\s*([\d\- :]+)", tail)
    return url, upd.group(1).strip() if upd else None


def content_hash(data: dict) -> str:
    payload = json.dumps(data["lessons"], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def sanity_check(new: dict, old: dict | None):
    n = len(new["lessons"])
    if new["template_count"] < 8 or n < 30:
        raise RuntimeError(f"подозрительно мало пар: шаблонов {new['template_count']}, занятий {n}")
    if old and n < 0.5 * len(old.get("lessons", [])):
        raise RuntimeError(f"занятий стало {n} вместо {len(old['lessons'])} — похоже на ошибку разбора")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", help="локальный PDF вместо скачивания")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    old = None
    if os.path.exists(OUT_FILE):
        with open(OUT_FILE, encoding="utf-8") as f:
            old = json.load(f)

    if args.pdf:
        pdf_path, src_url, src_updated = args.pdf, None, None
    else:
        html = http_get(PAGE_URL).decode("utf-8", errors="replace")
        src_url, src_updated = find_pdf_link(html)
        print("PDF:", src_url, "| обновлено:", src_updated)
        fd, pdf_path = tempfile.mkstemp(suffix=".pdf")
        with os.fdopen(fd, "wb") as f:
            f.write(http_get(src_url))

    data = parse_pdf(pdf_path, GROUP)
    for w in data["warnings"]:
        print("WARN:", w)
    sanity_check(data, old)

    now = dt.datetime.now(MSK)
    h = content_hash(data)
    changed = not old or old.get("version") != h
    result = {
        "version": h,
        "group": GROUP,
        "source_url": src_url,
        "source_updated": src_updated,
        "updated_at": now.isoformat(timespec="seconds") if changed else old.get("updated_at"),
        **{k: v for k, v in data.items() if k not in ("group", "warnings", "template_count")},
    }
    if changed or (old and (old.get("source_url") != src_url and src_url)):
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        print("Расписание обновлено, версия", h, "занятий:", len(data["lessons"]))
    else:
        print("Без изменений, версия", h)

    # раз в сутки обновляем status.json: приложение видит, что проверка работает,
    # а ежедневный коммит не даёт GitHub отключить расписание запусков за неактивность
    status = {"last_check_date": now.date().isoformat(), "version": h, "ok": True}
    prev = None
    if os.path.exists(STATUS_FILE):
        with open(STATUS_FILE, encoding="utf-8") as f:
            prev = json.load(f)
    if prev != status:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
