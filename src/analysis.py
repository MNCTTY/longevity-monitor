"""Поиск «соседей» и сборка пакета для анализа.

Строит TF-IDF индекс по статьям, находит ближайших соседей и упаковывает статью
вместе с ними в пакет для суммаризатора (src.summarize). Вызывается из src.cli.
"""
from .similarity.tfidf import TfidfIndex


def build_index(rows):
    # Индекс похожести по всем статьям; документ = "заголовок. абстракт".
    return TfidfIndex().fit([(r["paper_id"], f'{r["title"]}. {r["abstract"]}') for r in rows])


def neighbors_for(index, row, k=5):
    # top-k ближайших статей к данной; саму статью исключаем из выдачи.
    text = f'{row["title"]}. {row["abstract"]}'
    return index.query(text, k=k, exclude={row["paper_id"]})


def make_packet(row, neighbor_rows_with_scores):
    """Собрать пакет: статья + её top-k соседей — вход для суммаризатора."""
    return {
        "paper_id": row["paper_id"],
        "title": row["title"],
        "abstract": row["abstract"],
        "journal": row["journal"],
        "published": row["published"],
        "source": row["source"],
        "url": row["url"],
        "neighbors": [
            {
                "paper_id": nr["paper_id"],
                "title": nr["title"],
                "score": round(score, 3),
                "abstract": (nr["abstract"] or "")[:600],  # обрезаем, чтобы промпт не раздувался
                "url": nr["url"],
            }
            for nr, score in neighbor_rows_with_scores
        ],
    }
