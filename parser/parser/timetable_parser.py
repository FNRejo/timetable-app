"""
Парсер PDF-расписания ВПИ (филиал) ВолгГТУ -> список пар по конкретным датам.

Как устроен PDF:
  * таблица: Дни | Сентябрь | Октябрь | Ноябрь | Декабрь | Часы | группа1 | группа2 | ...
  * блоки "первая неделя" / "вторая неделя", в каждом 6 дней (пн-сб) по 6 строк-пар;
  * в колонках месяцев перечислены даты, в которые этот день недели идёт по этой неделе
    (красная дата = праздник, пар нет);
  * ячейка пары: жирным — предмет (и пометки вида "27.10 за 23.10"), курсивом — преподаватель,
    аудитория, тип (лек./пр./лаб.), иногда своё время "9:00-10:30" и даты "1.09-8.12", "28.09,26.10";
  * объединённая по вертикали ячейка = одна пара на несколько строк (лаба на 2 пары);
  * жёлтая заливка = изменение в расписании.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass, field

import pdfplumber

BELLS = {  # номер пары -> (начало, конец), дневное отделение
    1: ("08:00", "09:30"),
    2: ("09:40", "11:10"),
    3: ("11:20", "12:50"),
    4: ("13:10", "14:40"),
    5: ("14:50", "16:20"),
    6: ("16:25", "17:55"),
}

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}

TYPE_MAP = {"лек": "lecture", "пр": "practice", "лаб": "lab"}

RE_SLOT = re.compile(r"^\d{1,2}-\d{1,2}$")
RE_TIME_RANGE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")
RE_DATE_SPEC = re.compile(r"^\d{1,2}\.\d{1,2}\.?([,\-]\d{1,2}\.\d{1,2}\.?)*$")
RE_ROOM = re.compile(r"^([А-ЯA-Z]{0,4}-?\d{1,3}(,\d{3})*|\d-\d{3}[а-я]?)$")
YELLOW = (1.0, 1.0, 0.0)
RED = (1.0, 0.0, 0.0)


# ---------------------------------------------------------------- utils
def _color(obj):
    c = obj.get("non_stroking_color")
    if c is None:
        return None
    if isinstance(c, (int, float)):
        c = (c,)
    return tuple(round(float(v), 2) for v in c)


def _is_bold(word) -> bool:
    chars = [c for c in word["chars"] if c["text"].strip()]
    if not chars:
        return False
    bold = sum(1 for c in chars if "Bold" in c["fontname"])
    return bold * 2 >= len(chars)


def _is_red(word) -> bool:
    chars = [c for c in word["chars"] if c["text"].strip()]
    return bool(chars) and all(_color(c) == RED for c in chars)


def words_from_chars(chars) -> list[dict]:
    """Своя сборка слов: строки по близкому `top`, разрыв слова — пробел, большой зазор или смена жирности."""
    chars = [c for c in chars if c.get("upright", True)]
    chars.sort(key=lambda c: (c["top"], c["x0"]))
    lines: list[list[dict]] = []
    for c in chars:
        for ln in lines:
            if abs(ln[0]["top"] - c["top"]) <= 1.2:
                ln.append(c)
                break
        else:
            lines.append([c])
    words = []
    for ln in sorted(lines, key=lambda l: l[0]["top"]):
        ln.sort(key=lambda c: c["x0"])
        cur: list[dict] = []
        prev = None

        def flush():
            if cur:
                words.append({
                    "text": "".join(c["text"] for c in cur),
                    "x0": min(c["x0"] for c in cur), "x1": max(c["x1"] for c in cur),
                    "top": min(c["top"] for c in cur), "bottom": max(c["bottom"] for c in cur),
                    "chars": list(cur),
                })
                cur.clear()

        for c in ln:
            if not c["text"].strip():
                flush()
                prev = None
                continue
            if prev is not None:
                gap = c["x0"] - prev["x1"]
                if gap > 0.35 * c["size"] or gap < -0.5 * c["size"]:
                    flush()
            cur.append(c)
            prev = c
        flush()
    return words


def _center(o):
    return ((o["x0"] + o["x1"]) / 2, (o["top"] + o["bottom"]) / 2)


def _inside(pt, box, pad=0.0):
    x, y = pt
    return box[0] - pad <= x <= box[2] + pad and box[1] - pad <= y <= box[3] + pad


def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def norm_tokens(s: str) -> list[str]:
    s = s.lower().replace("ё", "е")
    return re.findall(r"[a-zа-я0-9]+", s)


def subjects_match(a: str, b: str) -> bool:
    """'Инду.раз.прогр.прод.' ~ 'Индустр.разраб.прогр.прод.' — сокращения по префиксам слов."""
    ta, tb = norm_tokens(a), norm_tokens(b)
    if not ta or not tb or len(ta) != len(tb):
        return False
    return all(x.startswith(y) or y.startswith(x) for x, y in zip(ta, tb))


def parse_date_spec(spec: str, year_of) -> list[tuple[dt.date, dt.date]]:
    """'28.09,26.10' / '1.09-8.12' / '07.11.' -> список диапазонов (включительно)."""
    out = []
    for part in spec.strip(".").split(","):
        part = part.strip().strip(".")
        if not part:
            continue
        ends = [x.strip(".") for x in part.split("-")]
        ds = []
        for e in ends:
            d, m = (int(v) for v in e.split(".")[:2])
            ds.append(dt.date(year_of(m), m, d))
        out.append((ds[0], ds[-1]))
    return out


# ---------------------------------------------------------------- data
@dataclass
class Row:  # строка-пара в колонке "Часы"
    top: float
    bottom: float
    pair: int


@dataclass
class DayBlock:
    week: int
    weekday: int  # 0 = пн
    rows: list[Row]
    dates: list[dt.date] = field(default_factory=list)
    holidays: list[dt.date] = field(default_factory=list)

    @property
    def top(self):
        return self.rows[0].top

    @property
    def bottom(self):
        return self.rows[-1].bottom


@dataclass
class Template:  # содержимое одной ячейки
    block: DayBlock
    pairs: list[int]
    subject: str
    teacher: str | None
    room: str | None
    type: str | None
    changed: bool
    note: str | None  # сырые пометки о датах, показываем пользователю
    only: list[tuple[dt.date, dt.date]] | None = None  # пара только в эти даты
    transfer_to: list[dt.date] | None = None  # "D1 за D2": проводится D1 ...
    transfer_from: list[dt.date] | None = None  # ... вместо D2
    custom_time: tuple[str, str] | None = None
    start_override: str | None = None  # "с 11.20"


# ---------------------------------------------------------------- parser
class ParseError(Exception):
    pass


class TimetableParser:
    def __init__(self, path: str, group: str):
        self.path = path
        self.group = group
        self.warnings: list[str] = []
        self.pages_info: list[dict] = []  # геометрия страниц — для отладочной визуализации

    # ---- геометрия
    @staticmethod
    def _major_vertical_edges(page, table_bbox):
        height = table_bbox[3] - table_bbox[1]
        acc = defaultdict(float)
        for e in page.edges:
            if e["orientation"] != "v":
                continue
            if e["x0"] < table_bbox[0] - 2 or e["x0"] > table_bbox[2] + 2:
                continue
            acc[round(e["x0"])] += e["bottom"] - e["top"]
        # склеиваем близкие x (рамки рисуются двойными прямоугольниками)
        xs = sorted(acc)
        clusters = []
        for x in xs:
            if clusters and x - clusters[-1][-1] <= 2:
                clusters[-1].append(x)
            else:
                clusters.append([x])
        major = []
        for cl in clusters:
            total = sum(acc[x] for x in cl)
            if total >= 0.4 * height:
                major.append(sum(cl) / len(cl))
        return major

    def parse(self) -> dict:
        with pdfplumber.open(self.path) as pdf:
            pages = list(pdf.pages)
            title = " ".join((pdf.metadata or {}).get("Title", "").split())
            first_text = pages[0].extract_text() or ""
            m = re.search(r"(\d{4})/(\d{4})", first_text)
            if not m:
                raise ParseError("не найден учебный год вида 2026/2027")
            y1, y2 = int(m.group(1)), int(m.group(2))
            self.year_of = lambda month: y1 if month >= 8 else y2

            # колонка группы — по заголовку на первой странице
            gx = None
            for w in pages[0].extract_words():
                if w["text"].replace(" ", "") == self.group:
                    gx = _center(w)[0]
            if gx is None:
                raise ParseError(f"группа {self.group} не найдена в заголовке")

            month_cols = None
            templates: list[Template] = []
            blocks_all: list[DayBlock] = []
            week = 0
            for pno, page in enumerate(pages):
                tables = page.find_tables()
                if not tables:
                    continue
                tb = max(tables, key=lambda t: (t.bbox[2] - t.bbox[0]) * (t.bbox[3] - t.bbox[1]))
                words = page.extract_words(return_chars=True)

                major = self._major_vertical_edges(page, tb.bbox)
                left = max([x for x in major if x < gx], default=None)
                right = min([x for x in major if x > gx], default=None)
                if left is None or right is None:
                    raise ParseError(f"стр.{pno + 1}: не удалось определить границы колонки группы")
                gcol = (left, right)

                # колонка "Часы": слова вида 1-2, 3-4 ... в левой части таблицы
                slot_words = [w for w in words if RE_SLOT.match(w["text"]) and w["x1"] < left and w["x0"] > tb.bbox[0] + 60]
                if not slot_words:
                    continue
                hx = sum(_center(w)[0] for w in slot_words) / len(slot_words)
                hour_cells = sorted(
                    [c for c in tb.cells if c[0] <= hx <= c[2] and any(_inside(_center(w), c) for w in slot_words)],
                    key=lambda c: c[1],
                )
                hours_left = min(c[0] for c in hour_cells)

                # заголовки недель (буквы набраны вразрядку)
                week_marks = []
                lines = defaultdict(list)
                for ch in page.chars:
                    lines[round(ch["top"])].append(ch)
                for top, chs in lines.items():
                    txt = "".join(c["text"] for c in sorted(chs, key=lambda c: c["x0"])).replace(" ", "").lower()
                    for i, name in enumerate(["первая", "вторая", "третья", "четвертая"], 1):
                        if name + "неделя" in txt:
                            week_marks.append((top, i))
                week_marks.sort()

                # дни: новый блок начинается со строки "1-2"
                blocks: list[DayBlock] = []
                cur: list[Row] = []
                for c in hour_cells:
                    label = next(w["text"] for w in slot_words if _inside(_center(w), c))
                    if label.startswith("1-") and cur:
                        blocks.append(cur)
                        cur = []
                    cur.append(Row(c[1], c[3], len(cur) + 1))
                if cur:
                    blocks.append(cur)

                day_blocks = []
                weekday = 0
                for rows in blocks:
                    w_here = [wk for top, wk in week_marks if top < rows[0].top]
                    new_week = w_here[-1] if w_here else week
                    if new_week != week:
                        week, weekday = new_week, 0
                    if week == 0:
                        week = 1
                    db = DayBlock(week=week, weekday=weekday, rows=rows)
                    weekday += 1
                    day_blocks.append(db)

                # колонки месяцев: между колонкой "Дни" и "Часы"
                xs = sorted({round(c[0], 1) for c in tb.cells} | {round(c[2], 1) for c in tb.cells})
                mcols_x = [x for x in xs if x <= hours_left + 0.5]
                mcols = list(zip(mcols_x[1:-1], mcols_x[2:]))  # первая колонка — "Дни"
                if month_cols is None:
                    month_cols = self._detect_months(words, mcols)
                for db in day_blocks:
                    for w in words:
                        cx, cy = _center(w)
                        if not (db.top - 1 <= cy <= db.bottom + 1) or not w["text"].isdigit():
                            continue
                        for (x0, x1), month in zip(mcols, month_cols):
                            if x0 <= cx <= x1:
                                d = dt.date(self.year_of(month), month, int(w["text"]))
                                (db.holidays if _is_red(w) else db.dates).append(d)
                    for d in db.dates + db.holidays:
                        if d.weekday() != db.weekday:
                            self.warnings.append(f"дата {d} не совпадает с днём недели блока (неделя {db.week}, день {db.weekday})")
                    db.dates.sort()
                blocks_all.extend(day_blocks)
                self.pages_info.append({
                    "page": pno,
                    "bbox": tb.bbox,
                    "gcol": gcol,
                    "neighbor_left": max([x for x in major if x < left], default=left),
                    "hours_right": max(c[2] for c in hour_cells),
                    "top": min(b.top for b in day_blocks) if day_blocks else tb.bbox[1],
                    "bottom": max(b.bottom for b in day_blocks) if day_blocks else tb.bbox[3],
                    "blocks": [
                        {"week": b.week, "weekday": b.weekday, "top": b.top, "bottom": b.bottom,
                         "rows": [(r.pair, r.top, r.bottom) for r in b.rows],
                         "dates": [d.isoformat() for d in b.dates],
                         "holidays": [d.isoformat() for d in b.holidays]}
                        for b in day_blocks
                    ],
                })

                # ячейки группы
                yellow_rects = [r for r in page.rects if _color(r) == YELLOW]
                gw = gcol[1] - gcol[0]
                raw = []  # (cell, db, pairs, words, changed)
                for cell in sorted(tb.cells, key=lambda c: (c[1], c[0])):
                    cw = cell[2] - cell[0]
                    if _overlap(cell[0], cell[2], *gcol) < 0.6 * min(cw, gw):
                        continue
                    hit = []
                    for db in day_blocks:
                        for r in db.rows:
                            h = r.bottom - r.top
                            if _overlap(cell[1], cell[3], r.top, r.bottom) >= 0.5 * h:
                                hit.append((db, r.pair))
                    if not hit:
                        continue
                    cell_words = words_from_chars([ch for ch in page.chars if _inside(_center(ch), cell)])
                    if not cell_words:
                        continue
                    db = hit[0][0]
                    pairs = [p for b, p in hit if b is db]
                    area = (cell[2] - cell[0]) * (cell[3] - cell[1])
                    changed = any(
                        _overlap(r["x0"], r["x1"], cell[0], cell[2]) * _overlap(r["top"], r["bottom"], cell[1], cell[3]) > 0.3 * area
                        for r in yellow_rects
                    )
                    # ячейка без жирного текста (только аудитория/преподаватель) — продолжение
                    # ячейки над ней: одна пара нарисована двумя ячейками
                    if not any(_is_bold(w) for w in cell_words) and raw:
                        pc, pdb, ppairs, pwords, pchg = raw[-1]
                        if pdb is db and ppairs[-1] + 1 == pairs[0] and abs(pc[0] - cell[0]) < 3 and abs(pc[2] - cell[2]) < 3:
                            raw[-1] = (pc, pdb, ppairs + pairs, pwords + cell_words, pchg or changed)
                            continue
                    raw.append((cell, db, pairs, cell_words, changed))
                for cell, db, pairs, cell_words, changed in raw:
                    t = self._parse_cell(cell_words, db, pairs, changed)
                    if t:
                        templates.append(t)

        if not blocks_all:
            raise ParseError("не найдено ни одного дня")
        self.week_of = {d: b.week for b in blocks_all for d in b.dates + b.holidays}
        lessons = self._expand(templates, blocks_all)
        return {
            "week_by_date": {d.isoformat(): w for d, w in sorted(self.week_of.items())},
            "group": self.group,
            "title": title,
            "bells": {str(k): {"start": v[0], "end": v[1]} for k, v in BELLS.items()},
            "holidays": sorted({d.isoformat() for b in blocks_all for d in b.holidays}),
            "range": {
                "from": min(d for b in blocks_all for d in b.dates).isoformat(),
                "to": max(d for b in blocks_all for d in b.dates).isoformat(),
            },
            "template_count": len(templates),
            "lessons": lessons,
            "warnings": self.warnings,
        }

    def _detect_months(self, words, mcols):
        found = []
        for x0, x1 in mcols:
            month = None
            for w in words:
                cx, _ = _center(w)
                if x0 - 3 <= cx <= x1 + 3:
                    t = w["text"].lower()
                    for stem, num in MONTHS.items():
                        if len(stem) > 2 and t.startswith(stem):
                            month = num
            found.append(month)
        if None in found:
            # заголовки могут слипнуться — достраиваем по соседям
            known = [(i, m) for i, m in enumerate(found) if m]
            if not known:
                raise ParseError("не удалось определить месяцы в колонках дат")
            i0, m0 = known[0]
            found = [((m0 - 1 + (i - i0)) % 12) + 1 for i in range(len(found))]
        return found

    # ---- содержимое ячейки
    def _parse_cell(self, words, db, pairs, changed) -> Template | None:
        bold =[w["text"] for w in words if _is_bold(w)]
        plain = [w for w in words if not _is_bold(w)]

        # жирная строка: [с HH.MM] [дата(ы)] [за дата] Предмет
        only = transfer_from = transfer_to = None
        start_override = None
        notes = []
        i = 0
        if len(bold) >= 3 and bold[0] == "с" and re.match(r"^\d{1,2}[.:]\d{2}$", bold[1]) and RE_DATE_SPEC.match(bold[2]):
            start_override = bold[1].replace(".", ":").zfill(5)
            notes.append(f"с {start_override}")
            i = 2
        if i < len(bold) and RE_DATE_SPEC.match(bold[i]):
            d1 = parse_date_spec(bold[i], self.year_of)
            notes.append(bold[i])
            i += 1
            if i + 1 < len(bold) and bold[i] == "за" and RE_DATE_SPEC.match(bold[i + 1]):
                transfer_to = [a for a, _ in d1]
                transfer_from = [a for a, _ in parse_date_spec(bold[i + 1], self.year_of)]
                notes.append(f"за {bold[i + 1]}")
                i += 2
            else:
                only = d1
        subject = " ".join(bold[i:]).strip()

        teacher_parts, room, typ, custom_time = [], None, None, None
        skip = False
        for k, w in enumerate(plain):
            if skip:
                skip = False
                continue
            t = w["text"]
            key = t.rstrip(".").lower()
            if key in TYPE_MAP:
                typ = TYPE_MAP[key]
            elif m := RE_TIME_RANGE.match(t):
                custom_time = (f"{int(m[1]):02d}:{m[2]}", f"{int(m[3]):02d}:{m[4]}")
            elif RE_DATE_SPEC.match(t):
                rng = parse_date_spec(t, self.year_of)
                only = (only or []) + rng
                notes.append(t)
            elif t == "СК" and k + 1 < len(plain):
                room = "СК " + plain[k + 1]["text"]
                skip = True
            elif RE_ROOM.match(t) and any(ch.isdigit() for ch in t):
                room = t
            elif t[:1].isupper() or re.match(r"^[А-ЯЁ]\.", t):
                teacher_parts.append(t)
            else:
                teacher_parts.append(t)
        teacher = " ".join(teacher_parts) or None

        if not subject:
            if teacher or room:
                self.warnings.append(f"ячейка без предмета: нед.{db.week} день {db.weekday} пары {pairs}: {[w['text'] for w in words]}")
            return None
        return Template(
            block=db, pairs=pairs, subject=subject, teacher=teacher, room=room, type=typ,
            changed=changed, note=" ".join(notes) or None, only=only,
            transfer_to=transfer_to, transfer_from=transfer_from,
            custom_time=custom_time, start_override=start_override,
        )

    # ---- разворачиваем шаблоны в конкретные даты
    def _expand(self, templates, blocks):
        holidays = {d for b in blocks for d in b.holidays}
        out = []

        def emit(t: Template, date: dt.date, transfer: bool):
            pairs = list(t.pairs)
            if t.start_override:
                start_pair = next((p for p, (s, _) in BELLS.items() if s == t.start_override), None)
                if start_pair:
                    pairs = [start_pair + k for k in range(len(pairs)) if start_pair + k in BELLS]
            if t.custom_time:
                items = [(pairs[0], t.custom_time)]
            else:
                items = [(p, BELLS[p]) for p in pairs]
            for p, (s, e) in items:
                out.append({
                    "date": date.isoformat(),
                    "week": self.week_of.get(date, t.block.week),
                    "pair": p,
                    "start": s,
                    "end": e,
                    "subject": t.subject,
                    "teacher": t.teacher,
                    "room": t.room,
                    "type": t.type,
                    "changed": t.changed,
                    "note": t.note,
                    "_transfer": transfer,
                })

        for t in templates:
            if t.transfer_to:
                for d in t.transfer_to:
                    emit(t, d, True)
                continue
            for d in t.block.dates:
                if d in holidays:
                    continue
                if t.only and not any(a <= d <= b for a, b in t.only):
                    continue
                emit(t, d, False)

        # переносы: "D1 за D2 Предмет" — убираем такую же пару в дату D2
        removed = []
        for t in templates:
            if not t.transfer_from:
                continue
            need = len(t.pairs) if not t.custom_time else 1
            for d in t.transfer_from:
                cands = [x for x in out if x["date"] == d.isoformat() and not x["_transfer"]
                         and subjects_match(x["subject"], t.subject)]
                cands.sort(key=lambda x: (x["type"] != t.type, x["pair"]))
                for x in cands[:need]:
                    out.remove(x)
                    removed.append((d.isoformat(), x["pair"], x["subject"]))
                if not cands:
                    self.warnings.append(f"перенос '{t.note} {t.subject}': на {d} не найдено пары для отмены")

        # полные названия вместо сокращений ("Проект. ЧМИ" -> "Проектирование ЧМИ")
        names = sorted({x["subject"] for x in out}, key=len, reverse=True)
        for x in out:
            for n in names:
                if n != x["subject"] and len(n) > len(x["subject"]) and subjects_match(n, x["subject"]):
                    x["subject"] = n
                    break
            del x["_transfer"]

        out.sort(key=lambda x: (x["date"], x["start"], x["pair"]))
        self.removed = removed
        return out


def parse_pdf(path: str, group: str) -> dict:
    p = TimetableParser(path, group)
    return p.parse()


if __name__ == "__main__":
    import json
    import sys

    res = parse_pdf(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "ВИП-408")
    print(json.dumps(res, ensure_ascii=False, indent=1))
