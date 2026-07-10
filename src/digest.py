"""Формирование дайджестов в Markdown из проанализированных статей.

Два вида отчёта: обычный дайджест статей (render_markdown) и снимок карты знаний
(render_knowledge_digest). Экспорт в Notion — заглушка до авторизации коннектора.
Вызывается из src.cli (команда digest).
"""
import os
import json
import datetime


def render_markdown(con, out_dir, title=None):
    # Обычный дайджест: все проанализированные статьи, новые сверху (по analyzed_at).
    os.makedirs(out_dir, exist_ok=True)
    rows = con.execute(
        """
        SELECT p.*, a.summary, a.key_findings, a.comparison, a.neighbors, a.model, a.analyzed_at
        FROM analysis a JOIN papers p ON p.paper_id = a.paper_id
        ORDER BY a.analyzed_at DESC
        """
    ).fetchall()
    date = datetime.date.today().isoformat()
    title = title or f"Longevity & aging digest — {date}"
    lines = [f"# {title}", "", f"Проанализировано статей: **{len(rows)}**", ""]
    for r in rows:
        lines.append(f"## {r['title']}")
        meta = " · ".join(x for x in [r["journal"], r["published"], r["source"]] if x)
        if meta:
            lines.append(f"*{meta}*")
        if r["url"]:
            lines.append(f"[Источник]({r['url']})")
        lines.append("")
        lines.append(f"**Суть.** {r['summary']}")
        if r["key_findings"]:
            lines.append("")
            lines.append(f"**Ключевые результаты.** {r['key_findings']}")
        if r["comparison"]:
            lines.append("")
            lines.append(f"**Сравнение с базой.** {r['comparison']}")
        neigh = json.loads(r["neighbors"] or "[]")
        if neigh:
            lines.append("")
            lines.append("**Похожие статьи из базы:**")
            for n in neigh:
                link = f"[{n['title']}]({n['url']})" if n.get("url") else n["title"]
                lines.append(f"- `{n['score']}` {link}")
        lines.append("")
        lines.append("---")
        lines.append("")
    path = os.path.join(out_dir, f"digest-{date}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _titles(con, ids):
    # По списку paper_id вернуть заголовки (усечённые) для читаемого вывода.
    out = []
    for pid in ids:
        r = con.execute("SELECT title FROM papers WHERE paper_id=?", (pid,)).fetchone()
        out.append(r["title"][:70] if r and r["title"] else pid)
    return out


def render_knowledge_digest(con, out_dir):
    """Дайджест v2: снимок карты знаний — scorecard'ы теорий, посылки под пересмотром
    и открытые противоречия."""
    os.makedirs(out_dir, exist_ok=True)
    date = datetime.date.today().isoformat()
    L = [f"# Knowledge map — {date}", ""]

    scored = con.execute(
        "SELECT * FROM theory_scorecard WHERE support_w>0 OR challenge_w>0 ORDER BY net DESC, support_w DESC"
    ).fetchall()
    contested = [r for r in scored if r["status"] == "contested"]
    L.append(f"**Theories with evidence:** {len(scored)} · **contested:** {len(contested)}")
    L.append("")
    L.append("## Теории по накопленным доказательствам")
    L.append("")
    L.append("| Теория | Статус | support | challenge | статей |")
    L.append("|---|---|--:|--:|--:|")
    for r in scored:
        L.append(f"| {r['name']} | {r['status']} | {r['support_w']:.1f} | {r['challenge_w']:.1f} | {r['n_papers']} |")
    L.append("")

    ledger = con.execute("SELECT * FROM premise_ledger WHERE flag!='' ORDER BY ABS(drift) DESC").fetchall()
    if ledger:
        L.append("## Посылки под пересмотром")
        L.append("")
        for r in ledger:
            L.append(f"- **{r['text']}** — seed confidence {r['seed_confidence']:.1f} → "
                     f"evidence {r['evidence_confidence']:.1f} (drift {r['drift']:+.1f}, "
                     f"for {r['n_for']} / against {r['n_against']}) — _{r['flag']}_")
        L.append("")

    contras = con.execute("SELECT * FROM contradictions WHERE status='open' ORDER BY strength DESC").fetchall()
    L.append(f"## Открытые противоречия ({len(contras)})")
    L.append("")
    for r in contras:
        fp = _titles(con, json.loads(r["for_papers"] or "[]"))
        ap = _titles(con, json.loads(r["against_papers"] or "[]"))
        L.append(f"### [{r['level']}] {r['ref_label']}  · strength {r['strength']}")
        if fp:
            L.append(f"- **За ({len(fp)}):** " + "; ".join(fp))
        if ap:
            L.append(f"- **Против ({len(ap)}):** " + "; ".join(ap))
        L.append("")

    provisional_t = con.execute("SELECT name FROM theories WHERE status='provisional'").fetchall()
    provisional_p = con.execute("SELECT text FROM premises WHERE status='provisional'").fetchall()
    if provisional_t or provisional_p:
        L.append("## Новые (provisional) узлы — ждут подтверждения/ревью")
        L.append("")
        for r in provisional_t:
            L.append(f"- теория: {r['name']}")
        for r in provisional_p:
            L.append(f"- посылка: {r['text'][:100]}")
        L.append("")

    path = os.path.join(out_dir, f"knowledge-map-{date}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return path


def export_notion(con, **kw):
    raise NotImplementedError(
        "Notion-экспорт появится после авторизации коннектора Notion "
        "(claude.ai → Settings → Connectors). Пока используется Markdown."
    )
