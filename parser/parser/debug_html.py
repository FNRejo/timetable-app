"""
Визуальная сверка разбора с PDF: слева — колонка группы из PDF, справа — что понял парсер,
строка в строку. Открывается в браузере.

  python parser/debug_html.py                 # скачать свежий PDF с volpi.ru
  python parser/debug_html.py --pdf file.pdf  # локальный PDF
  python parser/debug_html.py --no-open       # не открывать браузер
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import io
import os
import sys
import tempfile
import webbrowser
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pdfplumber  # noqa: E402
from PIL import ImageDraw  # noqa: E402

from timetable_parser import BELLS, TimetableParser  # noqa: E402

S = 2.9  # пикселей на пункт PDF в отображении
DPI = int(S * 72 * 2)  # рендер в 2 раза крупнее — чётко на любых экранах
MARGIN = 24  # пт сверху/снизу (видно заголовок недели)
WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
TYPES = {"lecture": "лек", "practice": "пр", "lab": "лаб", None: ""}
TYPE_CLS = {"lecture": "lec", "practice": "pr", "lab": "lab"}


def esc(s):
    return html.escape(str(s)) if s is not None else ""


def ddmm(iso):
    return f"{iso[8:10]}.{iso[5:7]}"


def render_crop(pdf_path, info):
    """Склеиваем: [Дни | месяцы | Часы] + [соседняя колонка + колонка группы]."""
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[info["page"]]
        img = page.to_image(resolution=DPI).original.convert("RGB")
    k = DPI / 72
    top = max(0, info["top"] - MARGIN)
    bottom = min(page.height, info["bottom"] + 4)
    x0 = info["bbox"][0]
    left = img.crop((int(x0 * k), int(top * k), int(info["hours_right"] * k) + 1, int(bottom * k)))
    right = img.crop((int(info["neighbor_left"] * k), int(top * k), int(info["gcol"][1] * k) + 2, int(bottom * k)))
    from PIL import Image

    out = Image.new("RGB", (left.width + right.width + 6, left.height), "white")
    out.paste(left, (0, 0))
    out.paste(right, (left.width + 6, 0))
    d = ImageDraw.Draw(out)
    gx0 = left.width + 6 + int((info["gcol"][0] - info["neighbor_left"]) * k)
    d.rectangle([gx0, 0, out.width - 1, out.height - 1], outline=(37, 99, 235), width=5)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode(), out.width / 2, out.height / 2, top


def build_cards(lessons, block):
    """Группируем занятия одного дня недели одной недели в карточки (как ячейки PDF)."""
    valid = [d for d in block["dates"] if d not in block["holidays"]]
    by_key = defaultdict(set)
    for l in lessons:
        key = (l["pair"], l["start"], l["end"], l["subject"], l["type"], l["teacher"], l["room"], l["changed"], l["note"])
        by_key[key].add(l["date"])
    # склеиваем соседние пары с одинаковым содержимым и датами (лаба на 2 пары)
    items = sorted(by_key.items(), key=lambda kv: (kv[0][0], kv[0][3]))
    cards = []
    for key, dates in items:
        pair, start, end, *rest = key
        prev = next((c for c in cards if c["rest"] == rest and c["dates"] == dates and c["pairs"][-1] + 1 == pair), None)
        if prev:
            prev["pairs"].append(pair)
            prev["end"] = end
        else:
            cards.append({"pairs": [pair], "start": start, "end": end, "rest": rest, "dates": dates})
    for c in cards:
        dates = sorted(c["dates"])
        missing = [d for d in valid if d not in c["dates"]]
        extra = [d for d in dates if d not in valid]  # перенесённые на другой день
        if not missing and not extra:
            c["when"] = f"каждую неделю ({len(dates)})"
            c["when_cls"] = "all"
        elif len(dates) > len(valid) / 2 and not extra:
            c["when"] = "кроме " + ", ".join(ddmm(d) for d in missing)
            c["when_cls"] = "except"
        else:
            c["when"] = "только " + ", ".join(ddmm(d) for d in dates)
            c["when_cls"] = "only"
    return cards


def layout_lanes(cards):
    """Если карточки пересекаются по парам — ставим рядом."""
    lanes = []
    for c in sorted(cards, key=lambda c: (c["pairs"][0], -len(c["pairs"]))):
        for i, lane in enumerate(lanes):
            if all(set(c["pairs"]).isdisjoint(o["pairs"]) for o in lane):
                lane.append(c)
                c["lane"] = i
                break
        else:
            lanes.append([c])
            c["lane"] = len(lanes) - 1
    return max(1, len(lanes))


def page_section(pdf_path, info, lessons_all):
    b64, w, h, crop_top = render_crop(pdf_path, info)
    y = lambda v: (v - crop_top) * S  # noqa: E731
    parts = []
    for blk in info["blocks"]:
        rows = {p: (t, b) for p, t, b in blk["rows"]}
        # линии строк
        for p, (t, b) in rows.items():
            parts.append(f'<div class="row{" first" if p == 1 else ""}" style="top:{y(t):.1f}px;height:{(b - t) * S:.1f}px">'
                         f'<span class="lbl">{WD[blk["weekday"]]} {p}</span></div>')
        if blk["holidays"]:
            parts.append(f'<div class="hol" style="top:{y(blk["top"]):.1f}px">праздник: {", ".join(ddmm(d) for d in blk["holidays"])}</div>')
        less = [l for l in lessons_all if l.get("week") == blk["week"]
                and dt.date.fromisoformat(l["date"]).weekday() == blk["weekday"]]
        cards = build_cards(less, blk)
        n = layout_lanes(cards)
        for c in cards:
            p0, p1 = c["pairs"][0], c["pairs"][-1]
            if p0 not in rows or p1 not in rows:
                continue
            top, bot = rows[p0][0], rows[p1][1]
            subject, typ, teacher, room, changed, note = c["rest"]
            std = BELLS.get(p0, ("", ""))[0] == c["start"] and BELLS.get(p1, ("", ""))[1] == c["end"]
            info_line = " · ".join(x for x in [TYPES.get(typ, ""), esc(teacher), f"<b>{esc(room)}</b>" if room else ""] if x)
            width = 100 / n
            time_extra = "" if std else f' · <span class="t">{c["start"]}–{c["end"]}</span>'
            parts.append(
                f'<div class="card {TYPE_CLS.get(typ, "")}{" chg" if changed else ""}" '
                f'style="top:{y(top) + 1:.1f}px;height:{(bot - top) * S - 2:.1f}px;left:calc(46px + (100% - 46px) * {c["lane"] * width / 100:.3f});'
                f'width:calc((100% - 46px) * {width / 100:.3f} - 3px)">'
                f'<div class="l1"><span class="subj">{esc(subject)}</span> <span class="meta">{info_line}</span></div>'
                f'<div class="when {c["when_cls"]}">{esc(c["when"])}{f" · {esc(note)}" if note else ""}{time_extra}</div>'
                f'</div>'
            )
    weeks = sorted({b["week"] for b in info["blocks"]})
    title = ", ".join(f"{w}-я неделя" for w in weeks)
    return f"""
<section>
  <h2>{title} <small>стр. {info["page"] + 1}</small></h2>
  <div class="pair">
    <img src="data:image/png;base64,{b64}" style="width:{w:.0f}px;height:{h:.0f}px">
    <div class="parsed" style="height:{h:.0f}px">{"".join(parts)}</div>
  </div>
</section>"""


def dates_section(data):
    by_date = defaultdict(list)
    for l in data["lessons"]:
        by_date[l["date"]].append(l)
    rows = []
    start = dt.date.fromisoformat(data["range"]["from"])
    end = dt.date.fromisoformat(data["range"]["to"])
    today = dt.date.today()
    d = start
    while d <= end:
        if d.weekday() != 6:
            iso = d.isoformat()
            ls = sorted(by_date.get(iso, []), key=lambda l: l["start"])
            wk = data["week_by_date"].get(iso, "")
            cls = ' class="today"' if d == today else ""
            if iso in data["holidays"]:
                body = '<span class="holtxt">праздник</span>'
            elif not ls:
                body = '<span class="empty">—</span>'
            else:
                body = "<br>".join(
                    f'<span class="pn">{l["pair"]}</span> {l["start"]}–{l["end"]} '
                    f'<span class="{"chgtxt" if l["changed"] else ""}">{esc(l["subject"])}</span> '
                    f'<span class="dim">{TYPES.get(l["type"], "")} {esc(l["teacher"] or "")} {esc(l["room"] or "")}</span>'
                    for l in ls)
            rows.append(f"<tr{cls}><td>{WD[d.weekday()]} {d.strftime('%d.%m')}<br><span class=dim>{wk}-я нед.</span></td><td>{body}</td></tr>")
            if d.weekday() == 5:
                rows.append('<tr class="sep"><td colspan=2></td></tr>')
        d += dt.timedelta(days=1)
    return f'<details><summary>Все даты семестра списком</summary><table class="dates">{"".join(rows)}</table></details>'


CSS = """
body{font:13px/1.35 system-ui,Segoe UI,Roboto,sans-serif;margin:20px;background:#f6f7f9;color:#111}
h1{font-size:20px;margin:0 0 4px} h2{font-size:16px;margin:28px 0 8px} h2 small{color:#888;font-weight:400}
.top{color:#555} .warn{color:#b91c1c} .legend span{display:inline-block;padding:2px 8px;border-radius:6px;margin-right:6px}
.pair{display:flex;gap:14px;align-items:flex-start}
.pair img{background:#fff;box-shadow:0 1px 4px #0002;flex:none}
.parsed{position:relative;flex:1;min-width:460px;max-width:700px;background:#fff;box-shadow:0 1px 4px #0002}
.row{position:absolute;left:0;right:0;border-top:1px solid #eee}
.row.first{border-top:2px solid #9ca3af}
.lbl{position:absolute;left:4px;top:50%;transform:translateY(-50%);font-size:10px;color:#9ca3af}
.card{position:absolute;box-sizing:border-box;border-radius:6px;padding:1px 6px;overflow:hidden;
  background:#f3f4f6;border-left:4px solid #9ca3af;font-size:11px;line-height:1.25}
.card.lec{background:#eff6ff;border-color:#3b82f6}.card.pr{background:#f0fdf4;border-color:#22c55e}
.card.lab{background:#faf5ff;border-color:#a855f7}
.card.chg{outline:2px solid #facc15;background:#fefce8}
.l1{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.subj{font-weight:600}.meta{color:#4b5563;font-size:10.5px}
.t{color:#c2410c;font-weight:600}
.when{font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.when.all{color:#6b7280}.when.except{color:#b91c1c}.when.only{color:#1d4ed8;font-weight:600}
.card:hover{overflow:visible;z-index:5;height:auto!important;min-height:30px;box-shadow:0 4px 14px #0003}
.card:hover .l1,.card:hover .when{white-space:normal}
.hol{position:absolute;right:6px;font-size:10px;color:#dc2626;font-weight:600}
details{margin-top:30px} summary{cursor:pointer;font-weight:600;font-size:15px}
.dates{border-collapse:collapse;background:#fff;margin-top:8px}
.dates td{border-bottom:1px solid #eee;padding:4px 10px;vertical-align:top}
.dates tr.today td{background:#ecfdf5} .dates tr.sep td{border-bottom:2px solid #9ca3af;padding:0}
.pn{display:inline-block;width:16px;text-align:center;background:#e5e7eb;border-radius:4px}
.dim{color:#6b7280}.empty{color:#bbb}.holtxt{color:#dc2626;font-weight:600}.chgtxt{background:#fef08a}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf")
    ap.add_argument("--group", default="ВИП-408")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "debug", "debug.html"))
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    src_note = ""
    if args.pdf:
        pdf_path = args.pdf
        src_note = os.path.basename(pdf_path)
    else:
        from fetch_and_build import PAGE_URL, find_pdf_link, http_get
        page_html = http_get(PAGE_URL).decode("utf-8", errors="replace")
        url, upd = find_pdf_link(page_html)
        print("PDF:", url, "| обновлено:", upd)
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        pdf_path = os.path.join(os.path.dirname(os.path.abspath(args.out)), "latest.pdf")
        with open(pdf_path, "wb") as f:
            f.write(http_get(url))
        src_note = f'<a href="{esc(url)}">{esc(url.rsplit("/", 1)[-1])}</a> (обновлено на сайте: {esc(upd)})'

    p = TimetableParser(pdf_path, args.group)
    data = p.parse()

    sections = "".join(page_section(pdf_path, info, data["lessons"]) for info in p.pages_info)
    warns = "".join(f'<div class="warn">⚠ {esc(w)}</div>' for w in data["warnings"])
    removed = ", ".join(f"{ddmm(d)} {pp}-я {esc(s)}" for d, pp, s in p.removed) or "—"
    page = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<title>Сверка расписания {esc(args.group)}</title><style>{CSS}</style></head><body>
<h1>Сверка разбора: {esc(args.group)}</h1>
<div class="top">Источник: {src_note} · занятий: {len(data["lessons"])} · семестр {data["range"]["from"]} … {data["range"]["to"]}
 · праздники: {", ".join(ddmm(d) for d in data["holidays"]) or "—"}</div>
<div class="top">Убрано переносами: {removed}</div>
{warns or '<div class="top">Предупреждений нет</div>'}
<p class="legend"><span style="background:#eff6ff">лекция</span><span style="background:#f0fdf4">практика</span>
<span style="background:#faf5ff">лаба</span><span style="outline:2px solid #facc15">изменение (жёлтое в PDF)</span>
<span style="color:#1d4ed8">только в даты</span><span style="color:#b91c1c">кроме дат</span><span style="color:#c2410c">нестандартное время</span></p>
<p class="top">Слева — PDF (синяя рамка — колонка группы, рядом соседняя группа для общих лекций). Справа — разбор, строка в строку.
Наведи на карточку, чтобы увидеть её целиком.</p>
{sections}
{dates_section(data)}
</body></html>"""
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(page)
    out = os.path.abspath(args.out)
    print("Готово:", out)
    if not args.no_open:
        webbrowser.open("file:///" + out.replace(os.sep, "/").lstrip("/"))


if __name__ == "__main__":
    main()
