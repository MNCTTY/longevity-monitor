"""Таблица соответствия «теория ↔ статьи».

Показывает, какие статьи говорят про каждую теорию (с позицией статьи), и
обратный срез — статьи, обсуждающие сразу несколько теорий. Источник данных —
таблица связей paper_theory (именно она и есть сопоставление статья↔теория;
knowledge-map лишь агрегирует эти связи до уровня теорий).

Рендер — самодостаточный HTML (тема light/dark), <details> для навигации по
длинному списку. Вызывается из src.cli (команда theory-table).
"""
import os
import html
import datetime
from collections import defaultdict

STANCE_RU = {"supports": "поддерживает", "challenges": "оспаривает", "extends": "расширяет",
             "discusses": "обсуждает", "mentions": "упоминает", "neutral": "нейтрально"}
STANCE_COLOR = {"supports": "#16a34a", "challenges": "#dc2626", "extends": "#0d9488",
                "discusses": "#6b7280", "mentions": "#9ca3af", "neutral": "#9ca3af"}


def _e(s):
    return html.escape(str(s if s is not None else ""))


def build(con):
    """Собрать структуры: теория→статьи и статья→теории (только активные связи)."""
    rows = con.execute(
        """SELECT pt.theory_id, COALESCE(t.name_ru, t.name) AS tname, t.status AS tstatus,
                  pt.stance, p.paper_id, p.title, p.url, p.source
           FROM paper_theory pt
           JOIN theories t ON t.theory_id = pt.theory_id
           JOIN papers p ON p.paper_id = pt.paper_id
           WHERE (pt.status='active' OR pt.status IS NULL)
           ORDER BY pt.theory_id"""
    ).fetchall()
    by_theory = defaultdict(lambda: {"name": "", "status": "", "papers": []})
    paper_theories = defaultdict(set)   # paper_id -> {theory name}
    paper_meta = {}
    for r in rows:
        t = by_theory[r["theory_id"]]
        t["name"], t["status"] = r["tname"], r["tstatus"]
        t["papers"].append(dict(paper_id=r["paper_id"], title=r["title"], url=r["url"],
                                stance=r["stance"], source=r["source"]))
        paper_theories[r["paper_id"]].add(r["tname"])
        paper_meta[r["paper_id"]] = {"title": r["title"], "url": r["url"]}
    return by_theory, paper_theories, paper_meta


def render_html(con, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    by_theory, paper_theories, paper_meta = build(con)
    date = datetime.date.today().isoformat()
    theories = sorted(by_theory.values(), key=lambda t: -len(t["papers"]))
    n_links = sum(len(t["papers"]) for t in theories)
    multi = [pid for pid, ths in paper_theories.items() if len(ths) >= 2]

    L = [_STYLE, '<div class="ta">']
    L.append(f'<h1>Теории ↔ статьи</h1>')
    L.append(f'<p class="sub">Обновлено {date}. Всего связей статья–теория: <b>{n_links}</b> · '
             f'теорий: <b>{len(theories)}</b> · статей с несколькими теориями: <b>{len(multi)}</b>. '
             f'Раскрой теорию, чтобы увидеть её статьи.</p>')

    def stance_badge(s):
        return f'<span class="stb" style="--c:{STANCE_COLOR.get(s, "#6b7280")}">{_e(STANCE_RU.get(s, s))}</span>'

    L.append('<h2>По теориям</h2>')
    for t in theories:
        L.append(f'<details><summary><b>{_e(t["name"])}</b> — {len(t["papers"])} ст.'
                 f'<span class="tst">{_e(t["status"])}</span></summary><ul>')
        for p in t["papers"]:
            title = f'<a href="{_e(p["url"])}">{_e(p["title"])}</a>' if p["url"] else _e(p["title"])
            also = sorted(paper_theories[p["paper_id"]] - {t["name"]})
            also_html = f' <span class="also">также: {_e(", ".join(also))}</span>' if also else ""
            L.append(f'<li>{stance_badge(p["stance"])} {title}{also_html}</li>')
        L.append('</ul></details>')

    if multi:
        L.append('<h2>Статьи с несколькими теориями</h2>')
        L.append('<p class="sub">Одна статья может обсуждать несколько теорий — она привязана к каждой из них.</p>')
        # сортируем по числу теорий
        for pid in sorted(multi, key=lambda x: -len(paper_theories[x])):
            m = paper_meta[pid]
            title = f'<a href="{_e(m["url"])}">{_e(m["title"])}</a>' if m["url"] else _e(m["title"])
            tags = "".join(f'<span class="tag">{_e(x)}</span>' for x in sorted(paper_theories[pid]))
            L.append(f'<div class="mrow"><div class="mtitle">{title}</div><div class="mtags">{tags}</div></div>')

    L.append('</div>')
    path = os.path.join(out_dir, "theory-articles.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(L))
    return path, len(theories), n_links, len(multi)


_STYLE = r"""<style>
  :root{--bg:#fff;--panel:#f6f7f9;--ink:#1a1d21;--muted:#6b7280;--line:#e5e7eb;--accent:#4f6bed}
  @media (prefers-color-scheme:dark){:root{--bg:#0f1216;--panel:#171b21;--ink:#e8eaed;--muted:#9aa4b2;--line:#242a32;--accent:#7c93f2}}
  :root[data-theme="light"]{--bg:#fff;--panel:#f6f7f9;--ink:#1a1d21;--muted:#6b7280;--line:#e5e7eb;--accent:#4f6bed}
  :root[data-theme="dark"]{--bg:#0f1216;--panel:#171b21;--ink:#e8eaed;--muted:#9aa4b2;--line:#242a32;--accent:#7c93f2}
  .ta{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:var(--ink);background:var(--bg);
      line-height:1.5;padding:20px;max-width:100%;box-sizing:border-box}
  .ta *{box-sizing:border-box}
  .ta h1{font-size:20px;margin:0 0 4px;font-weight:700}
  .ta h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:24px 0 10px;font-weight:600}
  .ta .sub{color:var(--muted);font-size:13px;margin:0 0 12px}
  .ta a{color:var(--accent);text-decoration:none}
  .ta a:hover{text-decoration:underline}
  details{border:1px solid var(--line);border-radius:8px;margin-bottom:6px;background:var(--panel)}
  summary{cursor:pointer;padding:9px 12px;font-size:14px;list-style:none;display:flex;align-items:center;gap:8px}
  summary::-webkit-details-marker{display:none}
  summary::before{content:"▸";color:var(--muted);font-size:12px}
  details[open] summary::before{content:"▾"}
  .tst{font-size:11px;color:var(--muted);margin-left:auto;text-transform:none;letter-spacing:0}
  details ul{margin:0;padding:2px 14px 12px 32px;list-style:none}
  details li{font-size:13px;padding:4px 0;border-top:1px solid var(--line)}
  .stb{font-size:10.5px;color:#fff;background:var(--c);border-radius:5px;padding:1px 6px;margin-right:4px;white-space:nowrap}
  .also{font-size:11.5px;color:var(--muted)}
  .mrow{display:flex;gap:12px;align-items:baseline;padding:7px 0;border-bottom:1px solid var(--line);flex-wrap:wrap}
  .mtitle{flex:1;min-width:240px;font-size:13px}
  .mtags{display:flex;flex-wrap:wrap;gap:5px}
  .tag{font-size:11px;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:1px 9px}
</style>"""
