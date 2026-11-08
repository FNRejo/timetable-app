# Расписание ВИП-408

Личное Android-приложение с виджетом. Расписание берётся с https://volpi.ru/timetable
(PDF ФЭИ, 4 курс), разбирается автоматически раз в час и публикуется как JSON.

```
volpi.ru/timetable ──► GitHub Actions (каждый час) ──► parser/ (Python, pdfplumber)
                                                         │
                                       docs/vip-408.json ◄┘  ──► GitHub Pages ──► приложение
```

## Структура

| Путь | Что это |
|---|---|
| `parser/timetable_parser.py` | разбор PDF → список пар по конкретным датам |
| `parser/fetch_and_build.py` | находит ссылку на сайте, скачивает PDF, парсит, проверяет, пишет `docs/` |
| `parser/tests/` | регрессионный тест на реальном PDF от 28.10.2026 |
| `.github/workflows/update.yml` | запуск по расписанию + коммит изменений |
| `docs/vip-408.json` | данные для приложения |
| `docs/status.json` | дата последней успешной проверки |
| `android/` | приложение (следующий этап) |

## Запуск на GitHub (один раз)

1. Создай **публичный** репозиторий, например `timetable-app`. GitHub Pages бесплатно работает только для
   публичных репозиториев. Само расписание и так лежит на сайте института в открытом доступе.
2. Залей туда содержимое этой папки:
   ```bash
   git init && git add . && git commit -m "init"
   git branch -M main
   git remote add origin https://github.com/<логин>/timetable-app.git
   git push -u origin main
   ```
3. **Settings → Pages**: Source = *Deploy from a branch*, Branch = `main`, папка `/docs` → Save.
4. **Actions → Update timetable → Run workflow**: первый ручной запуск. В логе шага *Fetch & parse*
   должна быть строка `PDF: https://volpi.ru/web/files/timetable/timetable_fei/4_kurs_....pdf`.
   Если шаг *Commit changes* падает с ошибкой 403: **Settings → Actions → General → Workflow permissions →
   Read and write permissions**.
5. Через минуту-две данные будут доступны по адресу
   `https://<логин>.github.io/timetable-app/vip-408.json`.

Если запуск упал (сайт недоступен, сменилась вёрстка, разбор выглядит подозрительно), GitHub пришлёт
письмо. Старый JSON при этом не трогается.

## Локально

```bash
pip install -r parser/requirements.txt
python parser/tests/test_parser.py                       # тест
python parser/fetch_and_build.py                         # с сайта
python parser/fetch_and_build.py --pdf some.pdf          # из файла
python parser/timetable_parser.py some.pdf ВИП-408       # просто вывести JSON
```

## Формат `vip-408.json`

```jsonc
{
  "version": "68b0278dc4901a34",        // хэш списка пар; изменился → расписание поменялось
  "group": "ВИП-408",
  "source_url": "https://volpi.ru/.../4_kurs_28102026133717.pdf",
  "source_updated": "2026-10-28 13:37", // "обновлено:" с сайта
  "updated_at": "2026-11-08T20:07:01+03:00",
  "bells": { "1": {"start": "08:00", "end": "09:30"}, ... },
  "holidays": ["2026-11-04"],
  "range": {"from": "2026-09-01", "to": "2026-12-26"},
  "lessons": [
    {
      "date": "2026-10-27", "pair": 3, "start": "11:20", "end": "12:50",
      "subject": "Индустр.разраб.прогр.прод.", "teacher": "Абрамова", "room": "1-306",
      "type": "lecture",            // lecture | practice | lab | null
      "changed": true,              // жёлтая заливка в PDF
      "note": "27.10 за 23.10"      // пометка о датах из ячейки, если есть
    }
  ]
}
```

Что парсер учитывает: первая/вторая неделя (по датам из колонок месяцев), праздники (красные даты),
пары только в отдельные даты (`28.09,26.10`) или период (`1.09-8.12`), своё время (`9:00-10:30`),
переносы `27.10 за 23.10` (пара добавляется 27.10 и убирается 23.10), `с 11.20` (сдвиг начала),
лабы на 2 пары, общие лекции с соседней группой, сокращённые названия (приводятся к полному).
