"""Единая нормализованная запись публикации для всех источников.

Класс Paper — общий формат, к которому адаптеры (src.sources.*) приводят статьи
из разных API; отсюда данные попадают в хранилище (src.db).
"""
import re
import json
import hashlib
from dataclasses import dataclass, field


def normalize_doi(doi):
    # Приводим DOI к канону: нижний регистр, без префикса https://doi.org/ и "doi:".
    if not doi:
        return None
    doi = doi.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    doi = doi.replace("doi:", "").strip()
    return doi or None


def make_paper_id(source, source_id, doi):
    # Стабильный идентификатор статьи. Приоритет — DOI (кросс-источниковый дедуп);
    # иначе source:source_id; в крайнем случае — хэш от источника.
    doi = normalize_doi(doi)
    if doi:
        return "doi:" + doi
    if source_id:
        return f"{source}:{source_id}"
    return "hash:" + hashlib.sha1(source.encode()).hexdigest()[:16]


@dataclass
class Paper:
    source: str
    source_id: str = ""
    doi: str = None
    title: str = ""
    abstract: str = ""
    authors: list = field(default_factory=list)
    journal: str = ""
    published: str = ""  # дата в формате ISO, по возможности (best-effort)
    url: str = ""
    raw: dict = field(default_factory=dict)  # сырой ответ источника (для отладки)

    @property
    def paper_id(self):
        # Идентификатор считается «на лету» из source/source_id/doi.
        return make_paper_id(self.source, self.source_id, self.doi)

    def to_row(self):
        # Плоский dict для вставки в таблицу papers; списки/словари — в JSON.
        return {
            "paper_id": self.paper_id,
            "source": self.source,
            "source_id": self.source_id or "",
            "doi": normalize_doi(self.doi),
            "title": (self.title or "").strip(),
            "abstract": (self.abstract or "").strip(),
            "authors": json.dumps(self.authors, ensure_ascii=False),
            "journal": self.journal or "",
            "published": self.published or "",
            "url": self.url or "",
            "raw": json.dumps(self.raw, ensure_ascii=False),
        }

    def text_for_similarity(self):
        # Текст для TF-IDF-похожести: заголовок + абстракт.
        return f"{self.title}. {self.abstract}".strip()
