"""Точка входа командной строки для монитора долголетия.

Связывает вместе все модули: источники (src.sources), хранилище (src.db),
анализ/похожесть (src.analysis), суммаризацию (src.summarize), граф знаний
(src.knowledge, src.positioning) и дайджесты (src.digest).

Использование (из корня проекта):
    python -m src.cli init
    python -m src.cli backfill --days 180 --max 100
    python -m src.cli run
    python -m src.cli prepare [--limit N]      # write data/to_analyze.json
    python -m src.cli import data/analyzed.json
    python -m src.cli autoanalyze [--limit N]  # needs ANTHROPIC_API_KEY
    python -m src.cli digest
    python -m src.cli stats
"""
import os
import sys
import json
import argparse
import datetime

from . import db, analysis, digest, knowledge, positioning, enrich, review, graph, translations, report
from .sources.pubmed import PubMedSource
from .sources.europepmc import EuropePmcSource
from .sources.biorxiv import BiorxivSource
from .sources.semanticscholar import SemanticScholarSource
from .summarize import BatchSummarizer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корень проекта


def load_config(path=None):
    # Читаем config.json (по умолчанию из корня проекта).
    path = path or os.path.join(ROOT, "config.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_sources(cfg):
    # Собираем список источников по флагам "enabled" из конфига.
    s = []
    sc = cfg["sources"]
    if sc.get("pubmed", {}).get("enabled"):
        s.append(PubMedSource(email=sc["pubmed"].get("email"), api_key=sc["pubmed"].get("api_key")))
    if sc.get("europepmc", {}).get("enabled"):
        s.append(EuropePmcSource())
    if sc.get("biorxiv", {}).get("enabled"):
        s.append(BiorxivSource(
            servers=tuple(sc["biorxiv"].get("servers", ["biorxiv", "medrxiv"])),
            max_pages=sc["biorxiv"].get("max_pages", 8),
        ))
    if sc.get("semanticscholar", {}).get("enabled"):
        s.append(SemanticScholarSource(api_key=sc["semanticscholar"].get("api_key")))
    return s


def _dbpath(cfg):
    return os.path.join(ROOT, cfg["paths"]["db"])


def _fetch(cfg, since, until, max_per_source, only=None):
    # Общая логика сбора: опрашиваем источники за окно дат и складываем в БД.
    con = db.connect(_dbpath(cfg))
    total = 0
    for src in build_sources(cfg):
        if only and src.name not in only:
            continue  # ограничение по --sources
        try:
            papers = src.fetch(cfg["keywords"], since, until, max_per_source)
        except Exception as e:
            # Один упавший источник не должен рушить весь прогон.
            print(f"  [{src.name}] ERROR: {e}")
            continue
        added = db.upsert_papers(con, papers)
        print(f"  [{src.name}] fetched {len(papers)}, new {added}")
        total += added
    db.set_state(con, "last_fetch", db.now_iso())
    con.close()
    return total


def cmd_init(cfg, args):
    con = db.connect(_dbpath(cfg))
    print("DB ready:", _dbpath(cfg))
    con.close()


def cmd_backfill(cfg, args):
    until = datetime.date.today()
    since = until - datetime.timedelta(days=args.days)
    print(f"Backfill {since}..{until}  max/source={args.max}")
    print("Total new:", _fetch(cfg, since, until, args.max, only=args.sources))


def cmd_run(cfg, args):
    until = datetime.date.today()
    since = until - datetime.timedelta(days=cfg["limits"].get("run_days", 7))
    print(f"Run {since}..{until}")
    print("Total new:", _fetch(cfg, since, until, cfg["limits"].get("max_per_source", 100)))


def _prepare_packets(cfg, con, limit=0):
    # Готовим пакеты для суммаризации: непроанализированные статьи + их соседи.
    todo = db.unanalyzed_papers(con)
    if limit:
        todo = todo[:limit]
    base = db.all_papers(con)
    index = analysis.build_index(base)  # индекс похожести строим по всей базе
    by_id = {r["paper_id"]: r for r in base}
    k = cfg["similarity"].get("top_k", 5)
    packets = []
    for row in todo:
        nb = analysis.neighbors_for(index, row, k=k)
        nrows = [(by_id[pid], sc) for pid, sc in nb if pid in by_id]
        packets.append(analysis.make_packet(row, nrows))
    return packets


def cmd_prepare(cfg, args):
    con = db.connect(_dbpath(cfg))
    packets = _prepare_packets(cfg, con, args.limit)
    work = os.path.join(ROOT, cfg["paths"]["work"])
    os.makedirs(work, exist_ok=True)
    path = os.path.join(work, "to_analyze.json")
    BatchSummarizer().dump(packets, path)
    print(f"Prepared {len(packets)} packets -> {path}")
    con.close()


def cmd_import(cfg, args):
    con = db.connect(_dbpath(cfg))
    with open(args.file, encoding="utf-8") as f:
        results = json.load(f)
    for r in results:
        db.save_analysis(
            con, r["paper_id"], r.get("summary", ""), r.get("key_findings", ""),
            r.get("comparison", ""), r.get("neighbors", []), r.get("model", "manual"),
        )
    print(f"Imported {len(results)} analyses")
    con.close()


def cmd_autoanalyze(cfg, args):
    from .summarize import AnthropicSummarizer
    con = db.connect(_dbpath(cfg))
    packets = _prepare_packets(cfg, con, args.limit)
    summ = AnthropicSummarizer(model=cfg.get("llm", {}).get("model", "claude-sonnet-5"))
    for p in packets:
        res = summ.summarize(p)
        db.save_analysis(con, p["paper_id"], res.get("summary", ""), res.get("key_findings", ""),
                         res.get("comparison", ""), p["neighbors"], summ.model)
        print("  analyzed:", p["title"][:70])
    con.close()


def cmd_digest(cfg, args):
    con = db.connect(_dbpath(cfg))
    out = os.path.join(ROOT, cfg["paths"]["digests"])
    path = digest.render_markdown(con, out)
    print("Digest:", path)
    kg = digest.render_knowledge_digest(con, out)
    print("Knowledge map:", kg)
    con.close()


def cmd_import_curated(cfg, args):
    con = db.connect(_dbpath(cfg))
    path = args.file or os.path.join(ROOT, "data", "seed", "knowledge_seed.json")
    with open(path, encoding="utf-8") as f:
        seed = json.load(f)
    if args.reset:
        knowledge.reset(con)
        print("(reset knowledge graph)")
    stats = knowledge.import_seed(con, seed)
    print("Imported curated knowledge base:")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    con.close()


def _unpositioned(con):
    # Статьи с абстрактом, ещё не размещённые на карте (нет связей с source batch/auto-llm).
    return con.execute(
        """SELECT p.* FROM papers p
           LEFT JOIN paper_theory pt ON p.paper_id = pt.paper_id AND pt.source IN ('batch','auto-llm')
           WHERE length(p.abstract) > 0 AND pt.paper_id IS NULL
           ORDER BY p.published DESC""").fetchall()


def cmd_position_prepare(cfg, args):
    con = db.connect(_dbpath(cfg))
    knowledge.ensure_schema(con)
    todo = _unpositioned(con)
    if args.limit:
        todo = todo[:args.limit]
    packets = [positioning.build_position_packet(con, r) for r in todo]
    work = os.path.join(ROOT, cfg["paths"]["work"])
    os.makedirs(work, exist_ok=True)
    path = os.path.join(work, "to_position.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(packets, f, ensure_ascii=False, indent=2)
    print(f"Prepared {len(packets)} positioning packets -> {path}")
    con.close()


def cmd_position_import(cfg, args):
    con = db.connect(_dbpath(cfg))
    with open(args.file, encoding="utf-8") as f:
        results = json.load(f)
    stats = positioning.import_positioning(con, results, source="batch")
    print("Positioned:")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    n = positioning.map_refresh(con)
    print(f"Map refreshed. Open contradictions: {n}")
    con.close()


def cmd_autoposition(cfg, args):
    from .summarize import AnthropicSummarizer
    con = db.connect(_dbpath(cfg))
    knowledge.ensure_schema(con)
    todo = _unpositioned(con)
    if args.limit:
        todo = todo[:args.limit]
    summ = AnthropicSummarizer(model=cfg.get("llm", {}).get("model", "claude-sonnet-5"))
    results = []
    for r in todo:
        packet = positioning.build_position_packet(con, r)
        results.append(summ.position(packet))
        print("  positioned:", r["title"][:70])
    stats = positioning.import_positioning(con, results, source="auto-llm")
    print("Positioned:", stats)
    positioning.map_refresh(con)
    con.close()


def cmd_map_refresh(cfg, args):
    con = db.connect(_dbpath(cfg))
    n = positioning.map_refresh(con)
    print(f"Scorecards + ledger rebuilt. Open contradictions: {n}")
    con.close()


def cmd_scorecard(cfg, args):
    con = db.connect(_dbpath(cfg))
    positioning.refresh_scorecards(con)
    q = "SELECT * FROM theory_scorecard WHERE n_papers>0 ORDER BY net DESC, support_w DESC"
    print(f"{'theory':40s} {'status':11s} {'supp':>5s} {'chal':>5s} {'att':>4s} {'papers':>6s}")
    for r in con.execute(q):
        if args.theory and args.theory.lower() not in (r["name"] or "").lower():
            continue
        print(f"{(r['name'] or '')[:40]:40s} {r['status']:11s} {r['support_w']:5.1f} {r['challenge_w']:5.1f} {r['attention']:4d} {r['n_papers']:6d}")
    con.close()


def cmd_review(cfg, args):
    con = db.connect(_dbpath(cfg))
    review.refresh(con)
    rows = review.list_pending(con)
    if not rows:
        print("Очередь ревью пуста.")
    else:
        print(f"На ревью ({len(rows)}):  approve → в карту, reject → удалить\n")
        cur_kind = None
        for r in rows:
            if r["kind"] != cur_kind:
                cur_kind = r["kind"]
                print(f"[{cur_kind}]")
            print(f"  #{r['review_id']:<4} {r['label']}")
        print("\nПрименить: python -m src.cli review-approve <id|all> / review-reject <id|all>")
    con.close()


def cmd_review_approve(cfg, args):
    con = db.connect(_dbpath(cfg))
    n = review.decide(con, args.id, approve=True)
    print(f"Подтверждено: {n}")
    con.close()


def cmd_review_reject(cfg, args):
    con = db.connect(_dbpath(cfg))
    n = review.decide(con, args.id, approve=False)
    print(f"Отклонено (удалено): {n}")
    con.close()


def cmd_contradictions(cfg, args):
    con = db.connect(_dbpath(cfg))
    knowledge.ensure_schema(con)
    rows = con.execute("SELECT * FROM contradictions WHERE status='open' ORDER BY strength DESC").fetchall()
    if not rows:
        print("No open contradictions.")
    for r in rows:
        fa = len(json.loads(r["for_papers"] or "[]"))
        ag = len(json.loads(r["against_papers"] or "[]"))
        print(f"[{r['level']}] {r['ref_label']}  (for={fa} / against={ag}, strength={r['strength']})")
    con.close()


def cmd_enrich_abstracts(cfg, args):
    con = db.connect(_dbpath(cfg))
    stats = enrich.enrich_curated(con, limit=args.limit)
    print(f"Enriched abstracts: checked {stats['checked']}, updated {stats['updated']}, "
          f"not found {stats['not_found']}")
    con.close()


def cmd_kg_stats(cfg, args):
    con = db.connect(_dbpath(cfg))
    s = knowledge.kg_stats(con)
    print(f"theories={s['theories']}  premises={s['premises']}  "
          f"paper-theory links={s['paper_theory']}  paper-premise links={s['paper_premise']}")
    print("\nTheories by attached papers (support / challenge / total):")
    for name, sup, chal, total in s["theory_ranking"]:
        if total:
            print(f"  {name:42s}  {sup:3d} / {chal:3d} / {total:3d}")
    con.close()


def cmd_translate(cfg, args):
    con = db.connect(_dbpath(cfg))
    s = translations.apply(con)
    print(f"Переводы применены: теорий {s['theories']}, посылок {s['premises']}")
    con.close()


def cmd_graph(cfg, args):
    con = db.connect(_dbpath(cfg))
    path, nn, nl = graph.render_html(con, os.path.join(ROOT, cfg["paths"]["digests"]))
    print(f"Graph: {path}  ({nn} nodes, {nl} links)")
    con.close()


def cmd_theory_table(cfg, args):
    con = db.connect(_dbpath(cfg))
    path, nt, nl, nm = report.render_html(con, os.path.join(ROOT, cfg["paths"]["digests"]))
    print(f"Theory-articles table: {path}  (теорий {nt}, связей {nl}, мультитеорийных статей {nm})")
    con.close()


def cmd_stats(cfg, args):
    con = db.connect(_dbpath(cfg))
    n = con.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"]
    na = con.execute("SELECT COUNT(*) c FROM analysis").fetchone()["c"]
    print(f"papers={n}  analyzed={na}")
    for r in con.execute("SELECT source, COUNT(*) c FROM papers GROUP BY source ORDER BY c DESC"):
        print(f"  {r['source']}: {r['c']}")
    con.close()


def main():
    ap = argparse.ArgumentParser(prog="longevity-monitor")
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    b = sub.add_parser("backfill")
    b.add_argument("--days", type=int, default=180)
    b.add_argument("--max", type=int, default=100)
    b.add_argument("--sources", nargs="*")
    sub.add_parser("run")
    pr = sub.add_parser("prepare")
    pr.add_argument("--limit", type=int, default=0)
    im = sub.add_parser("import")
    im.add_argument("file")
    aa = sub.add_parser("autoanalyze")
    aa.add_argument("--limit", type=int, default=0)
    ic = sub.add_parser("import-curated")
    ic.add_argument("file", nargs="?")
    ic.add_argument("--reset", action="store_true")
    sub.add_parser("kg-stats")
    ea = sub.add_parser("enrich-abstracts")
    ea.add_argument("--limit", type=int, default=0)
    pp = sub.add_parser("position-prepare")
    pp.add_argument("--limit", type=int, default=0)
    pi = sub.add_parser("position-import")
    pi.add_argument("file")
    apn = sub.add_parser("autoposition")
    apn.add_argument("--limit", type=int, default=0)
    sub.add_parser("map-refresh")
    sub.add_parser("review")
    ra = sub.add_parser("review-approve")
    ra.add_argument("id")
    rj = sub.add_parser("review-reject")
    rj.add_argument("id")
    scp = sub.add_parser("scorecard")
    scp.add_argument("theory", nargs="?")
    sub.add_parser("contradictions")
    sub.add_parser("translate")
    sub.add_parser("digest")
    sub.add_parser("graph")
    sub.add_parser("theory-table")
    sub.add_parser("stats")
    # Пытаемся переключить stdout на UTF-8 (для корректного вывода кириллицы в Windows).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = ap.parse_args()
    cfg = load_config(args.config)
    # Диспетчеризация: имя подкоманды -> функция-обработчик.
    handlers = {
        "init": cmd_init, "backfill": cmd_backfill, "run": cmd_run,
        "prepare": cmd_prepare, "import": cmd_import, "autoanalyze": cmd_autoanalyze,
        "import-curated": cmd_import_curated, "kg-stats": cmd_kg_stats,
        "enrich-abstracts": cmd_enrich_abstracts,
        "position-prepare": cmd_position_prepare, "position-import": cmd_position_import,
        "autoposition": cmd_autoposition, "map-refresh": cmd_map_refresh,
        "review": cmd_review, "review-approve": cmd_review_approve, "review-reject": cmd_review_reject,
        "scorecard": cmd_scorecard, "contradictions": cmd_contradictions,
        "translate": cmd_translate, "digest": cmd_digest, "graph": cmd_graph,
        "theory-table": cmd_theory_table, "stats": cmd_stats,
    }
    handlers[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
