"""Позиционирование: помещаем статью в карту знаний и накапливаем саму карту.

Конвейер (повторяет поток прозаической суммаризации):
    position-prepare  -> data/to_position.json  (списки кандидатов на статью, «заземлённые» TF-IDF)
    <заполнить схему вручную / через Claude Code, либо `autoposition` с API-ключом>
    position-import   -> канонизировать ссылки, записать взвешенные связи, залогировать
                         map_events, найти «мосты» (bridges) и противоречия
    map-refresh       -> пересобрать theory_scorecard + premise_ledger из связей

Глубина = теории + посылки (граф сущностей/утверждений намеренно вне охвата).
Всё на чистой стандартной библиотеке; шаг LLM — существующий подключаемый суммаризатор.
"""
import json

from . import db, knowledge
from .similarity.tfidf import TfidfIndex

# Пороги резолвинга (косинус по TF-IDF); см. проектную документацию.
# *_MAP — уверенное совпадение (связь идёт «в карту»);
# *_REVIEW — слабое совпадение (связь помечается needs_review и весит 0).
TH_THEORY_MAP = 0.55
TH_THEORY_REVIEW = 0.30
TH_PREM_MAP = 0.60
TH_PREM_REVIEW = 0.40

ESTABLISHED_NET = 2.0   # «net» теории, выше которого вызов (challenge) считается значимым
KAPPA = 4.0             # сила априорного распределения (prior) для ledger посылок

# Наборы stance'ов, объединяемые при агрегации.
THEORY_SUPPORT = ("supports", "extends")      # считаются поддержкой теории
THEORY_ATTENTION = ("discusses", "mentions")  # считаются «вниманием» (без знака)
PREM_FOR = "evidence_for"
PREM_AGAINST = "evidence_against"


# ---------------------------------------------------------------- grounding ---
def _theory_rows(con):
    return con.execute("SELECT theory_id, name, aliases, main_idea FROM theories").fetchall()


def _theory_index_tfidf(con):
    # TF-IDF индекс по теориям (имя + алиасы + основная идея) для поиска кандидатов.
    rows = _theory_rows(con)
    items = [(r["theory_id"], f'{r["name"]} {r["aliases"] or ""} {r["main_idea"] or ""}') for r in rows]
    names = {r["theory_id"]: r["name"] for r in rows}
    return (TfidfIndex().fit(items) if items else None), names


def _premise_rows(con):
    return con.execute("SELECT premise_id, text, taxonomic_scope, confidence FROM premises").fetchall()


def _premise_index_tfidf(con):
    # TF-IDF индекс по текстам посылок для поиска кандидатов.
    rows = _premise_rows(con)
    items = [(r["premise_id"], r["text"]) for r in rows]
    meta = {r["premise_id"]: r for r in rows}
    return (TfidfIndex().fit(items) if items else None), meta


def candidate_theories(con, text, k=12):
    # top-k теорий-кандидатов, близких к тексту статьи (для промпта позиционирования).
    idx, names = _theory_index_tfidf(con)
    if not idx:
        return []
    return [{"theory_id": tid, "name": names[tid], "score": round(s, 3)} for tid, s in idx.query(text, k=k)]


def candidate_premises(con, text, k=20):
    # top-k посылок-кандидатов, близких к тексту статьи.
    idx, meta = _premise_index_tfidf(con)
    if not idx:
        return []
    out = []
    for pid, s in idx.query(text, k=k):
        r = meta[pid]
        out.append({"premise_id": pid, "text": r["text"], "taxonomic_scope": r["taxonomic_scope"],
                    "confidence": r["confidence"], "score": round(s, 3)})
    return out


def build_position_packet(con, row, k_theory=12, k_prem=20):
    # Пакет для LLM/ручного позиционирования: статья + «заземлённые» списки кандидатов
    # (теории и посылки) + инструкции по заполнению схемы.
    text = f'{row["title"]}. {row["abstract"]}'
    return {
        "paper_id": row["paper_id"],
        "title": row["title"],
        "abstract": (row["abstract"] or "")[:1600],
        "candidate_theories": candidate_theories(con, text, k_theory),
        "candidate_premises": candidate_premises(con, text, k_prem),
        "instructions": {
            "theory_positions": "for each relevant theory: {theory_ref (id from candidates or null), "
                                "theory_name, stance in [supports,challenges,extends,discusses,mentions], "
                                "strength 0..1, evidence_note}",
            "premise_evidence": "for each relevant premise: {premise_ref (id or null), premise_text, "
                                "stance in [evidence_for,evidence_against,refines], strength 0..1, "
                                "taxonomic_scope, evidence_note}",
        },
        "theory_positions": [],
        "premise_evidence": [],
    }


# ------------------------------------------------------------ canonicalize ---
def resolve_theory(con, ref, name, note=""):
    """Вернуть (theory_id, link_status). link_status='needs_review' => нулевой вес связи.

    Порядок резолвинга: явный ref -> точное совпадение имени -> TF-IDF по порогам
    -> завести новый provisional-узел (если это похоже на одно имя теории).
    """
    if ref and con.execute("SELECT 1 FROM theories WHERE theory_id=?", (ref,)).fetchone():
        return ref, "active"  # LLM дала валидный id из кандидатов
    key = knowledge.normalize_theory_name(name)
    idx = knowledge.theory_index(con)
    if key and key in idx:
        return idx[key], "active"  # точное совпадение по нормализованному имени/алиасу
    tfidf, _ = _theory_index_tfidf(con)
    hits = tfidf.query(f"{name} {note}", k=1) if tfidf else []
    if hits and hits[0][1] >= TH_THEORY_MAP:
        return hits[0][0], "active"        # уверенное совпадение
    if hits and hits[0][1] >= TH_THEORY_REVIEW:
        return hits[0][0], "needs_review"  # слабое совпадение — на ручное ревью
    if knowledge.looks_like_single_theory(name):
        # Заводим новый провизорный узел теории (вес получит после подтверждения).
        tid = "th:" + knowledge.slug(key)
        if not con.execute("SELECT 1 FROM theories WHERE theory_id=?", (tid,)).fetchone():
            knowledge.upsert_theory(con, tid, name=name.strip(), source="paper-derived", status="provisional")
        return tid, "active"
    return None, None


def resolve_premise(con, ref, text, scope="", note=""):
    # Тот же порядок, что и для теорий, плюс проверка совместимости таксономического охвата.
    if ref and con.execute("SELECT 1 FROM premises WHERE premise_id=?", (ref,)).fetchone():
        return ref, "active"
    tfidf, meta = _premise_index_tfidf(con)
    hits = tfidf.query(text, k=1) if tfidf else []
    if hits and hits[0][1] >= TH_PREM_MAP:
        # Чтобы считать посылку той же, охваты (scope) должны быть совместимы.
        cand_scope = (meta[hits[0][0]]["taxonomic_scope"] or "").strip().lower()
        if not scope or not cand_scope or cand_scope == scope.strip().lower() or "all" in (cand_scope, scope.strip().lower()):
            return hits[0][0], "active"
        return hits[0][0], "needs_review"  # текст совпал, но охват другой — на ревью
    if hits and hits[0][1] >= TH_PREM_REVIEW:
        return hits[0][0], "needs_review"
    if len(text.split()) >= 4:  # правдоподобно новая посылка -> провизорная
        pid = knowledge.upsert_premise(con, text, scope=scope, source="paper-derived")
        con.execute("UPDATE premises SET status='provisional' WHERE premise_id=?", (pid,))
        con.commit()
        return pid, "active"
    return None, None


# ------------------------------------------------------------------ events ---
def log_event(con, paper_id, event_type, ref_type, ref_id, delta=None, detail=None):
    # Пишем событие в append-only журнал map_events. dedup_key не даёт задвоить
    # одно и то же событие (INSERT OR IGNORE по уникальному ключу).
    key = f"{paper_id}|{event_type}|{ref_id}"
    try:
        con.execute(
            "INSERT OR IGNORE INTO map_events(ts,paper_id,event_type,ref_type,ref_id,delta,detail,dedup_key)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (db.now_iso(), paper_id, event_type, ref_type, ref_id, delta,
             json.dumps(detail, ensure_ascii=False) if detail else None, key),
        )
    except Exception:
        pass


# ------------------------------------------------------------------ import ---
def import_positioning(con, results, source="batch"):
    # Импорт результатов позиционирования: канонизируем ссылки, пишем связи,
    # логируем события, строим «мосты» между со-упомянутыми теориями.
    knowledge.ensure_schema(con)
    stats = {"papers": 0, "theory_links": 0, "premise_links": 0,
             "new_theories": 0, "new_premises": 0, "bridges": 0, "needs_review": 0}
    known_theories = {r["theory_id"] for r in con.execute("SELECT theory_id FROM theories")}
    known_premises = {r["premise_id"] for r in con.execute("SELECT premise_id FROM premises")}
    for res in results:
        pid = res.get("paper_id")
        if not pid:
            continue
        stats["papers"] += 1
        placed_theories = []
        for tp in res.get("theory_positions", []) or []:
            name = (tp.get("theory_name") or "").strip()
            if not name:
                continue
            tid, status = resolve_theory(con, tp.get("theory_ref"), name, tp.get("evidence_note", ""))
            if not tid:
                continue
            if tid not in known_theories:
                known_theories.add(tid)
                stats["new_theories"] += 1
                log_event(con, pid, "new_theory", "theory", tid, None, {"name": name})
            stance = tp.get("stance", "discusses")
            con.execute(
                "INSERT OR REPLACE INTO paper_theory(paper_id,theory_id,stance,strength,note,source,status,evidence_note)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (pid, tid, stance, tp.get("strength"), "", source, status, tp.get("evidence_note", "")),
            )
            stats["theory_links"] += 1
            if status == "needs_review":
                stats["needs_review"] += 1
            if status == "active":
                placed_theories.append((tid, stance))
            log_event(con, pid, "theory_stance_added", "theory", tid, tp.get("strength"),
                      {"stance": stance, "name": name, "status": status})
        # premises
        for pe in res.get("premise_evidence", []) or []:
            text = (pe.get("premise_text") or "").strip()
            if not text:
                continue
            pmid, status = resolve_premise(con, pe.get("premise_ref"), text,
                                           pe.get("taxonomic_scope", ""), pe.get("evidence_note", ""))
            if not pmid:
                continue
            if pmid not in known_premises:
                known_premises.add(pmid)
                stats["new_premises"] += 1
                log_event(con, pid, "new_premise", "premise", pmid, None, {"text": text[:120]})
            stance = pe.get("stance", PREM_FOR)
            con.execute(
                "INSERT OR REPLACE INTO paper_premise(paper_id,premise_id,stance,note,source,strength,status)"
                " VALUES(?,?,?,?,?,?,?)",
                (pid, pmid, stance, pe.get("evidence_note", ""), source, pe.get("strength"), status),
            )
            stats["premise_links"] += 1
            if status == "needs_review":
                stats["needs_review"] += 1
            log_event(con, pid, "premise_evidence_added", "premise", pmid, pe.get("strength"),
                      {"stance": stance, "text": text[:120], "status": status})
        # «Мосты»: все пары со-активных теорий одной статьи -> связь theory_relation
        # со счётчиком статей. Пары нормализуем по возрастанию id (lo, hi), чтобы
        # (A,B) и (B,A) считались одной связью.
        for i in range(len(placed_theories)):
            for j in range(i + 1, len(placed_theories)):
                a, sa = placed_theories[i]
                b, sb = placed_theories[j]
                lo, hi = sorted([a, b])
                # Если одну поддерживают, а другую оспаривают — это «contrasts», иначе просто «co_discussed».
                rel = "contrasts" if ("challenges" in (sa, sb) and ("supports" in (sa, sb) or "extends" in (sa, sb))) else "co_discussed"
                row = con.execute("SELECT weight, evidence_papers FROM theory_relation WHERE src_theory_id=? AND dst_theory_id=? AND relation=?", (lo, hi, rel)).fetchone()
                papers = set(json.loads(row["evidence_papers"]) if row and row["evidence_papers"] else [])
                papers.add(pid)
                con.execute(
                    "INSERT OR REPLACE INTO theory_relation(src_theory_id,dst_theory_id,relation,source,weight,evidence_papers)"
                    " VALUES(?,?,?,?,?,?)",
                    (lo, hi, rel, source, len(papers), json.dumps(sorted(papers), ensure_ascii=False)),
                )
                if len(papers) == 2:
                    # Считаем «мостом» момент, когда связь подтвердила вторая статья.
                    stats["bridges"] += 1
                    log_event(con, pid, "bridge_added", "relation", f"{lo}|{hi}", None, {"relation": rel})
        con.commit()
    return stats


# --------------------------------------------------------------- weighting ---
def _quality(con, paper_id):
    # Множитель качества по источнику статьи: курируемые (1.0) чуть весомее прочих (0.9).
    r = con.execute("SELECT source FROM papers WHERE paper_id=?", (paper_id,)).fetchone()
    return 1.0 if (r and r["source"] == "curated") else 0.9


def _link_weight(con, paper_id, strength, source, status):
    # Итоговый вес связи = сила * качество статьи * множитель источника связи.
    if status and status == "needs_review":
        return 0.0  # связи на ревью в агрегаты не входят
    src_mult = 0.9 if source in ("auto-llm", "paper-derived") else 1.0  # автоматика чуть слабее ручной
    s = 0.5 if strength is None else max(0.1, min(1.0, float(strength)))  # сила: дефолт 0.5, клип в [0.1, 1.0]
    return s * _quality(con, paper_id) * src_mult


# ------------------------------------------------------------ scorecards ------
def refresh_scorecards(con):
    # Полностью пересобираем scorecard теорий из активных связей paper_theory:
    # суммируем взвешенные support/challenge, считаем net и «спорность» (contested),
    # присваиваем статус (established/contested/emerging/unlinked).
    knowledge.ensure_schema(con)
    con.execute("DELETE FROM theory_scorecard")
    theories = {r["theory_id"]: r["name"] for r in con.execute("SELECT theory_id, name FROM theories")}
    agg = {tid: {"support": 0.0, "challenge": 0.0, "attention": 0, "papers": set(), "last": ""} for tid in theories}
    for r in con.execute("SELECT paper_id, theory_id, stance, strength, source, status FROM paper_theory WHERE status='active' OR status IS NULL"):
        tid = r["theory_id"]
        if tid not in agg:
            continue
        w = _link_weight(con, r["paper_id"], r["strength"], r["source"], r["status"])
        if r["stance"] in THEORY_SUPPORT:
            agg[tid]["support"] += w
        elif r["stance"] == "challenges":
            agg[tid]["challenge"] += w
        if r["stance"] in THEORY_ATTENTION:
            agg[tid]["attention"] += 1
        agg[tid]["papers"].add(r["paper_id"])
        pub = con.execute("SELECT published FROM papers WHERE paper_id=?", (r["paper_id"],)).fetchone()
        if pub and (pub["published"] or "") > agg[tid]["last"]:
            agg[tid]["last"] = pub["published"] or ""
    for tid, a in agg.items():
        net = a["support"] - a["challenge"]           # чистый перевес поддержки над вызовами
        denom = a["support"] + a["challenge"]
        contested = (a["challenge"] / denom) if denom else 0.0  # доля вызовов = мера спорности
        # Классификация статуса: без связей — unlinked; заметная доля вызовов
        # (>=0.34) — contested; сильный перевес (net>=порог) — established; иначе emerging.
        if denom == 0 and a["attention"] == 0:
            status = "unlinked"
        elif contested >= 0.34:
            status = "contested"
        elif net >= ESTABLISHED_NET:
            status = "established"
        else:
            status = "emerging"
        con.execute(
            "INSERT INTO theory_scorecard(theory_id,name,support_w,challenge_w,attention,net,contested,status,n_papers,last_paper_at,refreshed_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (tid, theories[tid], round(a["support"], 2), round(a["challenge"], 2), a["attention"],
             round(net, 2), round(contested, 2), status, len(a["papers"]), a["last"], db.now_iso()),
        )
    con.commit()


def refresh_ledger(con):
    # Байесовский ledger посылок (модель Beta-Bernoulli).
    # Для каждой посылки: seed-уверенность c0 (из curated confidence 0..5, нормируем в 0..1)
    # задаёт априор Beta(a=KAPPA*c0, b=KAPPA*(1-c0)). Каждая статья «за»/«против» добавляет
    # свой вес к a/b. Итоговая evidence-уверенность ev = a/(a+b) — среднее апостериора.
    knowledge.ensure_schema(con)
    con.execute("DELETE FROM premise_ledger")
    for pr in con.execute("SELECT premise_id, text, confidence FROM premises"):
        pid = pr["premise_id"]
        c0 = (pr["confidence"] / 5.0) if pr["confidence"] is not None else 0.5
        a, b = KAPPA * c0, KAPPA * (1 - c0)  # параметры априорного Beta-распределения
        n_for = n_against = 0
        for r in con.execute("SELECT paper_id, stance, strength, source, status FROM paper_premise WHERE premise_id=? AND (status='active' OR status IS NULL)", (pid,)):
            w = _link_weight(con, r["paper_id"], r["strength"], r["source"], r["status"])
            if r["stance"] == PREM_FOR:
                a += w; n_for += 1        # доказательство «за» — в параметр a
            elif r["stance"] == PREM_AGAINST:
                b += w; n_against += 1    # доказательство «против» — в параметр b
        ev = a / (a + b) if (a + b) else 0.5  # апостериорная уверенность
        drift = ev - c0                       # насколько данные сдвинули seed-уверенность
        flag = ""
        if abs(drift) >= 0.2:
            flag = "under_revision"           # заметный дрейф — посылка «под пересмотром»
        if (c0 - 0.5) * (ev - 0.5) < 0:
            flag = "sign_flip"                # знак относительно 0.5 сменился — переворот вывода
        # Уверенности храним обратно в исходной шкале 0..5 (c0/ev/drift * 5).
        con.execute(
            "INSERT INTO premise_ledger(premise_id,text,seed_confidence,evidence_confidence,drift,n_for,n_against,flag,refreshed_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (pid, pr["text"], round(c0 * 5, 2), round(ev * 5, 2), round(drift * 5, 2), n_for, n_against, flag, db.now_iso()),
        )
    con.commit()


# --------------------------------------------------------- contradictions ---
def detect_contradictions(con):
    # Ищем «противоречия» двух уровней и складываем их в очередь contradictions.
    knowledge.ensure_schema(con)
    opened = 0
    # Уровень посылки: сопоставляем взвешенные доказательства «за» (fw) и «против» (aw).
    for pr in con.execute("SELECT premise_id, text FROM premises"):
        pid = pr["premise_id"]
        fw = aw = 0.0
        fp, ap = [], []
        for r in con.execute("SELECT paper_id, stance, strength, source, status FROM paper_premise WHERE premise_id=? AND (status='active' OR status IS NULL)", (pid,)):
            w = _link_weight(con, r["paper_id"], r["strength"], r["source"], r["status"])
            if r["stance"] == PREM_FOR:
                fw += w; fp.append(r["paper_id"])
            elif r["stance"] == PREM_AGAINST:
                aw += w; ap.append(r["paper_id"])
        lo, hi = min(fw, aw), max(fw, aw)
        # Противоречие фиксируем, только если слабая сторона тоже весома:
        # минимум >= 1.0 и не меньше четверти сильной стороны (обе стороны реально спорят).
        if lo >= 1.0 and hi and lo >= 0.25 * hi:
            sig = "prem:" + pid
            con.execute(
                "INSERT INTO contradictions(contradiction_id,level,ref_id,ref_label,for_papers,against_papers,strength,status,opened_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?, 'open', ?, ?)"
                " ON CONFLICT(contradiction_id) DO UPDATE SET for_papers=excluded.for_papers,"
                " against_papers=excluded.against_papers, strength=excluded.strength, updated_at=excluded.updated_at",
                (sig, "premise", pid, pr["text"][:120], json.dumps(fp), json.dumps(ap),
                 round(lo / hi, 2), db.now_iso(), db.now_iso()),
            )
            opened += 1
    # Уровень теории: вызов (challenge) устоявшейся теории (net выше порога established).
    for sc in con.execute("SELECT theory_id, name, net FROM theory_scorecard WHERE status IN ('established','contested')"):
        challengers = [r["paper_id"] for r in con.execute(
            "SELECT paper_id FROM paper_theory WHERE theory_id=? AND stance='challenges' AND (status='active' OR status IS NULL)", (sc["theory_id"],))]
        if challengers and sc["net"] and sc["net"] >= ESTABLISHED_NET:
            sig = "theory:" + sc["theory_id"]
            con.execute(
                "INSERT INTO contradictions(contradiction_id,level,ref_id,ref_label,for_papers,against_papers,strength,status,opened_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?, 'open', ?, ?)"
                " ON CONFLICT(contradiction_id) DO UPDATE SET against_papers=excluded.against_papers, updated_at=excluded.updated_at",
                (sig, "theory", sc["theory_id"], sc["name"], "[]", json.dumps(challengers),
                 round(len(challengers) / max(1, sc["net"]), 2), db.now_iso(), db.now_iso()),
            )
            opened += 1
    con.commit()
    return opened


def map_refresh(con):
    # Полное обновление производных данных карты. Порядок важен: scorecard'ы должны
    # быть готовы до detect_contradictions (тот читает статусы теорий).
    refresh_scorecards(con)
    refresh_ledger(con)
    n = detect_contradictions(con)
    return n
