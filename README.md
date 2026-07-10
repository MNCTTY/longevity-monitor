# Longevity & Aging-Biology Monitor + Knowledge Map

Агент, который **мониторит новые публикации** по биологии старения и продлению
жизни, складывает их в локальную базу, и **строит карту знаний**: связывает
статьи с теориями старения и эмпирическими посылками, накапливает доказательную
базу, отслеживает согласия и **противоречия** между работами.

Две подсистемы, одна база SQLite:

1. **Мониторинг + похожесть** — сбор статей из научных API, дедупликация,
   краткая суть каждой статьи и поиск похожих (TF-IDF).
2. **Карта знаний (theory-centric)** — позиционирование каждой статьи
   относительно теорий и посылок, scorecard'ы теорий, динамическая уверенность
   посылок, детекция противоречий.

---

## Ноль зависимостей

Ядро работает на **чистой стандартной библиотеке Python 3.10+**. Ставить ничего
не нужно: HTTP — `urllib`, база — `sqlite3`, поиск похожих — собственный TF-IDF.
Опциональные апгрейды (в `requirements-optional.txt`): `anthropic` для
авто-анализа и SPECTER2 для более качественной похожести.

---

## Как это работает: конвейер

```
                      ┌────────────── ИСТОЧНИКИ (src/sources/*) ──────────────┐
                      │  PubMed · Europe PMC · bioRxiv/medRxiv · Semantic S.  │
                      └───────────────────────────┬───────────────────────────┘
                                                  │ Paper (models.py)
                                                  ▼
   backfill / run ───────────────────────►  SQLite (db.py)  ◄──── import-curated (seed)
                                                  │            (knowledge.py: theories/
                                        ┌─────────┴─────────┐   premises/paper-links)
                                        ▼                   ▼
                         ПОХОЖЕСТЬ (analysis.py)     ПОЗИЦИОНИРОВАНИЕ (positioning.py)
                         TF-IDF соседи               grounding кандидатов → LLM →
                              │                      канонизация → связи + веса
                              ▼                              │
                    prepare → (LLM) → import          position-prepare → (LLM) →
                    summarize.py                       position-import → map-refresh
                              │                              │
                              ▼                              ▼
                     digest.py:                       digest.py:
                     digest-DATE.md                   knowledge-map-DATE.md
                     (суть + похожие)                 (scorecard'ы, посылки под
                                                       пересмотром, противоречия)
```

LLM-шаг (суть и позиционирование) **подключаемый**: без ключа — через батч
(`prepare` → заполнить → `import`), с `ANTHROPIC_API_KEY` — автоматически
(`autoanalyze` / `autoposition`).

---

## Структура репозитория

```
longevity-monitor/
├── config.json                 конфигурация: ключевые слова, источники, лимиты
├── requirements-optional.txt   опциональные зависимости (anthropic, SPECTER2)
├── README.md
├── src/
│   ├── __init__.py             версия пакета
│   ├── http.py                 stdlib-HTTP c ретраями и rate-limit
│   ├── models.py               Paper (нормализованная запись) + dedup-ключ
│   ├── db.py                   SQLite: papers / analysis / state
│   ├── similarity/
│   │   └── tfidf.py            TF-IDF индекс косинусной похожести (stdlib)
│   ├── sources/                адаптеры источников (один класс = один источник)
│   │   ├── pubmed.py
│   │   ├── europepmc.py
│   │   ├── biorxiv.py
│   │   └── semanticscholar.py
│   ├── analysis.py             поиск похожих + сборка пакетов для суммаризации
│   ├── summarize.py            LLM-шаг: BatchSummarizer / AnthropicSummarizer
│   ├── knowledge.py            граф знаний: схема, импорт seed, канонизация
│   ├── positioning.py          позиционирование, накопление, противоречия
│   ├── enrich.py               дотягивание реальных абстрактов curated (Europe PMC)
│   ├── review.py               очередь ревью спорных узлов/связей
│   ├── digest.py               рендер Markdown (дайджест + карта знаний)
│   ├── graph.py                интерактивный HTML-граф карты знаний
│   └── cli.py                  точка входа: все команды
├── data/                       (gitignored) база, seed, рабочие файлы
│   └── seed/knowledge_seed.json  кураторская база (теории/посылки/статьи)
└── digests/                    (gitignored) готовые дайджесты
```

### Что в каком файле и кто кого импортирует

| Файл | Отвечает за | Импортирует | Кто использует |
|---|---|---|---|
| `http.py` | GET-запросы через `urllib`, ретраи на 429/5xx, вежливые паузы | stdlib | все `sources/*` |
| `models.py` | dataclass `Paper`, `normalize_doi`, `make_paper_id` (dedup) | stdlib | `sources/*`, `knowledge` |
| `db.py` | схема SQLite, `upsert_papers` (дедуп), `save_analysis`, `state` | stdlib | `cli`, `knowledge`, `positioning` |
| `similarity/tfidf.py` | `TfidfIndex.fit/query` — косинусная похожесть | stdlib | `analysis`, `positioning` |
| `sources/pubmed.py` | PubMed E-utilities (esearch+efetch, XML) | `http`, `models` | `cli` |
| `sources/europepmc.py` | Europe PMC REST (журналы+препринты) | `http`, `models` | `cli` |
| `sources/biorxiv.py` | bioRxiv/medRxiv (локальная фильтрация по ключам) | `http`, `models` | `cli` |
| `sources/semanticscholar.py` | Semantic Scholar search (нужен api_key) | `http`, `models` | `cli` |
| `analysis.py` | `build_index`, `neighbors_for`, `make_packet` | `similarity.tfidf` | `cli` |
| `summarize.py` | суть статьи + `position()` (авто-режим), промпты | `anthropic` (опц.) | `cli` |
| `knowledge.py` | KG-схема, `import_seed`, канонизация теорий/посылок, `kg_stats` | `db`, `models` | `cli`, `positioning` |
| `positioning.py` | grounding, канонизация ссылок, веса, scorecard'ы, ledger, противоречия | `db`, `knowledge`, `similarity.tfidf` | `cli` |
| `enrich.py` | реальные абстракты curated-статей по DOI/названию (Europe PMC) | `http` | `cli` |
| `review.py` | очередь ревью: provisional/paper-derived узлы + needs_review связи | `db`, `knowledge`, `positioning` | `cli` |
| `digest.py` | `render_markdown`, `render_knowledge_digest` | stdlib | `cli` |
| `graph.py` | интерактивный force-directed HTML-граф (SVG+JS, самодостаточный) | `positioning` | `cli` |
| `cli.py` | разбор команд, оркестрация конвейера | всё выше | пользователь |

Правило зависимостей: `cli.py` — верхний слой, тянет всё; ниже —
`positioning → knowledge → db/models`; `sources → http/models`;
`analysis/positioning → similarity`. Циклов нет.

---

## Модель данных (SQLite)

**Мониторинг:**
- `papers` — статьи (PK `paper_id` = `doi:…` или `source:id`), дедуп при вставке.
- `analysis` — суть/ключевые результаты/сравнение на статью.
- `state` — служебные курсоры (напр. `last_fetch`).

**Карта знаний** (`knowledge.py` + `positioning.py`):
- `theories` — теории старения (11 канонических из seed + `provisional` от статей):
  парадигма, механистическая/феноменологическая, автор, критерии C1–C4/Q1–Q5.
- `premises` — эмпирические посылки: текст, таксономический охват, confidence.
- `paper_theory` — связь статья→теория со `stance`
  (supports/challenges/extends/discusses/mentions), `strength`, `source`, `status`.
- `paper_premise` — связь статья→посылка (evidence_for/against/refines).
- `theory_relation` — связи теория↔теория (bridging: co_discussed/contrasts).
- `map_events` — append-only журнал изменений карты.
- `contradictions` — очередь противоречий (уровень посылки/теории).
- `theory_scorecard`, `premise_ledger` — **производные кэши**, пересобираются
  из связей командой `map-refresh` (истина — в таблицах связей).

Ключ провенанса: `source` входит в PK связей, поэтому кураторские и
LLM-связи сосуществуют, не перетирая друг друга. `needs_review`-связи имеют
нулевой вес и не двигают карту до ревью.

---

## Механики карты знаний

- **Positioning.** По каждой статье собираются кандидатные теории/посылки
  (TF-IDF grounding), LLM определяет позицию к теориям и доказательства за/против
  посылок. Ссылки канонизируются (exact → fuzzy → новый provisional-узел).
- **Theory scorecard.** `support_w`/`challenge_w`/`net` (взвешенно по strength ×
  качество × источник) → статус emerging / **contested** / established.
- **Premise ledger.** Динамическая уверенность посылки по Байесу (Beta с
  приором из seed-confidence, κ=4): `evidence_confidence` против `seed_confidence`,
  расхождение = сигнал «посылка под пересмотром».
- **Contradictions.** Посылочный уровень (evidence_for vs evidence_against на
  одной посылке при сопоставимых весах) и теоретический (вызов established-теории).
- **Bridging.** Статья, обсуждающая две теории → ребро `theory_relation`
  (показывается при ≥2 подтверждениях).

---

## Команды

```bash
# из корня проекта longevity-monitor/

# --- мониторинг ---
python -m src.cli init                        # создать базу
python -m src.cli backfill --days 180 --max 60 # первичное наполнение
python -m src.cli run                         # добрать новое за N дней
python -m src.cli stats                       # что в базе

# --- суть статей (без ключа: батч) ---
python -m src.cli prepare --limit 10          # -> data/to_analyze.json
python -m src.cli import data/analyzed.json   # ... после заполнения
python -m src.cli autoanalyze --limit 10      # авто (нужен ANTHROPIC_API_KEY)

# --- карта знаний ---
python -m src.cli import-curated --reset      # засеять карту из seed
python -m src.cli enrich-abstracts            # дотянуть реальные абстракты curated-статей (Europe PMC)
python -m src.cli position-prepare --limit 0  # -> data/to_position.json (кандидаты)
python -m src.cli position-import data/positioned.json
python -m src.cli autoposition --limit 0      # авто (нужен ANTHROPIC_API_KEY)
python -m src.cli map-refresh                 # пересчитать scorecard/ledger/противоречия
python -m src.cli scorecard [theory]          # рейтинг теорий по доказательствам
python -m src.cli contradictions              # открытые противоречия
python -m src.cli kg-stats

# --- ревью спорных узлов/связей ---
python -m src.cli review                       # очередь: provisional/paper-derived узлы + needs_review связи
python -m src.cli review-approve <id|all>      # подтвердить (в карту)
python -m src.cli review-reject  <id|all>      # отклонить (удалить, чистка фрагментации)

# --- вывод ---
python -m src.cli digest                      # digests/digest-DATE.md + knowledge-map-DATE.md
python -m src.cli graph                       # digests/graph.html — интерактивный граф карты
```

---

## Источники

| Адаптер | Покрытие | Ключ |
|---|---|---|
| `pubmed` | Рецензированные biomed-статьи (NCBI E-utilities) | нет |
| `europepmc` | Журналы + препринты + часть полных текстов | нет |
| `biorxiv` | Препринты bioRxiv / medRxiv | нет |
| `semanticscholar` | Релевантный поиск + цитирования | нужен бесплатный api_key (иначе 429) |

Ключевые слова и включённые источники — в `config.json` (сейчас настроены на
mechanistic aging biology: senescence, senolytic, geroscience, epigenetic clock и т.д.).

---

## Автоматизация

Ежедневный прогон — через Windows Task Scheduler (или scheduled-агента):
`run → autoposition → map-refresh → digest`.

## Апгрейды (опционально)

- **Авто-анализ/позиционирование:** `pip install anthropic`, задать
  `ANTHROPIC_API_KEY` → `autoanalyze` / `autoposition`.
- **Качество похожести:** SPECTER2 (`sentence-transformers`) вместо TF-IDF.
- **Notion-вывод:** после авторизации коннектора Notion (сейчас — Markdown).
