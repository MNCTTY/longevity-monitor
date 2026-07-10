"""Слой графа знаний: теории, посылки (premises) и связи статья->теория/посылка.

Модель (собственная онтология пользователя, засеивается из курируемой таблицы):

    Premises  ──ground──▶  Theories  ◀──discuss/support/challenge──  Papers
    (эмпирические факты)   (рамки/каркасы)                           (доказательства)

У теорий есть критерии оценки (C1–C4: предлагает биомаркер / механизм /
вмешательство) и объясняющие вопросы (Q1–Q5: объясняет известные парадоксы).
У посылок есть таксономический охват (taxonomic scope) + уверенность (confidence).
Статьи привязываются к теориям с указанием *stance* (discusses / supports /
challenges / extends / mentions) — именно это позволяет карте со временем
накапливать согласие и противоречия. Используется src.positioning и src.cli.
"""
import re
import json
import hashlib

from . import db
from .models import Paper, normalize_doi

KG_SCHEMA = """
CREATE TABLE IF NOT EXISTS theories (
    theory_id TEXT PRIMARY KEY,
    name TEXT, abbreviation TEXT, aliases TEXT,
    paradigm TEXT, nature TEXT, quant_qual TEXT, evolutionary TEXT,
    proponent TEXT, year TEXT, stems_from TEXT, main_idea TEXT,
    criteria TEXT, integrity_score TEXT,
    source TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS premises (
    premise_id TEXT PRIMARY KEY,
    text TEXT, theory_hint TEXT, taxonomic_scope TEXT,
    level_of_abstraction TEXT, confidence REAL, link TEXT,
    source TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS paper_theory (
    paper_id TEXT, theory_id TEXT, stance TEXT, strength REAL,
    note TEXT, source TEXT, status TEXT DEFAULT 'active', evidence_note TEXT,
    PRIMARY KEY (paper_id, theory_id, source)
);
CREATE TABLE IF NOT EXISTS paper_premise (
    paper_id TEXT, premise_id TEXT, stance TEXT, note TEXT, source TEXT,
    strength REAL, status TEXT DEFAULT 'active',
    PRIMARY KEY (paper_id, premise_id, source)
);
CREATE TABLE IF NOT EXISTS theory_relation (
    src_theory_id TEXT, dst_theory_id TEXT, relation TEXT, source TEXT,
    weight REAL DEFAULT 1, evidence_papers TEXT,
    PRIMARY KEY (src_theory_id, dst_theory_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_pt_theory ON paper_theory(theory_id);
CREATE INDEX IF NOT EXISTS idx_pp_premise ON paper_premise(premise_id);

-- append-only log of how each paper changed the map (spine of Digest v2)
CREATE TABLE IF NOT EXISTS map_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, paper_id TEXT, event_type TEXT, ref_type TEXT, ref_id TEXT,
    delta REAL, detail TEXT, dedup_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON map_events(ts);

-- contradiction / tension queue
CREATE TABLE IF NOT EXISTS contradictions (
    contradiction_id TEXT PRIMARY KEY,   -- stable signature
    level TEXT, ref_id TEXT, ref_label TEXT,
    for_papers TEXT, against_papers TEXT,
    strength REAL, status TEXT DEFAULT 'open',
    opened_at TEXT, updated_at TEXT, note TEXT
);

-- derived caches (rebuildable by map-refresh)
CREATE TABLE IF NOT EXISTS theory_scorecard (
    theory_id TEXT PRIMARY KEY, name TEXT,
    support_w REAL, challenge_w REAL, attention INTEGER,
    net REAL, contested REAL, status TEXT, n_papers INTEGER,
    last_paper_at TEXT, refreshed_at TEXT
);
CREATE TABLE IF NOT EXISTS premise_ledger (
    premise_id TEXT PRIMARY KEY, text TEXT,
    seed_confidence REAL, evidence_confidence REAL, drift REAL,
    n_for INTEGER, n_against INTEGER, flag TEXT, refreshed_at TEXT
);
"""

# Колонки, которые могли отсутствовать в более ранней версии схемы. Добавляем их
# «догоняющими» ALTER TABLE в ensure_schema (ошибку «колонка уже есть» глушим).
_MIGRATIONS = [
    ("paper_theory", "status", "TEXT DEFAULT 'active'"),
    ("paper_theory", "evidence_note", "TEXT"),
    ("paper_premise", "strength", "REAL"),
    ("paper_premise", "status", "TEXT DEFAULT 'active'"),
    ("theory_relation", "weight", "REAL DEFAULT 1"),
    ("theory_relation", "evidence_papers", "TEXT"),
    ("theories", "status", "TEXT DEFAULT 'canonical'"),
    ("premises", "status", "TEXT DEFAULT 'canonical'"),
]

STANCES = ("supports", "challenges", "extends", "discusses", "mentions", "neutral")


def ensure_schema(con):
    # Создаём таблицы графа знаний и накатываем догоняющие миграции колонок.
    con.executescript(KG_SCHEMA)
    for table, col, decl in _MIGRATIONS:
        try:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        except Exception:
            pass  # колонка уже существует — это нормально
    con.commit()


def slug(text, maxlen=48):
    # Превращаем строку в безопасный «slug» для использования в id (только [a-z0-9-]).
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:maxlen] or "x"


# Явный вариант -> канон, по ключу *нормализованной* формы. Схлопывает очевидные
# дубликаты 11 курируемых теорий, не рискуя слить действительно разные каркасы
# (последние остаются отдельными узлами-заготовками для ручного ревью).
CANONICAL_SYNONYMS = {
    "somatic mutation": "somatic mutations",
    "oxidative damage": "oxidative damage accumulation",
    "oxidative stress": "oxidative damage accumulation",
    "telomere loss": "telomere",
    "telomere attrition": "telomere",
}


def normalize_theory_name(name):
    """Нестрогий ключ, чтобы 'hyperfunction theory' == 'Hyperfunction theory'."""
    # Нижний регистр, убираем скобки, унифицируем ageing->aging, срезаем артикль
    # и типовые «хвосты» (theory / hypothesis / of aging и т.п.), затем схлопываем
    # к канону по CANONICAL_SYNONYMS.
    s = (name or "").lower().replace("\n", " ").strip()
    s = re.sub(r"\(.*?\)", "", s)
    s = s.replace("ageing", "aging")
    if s.startswith("the "):
        s = s[4:]
    for junk in (" theory of aging", " theories of aging", " theory", " theories",
                 " model", " of aging", " hypothesis", " hypotheses"):
        s = s.replace(junk, "")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return CANONICAL_SYNONYMS.get(s, s)


def looks_like_single_theory(name):
    """Отсекает «список в ячейке» вида 'error theory, redundant message theory, ...'."""
    # Признаки списка: запятая или ' and ', либо слишком длинное имя (>8 слов).
    n = (name or "").strip()
    if not n:
        return False
    if n.count(",") >= 1 or " and " in n.lower():
        return False
    return len(n.split()) <= 8


# ---------------------------------------------------------------- theories ---
def upsert_theory(con, theory_id, **f):
    cols = ["theory_id", "name", "abbreviation", "aliases", "paradigm", "nature",
            "quant_qual", "evolutionary", "proponent", "year", "stems_from",
            "main_idea", "criteria", "integrity_score", "source", "created_at"]
    row = {c: f.get(c, "") for c in cols}
    row["theory_id"] = theory_id
    row.setdefault("created_at", db.now_iso())
    row["created_at"] = row.get("created_at") or db.now_iso()
    existing = con.execute("SELECT source FROM theories WHERE theory_id=?", (theory_id,)).fetchone()
    if existing:
        # Обогащаем заготовку более полными данными, но НИКОГДА не затираем
        # уже заполненные поля пустыми (COALESCE(NULLIF(...))).
        sets, vals = [], []
        for c in cols:
            if c in ("theory_id", "created_at"):
                continue
            if row.get(c) not in ("", None):
                sets.append(f"{c}=COALESCE(NULLIF(?, ''), {c})")
                vals.append(row[c])
        if sets:
            con.execute(f"UPDATE theories SET {','.join(sets)} WHERE theory_id=?", vals + [theory_id])
    else:
        con.execute(f"INSERT INTO theories ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                    [row[c] for c in cols])
    con.commit()
    return theory_id


def theory_index(con):
    """Словарь: нормализованное имя/алиас -> theory_id."""
    idx = {}
    for r in con.execute("SELECT theory_id, name, aliases FROM theories"):
        idx[normalize_theory_name(r["name"])] = r["theory_id"]
        for alias in (r["aliases"] or "").split(";"):
            if alias.strip():
                idx[normalize_theory_name(alias)] = r["theory_id"]
    return idx


def resolve_or_create_theory(con, name, source="paper-derived"):
    if not (name or "").strip():
        return None
    key = normalize_theory_name(name)
    if not key:
        return None
    idx = theory_index(con)
    if key in idx:
        return idx[key]
    # Новый узел заводим только для того, что похоже на одно имя теории;
    # «список в ячейке» пропускаем, чтобы карта оставалась чистой.
    if not looks_like_single_theory(name):
        return None
    tid = "th:" + slug(key)
    if con.execute("SELECT 1 FROM theories WHERE theory_id=?", (tid,)).fetchone():
        return tid
    upsert_theory(con, tid, name=name.strip(), source=source)
    return tid


def resolve_theory_list(con, raw, source="paper-derived"):
    """Разобрать ячейку свободного текста с несколькими теориями и резолвить каждую."""
    ids = []
    for part in re.split(r"[;,]", raw or ""):
        tid = resolve_or_create_theory(con, part, source=source)
        if tid and tid not in ids:
            ids.append(tid)
    return ids


# ---------------------------------------------------------------- premises ---
def upsert_premise(con, text, theory_hint="", scope="", level="", confidence=None,
                   link="", source="curated"):
    # id посылки — хэш от нормализованного текста, поэтому дубли текста совпадут по id.
    pid = "prem:" + hashlib.sha1(text.strip().lower().encode()).hexdigest()[:16]
    if con.execute("SELECT 1 FROM premises WHERE premise_id=?", (pid,)).fetchone():
        return pid  # такая посылка уже есть
    try:
        conf = float(confidence) if str(confidence).strip() else None
    except (ValueError, TypeError):
        conf = None
    con.execute(
        "INSERT INTO premises(premise_id,text,theory_hint,taxonomic_scope,level_of_abstraction,confidence,link,source,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (pid, text.strip(), theory_hint, scope, level, conf, link, source, db.now_iso()),
    )
    con.commit()
    return pid


# -------------------------------------------------------------------- links ---
def link_paper_theory(con, paper_id, theory_id, stance="discusses", strength=None, note="", source="curated"):
    if not (paper_id and theory_id):
        return
    con.execute(
        "INSERT OR IGNORE INTO paper_theory(paper_id,theory_id,stance,strength,note,source) VALUES(?,?,?,?,?,?)",
        (paper_id, theory_id, stance, strength, note, source),
    )
    con.commit()


def link_paper_premise(con, paper_id, premise_id, stance="evidence_for", note="", source="analysis"):
    if not (paper_id and premise_id):
        return
    con.execute(
        "INSERT OR IGNORE INTO paper_premise(paper_id,premise_id,stance,note,source) VALUES(?,?,?,?,?)",
        (paper_id, premise_id, stance, note, source),
    )
    con.commit()


# ------------------------------------------------------------------- import ---
def _clean_year(y):
    y = str(y or "").strip()
    return y[:-2] if y.endswith(".0") else y


def import_seed(con, seed):
    """Импорт курируемой базы знаний из 3 листов (Theories / Premises / Papers)."""
    ensure_schema(con)
    stats = {"theories": 0, "premises": 0, "papers_new": 0, "paper_theory_links": 0}

    for t in seed.get("Theories", {}).get("records", []):
        name = (t.get("Name of theory") or "").strip()
        if not name:
            continue
        # Критерии оценки C1–C4 и объясняющие вопросы Q1–Q5 собираем в один JSON.
        criteria = {k: v for k, v in t.items() if (k.startswith(("C1", "C2", "C3", "C4", "Q1", "Q2", "Q3", "Q4", "Q5"))) and str(v).strip()}
        upsert_theory(
            con, "th:" + slug(normalize_theory_name(name)),
            name=name, abbreviation=t.get("Theory abbreviation", ""),
            aliases=t.get("Possible aliases", ""), paradigm=t.get("Paradigm", ""),
            nature=t.get("Mechanistic or Phenomenological?", ""),
            quant_qual=t.get("Quantitative or qualitative?", ""),
            evolutionary=t.get("Evolutionary?", ""), proponent=t.get("Main proponent", ""),
            year=_clean_year(t.get("Year of inception", "")), stems_from=t.get("Stems from", ""),
            main_idea=t.get("Main idea", ""), criteria=json.dumps(criteria, ensure_ascii=False),
            integrity_score=str(t.get("Integrity score", "")), source="curated",
        )
        stats["theories"] += 1

    for p in seed.get("Premises", {}).get("records", []):
        text = (p.get("Premise") or "").strip()
        if not text:
            continue
        upsert_premise(
            con, text, theory_hint=p.get("A ground for which theory?", ""),
            scope=p.get("Taxonomic scope", ""), level=p.get("Level of abstraction", ""),
            confidence=p.get("Confidence score"), link=p.get("Link (if needed)", ""),
        )
        stats["premises"] += 1

    for r in seed.get("Papers", {}).get("records", []):
        title = (r.get("Title") or "").strip()
        if not title:
            continue
        summary = (r.get("Article summary") or "").strip()
        doi = normalize_doi(r.get("DOI"))
        authors = [a.strip() for a in [r.get("First author"), r.get("Last author")] if (a or "").strip()]
        paper = Paper(
            source="curated", source_id=slug(title, 60), doi=doi, title=title,
            # Настоящего абстракта в таблице нет; используем курируемое summary, чтобы
            # статья участвовала в похожести и карте. Реальный абстракт дозагрузим позже.
            abstract=summary, authors=authors, journal=r.get("Journal", "") or "",
            published=_clean_year(r.get("Year", "")),
            url=f"https://doi.org/{doi}" if doi else "",
            raw={"curated": True, "article_type": r.get("Article type", ""),
                 "impact": r.get("Impact score", ""), "relevance": r.get("Relevance score", ""),
                 "significance": r.get("Significance score", "")},
        )
        added = db.upsert_papers(con, [paper])
        stats["papers_new"] += added
        pid = paper.paper_id
        if summary:
            db.save_analysis(con, pid, summary, "", "", [], "curated")
        # Поле «основной теории» само может быть списком: первая = discusses, остальные = mentions.
        main_ids = resolve_theory_list(con, r.get("What theory does the article discuss (mostly)?", ""), source="paper-derived")
        main = main_ids[0] if main_ids else None
        seen = set()
        if main:
            link_paper_theory(con, pid, main, stance="discusses", source="curated")
            seen.add(main); stats["paper_theory_links"] += 1
        others = main_ids[1:] + resolve_theory_list(con, r.get("What other theories of aging the article discuss (if any)?", ""), source="paper-derived")
        for tid in others:
            if tid and tid not in seen:
                link_paper_theory(con, pid, tid, stance="mentions", source="curated")
                seen.add(tid); stats["paper_theory_links"] += 1
    return stats


def reset(con):
    """Очистить таблицы графа знаний (статьи сохраняются) для чистого пере-импорта."""
    ensure_schema(con)
    for t in ("paper_theory", "paper_premise", "theory_relation", "theories", "premises"):
        con.execute(f"DELETE FROM {t}")
    con.commit()


# -------------------------------------------------------------------- views ---
def kg_stats(con):
    # Сводка по графу знаний: счётчики узлов/связей + рейтинг теорий по числу
    # привязанных статей (support = supports/discusses/extends, challenge = challenges).
    ensure_schema(con)
    out = {}
    out["theories"] = con.execute("SELECT COUNT(*) c FROM theories").fetchone()["c"]
    out["premises"] = con.execute("SELECT COUNT(*) c FROM premises").fetchone()["c"]
    out["paper_theory"] = con.execute("SELECT COUNT(*) c FROM paper_theory").fetchone()["c"]
    out["paper_premise"] = con.execute("SELECT COUNT(*) c FROM paper_premise").fetchone()["c"]
    ranking = con.execute(
        """SELECT t.name,
                  SUM(CASE WHEN pt.stance IN ('supports','discusses','extends') THEN 1 ELSE 0 END) AS support,
                  SUM(CASE WHEN pt.stance='challenges' THEN 1 ELSE 0 END) AS challenge,
                  COUNT(*) AS total
           FROM theories t LEFT JOIN paper_theory pt ON t.theory_id=pt.theory_id
           GROUP BY t.theory_id ORDER BY total DESC"""
    ).fetchall()
    out["theory_ranking"] = [(r["name"], r["support"] or 0, r["challenge"] or 0, r["total"]) for r in ranking]
    return out
