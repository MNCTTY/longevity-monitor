"""Небольшой HTTP-помощник на стандартной библиотеке.

Даёт «вежливые» повторы запросов и ограничение частоты. Используется всеми
адаптерами источников (src.sources.*) вместо внешних зависимостей вроде requests.
"""
import json
import time
import urllib.parse
import urllib.request
import urllib.error

# User-Agent с контактным email — «вежливая» идентификация для публичных API.
DEFAULT_UA = "longevity-monitor/0.1 (mailto:karin.sadovs@gmail.com)"


def _request(url, timeout=30, headers=None):
    hdrs = {"User-Agent": DEFAULT_UA, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def get(url, params=None, timeout=30, retries=3, backoff=1.5, sleep=0.0, headers=None):
    # GET с повторами. params при наличии дописываются в query-строку.
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries):
        try:
            data = _request(url, timeout=timeout, headers=headers)
            if sleep:
                time.sleep(sleep)  # пауза между запросами — для соблюдения rate limit
            return data
        except urllib.error.HTTPError as e:
            last = e
            # Повторяем только на «временных» кодах (429 и 5xx); линейная задержка.
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(backoff * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            # Сетевые ошибки/таймаут — тоже повторяем с нарастающей паузой.
            last = e
            time.sleep(backoff * (attempt + 1))
    raise last  # исчерпали попытки — пробрасываем последнюю ошибку


def get_json(url, params=None, **kw):
    # Получить ответ и распарсить его как JSON.
    return json.loads(get(url, params=params, **kw).decode("utf-8", "replace"))


def get_text(url, params=None, **kw):
    # Получить ответ как текст (например, XML от PubMed).
    return get(url, params=params, **kw).decode("utf-8", "replace")
