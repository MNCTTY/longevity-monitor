"""TF-IDF индекс косинусной похожести на чистой стандартной библиотеке (без зависимостей).

Бэкенд похожести по умолчанию. Достаточно хорош для поиска почти-дубликатов и
тематических «соседей» по тысячам абстрактов. Используется в src.analysis и
src.positioning. Для более качественного (семантического) сопоставления
перефразированных результатов можно подключить опциональный бэкенд SPECTER2 — см. README.
"""
import re
import math
from collections import Counter

# Токен: слово из букв/цифр (допускается дефис внутри), минимум 2 символа.
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-]+")

# Стоп-слова: частые служебные и «водяные» термины научных абстрактов, которые
# только зашумляют похожесть. Литерал не трогаем — это данные модели.
_STOP = set(
    """the a an and or of to in for on with by is are was were be been being this
    that these those we our their its it as at from into than then also can may
    using used use study results result show shows showed found via based between
    during both not no using here we report using significantly higher lower
    increase decrease associated compared""".split()
)


def tokenize(text):
    # Разбиваем текст на токены в нижнем регистре, отбрасывая стоп-слова и короткие (<=2).
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOP and len(t) > 2]


class TfidfIndex:
    def __init__(self):
        self.idf = {}
        self.docs = {}   # paper_id -> {term: вес}
        self.norms = {}  # paper_id -> норма вектора

    def fit(self, items):
        """Построить индекс. items: итерируемое из пар (paper_id, text)."""
        # df — в скольких документах встретился термин (document frequency).
        df = Counter()
        tfs = {}
        for pid, text in items:
            tf = Counter(tokenize(text))
            tfs[pid] = tf
            for term in tf:
                df[term] += 1
        n = max(1, len(tfs))
        # Сглаженный IDF: log((1+N)/(1+df)) + 1 — редкие термины весят больше.
        self.idf = {t: math.log((1 + n) / (1 + dfc)) + 1.0 for t, dfc in df.items()}
        for pid, tf in tfs.items():
            # Вектор документа: tf * idf по каждому термину, плюс евклидова норма.
            vec = {t: c * self.idf.get(t, 0.0) for t, c in tf.items()}
            self.docs[pid] = vec
            self.norms[pid] = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return self

    def _vectorize(self, text):
        # Превратить произвольный текст в tf-idf вектор запроса и его норму.
        tf = Counter(tokenize(text))
        vec = {t: c * self.idf.get(t, 0.0) for t, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return vec, norm

    def query(self, text, k=5, exclude=None):
        # Вернуть top-k документов по косинусной близости к тексту запроса.
        qvec, qnorm = self._vectorize(text)
        exclude = exclude or set()
        scores = []
        for pid, dvec in self.docs.items():
            if pid in exclude:
                continue
            # Скалярное произведение считаем перебором меньшего из векторов (оптимизация).
            if len(qvec) < len(dvec):
                dot = sum(qval * dvec.get(t, 0.0) for t, qval in qvec.items())
            else:
                dot = sum(dval * qvec.get(t, 0.0) for t, dval in dvec.items())
            if dot <= 0:
                continue  # нет общих терминов — пропускаем
            # Косинус = скалярное произведение / произведение норм.
            scores.append((pid, dot / (qnorm * self.norms[pid])))
        scores.sort(key=lambda x: -x[1])  # по убыванию похожести
        return scores[:k]
