"""Хранилище на SQLite: статьи, их анализ и состояние прогонов.

Базовый слой доступа к данным: определяет схему таблиц (papers/analysis/state),
даёт подключение и функции чтения/записи. Используется CLI и модулями анализа.
"""
import os
import json
import sqlite3
import datetime

# Схема таблиц. CREATE ... IF NOT EXISTS — безопасно вызывать при каждом подключении.
SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    paper_id   TEXT PRIMARY KEY,
    source     TEXT,
    source_id  TEXT,
    doi        TEXT,
    title      TEXT,
    abstract   TEXT,
    authors    TEXT,
    journal    TEXT,
    published  TEXT,
    url        TEXT,
    raw        TEXT,
    fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_papers_source ON papers(source);
CREATE INDEX IF NOT EXISTS idx_papers_published ON papers(published);

CREATE TABLE IF NOT EXISTS analysis (
    paper_id     TEXT PRIMARY KEY,
    summary      TEXT,
    key_findings TEXT,
    comparison   TEXT,
    neighbors    TEXT,
    model        TEXT,
    analyzed_at  TEXT,
    FOREIGN KEY(paper_id) REFERENCES papers(paper_id)
);

CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""


def now_iso():
    # Текущее время в UTC в формате ISO — единый формат меток времени в БД.
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def connect(path):
    # Открываем БД, создаём каталог при необходимости и применяем схему.
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row  # доступ к колонкам по имени (row["title"])
    con.executescript(SCHEMA)
    return con


def upsert_papers(con, papers):
    """Вставить новые статьи, пропустить существующие (дедуп по paper_id).

    Возвращает число добавленных.
    """
    added = 0
    for p in papers:
        row = p.to_row()
        if not row["paper_id"]:
            continue
        if con.execute("SELECT 1 FROM papers WHERE paper_id=?", (row["paper_id"],)).fetchone():
            continue  # такая статья уже есть — пропускаем
        row["fetched_at"] = now_iso()
        # Колонки и плейсхолдеры строятся из ключей row, значения — параметрами.
        cols = ",".join(row.keys())
        ph = ",".join("?" * len(row))
        con.execute(f"INSERT INTO papers ({cols}) VALUES ({ph})", list(row.values()))
        added += 1
    con.commit()
    return added


def get_state(con, key, default=None):
    r = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_state(con, key, value):
    con.execute(
        "INSERT INTO state(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    con.commit()


def all_papers(con, with_abstract_only=True):
    # Все статьи; по умолчанию только с непустым абстрактом (нужно для похожести).
    q = "SELECT * FROM papers"
    if with_abstract_only:
        q += " WHERE abstract IS NOT NULL AND length(abstract) > 0"
    return con.execute(q).fetchall()


def unanalyzed_papers(con):
    # Статьи с абстрактом, для которых ещё нет записи в analysis (LEFT JOIN + IS NULL).
    return con.execute(
        """
        SELECT p.* FROM papers p
        LEFT JOIN analysis a ON p.paper_id = a.paper_id
        WHERE a.paper_id IS NULL AND length(p.abstract) > 0
        ORDER BY p.published DESC
        """
    ).fetchall()


def save_analysis(con, paper_id, summary, key_findings, comparison, neighbors, model):
    # Сохранить/обновить анализ статьи (upsert по paper_id). neighbors — JSON-список.
    con.execute(
        """
        INSERT INTO analysis(paper_id, summary, key_findings, comparison, neighbors, model, analyzed_at)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(paper_id) DO UPDATE SET
            summary=excluded.summary, key_findings=excluded.key_findings,
            comparison=excluded.comparison, neighbors=excluded.neighbors,
            model=excluded.model, analyzed_at=excluded.analyzed_at
        """,
        (paper_id, summary, key_findings, comparison,
         json.dumps(neighbors, ensure_ascii=False), model, now_iso()),
    )
    con.commit()
