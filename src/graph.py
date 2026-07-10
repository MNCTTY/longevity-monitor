"""Читаемая визуализация карты знаний (самодостаточный HTML).

Не «волосяной шар» из физики, а информационный дашборд, где каждое значение
подписано:
  - рейтинг теорий с дивержентными полосами поддержка(зелёная)/вызовы(красная);
  - компактный круговой граф связей между теориями (мосты);
  - блок открытых противоречий;
  - посылки с динамической уверенностью (evidence vs seed).

Рендер — статический SVG+CSS (тема light/dark), без внешних зависимостей и без
физики: позиции считаются здесь, в Python. Можно открыть локально или
опубликовать как artifact. Вызывается из src.cli (команда graph).
"""
import os
import math
import html
import datetime

from . import positioning

# Семантические цвета статусов (одинаковые в обеих темах — это данные, не фон).
STATUS_COLOR = {
    "established": "#16a34a",
    "contested": "#f59e0b",
    "emerging": "#3b82f6",
    "provisional": "#9ca3af",
    "premise": "#a855f7",
}
STATUS_RU = {"established": "устоявшаяся", "contested": "спорная",
             "emerging": "формирующаяся", "provisional": "черновая", "unlinked": "без связей"}


def _e(s):
    return html.escape(str(s if s is not None else ""))


def build_data(con):
    positioning.refresh_scorecards(con)
    positioning.refresh_ledger(con)

    theories = con.execute(
        "SELECT s.theory_id, t.status AS node_status, s.name, s.status, s.support_w, s.challenge_w, "
        "s.attention, s.n_papers FROM theory_scorecard s JOIN theories t ON t.theory_id=s.theory_id "
        "WHERE s.n_papers > 0 ORDER BY s.support_w DESC, s.n_papers DESC"
    ).fetchall()

    bridges = con.execute(
        "SELECT src_theory_id, dst_theory_id, relation, weight FROM theory_relation "
        "WHERE weight >= 1 ORDER BY weight DESC"
    ).fetchall()

    contradictions = con.execute(
        "SELECT level, ref_label, for_papers, against_papers, strength FROM contradictions "
        "WHERE status='open' ORDER BY strength DESC"
    ).fetchall()

    premises = con.execute(
        "SELECT text, seed_confidence, evidence_confidence, n_for, n_against, flag "
        "FROM premise_ledger WHERE n_for>0 OR n_against>0 ORDER BY (n_for+n_against) DESC"
    ).fetchall()

    return theories, bridges, contradictions, premises


def _title(con, ids_json):
    import json
    out = []
    for pid in json.loads(ids_json or "[]"):
        r = con.execute("SELECT title FROM papers WHERE paper_id=?", (pid,)).fetchone()
        out.append((r["title"] if r and r["title"] else pid)[:60])
    return out


# ------------------------------------------------------------- компоненты ---
def _theory_bars(theories):
    """Дивержентные полосы: влево — вызовы (красное), вправо — поддержка (зелёное)."""
    max_ev = max([1.0] + [(t["support_w"] or 0) + (t["challenge_w"] or 0) for t in theories])
    rows = []
    for t in theories[:18]:
        sup, chal = t["support_w"] or 0, t["challenge_w"] or 0
        node_status = "provisional" if t["node_status"] == "provisional" else t["status"]
        col = STATUS_COLOR.get(node_status, "#3b82f6")
        sup_pct = sup / max_ev * 50
        chal_pct = chal / max_ev * 50
        rows.append(f"""
      <div class="row">
        <div class="tname" title="{_e(t['name'])}">{_e(t['name'])}</div>
        <div class="bar">
          <div class="half left"><div class="fill chal" style="width:{chal_pct:.1f}%"></div></div>
          <div class="axis"></div>
          <div class="half right"><div class="fill sup" style="width:{sup_pct:.1f}%"></div></div>
        </div>
        <div class="nums"><span class="c">{chal:.1f}</span>&nbsp;/&nbsp;<span class="s">{sup:.1f}</span></div>
        <div class="pill" style="--c:{col}">{_e(STATUS_RU.get(node_status, node_status))}</div>
        <div class="pcount">{t['n_papers']}</div>
      </div>""")
    return "".join(rows)


def _bridge_graph(bridges):
    """Компактный круговой граф связей между теориями. Позиции считаем тут."""
    if not bridges:
        return "<p class='muted'>Пока нет накопленных связей между теориями.</p>"
    # уникальные узлы, участвующие в мостах (ограничим, чтобы читалось)
    deg = {}
    for b in bridges:
        deg[b["src_theory_id"]] = deg.get(b["src_theory_id"], 0) + 1
        deg[b["dst_theory_id"]] = deg.get(b["dst_theory_id"], 0) + 1
    node_ids = [n for n, _ in sorted(deg.items(), key=lambda kv: -kv[1])][:14]
    idset = set(node_ids)
    edges = [b for b in bridges if b["src_theory_id"] in idset and b["dst_theory_id"] in idset]
    labels = {b["src_theory_id"]: b["src_theory_id"] for b in bridges}  # fallback
    # имена из theory_id: берём часть после 'th:'
    def nm(tid):
        return tid.split("th:")[-1].replace("-", " ")
    W, H, cx, cy, R = 680, 520, 340, 250, 185
    pos = {}
    n = len(node_ids)
    for i, tid in enumerate(node_ids):
        ang = -math.pi / 2 + 2 * math.pi * i / max(1, n)
        pos[tid] = (cx + R * math.cos(ang), cy + R * math.sin(ang), math.cos(ang))
    max_w = max([1] + [e["weight"] or 1 for e in edges])
    svg = [f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet" class="bridgesvg">']
    for e in edges:
        x1, y1, _ = pos[e["src_theory_id"]]
        x2, y2, _ = pos[e["dst_theory_id"]]
        op = 0.25 + 0.5 * ((e["weight"] or 1) / max_w)
        wdt = 1 + 2 * ((e["weight"] or 1) / max_w)
        svg.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
                   f'stroke="var(--edge)" stroke-width="{wdt:.1f}" stroke-opacity="{op:.2f}"/>')
    for tid in node_ids:
        x, y, cosang = pos[tid]
        r = 5 + min(9, deg[tid] * 1.6)
        anchor = "start" if cosang >= 0 else "end"
        lx = x + (12 if cosang >= 0 else -12)
        svg.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r:.0f}" fill="var(--accent)" '
                   f'stroke="var(--bg)" stroke-width="2"/>')
        svg.append(f'<text x="{lx:.0f}" y="{y+3:.0f}" text-anchor="{anchor}" class="glabel">{_e(nm(tid))}</text>')
    svg.append("</svg>")
    return "".join(svg)


def _contradictions(con, contradictions):
    if not contradictions:
        return "<p class='muted'>Открытых противоречий нет.</p>"
    cards = []
    for c in contradictions:
        forp = _title(con, c["for_papers"])
        againstp = _title(con, c["against_papers"])
        lvl = "посылка" if c["level"] == "premise" else "теория"
        fa = "".join(f"<li>{_e(x)}</li>" for x in forp) or "<li class='muted'>—</li>"
        ag = "".join(f"<li>{_e(x)}</li>" for x in againstp) or "<li class='muted'>—</li>"
        cards.append(f"""
      <div class="ccard">
        <div class="chead"><span class="ctag">{lvl}</span> {_e(c['ref_label'])}
          <span class="cstr" title="сила противоречия 0..1">напряжённость {c['strength']:.2f}</span></div>
        <div class="csides">
          <div class="side for"><div class="slab">за ({len(forp)})</div><ul>{fa}</ul></div>
          <div class="side ag"><div class="slab">против ({len(againstp)})</div><ul>{ag}</ul></div>
        </div>
      </div>""")
    return "".join(cards)


def _premises(premises):
    if not premises:
        return "<p class='muted'>Нет посылок с накопленными доказательствами.</p>"
    rows = []
    for p in premises[:12]:
        ev = (p["evidence_confidence"] or 0) / 5 * 100
        seed = (p["seed_confidence"] or 0) / 5 * 100
        flag = ""
        if p["flag"] == "under_revision":
            flag = "<span class='flag'>под пересмотром</span>"
        elif p["flag"] == "sign_flip":
            flag = "<span class='flag flip'>смена знака</span>"
        rows.append(f"""
      <div class="prow">
        <div class="ptext">{_e(p['text'])}{flag}</div>
        <div class="pmeter" title="seed {p['seed_confidence']:.1f} → evidence {p['evidence_confidence']:.1f} (из 5)">
          <div class="seedmark" style="left:{seed:.0f}%"></div>
          <div class="pfill" style="width:{ev:.0f}%"></div>
        </div>
        <div class="pfa">за {p['n_for']} / против {p['n_against']}</div>
      </div>""")
    return "".join(rows)


def render_html(con, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    theories, bridges, contradictions, premises = build_data(con)
    date = datetime.date.today().isoformat()
    n_theory = len([t for t in theories])
    n_contra = len(contradictions)
    n_papers = con.execute("SELECT COUNT(DISTINCT paper_id) c FROM paper_theory "
                           "WHERE source IN ('batch','auto-llm')").fetchone()["c"]

    body = _TEMPLATE.format(
        date=_e(date), n_theory=n_theory, n_papers=n_papers, n_contra=n_contra,
        n_bridges=len(bridges),
        theory_bars=_theory_bars(theories),
        bridge_graph=_bridge_graph(bridges),
        contradictions=_contradictions(con, contradictions),
        premises=_premises(premises),
    )
    path = os.path.join(out_dir, "graph.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path, n_theory, len(bridges)


_TEMPLATE = r"""<style>
  :root{{
    --bg:#ffffff; --panel:#f6f7f9; --ink:#1a1d21; --muted:#6b7280; --line:#e5e7eb;
    --accent:#4f6bed; --edge:#94a3b8; --sup:#16a34a; --chal:#dc2626; --seed:#111827;
  }}
  @media (prefers-color-scheme:dark){{
    :root{{ --bg:#0f1216; --panel:#171b21; --ink:#e8eaed; --muted:#9aa4b2; --line:#242a32;
            --accent:#7c93f2; --edge:#5b6672; --seed:#e8eaed; }}
  }}
  :root[data-theme="light"]{{ --bg:#ffffff; --panel:#f6f7f9; --ink:#1a1d21; --muted:#6b7280;
    --line:#e5e7eb; --accent:#4f6bed; --edge:#94a3b8; --seed:#111827; }}
  :root[data-theme="dark"]{{ --bg:#0f1216; --panel:#171b21; --ink:#e8eaed; --muted:#9aa4b2;
    --line:#242a32; --accent:#7c93f2; --edge:#5b6672; --seed:#e8eaed; }}

  .km{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:var(--ink);
       background:var(--bg);line-height:1.45;padding:20px;max-width:100%;box-sizing:border-box}}
  .km *{{box-sizing:border-box}}
  .km h1{{font-size:20px;margin:0 0 2px;font-weight:700;letter-spacing:-.01em}}
  .km h2{{font-size:14px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
          margin:26px 0 12px;font-weight:600}}
  .km .sub{{color:var(--muted);font-size:13px;margin:0 0 16px}}
  .km .muted{{color:var(--muted);font-size:13px}}
  .chips{{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0 4px}}
  .chip{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 12px}}
  .chip b{{font-size:19px;display:block;font-variant-numeric:tabular-nums}}
  .chip span{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}}
  .hint{{font-size:12px;color:var(--muted);margin:-4px 0 10px}}

  /* Полосы теорий */
  .row{{display:grid;grid-template-columns:190px 1fr 74px 108px 34px;align-items:center;gap:10px;
        padding:5px 0;border-bottom:1px solid var(--line)}}
  .tname{{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .bar{{display:flex;align-items:center;height:16px}}
  .half{{width:50%;height:10px;display:flex}}
  .half.left{{justify-content:flex-end}}
  .fill{{height:100%;border-radius:3px}}
  .fill.sup{{background:var(--sup)}}
  .fill.chal{{background:var(--chal)}}
  .axis{{width:1px;height:16px;background:var(--line)}}
  .nums{{font-size:12px;text-align:right;font-variant-numeric:tabular-nums}}
  .nums .c{{color:var(--chal)}} .nums .s{{color:var(--sup)}}
  .pill{{font-size:11px;color:#fff;background:var(--c);border-radius:20px;padding:2px 9px;
         text-align:center;justify-self:start;white-space:nowrap}}
  .pcount{{font-size:12px;color:var(--muted);text-align:right;font-variant-numeric:tabular-nums}}

  /* Граф мостов */
  .bridgesvg{{width:100%;height:auto;max-height:60vh;display:block;background:var(--panel);
              border:1px solid var(--line);border-radius:10px}}
  .glabel{{font-size:10.5px;fill:var(--ink);font-family:system-ui,sans-serif}}

  /* Противоречия */
  .ccard{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:10px}}
  .chead{{font-size:13.5px;font-weight:600;display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
  .ctag{{font-size:11px;background:var(--accent);color:#fff;border-radius:5px;padding:1px 7px;font-weight:600}}
  .cstr{{margin-left:auto;font-size:11px;color:var(--muted);font-weight:500}}
  .csides{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px}}
  .side{{border-radius:8px;padding:8px 10px;background:var(--bg);border:1px solid var(--line)}}
  .slab{{font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;font-weight:600}}
  .side.for .slab{{color:var(--sup)}} .side.ag .slab{{color:var(--chal)}}
  .side ul{{margin:0;padding-left:16px;font-size:12px}} .side li{{margin:2px 0}}

  /* Посылки */
  .prow{{display:grid;grid-template-columns:1fr 180px 120px;align-items:center;gap:12px;
         padding:6px 0;border-bottom:1px solid var(--line)}}
  .ptext{{font-size:13px}}
  .flag{{font-size:10px;background:var(--chal);color:#fff;border-radius:4px;padding:1px 6px;margin-left:6px}}
  .flag.flip{{background:var(--accent)}}
  .pmeter{{position:relative;height:9px;background:var(--line);border-radius:5px}}
  .pfill{{position:absolute;left:0;top:0;height:100%;background:var(--accent);border-radius:5px}}
  .seedmark{{position:absolute;top:-3px;width:2px;height:15px;background:var(--seed);opacity:.6}}
  .pfa{{font-size:12px;color:var(--muted);text-align:right;font-variant-numeric:tabular-nums}}
  .legend{{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--muted);margin:6px 0 2px}}
  .legend span{{display:inline-flex;align-items:center;gap:6px}}
  .dot{{width:10px;height:10px;border-radius:50%;display:inline-block}}
</style>
<div class="km">
  <h1>Карта знаний по биологии старения</h1>
  <p class="sub">Обновлено {date}. Статьи связаны с теориями старения и эмпирическими посылками; здесь — накопленный баланс доказательств.</p>
  <div class="chips">
    <div class="chip"><b>{n_theory}</b><span>теорий с доказательствами</span></div>
    <div class="chip"><b>{n_papers}</b><span>статей позиционировано</span></div>
    <div class="chip"><b>{n_bridges}</b><span>связей теория–теория</span></div>
    <div class="chip"><b>{n_contra}</b><span>открытых противоречий</span></div>
  </div>

  <h2>Теории по накопленным доказательствам</h2>
  <div class="hint">Полоса: влево (красное) — сколько статей <b>оспаривают</b> теорию, вправо (зелёное) — <b>поддерживают</b> (с учётом уверенности статьи). Справа — статус и число статей.</div>
  <div class="legend">
    <span><i class="dot" style="background:#16a34a"></i>устоявшаяся</span>
    <span><i class="dot" style="background:#f59e0b"></i>спорная</span>
    <span><i class="dot" style="background:#3b82f6"></i>формирующаяся</span>
    <span><i class="dot" style="background:#9ca3af"></i>черновая</span>
  </div>
  {theory_bars}

  <h2>Связи между теориями</h2>
  <div class="hint">Точки — теории (крупнее = больше связей), линии — статьи, обсуждающие обе теории сразу («мосты»). Толще линия — больше таких статей.</div>
  {bridge_graph}

  <h2>Открытые противоречия</h2>
  <div class="hint">Статьи по одной посылке/теории с противоположными позициями. Напряжённость — насколько силы сторон сопоставимы (1.0 = поровну).</div>
  {contradictions}

  <h2>Посылки: уверенность</h2>
  <div class="hint">Полоса — текущая уверенность по доказательствам (из 5). Вертикальная метка — исходная (seed) уверенность; расхождение = посылка сдвигается.</div>
  {premises}
</div>"""
