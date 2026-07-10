"""Очередь ревью для спорных узлов и связей карты знаний.

Что попадает в очередь:
  - теории/посылки, заведённые из статей (source='paper-derived') или помеченные
    как provisional — их нужно либо подтвердить (canonical), либо отклонить (удалить,
    чистка фрагментации теорий);
  - связи paper->theory/premise со статусом 'needs_review' (слабое совпадение при
    канонизации) — подтвердить (active, получит вес) или отклонить (удалить).

Решения меняют статус узла/связи; после этого веса пересчитываются (map-refresh).
Вызывается из src.cli (команды review / review-approve / review-reject).
"""
from . import db, knowledge, positioning

REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS map_review (
    review_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT,          -- theory | premise | link_theory | link_premise
    paper_id   TEXT,          -- для связей; для узлов пусто
    ref_id     TEXT,          -- theory_id / premise_id
    label      TEXT,
    status     TEXT DEFAULT 'pending',
    created_at TEXT,
    UNIQUE(kind, paper_id, ref_id)
);
"""


def ensure_schema(con):
    knowledge.ensure_schema(con)
    con.executescript(REVIEW_SCHEMA)
    con.commit()


def refresh(con):
    """Пересобрать список ожидающих ревью из текущего состояния карты.

    Полная пересборка идемпотентна: подтверждённые (стали canonical/active) и
    отклонённые (удалены) элементы больше не подходят под условия и не вернутся.
    """
    ensure_schema(con)
    con.execute("DELETE FROM map_review")
    # Узлы-теории, заведённые из статей или провизорные.
    for r in con.execute(
        "SELECT theory_id, name FROM theories WHERE source='paper-derived' OR status='provisional'"
    ):
        n = con.execute("SELECT COUNT(*) c FROM paper_theory WHERE theory_id=?", (r["theory_id"],)).fetchone()["c"]
        _add(con, "theory", "", r["theory_id"], f"{r['name']}  (статей: {n})")
    # Узлы-посылки, заведённые из статей или провизорные.
    for r in con.execute(
        "SELECT premise_id, text FROM premises WHERE source='paper-derived' OR status='provisional'"
    ):
        _add(con, "premise", "", r["premise_id"], r["text"][:80])
    # Связи с needs_review.
    for r in con.execute(
        """SELECT pt.paper_id, pt.theory_id, pt.stance, t.name, p.title
           FROM paper_theory pt JOIN theories t ON t.theory_id=pt.theory_id
           JOIN papers p ON p.paper_id=pt.paper_id WHERE pt.status='needs_review'"""):
        _add(con, "link_theory", r["paper_id"], r["theory_id"],
             f"{(r['title'] or '')[:45]} --{r['stance']}--> {r['name']}")
    for r in con.execute(
        """SELECT pp.paper_id, pp.premise_id, pp.stance, pr.text, p.title
           FROM paper_premise pp JOIN premises pr ON pr.premise_id=pp.premise_id
           JOIN papers p ON p.paper_id=pp.paper_id WHERE pp.status='needs_review'"""):
        _add(con, "link_premise", r["paper_id"], r["premise_id"],
             f"{(r['title'] or '')[:45]} --{r['stance']}--> {(r['text'] or '')[:40]}")
    con.commit()
    return con.execute("SELECT COUNT(*) c FROM map_review WHERE status='pending'").fetchone()["c"]


def _add(con, kind, paper_id, ref_id, label):
    con.execute(
        "INSERT OR IGNORE INTO map_review(kind,paper_id,ref_id,label,status,created_at)"
        " VALUES(?,?,?,?, 'pending', ?)",
        (kind, paper_id, ref_id, label, db.now_iso()),
    )


def list_pending(con):
    ensure_schema(con)
    return con.execute(
        "SELECT * FROM map_review WHERE status='pending' ORDER BY kind, review_id"
    ).fetchall()


def _apply(con, row, approve):
    kind, ref, paper = row["kind"], row["ref_id"], row["paper_id"]
    if kind == "theory":
        if approve:
            con.execute("UPDATE theories SET status='canonical', source='curated' WHERE theory_id=?", (ref,))
        else:  # отклонить: удалить узел и все его связи
            con.execute("DELETE FROM paper_theory WHERE theory_id=?", (ref,))
            con.execute("DELETE FROM theory_relation WHERE src_theory_id=? OR dst_theory_id=?", (ref, ref))
            con.execute("DELETE FROM theories WHERE theory_id=?", (ref,))
    elif kind == "premise":
        if approve:
            con.execute("UPDATE premises SET status='canonical' WHERE premise_id=?", (ref,))
        else:
            con.execute("DELETE FROM paper_premise WHERE premise_id=?", (ref,))
            con.execute("DELETE FROM premises WHERE premise_id=?", (ref,))
    elif kind == "link_theory":
        if approve:
            con.execute("UPDATE paper_theory SET status='active' WHERE paper_id=? AND theory_id=?", (paper, ref))
        else:
            con.execute("DELETE FROM paper_theory WHERE paper_id=? AND theory_id=? AND status='needs_review'", (paper, ref))
    elif kind == "link_premise":
        if approve:
            con.execute("UPDATE paper_premise SET status='active' WHERE paper_id=? AND premise_id=?", (paper, ref))
        else:
            con.execute("DELETE FROM paper_premise WHERE paper_id=? AND premise_id=? AND status='needs_review'", (paper, ref))
    con.execute("UPDATE map_review SET status=? WHERE review_id=?",
                ("approved" if approve else "rejected", row["review_id"]))
    con.commit()


def decide(con, review_id, approve):
    """Применить решение к одному элементу (по id) или ко всем (review_id='all')."""
    ensure_schema(con)
    if str(review_id).lower() == "all":
        rows = list_pending(con)
    else:
        rows = con.execute("SELECT * FROM map_review WHERE review_id=? AND status='pending'", (int(review_id),)).fetchall()
    for row in rows:
        _apply(con, row, approve)
    positioning.map_refresh(con)  # веса/scorecard'ы/противоречия могли измениться
    return len(rows)
