# -*- coding: utf-8 -*-
"""信号监控主程序
用法：
  python run.py              自动：北京时间 14:30–14:59 跑盘中，15:35 以后跑收盘；当天跑过就跳过；非交易日退出
  python run.py --mode intraday|close [--force] [--no-push]
"""
import os, sys, json, argparse, subprocess, traceback, time
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data as D
from market import Mkt
import strategies as S
import combo2 as C2
import cloud

ROOT = D.ROOT
OUT = os.path.join(ROOT, "data")
CONF = json.load(open(os.path.join(ROOT, "engine", "config.json"), encoding="utf-8"))
DONE = os.path.join(D.CACHE, "done.json")
N_STARTS = 60


def done_get():
    try:
        return json.load(open(DONE, encoding="utf-8"))
    except Exception:
        return {}


def done_set(day, mode):
    x = done_get(); x[day + ":" + mode] = D.bj_now().strftime("%H:%M"); json.dump(x, open(DONE, "w"))


def pick_mode(now):
    t = now.hour * 60 + now.minute
    if 14 * 60 + 30 <= t < 15 * 60:
        return "intraday"
    if t >= 15 * 60 + 35:
        return "close"
    return None


def short(code):
    return code[2:]


def build_output(strat, start, b, extra, m, q, today, mode, prev):
    d = m.dates[-1]
    w = b.weights(d)
    etf_names = extra.get("etf_names", {})
    def nm(c):
        if c in q.index:
            return q.at[c, "name"]
        return etf_names.get(c, m.name(c) or c)
    def px(c):
        if c in q.index:
            return float(q.at[c, "price"]), (float(q.at[c, "price"] / q.at[c, "prev"] - 1) if q.at[c, "prev"] else None)
        e = getattr(D, "_etf_spot", {}).get(c)
        return (e[0], e[1]) if e else (None, None)
    def tr(t):
        p, _ = px(t["code"])
        return dict(code=short(t["code"]), name=nm(t["code"]), side=t["side"], w0=t["w0"], w1=t["w1"], why=t.get("tag", ""), price=p)
    big = lambda t: t["side"] in ("买入", "卖出") or abs(t["w1"] - t["w0"]) >= 0.015       # 仓位变动不到 1.5% 的微调不提示
    raw = b.trades.get(d, []) if d == pd.Timestamp(today) else []
    today_tr = [tr(t) for t in raw if big(t)]
    minor = len(raw) - len(today_tr)
    buys = [t for t in today_tr if t["side"] in ("买入", "加仓")]
    sells = [t for t in today_tr if t["side"] in ("卖出", "减仓")]
    if buys and sells:
        action = "调仓"
    elif buys:
        action = "买入"
    elif sells:
        action = "卖出"
    else:
        action = "空仓" if not w else "不动"
    hold = []
    for c, x in sorted(w.items(), key=lambda kv: -kv[1]):
        p, chg = px(c)
        cost = b.cost.get(c)
        hold.append(dict(code=short(c), name=nm(c), weight=round(x, 4), price=p, chg=None if chg is None else round(chg, 4),
                         gain=None if not cost else round(b.px(c, d) / cost - 1, 4)))
    nav = pd.Series(b.nav).sort_index()
    nav = nav / nav.iloc[0]
    hist = []
    for day in sorted(b.trades)[::-1]:
        if len(hist) >= 40:
            break
        ts = [dict(code=short(t["code"]), name=nm(t["code"]), side=t["side"], w0=t["w0"], w1=t["w1"], why=t.get("tag", ""))
              for t in b.trades[day] if big(t)]
        if ts:
            hist.append(dict(date=day.strftime("%Y-%m-%d"), trades=ts))
    ck, every = extra["clock"], extra.get("every", 5)
    nr = ck.next_rebal(d, every)
    out = dict(id=strat["id"], start=start, date=today, mode=mode, title=strat["title"], rule=extra.get("rule", ""),
               action=action, trades=today_tr, minor=minor, holdings=hold, cash=round(1 - sum(w.values()), 4),
               conditions=extra.get("conditions", []),
               rebalance_today=bool(ck.n(d) % every == 0),
               next_rebalance=nr.strftime("%Y-%m-%d") if nr is not None else None,
               nav=dict(dates=[x.strftime("%Y-%m-%d") for x in nav.index], v=[round(float(v), 4) for v in nav.values]),
               perf=dict(total=round(float(nav.iloc[-1] - 1), 4), maxdd=round(float((nav / nav.cummax() - 1).min()), 4),
                         start=nav.index[0].strftime("%Y-%m-%d")),
               history=hist)
    if "alloc" in extra:
        out["alloc"] = extra["alloc"]
    # 收盘版保留同一天、同一起点的盘中提示，方便对照
    if mode == "close" and prev and prev.get("date") == today:
        src = prev.get("intraday") if prev.get("mode") == "close" else dict(action=prev["action"], trades=prev["trades"])
        if src:
            out["intraday"] = src
            key = lambda ts: sorted((t["code"], t["side"]) for t in ts)
            out["intraday_same"] = key(src["trades"]) == key(today_tr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["intraday", "close", "auto"], default="auto")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    now = D.bj_now()
    today = now.strftime("%Y-%m-%d")
    if not D.is_trade_day(today) and not a.force:
        D.log("今天不是交易日，退出"); return
    mode = a.mode if a.mode != "auto" else pick_mode(now)
    if mode is None:
        return
    if (today + ":" + mode) in done_get() and not a.force:
        return
    t0 = time.time()
    D.log("开始 %s 运行" % ("盘中" if mode == "intraday" else "收盘"))
    warn = []
    codes = D.stock_codes()
    q = D.tx_quote(codes)
    ts_day = q["ts"].astype(str).str[:8].value_counts().idxmax()
    if len(q) < 3000 or ts_day != now.strftime("%Y%m%d"):
        D.log("行情异常：%d 只，日期 %s，放弃本次运行" % (len(q), ts_day)); sys.exit(2)
    D.log("腾讯行情 %d 只（%.0fs）" % (len(q), time.time() - t0))
    H = D.history()
    last = H["close"].index[-1]
    cal = D.calendar()
    prev_day = cal[cal < pd.Timestamp(today)][-1]
    if last < prev_day:
        warn.append("历史行情缺 %s 之后的数据，今天的结果可能不准" % last.strftime("%m-%d"))
        if mode == "close":
            D.log("历史缺口，重建历史缓存"); H = D.build_history(codes)
    if mode == "close":
        H = D.append_today(H, q, today); D.save("hist.pkl", H)
        Hx = H
    else:
        Hx = D.with_snapshot(H, q, today)
    # 指数（今天用实时价补上）
    spot = D.index_spot(list(C2.INDEX_NAMES))
    def idx(sym):
        s = D.index_daily(sym)
        s = s[s.index < pd.Timestamp(today)]
        s.loc[pd.Timestamp(today)] = float(spot[sym])
        return s
    i1000, i300 = idx("sh000852"), idx("sh000300")
    fresh = 0.3 if mode == "close" else 3
    l1 = D.sw_list(1); l2 = D.sw_list(2)
    sw1 = D.sw_hist([c for c, _ in l1], "sw_l1.pkl", fresh)
    sw2 = D.sw_hist([c for c, _ in l2], "sw_l2.pkl", fresh)
    ind = D.sw_members([c for c, _ in l2])
    banks = D.sw_members(["801780"], "sw_bank.pkl")
    bank_codes = [("sh" if c.startswith("6") else "sz") + c for c in banks]
    F = D.fin_reports() if mode == "close" or D.age_days("fin.pkl") > 3 else D.load("fin.pkl")
    elist = D.etf_list()
    cand = [c for c, n in elist if not any(k in n for k in S.ETF_EXCLUDE)]
    ec, ea = D.etf_hist(cand, 0.3 if mode == "close" else 3)
    es = D.retry(lambda: D._ak().fund_etf_category_sina(symbol="ETF基金")).set_index("代码")
    es = es[pd.to_numeric(es["最新价"], errors="coerce") > 0]
    D._etf_spot = {c: (float(es.at[c, "最新价"]), float(es.at[c, "最新价"]) / float(es.at[c, "昨收"]) - 1 if float(es.at[c, "昨收"]) else None)
                   for c in es.index}
    ec = ec[ec.index < pd.Timestamp(today)].copy()
    ec.loc[pd.Timestamp(today)] = pd.to_numeric(es["最新价"], errors="coerce").reindex(ec.columns)
    D.log("数据准备完成（%.0fs）" % (time.time() - t0))
    names = q["name"]
    m = Mkt(Hx, names, cal, today)
    ctx = S.Ctx(F=F, ind=ind, q=q, bank_codes=bank_codes, i1000=i1000, i300=i300, sw1=sw1, sw2=sw2,
                sw2_names=dict(l2), etf_close=ec, etf_amt=ea, etf_list=elist)
    ctx.today_spot = (pd.Timestamp(today), lambda sym: float(spot[sym]) if sym in spot.index and pd.notna(spot[sym]) else None)
    # 云端「我的组合」：每个组合当成一个策略，归到「我的组合策略」分类
    strategies = [x for x in CONF["strategies"] if x.get("enabled", True)]
    cl, cl_stale = cloud.combos()
    if cl_stale:
        warn.append("这次没连上云端，「我的组合策略」用的是上一次读到的配置")
    for c in cl:
        bad = C2.check(c["config"])
        if bad:
            warn.append("组合「%s」配置有问题，跳过：%s" % (c["name"], "；".join(bad))); continue
        strategies.append(dict(id="u-" + c["id"][:8], cat="mine", title=c["name"], tagline=C2.summary(c["config"]),
                               link=cloud.VIEW + c["id"], cfg=c["config"], combo_id=c["id"]))
    try:
        prev_json = json.load(open(os.path.join(OUT, "signals.json"), encoding="utf-8"))
    except Exception:
        prev_json = None
    starts = [x.strftime("%Y-%m-%d") for x in m.dates[-N_STARTS:]]     # 可选起点：最近 60 个交易日（含今天）
    res = []
    for st in strategies:
        t1 = time.time()
        folder = os.path.join(OUT, "s", st["id"])
        try:
            files, summ = {}, {}
            for sd in starts:
                try:
                    prev = json.load(open(os.path.join(folder, sd + ".json"), encoding="utf-8"))
                except Exception:
                    prev = None
                if "cfg" in st:
                    b, extra = C2.run(m, ctx, sd, st["cfg"])
                else:
                    b, extra = getattr(S, st["func"])(m, ctx, sd, **st.get("params", {}))
                o = build_output(st, sd, b, extra, m, q, today, mode, prev)
                files[sd] = o
                nb = sum(t["side"] in ("买入", "加仓") for t in o["trades"]); ns = len(o["trades"]) - nb
                summ[sd] = [o["action"], nb, ns, len(o["holdings"]), o["rebalance_today"], o["next_rebalance"]]
            if os.path.isdir(folder):
                for f in os.listdir(folder):
                    os.remove(os.path.join(folder, f))
            os.makedirs(folder, exist_ok=True)
            for sd, o in files.items():
                with open(os.path.join(folder, sd + ".json"), "w", encoding="utf-8") as f:
                    json.dump(o, f, ensure_ascii=False, separators=(",", ":"))
            res.append(dict(id=st["id"], title=st["title"], cat=st.get("cat"), tagline=st.get("tagline", ""), link=st.get("link"),
                            default_start=starts[0], starts=summ, **({"combo_id": st["combo_id"]} if "combo_id" in st else {})))
            D.log("%s：%d 个起点，最早起点今天%s（%.0fs）" % (st["title"], len(starts), summ[starts[0]][0], time.time() - t1))
        except Exception:
            D.log("%s 出错：\n%s" % (st["title"], traceback.format_exc()))
            warn.append("%s 本次计算出错，显示的是上一次结果" % st["title"])
            old = next((s for s in (prev_json or {}).get("strategies", []) if s["id"] == st["id"]), None)
            if old:
                old["stale"] = True; res.append(old)
    live = {x["id"] for x in strategies}
    sdir = os.path.join(OUT, "s")
    for f in os.listdir(sdir) if os.path.isdir(sdir) else []:
        if f.startswith("u-") and f not in live:
            import shutil; shutil.rmtree(os.path.join(sdir, f), ignore_errors=True)
    out = dict(date=today, mode=mode, updated=D.bj_now().strftime("%Y-%m-%d %H:%M"),
               quote_time=pd.to_datetime(q["ts"].astype(str).max(), format="%Y%m%d%H%M%S").strftime("%H:%M"),
               next_trade_day=cal[cal > pd.Timestamp(today)][0].strftime("%Y-%m-%d"),
               warnings=warn, cats=CONF.get("cats", []), strategies=res)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "signals.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    D.log("写出 data/signals.json，总用时 %.0fs" % (time.time() - t0))
    done_set(today, mode)
    if not a.no_push:
        push("%s %s" % (today, "盘中提示" if mode == "intraday" else "收盘复核"))


def push(msg):
    g = lambda *x: subprocess.run(["git", "-C", ROOT] + list(x), capture_output=True, text=True, encoding="utf-8")
    g("add", "-A", "data")
    r = g("commit", "-m", msg)
    if "nothing to commit" in (r.stdout + r.stderr):
        return
    for i in range(3):
        r = g("push")
        if r.returncode == 0:
            D.log("已推送：%s" % msg); return
        g("pull", "--rebase")
    D.log("推送失败：%s" % r.stderr[-300:])


if __name__ == "__main__":
    main()
