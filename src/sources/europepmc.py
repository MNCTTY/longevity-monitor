"""Адаптер Europe PMC (единый источник: журналы + препринты + часть полных текстов).

Один REST-запрос покрывает и статьи, и препринты. Приводит записи к Paper (src.models).
Встраивается в src.cli.build_sources.
"""
from ..http import get_json
from ..models import Paper

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


class EuropePmcSource:
    name = "europepmc"

    def fetch(self, keywords, since, until, max_results=100):
        # Ключевые слова через OR (фразы — в кавычках) + фильтр по дате первой публикации.
        kw = " OR ".join(f'"{k}"' if " " in k else k for k in keywords)
        query = f"({kw}) AND (FIRST_PDATE:[{since.isoformat()} TO {until.isoformat()}])"
        params = {
            "query": query,
            "format": "json",
            "pageSize": min(max_results, 100),
            "resultType": "core",
            "sort": "P_PDATE_D desc",
        }
        data = get_json(BASE, params, sleep=0.2)
        out = []
        for r in data.get("resultList", {}).get("result", []):
            authors = []
            for a in (r.get("authorList", {}) or {}).get("author", []) or []:
                nm = a.get("fullName") or a.get("lastName") or ""
                if nm:
                    authors.append(nm)
            journal = ((r.get("journalInfo", {}) or {}).get("journal", {}) or {}).get("title", "") or ""
            doi = r.get("doi")
            sid = r.get("id") or r.get("pmid") or r.get("pmcid") or ""
            # URL: приоритет — страница по PMID, затем DOI, иначе пусто.
            if r.get("pmid"):
                url = f"https://europepmc.org/abstract/MED/{r['pmid']}"
            elif doi:
                url = f"https://doi.org/{doi}"
            else:
                url = ""
            out.append(Paper(
                source="europepmc", source_id=str(sid), doi=doi,
                title=r.get("title", "") or "", abstract=r.get("abstractText", "") or "",
                authors=authors, journal=journal,
                published=r.get("firstPublicationDate", "") or "", url=url,
                raw={"src": r.get("source"), "pubType": r.get("pubType")},
            ))
        return out
