"""Обогащение curated-статей настоящими абстрактами.

В кураторской таблице у статей нет абстрактов — при импорте в поле abstract
кладётся краткое summary (или пусто). Этот модуль дотягивает НАСТОЯЩИЙ абстракт
из Europe PMC по DOI (а при его отсутствии — по названию) и обновляет
papers.abstract. Кураторское summary остаётся нетронутым в таблице analysis.

Вызывается из src.cli (команда enrich-abstracts). Зависит только от src.http.
"""
import re

from .http import get_json

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def _norm(s):
    # Грубая нормализация названия для сверки (нижний регистр, только буквы/цифры).
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def fetch_abstract(doi=None, title=None):
    """Вернуть (abstract, source) из Europe PMC. Приоритет — точный поиск по DOI."""
    # 1) по DOI — самый надёжный путь.
    if doi:
        try:
            data = get_json(BASE, {"query": f'DOI:"{doi}"', "format": "json",
                                   "resultType": "core", "pageSize": 1}, sleep=0.2)
            res = data.get("resultList", {}).get("result", [])
            if res and res[0].get("abstractText"):
                return res[0]["abstractText"], "europepmc:doi"
        except Exception:
            pass
    # 2) по названию — принимаем результат, только если название совпадает достаточно точно.
    if title:
        try:
            data = get_json(BASE, {"query": f'TITLE:"{title}"', "format": "json",
                                   "resultType": "core", "pageSize": 3}, sleep=0.2)
            want = _norm(title)
            for r in data.get("resultList", {}).get("result", []):
                if r.get("abstractText") and _norm(r.get("title")) == want:
                    return r["abstractText"], "europepmc:title"
        except Exception:
            pass
    return "", ""


def enrich_curated(con, limit=0, verbose=True):
    """Обновить абстракты curated-статей. Обновляем только если нашли реальный
    и достаточно длинный текст (короткие/пустые ответы игнорируем)."""
    rows = con.execute(
        "SELECT paper_id, doi, title, abstract FROM papers WHERE source='curated' ORDER BY title"
    ).fetchall()
    if limit:
        rows = rows[:limit]
    stats = {"checked": 0, "updated": 0, "not_found": 0}
    for r in rows:
        stats["checked"] += 1
        abstract, src = fetch_abstract(r["doi"], r["title"])
        # Обновляем, только если абстракт реальный и длиннее текущего placeholder.
        if abstract and len(abstract) > 120 and len(abstract) > len(r["abstract"] or ""):
            con.execute("UPDATE papers SET abstract=? WHERE paper_id=?", (abstract, r["paper_id"]))
            stats["updated"] += 1
            if verbose:
                print(f"  + {r['title'][:60]}  ({src})")
        else:
            stats["not_found"] += 1
    con.commit()
    return stats
