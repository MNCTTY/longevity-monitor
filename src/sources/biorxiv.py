"""Адаптер препринтов bioRxiv / medRxiv.

У API bioRxiv нет серверного поиска по ключевым словам: он отдаёт ВСЕ препринты за
окно дат. Поэтому мы листаем окно постранично и фильтруем локально по совпадению
ключевых слов в заголовке + абстракте, ограничивая число страниц (чтобы быть
«вежливыми»). Приводит записи к Paper (src.models).
"""
from ..http import get_json
from ..models import Paper


def _kw_match(text, keywords):
    # Локальный фильтр: есть ли хотя бы одно ключевое слово в тексте (без учёта регистра).
    t = (text or "").lower()
    return any(k.lower() in t for k in keywords)


class BiorxivSource:
    name = "biorxiv"

    def __init__(self, servers=("biorxiv", "medrxiv"), max_pages=8):
        self.servers = servers        # какие препринт-серверы опрашивать
        self.max_pages = max_pages    # верхний предел страниц на сервер (вежливость)

    def fetch(self, keywords, since, until, max_results=100):
        # Обходим все серверы и объединяем результаты.
        out = []
        for server in self.servers:
            out.extend(self._fetch_server(server, keywords, since, until, max_results))
        return out

    def _fetch_server(self, server, keywords, since, until, max_results):
        base = f"https://api.biorxiv.org/details/{server}/{since.isoformat()}/{until.isoformat()}"
        collected = []
        cursor = 0
        for _ in range(self.max_pages):
            try:
                data = get_json(f"{base}/{cursor}", sleep=0.25)
            except Exception:
                break  # ошибка запроса — прекращаем листать этот сервер
            coll = data.get("collection", []) or []
            if not coll:
                break  # пустая страница — конец окна
            for r in coll:
                if _kw_match(f"{r.get('title', '')} {r.get('abstract', '')}", keywords):
                    doi = r.get("doi")
                    collected.append(Paper(
                        source=server,
                        source_id=doi or (r.get("title", "")[:60]),
                        doi=doi,
                        title=r.get("title", "") or "",
                        abstract=r.get("abstract", "") or "",
                        authors=[a.strip() for a in (r.get("authors", "") or "").split(";") if a.strip()],
                        journal=server,
                        published=r.get("date", "") or "",
                        url=f"https://doi.org/{doi}" if doi else "",
                        raw={"category": r.get("category")},
                    ))
            # Сколько всего записей в окне (для условия остановки пагинации).
            try:
                total = int((data.get("messages") or [{}])[0].get("total", 0) or 0)
            except (ValueError, TypeError):
                total = 0
            cursor += len(coll)
            # Останавливаемся, когда прошли всё окно или набрали достаточно совпадений.
            if (total and cursor >= total) or len(collected) >= max_results:
                break
        return collected[:max_results]
