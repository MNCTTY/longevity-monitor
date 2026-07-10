"""Адаптер Semantic Scholar (поиск по релевантности + метаданные цитирования).

Без ключа доступ жёстко ограничен по частоте (частые HTTP 429). Бесплатный
API-ключ (https://www.semanticscholar.org/product/api) в config делает источник
надёжным. Без ключа источник «мягко» возвращает пустой результат, а не роняет
весь прогон. Приводит записи к Paper (src.models).
"""
from ..http import get_json
from ..models import Paper

BASE = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "title,abstract,year,authors,externalIds,url,venue,publicationDate"


class SemanticScholarSource:
    name = "semanticscholar"

    def __init__(self, api_key=None):
        self.api_key = api_key

    def fetch(self, keywords, since, until, max_results=100):
        # Берём первые 6 ключевых слов как единый запрос (у API есть лимит длины).
        query = " ".join(keywords[:6])
        params = {
            "query": query,
            "fields": FIELDS,
            "limit": min(max_results, 100),
            "year": f"{since.year}-{until.year}",
        }
        headers = {"x-api-key": self.api_key} if self.api_key else None
        # С ключом API быстрый; без ключа делаем паузу больше, чтобы реже ловить 429.
        sleep = 0.2 if self.api_key else 1.1
        try:
            data = get_json(BASE, params, sleep=sleep, retries=3, headers=headers)
        except Exception:
            return []  # «мягкая» деградация: не роняем весь прогон из-за одного источника
        out = []
        for r in data.get("data", []) or []:
            if not r.get("abstract"):
                continue  # без абстракта статья бесполезна для анализа/похожести
            ext = r.get("externalIds", {}) or {}
            authors = [a.get("name", "") for a in (r.get("authors") or []) if a.get("name")]
            published = r.get("publicationDate") or (str(r["year"]) if r.get("year") else "")
            out.append(Paper(
                source="semanticscholar", source_id=r.get("paperId", ""),
                doi=ext.get("DOI"), title=r.get("title", "") or "",
                abstract=r.get("abstract", "") or "", authors=authors,
                journal=r.get("venue", "") or "", published=published,
                url=r.get("url", "") or "",
            ))
        return out
