"""Регрессионный тест на реальном PDF от 28.10.2026: python parser/tests/test_parser.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from timetable_parser import parse_pdf  # noqa: E402

PDF = os.path.join(os.path.dirname(__file__), "fixtures", "4_kurs_28102026.pdf")


def day(r, date):
    return [(l["pair"], l["subject"], l["teacher"], l["room"], l["type"]) for l in r["lessons"] if l["date"] == date]


def main():
    r = parse_pdf(PDF, "ВИП-408")
    assert not r["warnings"], r["warnings"]
    assert r["holidays"] == ["2026-11-04"]
    assert day(r, "2026-11-04") == []  # праздник

    # пн, 2-я неделя
    assert day(r, "2026-10-19") == [
        (2, "Основы проект. web-приложений", "Маслова М.А.", "1-311", "lecture"),
        (3, "Основы проект. WEB-приложений", "Маслова М.А.", "1-502", "lab"),
        (4, "Основы проект. WEB-приложений", "Маслова М.А.", "1-502", "lab"),
        (5, "Комп.методы обработки экспер.данных", "Свиридова", "1-309", "lecture"),
    ]
    # пт, 1-я неделя: лаба нарисована двумя ячейками
    assert day(r, "2026-10-30")[-2:] == [
        (3, "Индустр.разраб.прогр.прод.", "Абрамова", "1-308", "lab"),
        (4, "Индустр.разраб.прогр.прод.", "Абрамова", "1-308", "lab"),
    ]
    # перенос "27.10 за 23.10": 23.10 Индустр. нет, есть перенос Анал.ПО "с 11.20"
    d23 = day(r, "2026-10-23")
    assert not any("Индустр" in x[1] for x in d23), d23
    assert (3, "Аналитическое ПО", "Рыбанов", "1-306", "lab") in d23
    assert any("Индустр" in x[1] for x in day(r, "2026-10-27"))
    # "29.10 за 20.11 Проект. ЧМИ": 20.11 ЧМИ отменена
    assert not any("ЧМИ" in x[1] for x in day(r, "2026-11-20"))
    # "23.10 за 19.11 Анал. ПО": 19.11 пусто
    assert day(r, "2026-11-19") == []
    # физ-ра со своим временем и периодом
    pe = [l for l in r["lessons"] if l["date"] == "2026-12-08" and "спорт" in l["subject"]]
    assert pe and pe[0]["start"] == "09:00" and pe[0]["end"] == "10:30"
    print("OK:", len(r["lessons"]), "занятий")


if __name__ == "__main__":
    main()
