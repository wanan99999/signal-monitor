/* 策略信号前端：首页 = 策略卡片（买入/卖出计数）；#/策略id[/buy|sell|hold] = 该策略的操作提示 */
(function () {
  "use strict";
  var KEY = "sig-picked";
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
  var pct = function (x, d) { return x == null ? "—" : (x * 100).toFixed(d == null ? 1 : d) + "%"; };
  var spct = function (x, d) { return x == null ? "—" : (x > 0 ? "+" : "") + (x * 100).toFixed(d == null ? 2 : d) + "%"; };
  var md = function (s) { return s ? +s.slice(5, 7) + "月" + +s.slice(8, 10) + "日" : ""; };
  var WEEK = "日一二三四五六";
  var ALLOC = [["a", "稳健块", "#2a78d6"], ["s", "个股块", "#8a5cd1"], ["b", "板块块（ETF）", "#c2871a"]];
  var isBuy = function (t) { return t.side === "买入" || t.side === "加仓"; };
  var isSell = function (t) { return t.side === "卖出" || t.side === "减仓"; };
  var DATA = null, SKEY = "sig-start", CUR = null;   // CUR：详情页当前加载的起点数据

  // 每个策略自己的起点（存在浏览器里）；不在可选范围内就退回最早可选日
  function getStart(s) {
    var m = {}; try { m = JSON.parse(localStorage.getItem(SKEY)) || {}; } catch (e) {}
    return s.starts[m[s.id]] ? m[s.id] : s.default_start;
  }
  function setStart(id, d) {
    var m = {}; try { m = JSON.parse(localStorage.getItem(SKEY)) || {}; } catch (e) {}
    m[id] = d; try { localStorage.setItem(SKEY, JSON.stringify(m)); } catch (e) {}
  }
  function summ(s) { var x = s.starts[getStart(s)] || []; return { action: x[0], nb: x[1] || 0, ns: x[2] || 0, nh: x[3] || 0, reb: x[4], next: x[5] }; }

  function getPicked(all) {
    try { var x = JSON.parse(localStorage.getItem(KEY)); if (Array.isArray(x)) return x.filter(function (id) { return all.indexOf(id) >= 0; }); } catch (e) {}
    return all.slice();
  }
  function setPicked(x) { try { localStorage.setItem(KEY, JSON.stringify(x)); } catch (e) {} }
  function bjNow() { var d = new Date(); return new Date(d.getTime() + (d.getTimezoneOffset() + 480) * 60000); }
  function ymd(d) { return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2); }
  function modeTxt() { return DATA.mode === "intraday" ? "盘中提示" : "收盘复核"; }

  function load() {
    var src = (location.search.match(/[?&]src=([\w.-]+)/) || [])[1] || "signals.json";   // ?src= 仅用于本地测试
    fetch("data/" + src + "?t=" + Date.now()).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (j) { DATA = j; route(); })
      .catch(function () { $("view").innerHTML = '<p class="empty">数据加载失败，请稍后刷新。</p>'; });
  }

  function banners() {
    var j = DATA, now = bjNow(), mins = now.getHours() * 60 + now.getMinutes(), b = [];
    var wk = now.getDay() >= 1 && now.getDay() <= 5;
    if (j.date !== ymd(now) && wk && mins >= 14 * 60 + 45) b.push("今天的提示还没出来（最近一次是 " + md(j.date) + modeTxt() + "）。如果今天是交易日，可能是电脑没开机，请以聚宽为准。");
    if (j.date === ymd(now) && j.mode === "intraday" && mins >= 15 * 60 + 50) b.push("今天的收盘复核还没出来，下面是盘中提示。");
    (j.warnings || []).forEach(function (w) { b.push(w); });
    return b.length ? '<div class="banners">' + b.map(function (x) { return '<div class="banner">' + esc(x) + "</div>"; }).join("") + "</div>" : "";
  }

  function route() {
    if (!DATA) return;
    $("status").textContent = md(DATA.date) + " " + modeTxt() + " · 行情 " + DATA.quote_time;
    var m = location.hash.match(/^#\/(\w+)(?:\/(buy|sell|hold))?/);
    var s = m && DATA.strategies.filter(function (x) { return x.id === m[1]; })[0];
    if (!s) { $("view").innerHTML = home(); document.title = "策略信号"; return; }
    var sd = getStart(s), tab = m[2];
    document.title = s.title + " · 策略信号";
    if (CUR && CUR.id === s.id && CUR.start === sd && CUR.date === DATA.date && CUR.mode === DATA.mode) return show(s, CUR, tab);
    $("view").innerHTML = '<a class="back" href="#/">← 全部策略</a><p class="loading">加载中…</p>';
    fetch("data/s/" + s.id + "/" + sd + ".json?t=" + Date.now()).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (o) { CUR = o; show(s, o, tab); })
      .catch(function () { $("view").innerHTML = '<a class="back" href="#/">← 全部策略</a><p class="empty">这个起点的数据加载失败，请稍后刷新。</p>'; });
  }

  function show(meta, o, tab) {
    $("view").innerHTML = detail(meta, o, tab);
    chart(o); bindTabs(o);
    var sel = $("startSel");
    if (sel) sel.addEventListener("change", function () {
      setStart(meta.id, sel.value); CUR = null;
      history.replaceState(null, "", "#/" + meta.id); route();
    });
  }

  function startPicker(meta, cur) {
    var ds = Object.keys(meta.starts).sort().reverse(), today = DATA.date;
    var opt = ds.map(function (d) {
      var w = WEEK[new Date(d + "T00:00:00").getDay()];
      var tail = d === today ? "（今天）" : d === meta.default_start ? "（最早可选）" : "";
      return '<option value="' + d + '"' + (d === cur ? " selected" : "") + ">" + md(d) + " 周" + w + tail + "</option>";
    }).join("");
    return '<label class="startpick"><span>从</span><select id="startSel" aria-label="开始跟的日期">' + opt + "</select><span>开始跟</span></label>";
  }

  // ---------------- 首页 ----------------
  function home() {
    var j = DATA, all = j.strategies.map(function (s) { return s.id; }), picked = getPicked(all);
    var shown = j.strategies.filter(function (s) { return picked.indexOf(s.id) >= 0; });
    var nb = 0, ns = 0, act = 0;
    shown.forEach(function (s) {
      var x = summ(s);
      nb += x.nb; ns += x.ns; if (x.nb + x.ns) act++;
    });
    var d = new Date(j.date + "T00:00:00");
    var when = j.mode === "intraday"
      ? "按 " + j.quote_time + " 的价格估算今天 14:50 该做的操作，收盘后 15:35 用收盘价再核对一次。"
      : "已用收盘价复核今天的操作；下一个交易日 " + md(j.next_trade_day) + " 14:30 出新的盘中提示。";
    var h = ['<section class="hero"><div class="hero-t"><h1>' + md(j.date) + " 周" + WEEK[d.getDay()] + '</h1><span class="pill' + (j.mode === "intraday" ? " live" : "") +
      '"><span class="dot"></span>' + modeTxt() + " · 行情 " + j.quote_time + "</span></div>",
      "<p>" + (shown.length ? (act ? "你关注的 " + shown.length + " 个策略里，<b>" + act + " 个今天要操作</b>。" : "你关注的 " + shown.length + " 个策略<b>今天都不用操作</b>。") : "") + when + "</p>",
      '<div class="stats"><div class="stat"><div class="k">今天要买入</div><div class="v num buy">' + nb + '<small>笔</small></div></div>' +
      '<div class="stat"><div class="k">今天要卖出</div><div class="v num sell">' + ns + '<small>笔</small></div></div>' +
      '<div class="stat"><div class="k">下个交易日</div><div class="v num">' + +j.next_trade_day.slice(5, 7) + "/" + +j.next_trade_day.slice(8, 10) + "</div></div></div></section>", banners()];
    if (!shown.length) h.push('<p class="empty">还没有选择策略。点右上角「选择策略」勾选要看的策略。</p>');
    (j.cats || [{ id: null, title: "策略", desc: "" }]).forEach(function (c) {
      var ss = shown.filter(function (s) { return !c.id || s.cat === c.id; });
      if (!ss.length) return;
      h.push('<section class="sec" style="--c:var(--c-' + c.id + ')"><div class="sec-h"><h2>' + esc(c.title) + '</h2><span class="n">' + ss.length + "</span></div><p>" + esc(c.desc) + '</p><div class="cards">' + ss.map(card).join("") + "</div></section>");
    });
    return h.join("");
  }

  function card(s) {
    var x = summ(s), b = x.nb, se = x.ns;
    var tag = s.stale ? "上次结果" : "今天" + x.action;
    var reb = (x.reb ? "今天调仓日" : "下次调仓 " + md(x.next)) + " · " + md(getStart(s)) + "起跟";
    return '<article class="card"><a class="cover" href="#/' + s.id + '" aria-label="查看' + esc(s.title) + '的操作提示"></a>' +
      '<div class="card-top"><span class="tag t-' + x.action + '">' + tag + '</span><span class="card-meta">' + reb + "</span></div>" +
      "<h3>" + esc(s.title) + "</h3><p>" + esc(s.tagline || s.rule) + "</p>" +
      '<div class="card-sig"><a class="sig buy' + (b ? " on" : "") + '" href="#/' + s.id + '/buy">买入 <b>' + b + '</b></a>' +
      '<a class="sig sell' + (se ? " on" : "") + '" href="#/' + s.id + '/sell">卖出 <b>' + se + "</b></a>" +
      '<span class="hold">持有 ' + x.nh + " 只</span></div></article>";
  }

  // ---------------- 详情页 ----------------
  function detail(meta, s, tab) {
    var buys = s.trades.filter(isBuy), sells = s.trades.filter(isSell);
    tab = tab || (buys.length ? "buy" : sells.length ? "sell" : "hold");
    var h = ['<a class="back" href="#/">← 全部策略</a>', banners()];
    h.push('<section class="d-head"><div class="card-top"><span class="tag t-' + s.action + '">今天' + s.action + '</span><span class="pill' + (DATA.mode === "intraday" ? " live" : "") + '"><span class="dot"></span>' + modeTxt() + " · " + DATA.quote_time + "</span></div>" +
      "<h1>" + esc(meta.title) + "</h1><p>" + esc(meta.tagline || "") + "</p>" + startPicker(meta, s.start) +
      '<p class="note">' + (s.start === DATA.date ? "今天开始跟：今天空仓，按策略清单建仓" : md(s.start) + " 空仓起步、当天建仓") +
      "，之后按规则调仓。下面的买卖提示、持有清单和走势都按这个起点计算。</p>" +
      '<div class="d-meta"><span>' + (s.rebalance_today ? "今天是例行调仓日" : "下次例行调仓：" + md(s.next_rebalance)) + "</span><span>持有 " + s.holdings.length + " 只 · 现金 " + pct(Math.max(0, s.cash)) + "</span>" +
      (meta.link ? '<a href="' + meta.link + '" target="_blank" rel="noopener">规则详细说明 ↗</a>' : "") + "</div></section>");
    if (s.alloc) {
      var al = s.alloc;
      h.push('<section class="panel"><h2>今天钱怎么分</h2><div class="alloc" role="img" aria-label="资金分配">' + ALLOC.map(function (a) { return al[a[0]] > 0 ? '<i style="flex:' + al[a[0]] + ";background:" + a[2] + '"></i>' : ""; }).join("") + "</div>" +
        '<div class="legend">' + ALLOC.map(function (a) { return '<span style="--c:' + a[2] + '">' + a[1] + " " + Math.round(al[a[0]] * 100) + "%</span>"; }).join("") + "</div></section>");
    }
    var label = DATA.mode === "intraday" ? "按 " + DATA.quote_time + " 价格估算，14:50 操作" : "收盘价复核后的今天操作";
    h.push('<section class="panel"><h2>操作提示 <span class="hint">' + label + "</span></h2>" +
      '<div class="tabs" role="tablist">' +
      tabBtn("buy", "买入", buys.length, tab) + tabBtn("sell", "卖出", sells.length, tab) + tabBtn("hold", "持有", s.holdings.length, tab) + "</div>" +
      '<div class="list" id="list">' + listHtml(s, tab) + "</div>" +
      (s.minor ? '<div class="note">另有 ' + s.minor + " 笔仓位变动不到 1.5% 的微调，可忽略。</div>" : "") +
      (s.intraday ? '<div class="cmp' + (s.intraday_same ? "" : " diff") + '">' + (s.intraday_same ? "和盘中提示一致。" :
        "和盘中提示不一致（盘中提示：" + (s.intraday.trades.length ? s.intraday.trades.map(function (t) { return t.side + t.name; }).join("、") : "不操作") + "），以这里为准。") + "</div>" : "") +
      (s.start === DATA.date ? "" : '<div class="note">如果你是今天才开始、现在空仓：照「持有」里的清单和比例买入，就和这个起点的策略同步了；或者把起点改成今天。</div>') + "</section>");
    h.push('<div class="grid2"><section class="panel"><h2>判断依据</h2><ul class="conds">' + (s.conditions || []).map(function (c) {
      return '<li><span class="' + (c.ok ? "ok" : "no") + '">' + (c.ok ? "✓" : "✗") + "</span><span>" + esc(c.text) + "</span></li>";
    }).join("") + '</ul><div class="note">规则：' + esc(s.rule) + "</div></section>" +
      '<section class="panel"><h2>模拟账户走势 <span class="hint">' + md(s.nav.dates[0]) + " 起按规则运行</span></h2>" +
      '<div class="chart" id="chart"></div><div class="note">累计 <span class="num">' + spct(s.perf.total, 1) + "</span> · 期间最大回落 <span class=\"num\">" + pct(s.perf.maxdd) + "</span>（含手续费估算）</div></section></div>");
    h.push('<section class="panel"><h2>最近的操作</h2><div class="hist">' + s.history.slice(0, 20).map(function (d) {
      return '<div><span class="d">' + d.date + "</span>" + d.trades.map(function (t) { return '<span class="s-' + t.side + '">' + t.side + "</span>" + esc(t.name); }).join("，") + "</div>";
    }).join("") + "</div></section>");
    return h.join("");
  }

  function tabBtn(id, name, n, cur) {
    var cls = id === "buy" ? "buy" : id === "sell" ? "sell" : "hold";
    return '<button type="button" role="tab" class="sig ' + cls + (n ? " on" : "") + '" data-tab="' + id + '" aria-selected="' + (id === cur) + '">' + name + " <b>" + n + "</b></button>";
  }

  function listHtml(s, tab) {
    if (tab === "hold") {
      if (!s.holdings.length) return '<p class="empty-s">现在空仓</p>';
      return s.holdings.map(function (x) {
        var c = x.chg == null ? "" : x.chg > 0 ? "up" : x.chg < 0 ? "dn" : "";
        return '<div class="row"><div class="nm">' + esc(x.name) + '<span class="cd">' + x.code + '</span></div><div class="rt num">' + pct(x.weight) +
          '<small class="' + c + '">' + (x.price != null ? x.price : "—") + " · " + spct(x.chg) + "</small></div></div>";
      }).join("");
    }
    var ts = s.trades.filter(tab === "buy" ? isBuy : isSell);
    if (!ts.length) return '<p class="empty-s">今天没有需要' + (tab === "buy" ? "买入" : "卖出") + "的股票</p>";
    return ts.map(function (t) {
      return '<div class="row"><div class="nm"><span class="side ' + t.side + '">' + t.side + "</span>" + esc(t.name) + '<span class="cd">' + t.code + "</span></div>" +
        '<div class="rt num">' + pct(t.w0) + " → " + pct(t.w1) + "<small>" + (t.price != null ? "现价 " + t.price : "") + "</small></div>" +
        (t.why ? '<div class="sub">' + esc(t.why) + "</div>" : "") + "</div>";
    }).join("");
  }

  function bindTabs(s) {
    [].forEach.call(document.querySelectorAll(".tabs [data-tab]"), function (b) {
      b.addEventListener("click", function () {
        var t = b.getAttribute("data-tab");
        history.replaceState(null, "", "#/" + s.id + "/" + t);
        [].forEach.call(document.querySelectorAll(".tabs [data-tab]"), function (x) { x.setAttribute("aria-selected", x === b); });
        $("list").innerHTML = listHtml(s, t);
      });
    });
  }

  function chart(s) {
    var el = $("chart"); if (!el || !s.nav || !s.nav.v.length) return;
    var v = s.nav.v, dts = s.nav.dates, W = 600, H = 170, P = 4;
    var lo = Math.min.apply(null, v), hi = Math.max.apply(null, v); if (hi === lo) hi = lo + 0.01;
    var x = function (i) { return P + i * (W - 2 * P) / Math.max(1, v.length - 1); };
    var y = function (val) { return P + (hi - val) * (H - 2 * P) / (hi - lo); };
    var d = v.map(function (val, i) { return (i ? "L" : "M") + x(i).toFixed(1) + " " + y(val).toFixed(1); }).join("");
    var base = y(1);
    el.innerHTML = '<svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" role="img" aria-label="模拟账户净值">' +
      '<defs><linearGradient id="fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="var(--accent)" stop-opacity=".16"/><stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>' +
      '<path d="' + d + "L" + x(v.length - 1) + " " + H + "L" + x(0) + " " + H + 'Z" fill="url(#fill)"/>' +
      (base > P && base < H - P ? '<line x1="0" x2="' + W + '" y1="' + base + '" y2="' + base + '" stroke="var(--rule)" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>' : "") +
      '<path d="' + d + '" fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke"/>' +
      '<line class="cx" x1="0" x2="0" y1="0" y2="' + H + '" stroke="var(--ink-3)" stroke-width="1" vector-effect="non-scaling-stroke" visibility="hidden"/></svg>';
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

  $("pickBtn").addEventListener("click", function () {
    if (!DATA) return;
    var all = DATA.strategies.map(function (s) { return s.id; }), picked = getPicked(all);
    $("pickList").innerHTML = DATA.strategies.map(function (s) {
      return '<label><input type="checkbox" value="' + s.id + '"' + (picked.indexOf(s.id) >= 0 ? " checked" : "") + ">" + esc(s.title) + "</label>";
    }).join("");
    $("picker").showModal();
  });
  $("picker").addEventListener("close", function () {
    setPicked([].slice.call($("pickList").querySelectorAll("input:checked")).map(function (i) { return i.value; }));
    location.hash = "#/"; route();
  });
  window.addEventListener("hashchange", function () { route(); window.scrollTo(0, 0); });
  load();
  setInterval(function () { if (!document.hidden) load(); }, 5 * 60 * 1000);
})();
