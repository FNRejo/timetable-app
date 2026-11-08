"""
Отладочный просмотр расписания в консоли.

  python parser/view.py                     # текущая неделя (из docs/vip-408.json)
  python parser/view.py --today             # только сегодня, с отметкой "сейчас/следующая"
  python parser/view.py --date 12.11        # конкретный день
  python parser/view.py --week 16.11        # неделя, в которую входит дата
  python parser/view.py --days 14           # 14 дней начиная с сегодня
  python parser/view.py --all               # весь семестр
  python parser/view.py --changes           # только изменённые пары и переносы

Источник данных (по умолчанию docs/vip-408.json):
  --fetch                                   # скачать свежий PDF с volpi.ru и разобрать (ничего не пишет)
  --pdf путь/к/файлу.pdf                    # разобрать локальный PDF
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

JSON_PATH = os.path.join(HERE, "..", "docs", "vip-408.json")
WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
TYPES = {"lecture": "лек", "practice": "пр", "lab": "лаб", None: "—"}
PAIR_NUM = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6"}

# ---- цвета (Windows 10+ понимает ANSI после os.system(""))
USE_COLOR = sys.stdout.isatty()
if os.name == "nt":
    os.system("")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def c(text, code):
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


BOLD, DIM, RED, GREEN, YELLOW, BLUE, CYAN, MAGENTA = "1", "2", "31", "32", "33", "34", "36", "35"
TYPE_COLOR = {"lecture": BLUE, "practice": GREEN, "lab": MAGENTA}


# ---- загрузка
def load(args) -> dict:
    if args.pdf:
        from timetable_parser import parse_pdf
        return parse_pdf(args.pdf, args.group)
    if args.fetch:
        from fetch_and_build import PAGE_URL, find_pdf_link, http_get
        from timetable_parser import parse_pdf
        html = http_get(PAGE_URL).decode("utf-8", errors="replace")
        url, upd = find_pdf_link(html)
        print(c(f"PDF: {url}  (обновлено: {upd})", DIM))
        fd, path = tempfile.mkstemp(suffix=".pdf")
        with os.fdopen(fd, "wb") as f:
            f.write(http_get(url))
        data = parse_pdf(path, args.group)
        data["source_url"], data["source_updated"] = url, upd
        return data
    if not os.path.exists(JSON_PATH):
        sys.exit(f"Нет файла {JSON_PATH}. Запусти fetch_and_build.py или используй --fetch / --pdf")
    with open(JSON_PATH, encoding="utf-8") as f:
        return json.load(f)


def parse_user_date(s: str, today: dt.date) -> dt.date:
    s = s.strip()
    if s.count(".") == 1:  # "12.11" -> текущий год
        s = f"{s}.{today.year}"
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    sys.exit(f"Не понял дату: {s}. Примеры: 12.11, 12.11.2026, 2026-11-12")


# ---- вывод
def fmt_lesson(l: dict, mark: str = "") -> str:
    typ = TYPES.get(l["type"], l["type"] or "—")
    typ_s = c(f"{typ:<3}", TYPE_COLOR.get(l["type"], DIM))
    time_s = f"{l['start']}–{l['end']}"
    subj = l["subject"]
    if l.get("changed"):
        subj = c(subj, YELLOW + ";" + BOLD) + c(" [изм.]", YELLOW)
    else:
        subj = c(subj, BOLD)
    extra = []
    if l.get("teacher"):
        extra.append(l["teacher"])
    if l.get("room"):
        extra.append(c(l["room"], CYAN))
    line = f"  {mark:<2}{l['pair']} │ {time_s} │ {typ_s} │ {subj}"
    line2 = "      │             │     │ " + c(" · ", DIM).join(extra)
    if l.get("note"):
        line2 += c(f"   ({l['note']})", DIM)
    return line + "\n" + line2


def print_day(d: dt.date, lessons: list[dict], holidays: set[str], today: dt.date, now: dt.datetime):
    title = f"{WEEKDAYS[d.weekday()]}, {d.strftime('%d.%m.%Y')}"
    if d == today:
        title += "  ← сегодня"
    print(c(title, BOLD + ";" + (GREEN if d == today else "37")))
    if d.isoformat() in holidays:
        print(c("  Праздник, пар нет", RED))
        return
    if not lessons:
        print(c("  Пар нет", DIM))
        return

    current = nxt = None
    if d == today:
        hm = now.strftime("%H:%M")
        for l in lessons:
            if l["start"] <= hm < l["end"]:
                current = l
            elif l["start"] > hm and nxt is None:
                nxt = l
    for l in lessons:
        mark = "▶" if l is current else ("→" if l is nxt else "")
        print(fmt_lesson(l, mark))
    if d == today:
        hm = now.strftime("%H:%M")
        if current:
            mins = minutes_between(hm, current["end"])
            print(c(f"  ▶ сейчас {current['pair']}-я пара, до конца {mins} мин", GREEN))
        if nxt:
            mins = minutes_between(hm, nxt["start"])
            print(c(f"  → следующая: {nxt['subject']} в {nxt['start']}, ауд. {nxt.get('room') or '?'} (через {fmt_mins(mins)})", GREEN))
        if not current and not nxt and lessons[-1]["end"] <= hm:
            print(c("  Пары на сегодня закончились", DIM))


def minutes_between(a: str, b: str) -> int:
    ha, ma = map(int, a.split(":"))
    hb, mb = map(int, b.split(":"))
    return (hb * 60 + mb) - (ha * 60 + ma)


def fmt_mins(m: int) -> str:
    return f"{m // 60} ч {m % 60} мин" if m >= 60 else f"{m} мин"


def main():
    ap = argparse.ArgumentParser(description="Просмотр расписания в консоли (отладка)")
    ap.add_argument("--group", default="ВИП-408")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--fetch", action="store_true", help="скачать свежий PDF с сайта")
    src.add_argument("--pdf", help="разобрать локальный PDF")
    when = ap.add_mutually_exclusive_group()
    when.add_argument("--today", action="store_true")
    when.add_argument("--date")
    when.add_argument("--week", help="дата внутри нужной недели")
    when.add_argument("--days", type=int)
    when.add_argument("--all", action="store_true")
    when.add_argument("--changes", action="store_true")
    ap.add_argument("--now", help="подменить текущее время для проверки, напр. '2026-11-12 10:00'")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()

    global USE_COLOR
    if args.no_color:
        USE_COLOR = False

    now = dt.datetime.strptime(args.now, "%Y-%m-%d %H:%M") if args.now else dt.datetime.now()
    today = now.date()
    data = load(args)

    by_date: dict[str, list[dict]] = {}
    for l in data["lessons"]:
        by_date.setdefault(l["date"], []).append(l)
    for v in by_date.values():
        v.sort(key=lambda l: (l["start"], l["pair"]))
    holidays = set(data.get("holidays", []))

    # шапка
    print(c(f"Расписание {data.get('group', args.group)}", BOLD), end="")
    if data.get("source_updated"):
        print(c(f"  · PDF обновлён: {data['source_updated']}", DIM), end="")
    if data.get("updated_at"):
        print(c(f"  · разобран: {data['updated_at'][:16].replace('T', ' ')}", DIM), end="")
    print()
    rng = data.get("range", {})
    print(c(f"Семестр: {rng.get('from')} … {rng.get('to')}, занятий: {len(data['lessons'])}", DIM))
    for w in data.get("warnings", []):
        print(c(f"⚠ {w}", RED))
    print()

    if args.changes:
        rows = [l for l in data["lessons"] if l.get("changed") or (l.get("note") and " за " in l["note"])]
        if not rows:
            print("Изменений нет")
        for l in rows:
            d = dt.date.fromisoformat(l["date"])
            print(c(f"{WEEKDAYS[d.weekday()][:2]} {d.strftime('%d.%m')}", BOLD))
            print(fmt_lesson(l))
        return

    if args.today:
        dates = [today]
    elif args.date:
        dates = [parse_user_date(args.date, today)]
    elif args.days:
        dates = [today + dt.timedelta(days=i) for i in range(args.days)]
    elif args.all:
        start = dt.date.fromisoformat(rng["from"])
        end = dt.date.fromisoformat(rng["to"])
        start -= dt.timedelta(days=start.weekday())
        dates = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    else:
        base = parse_user_date(args.week, today) if args.week else today
        if not args.week and base.weekday() == 6:  # в воскресенье показываем следующую неделю
            base += dt.timedelta(days=1)
        monday = base - dt.timedelta(days=base.weekday())
        dates = [monday + dt.timedelta(days=i) for i in range(6)]

    for d in dates:
        if d.weekday() == 6 and not by_date.get(d.isoformat()):
            continue
        if d.weekday() == 0 and len(dates) > 7:
            print(c("═" * 70, DIM))
        print_day(d, by_date.get(d.isoformat(), []), holidays, today, now)
        print()


if __name__ == "__main__":
    main()
