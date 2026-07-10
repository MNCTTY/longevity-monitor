"""Интерактивная визуализация карты знаний (самодостаточный HTML).

Строит граф: узлы — теории (размер по числу статей, цвет по статусу
established/contested/emerging/provisional) и посылки; рёбра — «мосты»
теория↔теория (theory_relation) и связи теория↔посылка через общие статьи.

Рендер — force-directed на чистом SVG+JS (без внешних библиотек, CSP-safe:
можно открыть локально или опубликовать как artifact). Вызывается из src.cli
(команда graph). Данные встраиваются в HTML как JSON.
"""
import os
import json
import datetime

from . import positioning


def build_graph_data(con):
    positioning.refresh_scorecards(con)
    nodes, links = [], []

    # Узлы-теории с хотя бы одной привязанной статьёй.
    theo = {}
    for r in con.execute(
        "SELECT s.theory_id, s.name, s.status, s.support_w, s.challenge_w, s.n_papers, t.status AS node_status "
        "FROM theory_scorecard s JOIN theories t ON t.theory_id=s.theory_id WHERE s.n_papers > 0"
    ):
        node_status = "provisional" if r["node_status"] == "provisional" else r["status"]
        theo[r["theory_id"]] = True
        nodes.append({
            "id": r["theory_id"], "label": r["name"].replace("\n", " "), "type": "theory",
            "group": node_status, "size": r["n_papers"],
            "detail": f"теория · {r['status']} · статей {r['n_papers']} · "
                      f"support {r['support_w']:.1f} / challenge {r['challenge_w']:.1f}",
        })

    # Узлы-посылки (с доказательной активностью или из seed).
    for r in con.execute(
        "SELECT pr.premise_id, pr.text, l.n_for, l.n_against, l.evidence_confidence "
        "FROM premises pr LEFT JOIN premise_ledger l ON l.premise_id=pr.premise_id"
    ):
        nf, na = r["n_for"] or 0, r["n_against"] or 0
        nodes.append({
            "id": r["premise_id"], "label": r["text"][:60], "type": "premise",
            "group": "premise", "size": 1 + nf + na,
            "detail": f"посылка · за {nf} / против {na}" +
                      (f" · evidence {r['evidence_confidence']:.1f}" if r["evidence_confidence"] is not None else ""),
        })
    valid = {n["id"] for n in nodes}

    # Рёбра теория↔теория (мосты).
    for r in con.execute("SELECT src_theory_id, dst_theory_id, relation, weight FROM theory_relation"):
        if r["src_theory_id"] in valid and r["dst_theory_id"] in valid:
            links.append({"source": r["src_theory_id"], "target": r["dst_theory_id"],
                          "w": r["weight"] or 1, "kind": r["relation"]})

    # Рёбра теория↔посылка через общие статьи.
    for r in con.execute(
        """SELECT pt.theory_id, pp.premise_id, COUNT(DISTINCT pt.paper_id) AS w
           FROM paper_theory pt JOIN paper_premise pp ON pt.paper_id = pp.paper_id
           WHERE (pt.status='active' OR pt.status IS NULL)
           GROUP BY pt.theory_id, pp.premise_id"""):
        if r["theory_id"] in valid and r["premise_id"] in valid:
            links.append({"source": r["theory_id"], "target": r["premise_id"],
                          "w": r["w"], "kind": "evidence"})
    return {"nodes": nodes, "links": links}


def render_html(con, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    data = build_graph_data(con)
    date = datetime.date.today().isoformat()
    html = _TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False)).replace("__DATE__", date)
    path = os.path.join(out_dir, "graph.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path, len(data["nodes"]), len(data["links"])


# Самодостаточный фрагмент (style + div + script). Открывается локально и годится
# как содержимое artifact (без <!doctype>/<html>/<head>/<body>).
_TEMPLATE = r"""<style>
  #kmap{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#0f1419;color:#e6e6e6;
        border-radius:10px;padding:12px;max-width:100%;box-sizing:border-box}
  #kmap h2{margin:4px 6px 8px;font-size:16px;font-weight:600}
  #kmap .legend{font-size:12px;margin:0 6px 8px;display:flex;flex-wrap:wrap;gap:12px;color:#b8c0cc}
  #kmap .legend span{display:inline-flex;align-items:center;gap:5px}
  #kmap .dot{width:11px;height:11px;border-radius:50%;display:inline-block}
  #kmap svg{width:100%;height:70vh;min-height:420px;display:block;background:#0f1419;border-radius:8px;touch-action:none}
  #kmap .tip{position:fixed;pointer-events:none;background:#1f2937;border:1px solid #374151;
        color:#f3f4f6;font-size:12px;padding:6px 9px;border-radius:6px;max-width:320px;opacity:0;transition:opacity .1s;z-index:9}
  #kmap text{font-size:10px;fill:#cbd5e1;pointer-events:none}
  #kmap line{stroke:#3a4655}
</style>
<div id="kmap">
  <h2>Карта знаний по биологии старения — __DATE__</h2>
  <div class="legend">
    <span><i class="dot" style="background:#22c55e"></i>established</span>
    <span><i class="dot" style="background:#f59e0b"></i>contested</span>
    <span><i class="dot" style="background:#3b82f6"></i>emerging</span>
    <span><i class="dot" style="background:#6b7280"></i>provisional</span>
    <span><i class="dot" style="background:#a855f7"></i>посылка</span>
    <span>линия — связь (мост / доказательство)</span>
  </div>
  <svg viewBox="0 0 960 620" preserveAspectRatio="xMidYMid meet"></svg>
  <div class="tip"></div>
</div>
<script>
(function(){
  var DATA = __DATA__;
  var W=960,H=620, svg=document.querySelector('#kmap svg'), tip=document.querySelector('#kmap .tip');
  var NS='http://www.w3.org/2000/svg';
  var COL={established:'#22c55e',contested:'#f59e0b',emerging:'#3b82f6',provisional:'#6b7280',premise:'#a855f7'};
  var nodes=DATA.nodes, links=DATA.links, byId={};
  nodes.forEach(function(n,i){ n.x=W/2+Math.cos(i)*200*Math.random(); n.y=H/2+Math.sin(i)*200*Math.random();
    n.vx=0; n.vy=0; n.r=(n.type==='theory'?5:4)+Math.sqrt(n.size)*2.4; byId[n.id]=n; });
  links.forEach(function(l){ l.s=byId[l.source]; l.t=byId[l.target]; });
  links=links.filter(function(l){return l.s&&l.t;});
  // Простая force-модель (Fruchterman–Reingold-подобная).
  var k=Math.sqrt(W*H/Math.max(1,nodes.length))*0.7;
  function tick(temp){
    for(var i=0;i<nodes.length;i++){var a=nodes[i];
      for(var j=i+1;j<nodes.length;j++){var b=nodes[j];
        var dx=a.x-b.x, dy=a.y-b.y, d=Math.sqrt(dx*dx+dy*dy)||0.01;
        var f=k*k/d/d; a.vx+=dx*f; a.vy+=dy*f; b.vx-=dx*f; b.vy-=dy*f;}}
    links.forEach(function(l){var dx=l.s.x-l.t.x, dy=l.s.y-l.t.y, d=Math.sqrt(dx*dx+dy*dy)||0.01;
      var f=(d*d)/k/900*(1+(l.w||1)*0.15); var ux=dx/d*f, uy=dy/d*f;
      l.s.vx-=ux; l.s.vy-=uy; l.t.vx+=ux; l.t.vy+=uy;});
    nodes.forEach(function(n){ n.vx+=(W/2-n.x)*0.006; n.vy+=(H/2-n.y)*0.006;
      if(n.fx==null){ n.x+=Math.max(-temp,Math.min(temp,n.vx)); n.y+=Math.max(-temp,Math.min(temp,n.vy)); }
      n.vx*=0.85; n.vy*=0.85;
      n.x=Math.max(n.r,Math.min(W-n.r,n.x)); n.y=Math.max(n.r,Math.min(H-n.r,n.y)); });
  }
  var t=18; for(var it=0;it<420;it++){ tick(t); t*=0.992; }  // прогрев до стабильной раскладки
  // Отрисовка
  var gL=document.createElementNS(NS,'g'), gN=document.createElementNS(NS,'g');
  svg.appendChild(gL); svg.appendChild(gN);
  var lineEls=links.map(function(l){var e=document.createElementNS(NS,'line');
    e.setAttribute('stroke-width', Math.min(4,0.6+(l.w||1)*0.4));
    e.setAttribute('stroke-opacity', l.kind==='evidence'?0.28:0.6);
    if(l.kind!=='evidence') e.setAttribute('stroke','#8b98a8'); gL.appendChild(e); return e;});
  var nodeEls=nodes.map(function(n){var g=document.createElementNS(NS,'g'); g.style.cursor='pointer';
    var c=document.createElementNS(NS,'circle'); c.setAttribute('r',n.r);
    c.setAttribute('fill',COL[n.group]||'#3b82f6'); c.setAttribute('stroke','#0f1419'); c.setAttribute('stroke-width',1.5);
    g.appendChild(c);
    if(n.type==='theory' && n.size>=3){var tx=document.createElementNS(NS,'text');
      tx.setAttribute('text-anchor','middle'); tx.setAttribute('dy',-n.r-3);
      tx.textContent=n.label.length>26?n.label.slice(0,25)+'…':n.label; g.appendChild(tx);}
    g.addEventListener('mousemove',function(ev){tip.style.opacity=1; tip.style.left=(ev.clientX+12)+'px';
      tip.style.top=(ev.clientY+12)+'px'; tip.innerHTML='<b>'+esc(n.label)+'</b><br>'+esc(n.detail);});
    g.addEventListener('mouseleave',function(){tip.style.opacity=0;});
    enableDrag(g,n); gN.appendChild(g); return g;});
  function esc(s){return (s||'').replace(/[&<>]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
  function draw(){ links.forEach(function(l,i){lineEls[i].setAttribute('x1',l.s.x);lineEls[i].setAttribute('y1',l.s.y);
      lineEls[i].setAttribute('x2',l.t.x);lineEls[i].setAttribute('y2',l.t.y);});
    nodes.forEach(function(n,i){nodeEls[i].setAttribute('transform','translate('+n.x+','+n.y+')');}); }
  draw();
  function enableDrag(g,n){var moving=false;
    g.addEventListener('pointerdown',function(ev){moving=true; n.fx=n.x; g.setPointerCapture(ev.pointerId); ev.preventDefault();});
    g.addEventListener('pointermove',function(ev){ if(!moving)return; var p=pt(ev); n.x=p.x; n.y=p.y; n.fx=p.x; n.fy=p.y;
      for(var i=0;i<40;i++)tick(6); draw();});
    g.addEventListener('pointerup',function(ev){moving=false; n.fx=null; n.fy=null;});}
  function pt(ev){var r=svg.getBoundingClientRect(); return {x:(ev.clientX-r.left)/r.width*W, y:(ev.clientY-r.top)/r.height*H};}
})();
</script>"""
