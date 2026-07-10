"""Подключаемые суммаризаторы (два взаимозаменяемых бэкенда).

BatchSummarizer   — без API-ключа: выгружает пакеты анализа в JSON, чтобы их можно
                    было суммаризировать вручную или в сессии Claude Code, а затем
                    вернуть обратно через `cli import`.
AnthropicSummarizer — авто-режим: вызывает Claude API. Требует ANTHROPIC_API_KEY
                    и `pip install anthropic`.

Промпты (PROMPT_TEMPLATE / POSITION_PROMPT) намеренно на русском — это и есть
инструкции модели; их менять нельзя. Модуль вызывается из src.cli.
"""
import os
import json

PROMPT_TEMPLATE = """Ты — научный аналитик по биологии старения и продлению жизни.
Проанализируй статью и верни СТРОГО валидный JSON с ключами "summary", "key_findings", "comparison".

Статья:
Заголовок: {title}
Абстракт: {abstract}

Похожие статьи из базы (score = близость):
{neighbors}

Требования:
- summary: 2-3 предложения по-русски — в чём суть (что сделали и что нашли).
- key_findings: ключевые результаты списком (одна строка, пункты через "; ").
- comparison: как результат соотносится с похожими статьями — подтверждает, дополняет,
  противоречит или это новизна. Если список похожих пуст, честно укажи, что база пока пуста.
Верни только JSON, без markdown-обёртки."""


def _format_neighbors(neighbors):
    if not neighbors:
        return "(похожих статей в базе нет)"
    return "\n".join(
        f'- [{n["score"]}] {n["title"]}: {n["abstract"][:300]}' for n in neighbors
    )


def build_prompt(packet):
    # Подставляем поля статьи и её соседей в шаблон промпта суммаризации.
    return PROMPT_TEMPLATE.format(
        title=packet["title"],
        abstract=packet["abstract"],
        neighbors=_format_neighbors(packet["neighbors"]),
    )


POSITION_PROMPT = """Ты — научный аналитик по биологии старения. Помести статью в карту знаний.
Верни СТРОГО JSON: {{"paper_id","theory_positions":[...],"premise_evidence":[...]}}.

Статья:
Заголовок: {title}
Абстракт: {abstract}

Существующие теории-кандидаты (используй их id, если подходит; иначе theory_ref=null и укажи theory_name):
{theories}

Существующие посылки-кандидаты (используй premise_ref, если подходит; иначе null + premise_text):
{premises}

theory_positions[i] = {{"theory_ref","theory_name","stance" in [supports,challenges,extends,discusses,mentions],"strength" 0..1,"evidence_note"}}
premise_evidence[i] = {{"premise_ref","premise_text","stance" in [evidence_for,evidence_against,refines],"strength" 0..1,"taxonomic_scope","evidence_note"}}
Только реально затронутые теории/посылки. Верни только JSON."""


class BatchSummarizer:
    model = "manual"

    def dump(self, packets, path):
        # Ручной режим: просто выгружаем пакеты в JSON для внешней суммаризации.
        with open(path, "w", encoding="utf-8") as f:
            json.dump(packets, f, ensure_ascii=False, indent=2)
        return path


class AnthropicSummarizer:
    def __init__(self, api_key=None, model="claude-sonnet-5"):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])
        self.model = model

    def summarize(self, packet):
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": build_prompt(packet)}],
        )
        text = msg.content[0].text
        # Модель должна вернуть чистый JSON; на всякий случай вырезаем его между
        # первой '{' и последней '}'. Если распарсить не удалось — кладём текст в summary.
        try:
            data = json.loads(text[text.index("{"):text.rindex("}") + 1])
        except Exception:
            data = {"summary": text.strip(), "key_findings": "", "comparison": ""}
        return data

    def position(self, packet):
        prompt = POSITION_PROMPT.format(
            title=packet["title"], abstract=packet["abstract"],
            theories="\n".join(f'- {c["theory_id"]}: {c["name"]}' for c in packet["candidate_theories"]) or "(нет)",
            premises="\n".join(f'- {c["premise_id"]}: {c["text"][:120]}' for c in packet["candidate_premises"]) or "(нет)",
        )
        msg = self.client.messages.create(model=self.model, max_tokens=1500,
                                          messages=[{"role": "user", "content": prompt}])
        text = msg.content[0].text
        # Аналогично: вытаскиваем JSON-объект позиционирования; при сбое — пустой каркас.
        try:
            data = json.loads(text[text.index("{"):text.rindex("}") + 1])
        except Exception:
            data = {"paper_id": packet["paper_id"], "theory_positions": [], "premise_evidence": []}
        data["paper_id"] = packet["paper_id"]  # гарантируем правильный paper_id
        return data
