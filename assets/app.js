/* 策略信号前端：读 data/signals.json，按用户勾选显示各策略今天的操作 */
(function () {
  "use strict";
  var KEY = "sig-picked";
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
  var pct = function (x, d) { return x == null ? "—" : (x * 100).toFixed(d == null ? 1 : d) + "%"; };
  var spct = function (x) { return x == null ? "—" : (x > 0 ? "+" : "") + (x * 100).toFixed(2) + "%"; };
  var md = function (s) { return s ? s.slice(5).replace("-", "/") : ""; };
  var ALLOC = [["a", "稳健块", "#2a78d6"], ["s", "个股块", "#8a5cd1"], ["b", "板块块（ETF）", "#c2871a"]];

  function getPicked(all) {
    try { var x = JSON.parse(localStorage.getItem(KEY)); if (Array.isArray(x)) return x.filter(function (id) { return all.indexOf(id) >= 0; }); } catch (e) {}
    return all.slice();
  }
  function setPicked(x) { try { localStorage.setItem(KEY, JSON.stringify(x)); } catch (e) {} }

  function bjNow() { var d = new Date(); return new Date(d.getTime() + (d.getTimezoneOffset() + 480) * 60000); }
  function ymd(d) { return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2); }

  var DATA = null;

  function load() {
    fetch("data/signals.json?t=" + Date.now()).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (j) { DATA = j; render(); })
      .catch(function () { $("status").textContent = "数据加载失败，请稍后刷新"; });
  }

  function render() {
    var j = DATA, all = j.strategies.map(function (s) { return s.id; });
    var picked = getPicked(all);
    var modeTxt = j.mode === "intraday" ? "盘中提示" : "收盘复核";
    $("status").textContent = j.date + " " + modeTxt + " · 行情 " + j.quote_time;
    // 横幅：数据是否是今天的
    var now = bjNow(), today = ymd(now), mins = now.getHours() * 60 + now.getMinutes(), b = [];
    var isWeekday = now.getDay() >= 1 && now.getDay() <= 5;
    if (j.date !== today && isWeekday && mins >= 14 * 60 + 45) b.push("今天的提示还没出来（最近一次是 " + j.date + " " + modeTxt + "）。如果今天是交易日，可能是电脑没开机，请以聚宽为准。");
    if (j.date === today && j.mode === "intraday" && mins >= 15 * 60 + 50) b.push("今天的收盘复核还没出来，下面是 14:35 的盘中提示。");
    (j.warnings || []).forEach(function (w) { b.push(w); });
    $("banner").innerHTML = b.map(function (x) { return '<div class="banner">' + esc(x) + "</div>"; }).join("");
    // 说明
    var when = j.mode === "intraday"
      ? "下面是按 <b>" + j.quote_time + "</b> 的价格估算的、今天 <b>14:50</b> 该做的操作。收盘后 15:40 会用收盘价再核对一次。"
      : "下面是用 <b>收盘价</b> 复核后的今天的操作；下一个交易日 " + md(j.next_trade_day) + " 14:35 出新的盘中提示。";
    var shown = j.strategies.filter(function (s) { return picked.indexOf(s.id) >= 0; });
    var ov = shown.map(function (s) {
      return '<a class="ov" href="#' + s.id + '"><span class="badge b-' + s.action + '">' + s.action + "</span>" + esc(s.title) + "</a>";
    }).join("");
    $("intro").innerHTML = '<div class="overview">' + ov + "</div><p>" + when + " 你现在是空仓：照着各策略「现在该持有」的清单和比例买入，就和策略同步了。</p>";
    $("cards").innerHTML = shown.map(card).join("");
    $("empty").hidden = shown.length > 0;
    shown.forEach(function (s) { chart(s); });
  }

  function trRows(ts) {
    return ts.map(function (t) {
      return "<tr><td><span class=\"side s-" + t.side + "\">" + t.side + "</span></td><td>" + esc(t.name) + '<span class="code">' + t.code + "</span>" +
        (t.why ? '<span class="why">' + esc(t.why) + "</span>" : "") + '</td><td class="r num">' + pct(t.w0) + " → " + pct(t.w1) + "</td>" +
        '<td class="r num hide-s">' + (t.price != null ? t.price.toFixed(3).replace(/0$/, "") : "—") + "</td></tr>";
    }).join("");
  }

  function card(s) {
    var mode = DATA.mode, h = [];
    var sub = s.rebalance_today ? "今天是例行调仓日" : "下次例行调仓：" + md(s.next_rebalance);
    if (s.stale) sub = "本次计算出错，显示的是上一次结果";
    h.push('<article class="card" id="' + s.id + '"><div class="card-h"><h2>' + esc(s.title) + '<span class="sub">' + sub + '</span></h2><span class="badge b-' + s.action + '">' + s.action + "</span></div>");
    var verb = mode === "intraday" ? "预计 14:50 " : "今天 14:50 ";
    var line = s.trades.length ? verb + "要操作 " + s.trades.length + " 笔：" + s.trades.filter(function (t) { return t.side === "买入" || t.side === "加仓"; }).length + " 买、" +
      s.trades.filter(function (t) { return t.side === "卖出" || t.side === "减仓"; }).length + " 卖" : (s.holdings.length ? "今天不用操作，继续持有。" : "今天空仓，不用操作。");
    if (s.minor) line += '<span class="muted">（另有 ' + s.minor + " 笔仓位变动不到 1.5% 的微调，可忽略）</span>";
    h.push('<div class="headline">' + line + "</div>");
    if (s.alloc) {
      var al = s.alloc;
      h.push('<div class="alloc" role="img" aria-label="资金分配">' + ALLOC.map(function (a) { return al[a[0]] > 0 ? '<i style="flex:' + al[a[0]] + ";background:" + a[2] + '"></i>' : ""; }).join("") + "</div>" +
        '<div class="legend">' + ALLOC.map(function (a) { return '<span style="--c:' + a[2] + '">' + a[1] + " " + Math.round(al[a[0]] * 100) + "%</span>"; }).join("") + "</div>");
    }
    if (s.trades.length) {
      h.push("<div><h3>" + (mode === "intraday" ? "预计操作（按 " + DATA.quote_time + " 价格）" : "今天的操作") + '</h3><div class="tblwrap"><table class="tbl"><thead><tr><th>动作</th><th>名称</th><th class="r">仓位</th><th class="r hide-s">价格</th></tr></thead><tbody>' + trRows(s.trades) + "</tbody></table></div></div>");
    }
    if (s.intraday) {
      h.push('<div class="cmp' + (s.intraday_same ? "" : " diff") + '">' + (s.intraday_same ? "和 14:35 的盘中提示一致。" :
        "和 14:35 的盘中提示不一致（盘中提示：" + (s.intraday.trades.length ? s.intraday.trades.map(function (t) { return t.side + t.name; }).join("、") : "不操作") + "），以这里为准。") + "</div>");
    }
    // 现在该持有
    var hold = s.holdings.map(function (x) {
      var c = x.chg == null ? "" : (x.chg > 0 ? "up" : x.chg < 0 ? "dn" : "");
      return "<tr><td>" + esc(x.name) + '<span class="code">' + x.code + '</span></td><td class="r num">' + pct(x.weight) + '</td><td class="r num hide-s">' +
        (x.price != null ? x.price : "—") + '</td><td class="r num ' + c + '">' + spct(x.chg) + "</td></tr>";
    }).join("");
    h.push("<div><h3>现在该持有（共 " + s.holdings.length + " 只，现金 " + pct(Math.max(0, s.cash)) + "）</h3>" + (hold ? '<div class="tblwrap"><table class="tbl"><thead><tr><th>名称</th><th class="r">占比</th><th class="r hide-s">现价</th><th class="r">今日</th></tr></thead><tbody>' + hold + "</tbody></table></div>" : '<p class="rule">空仓</p>') + "</div>");
    if (s.conditions && s.conditions.length) {
      h.push('<div><h3>判断依据</h3><ul class="conds">' + s.conditions.map(function (c) { return '<li><span class="' + (c.ok ? "ok" : "no") + '">' + (c.ok ? "✓" : "✗") + "</span><span>" + esc(c.text) + "</span></li>"; }).join("") + "</ul></div>");
    }
    h.push('<p class="rule">规则：' + esc(s.rule) + (s.link ? ' <a href="' + s.link + '" target="_blank" rel="noopener">详细说明</a>' : "") + "</p>");
    // 模拟走势 + 历史操作
    h.push('<details><summary>模拟账户走势与最近的操作</summary><div class="chart" id="ch-' + s.id + '"></div><div class="hist">' +
      s.history.slice(0, 15).map(function (d) {
        return '<div><span class="d">' + d.date + "</span> " + d.trades.map(function (t) { return '<span class="s-' + t.side + '">' + t.side + "</span>" + esc(t.name); }).join("，") + "</div>";
      }).join("") + "</div></details>");
    h.push("</article>");
    return h.join("");
  }

  function chart(s) {
    var el = $("ch-" + s.id); if (!el || !s.nav || !s.nav.v.length) return;
    var v = s.nav.v, dts = s.nav.dates, W = 600, H = 160, P = 4;
    var lo = Math.min.apply(null, v), hi = Math.max.apply(null, v); if (hi === lo) hi = lo + 0.01;
    var x = function (i) { return P + i * (W - 2 * P) / Math.max(1, v.length - 1); };
    var y = function (val) { return P + (hi - val) * (H - 2 * P) / (hi - lo); };
    var d = v.map(function (val, i) { return (i ? "L" : "M") + x(i).toFixed(1) + " " + y(val).toFixed(1); }).join("");
    var base = y(1);
    el.innerHTML = '<svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" role="img" aria-label="模拟账户净值">' +
      (base > P && base < H - P ? '<line x1="0" x2="' + W + '" y1="' + base + '" y2="' + base + '" stroke="var(--rule)" stroke-dasharray="3 3"/>' : "") +
      '<path d="' + d + '" fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke"/>' +
      '<line class="cx" x1="0" x2="0" y1="0" y2="' + H + '" stroke="var(--ink-3)" stroke-width="1" vector-effect="non-scaling-stroke" visibility="hidden"/></svg>' +
      '<div class="meta">' + s.nav.dates[0] + " 起按规则模拟：累计 " + spct(s.perf.total) + "，期间最大回落 " + pct(s.perf.maxdd) + "（含手续费估算）</div>";
    var svg = el.querySelector("svg"), cx = el.querySelector(".cx"), tip = $("tip");
    function move(ev) {
      var r = svg.getBoundingClientRect(), px = (ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left;
      var i = Math.round((px / r.width * W - P) / (W - 2 * P) * (v.length - 1)); i = Math.max(0, Math.min(v.length - 1, i));
      cx.setAttribute("x1", x(i)); cx.setAttribute("x2", x(i)); cx.setAttribute("visibility", "visible");
      tip.hidden = false; tip.innerHTML = dts[i] + "<br>净值 <b class='num'>" + v[i].toFixed(3) + "</b>";
      var tx = r.left + px + 12; if (tx + 130 > window.innerWidth) tx = r.left + px - 140;
      tip.style.left = tx + "px"; tip.style.top = (r.top + 8) + "px";
    }
    function out() { cx.setAttribute("visibility", "hidden"); tip.hidden = true; }
    svg.addEventListener("mousemove", move); svg.addEventListener("touchmove", move, { passive: true });
    svg.addEventListener("mouseleave", out); svg.addEventListener("touchend", out);
  }

  // 选择策略
  $("pickBtn").addEventListener("click", function () {
    if (!DATA) return;
    var all = DATA.strategies.map(function (s) { return s.id; }), picked = getPicked(all);
    $("pickList").innerHTML = DATA.strategies.map(function (s) {
      return '<label><input type="checkbox" value="' + s.id + '"' + (picked.indexOf(s.id) >= 0 ? " checked" : "") + ">" + esc(s.title) + "</label>";
    }).join("");
    $("picker").showModal();
  });
  $("picker").addEventListener("close", function () {
    var x = [].slice.call($("pickList").querySelectorAll("input:checked")).map(function (i) { return i.value; });
    setPicked(x); render();
  });
  load();
  setInterval(function () { if (!document.hidden) load(); }, 5 * 60 * 1000);
})();
