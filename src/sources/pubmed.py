"""Адаптер PubMed через NCBI E-utilities (esearch + efetch).

esearch отдаёт список PMID по запросу, efetch — их полные XML-записи. Приводит
результат к общему формату Paper (src.models). Встраивается в src.cli.build_sources.
"""
import xml.etree.ElementTree as ET

from ..http import get_json, get_text
from ..models import Paper

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def build_term(keywords):
    # Собираем поисковый запрос PubMed: каждое ключевое слово ищется в Title/Abstract,
    # фразы (с пробелом) берём в кавычки; всё объединяем через OR.
    parts = []
    for k in keywords:
        parts.append(f'"{k}"[Title/Abstract]' if " " in k else f"{k}[Title/Abstract]")
    return "(" + " OR ".join(parts) + ")"


class PubMedSource:
    name = "pubmed"

    def __init__(self, email=None, api_key=None):
        self.email = email
        self.api_key = api_key
        # Без API-ключа NCBI разрешает ~3 запроса/с, с ключом — 10 запросов/с.
        # Отсюда пауза между запросами: 0.34с или 0.11с.
        self.sleep = 0.34 if not api_key else 0.11

    def _common(self):
        # Общие параметры (email/api_key) для всех вызовов E-utilities.
        p = {}
        if self.email:
            p["email"] = self.email
        if self.api_key:
            p["api_key"] = self.api_key
        return p

    def fetch(self, keywords, since, until, max_results=100):
        params = {
            "db": "pubmed",
            "term": build_term(keywords),
            "retmax": min(max_results, 200),
            "retmode": "json",
            "datetype": "pdat",
            "sort": "pub_date",
            "mindate": since.strftime("%Y/%m/%d"),
            "maxdate": until.strftime("%Y/%m/%d"),
        }
        params.update(self._common())
        # Шаг 1: esearch — получить список PMID по запросу за окно дат.
        data = get_json(ESEARCH, params, sleep=self.sleep)
        ids = data.get("esearchresult", {}).get("idlist", [])
        papers = []
        # Шаг 2: efetch — тянем полные записи пачками по 100 идентификаторов.
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            fp = {"db": "pubmed", "id": ",".join(chunk), "retmode": "xml"}
            fp.update(self._common())
            xml = get_text(EFETCH, fp, sleep=self.sleep)
            papers.extend(self._parse(xml))
        return papers

    def _parse(self, xml):
        # Разбор XML efetch в список Paper. Битый XML -> пустой список (не роняем прогон).
        out = []
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return out
        for art in root.findall(".//PubmedArticle"):
            pmid = art.findtext(".//PMID") or ""
            title_el = art.find(".//ArticleTitle")
            title = "".join(title_el.itertext()).strip() if title_el is not None else ""
            abstract = " ".join(
                "".join(n.itertext()).strip() for n in art.findall(".//Abstract/AbstractText")
            ).strip()
            journal = art.findtext(".//Journal/Title") or ""
            authors = []
            for a in art.findall(".//AuthorList/Author"):
                nm = " ".join(x for x in [a.findtext("ForeName"), a.findtext("LastName")] if x).strip()
                if nm:
                    authors.append(nm)
            # Берём DOI только из СОБСТВЕННЫХ идентификаторов статьи. Голый
            # `.//ArticleId` цепляет ещё и DOI из списка литературы — это давало бы
            # чужие DOI, поэтому сначала пробуем ELocationID, затем ArticleIdList.
            doi = None
            el = art.find(".//Article/ELocationID[@EIdType='doi']")
            if el is not None and el.text:
                doi = el.text
            if not doi:
                for idn in art.findall("./PubmedData/ArticleIdList/ArticleId"):
                    if idn.get("IdType") == "doi":
                        doi = idn.text
                        break
            out.append(Paper(
                source="pubmed", source_id=pmid, doi=doi, title=title,
                abstract=abstract, authors=authors, journal=journal,
                published=self._date(art),
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            ))
        return out

    def _date(self, art):
        # Дата публикации по возможности в ISO. PubMed отдаёт её неровно:
        # иногда есть только год (MedlineDate), иногда год+месяц, реже полный день.
        pd = art.find(".//Article/Journal/JournalIssue/PubDate") or art.find(".//PubDate")
        if pd is None:
            return ""
        y = pd.findtext("Year")
        if not y:
            # Года нет — пытаемся вытащить 4 цифры года из свободного MedlineDate.
            md = pd.findtext("MedlineDate") or ""
            return md[:4] if md[:4].isdigit() else ""
        mm = _MONTHS.get((pd.findtext("Month") or "")[:3].lower())
        d = pd.findtext("Day")
        try:
            if mm and d:
                return f"{int(y):04d}-{mm:02d}-{int(d):02d}"
            if mm:
                return f"{int(y):04d}-{mm:02d}-01"
            return f"{int(y):04d}-01-01"
        except ValueError:
            return f"{y}-01-01"
