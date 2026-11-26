# -*- coding: utf-8 -*-
"""每晚本地回测：云端「我的组合」+ V1.4 原方案，从 2023-01-03 跑到最近收盘，写 data/combos/results.json 并推送。
数据 = 回测底库 cache/bt/（tools/bt_base.py 一次性搭好）+ 实盘缓存 cache/ 的新数据，接缝处对齐复权，不重抓。
用法：
  python engine/bt_nightly.py            直接跑
  python engine/bt_nightly.py --auto     定时任务用：今天收盘已复核、今晚还没算过才跑
  python engine/bt_nightly.py --no-push
"""
import os, sys, json, time, pickle, argparse, subprocess, threading, socket, hashlib
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
socket.setdefaulttimeout(30)
import data as D
import prep
import combo2 as C2
import cloud

ROOT = D.ROOT
BT = os.path.join(D.CACHE, "bt")
OUT = os.path.join(ROOT, "data", "combos", "results.json")
START = "2023-01-03"
V14_ID = "_v14"


def _watchdog(minutes=90):
    time.sleep(minutes * 60)
    try: D.log("夜间回测超过 %d 分钟，强制结束" % minutes)
    finally: os._exit(3)


def bload(name):
    p = os.path.join(BT, name)
    return pickle.load(open(p, "rb")) if os.path.exists(p) else None


def splice_scaled(base, live):
    """base 在 live 第一天之前的部分接到 live 前面；按接缝那天把 base 缩放到 live 的口径（复权价、份额折算）"""
    d0 = live.index[0]
    b = base[base.index <= d0]
    if not len(b):
        return live
    ref = b.iloc[-1] if b.index[-1] == d0 else None
    if ref is None:
        return pd.concat([b, live]).sort_index()
    s = (live.loc[d0] / ref).reindex(b.columns)
    s = s.where(np.isfinite(s) & (s > 0), 1.0)
    old = b[b.index < d0].mul(s, axis=1)
    return pd.concat([old, live]).sort_index()


def build_hist():
    B, L = bload("hist.pkl"), D.load("hist.pkl")
    d0 = L["close"].index[0]
    if B is None or d0 not in B["close"].index:
        raise RuntimeError("回测底库缺失或接不上（先跑 tools/bt_base.py）")
    H = {}
    for k in L:
        if k == "fac":
            # 前复权 = close / fac：接缝那天 base 的 fac 乘到和 live 一样
            H[k] = splice_scaled(B[k], L[k]).ffill().fillna(1.0)
        else:
            H[k] = pd.concat([B[k][B[k].index < d0], L[k]]).sort_index()
    cols = L["close"].columns
    H = {k: v.reindex(columns=cols) for k, v in H.items()}
    H["fac"] = H["fac"].ffill().fillna(1.0)
    return H


def build_ctx():
    t0 = time.time()
    H = build_hist()
    today = H["close"].index[-1]
    q = D.tx_quote(D.load("codes.pkl"))
    cal = D.calendar()
    i1000 = D.index_daily("sh000852"); i300 = D.index_daily("sh000300")
    i1000, i300 = i1000[i1000.index <= today], i300[i300.index <= today]
    sw = {}
    for nm in ("sw_l1.pkl", "sw_l2.pkl"):
        b, l = bload(nm), D.load(nm)
        sw[nm] = (l.combine_first(b) if b is not None else l).sort_index()
        sw[nm] = sw[nm][sw[nm].index <= today]
    F = dict(bload("fin.pkl") or {}); F.update(D.load("fin.pkl") or {})
    F = D.fix_annual_pub(F, D.report_dates(range(2022, today.year)))       # 往年年报的公告日被改写过，换成实际披露日
    elist = D.load("etf_list.pkl")
    (bc, ba), (lc, la) = bload("etf_hist.pkl"), D.load("etf_hist.pkl")
    ec = splice_scaled(bc, lc); ea = pd.concat([ba[ba.index < la.index[0]], la]).sort_index()
    ec, ea = ec[ec.index <= today], ea[ea.index <= today]
    m, ctx = prep.assemble(H, q, cal, today, F=F, sw1=sw["sw_l1.pkl"], sw2=sw["sw_l2.pkl"], i1000=i1000, i300=i300,
                           etf_close=ec, etf_amt=ea, etf_list=elist)
    D.log("夜间回测数据就绪：%s ~ %s，%d 只（%.0fs）" % (H["close"].index[0].date(), today.date(), H["close"].shape[1], time.time() - t0))
    return m, ctx


def stats(nav):
    nav = pd.Series(nav).sort_index()
    nav = nav / nav.iloc[0]
    n = len(nav) - 1
    ann = float(nav.iloc[-1] ** (252 / n) - 1) if n > 0 else 0.0
    mdd = float((nav / nav.cummax() - 1).min())
    yrs = {}
    for y, s in nav.groupby(nav.index.year):
        prev = nav[nav.index.year < y]
        base = prev.iloc[-1] if len(prev) else s.iloc[0]
        yrs[str(y)] = round(float(s.iloc[-1] / base - 1), 4)
    wk = nav.groupby([nav.index.isocalendar().year, nav.index.isocalendar().week]).tail(1)
    if wk.index[-1] != nav.index[-1]:
        wk = pd.concat([wk, nav.iloc[-1:]])
    return dict(ann=round(ann, 4), mdd=round(mdd, 4), cum=round(float(nav.iloc[-1] - 1), 4), years=yrs,
                curve=dict(d=[x.strftime("%Y-%m-%d") for x in wk.index], v=[round(float(v), 4) for v in wk.values]))


def cfg_hash(cfg):
    c = {k: v for k, v in cfg.items() if k != "ui"}
    return hashlib.md5(json.dumps(c, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10]


def push(msg):
    for i in range(4):
        r = subprocess.run(["git", "add", "data/combos/results.json"], cwd=ROOT, capture_output=True, text=True)
        r = subprocess.run(["git", "commit", "-m", msg], cwd=ROOT, capture_output=True, text=True)
        r = subprocess.run(["git", "push"], cwd=ROOT, capture_output=True, text=True)
        if r.returncode == 0:
            D.log("已推送：%s" % msg); return True
        time.sleep(10 * (i + 1))
    D.log("推送失败：%s" % r.stderr[-300:]); return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--only", help="只算某个组合 id（调试）")
    a = ap.parse_args()
    old = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    if a.auto:
        now = D.bj_now(); today = now.strftime("%Y-%m-%d")
        done = json.load(open(os.path.join(D.CACHE, "done.json"), encoding="utf-8")) if os.path.exists(os.path.join(D.CACHE, "done.json")) else {}
        if (today + ":close") not in done:
            return
        if old.get(V14_ID, {}).get("asof") == today:
            return
    threading.Thread(target=_watchdog, daemon=True).start()
    t0 = time.time()
    D.log("夜间回测开始")
    m, ctx = build_ctx()
    asof = m.today.strftime("%Y-%m-%d")
    combos, stale = cloud.combos()
    jobs = [(V14_ID, "全行情切换组合 V1.4（原方案）", C2.V14)] + [(c["id"], c["name"], c["config"]) for c in combos]
    if a.only:
        jobs = [j for j in jobs if j[0].startswith(a.only)]
    out = {}
    solo = {}                                            # 子策略单独全程跑（同样的插件+参数只算一次）

    def parts(cfg):
        res = []
        for sid, sv in cfg["sleeves"].items():
            if not any(row.get(sid, 0) > 0 for row in cfg["alloc"].values()):
                continue
            key = json.dumps([sv["plugin"], sv.get("params", {})], sort_keys=True)
            if key not in solo:
                t2 = time.time()
                try:
                    one = dict(name=sid, max_hold=99, tol=cfg.get("tol", 0.25), rule=dict(type="fixed"),
                               sleeves={sid: sv}, alloc=dict(normal={sid: 1.0}))
                    b1, _ = C2.run(m, ctx, START, one)
                    x = stats(b1.nav)
                    solo[key] = {k: x[k] for k in ("ann", "mdd", "cum", "years")}
                    D.log("  子策略 %s %s 单独跑：年化 %.1f%%（%.0fs）" % (sv["plugin"], sv.get("params", {}), x["ann"] * 100, time.time() - t2))
                except Exception as e:
                    D.log("  子策略 %s 单独跑出错：%s" % (sv["plugin"], e)); solo[key] = None
            if solo[key]:
                use = {st: round(row[sid], 4) for st, row in cfg["alloc"].items() if row.get(sid, 0) > 0}
                res.append(dict(sid=sid, plugin=sv["plugin"], name=sv.get("name") or C2.PLUGINS[sv["plugin"]].META["name"],
                                hold=sv.get("params", {}).get("hold"), use=use, **solo[key]))
        return res

    for cid, name, cfg in jobs:
        t1 = time.time()
        try:
            errs = C2.check(cfg)
            if errs:
                raise ValueError("；".join(errs))
            b, extra = C2.run(m, ctx, START, cfg)
            r = stats(b.nav)
            r.update(asof=asof, ran=D.bj_now().strftime("%Y-%m-%d %H:%M"), start=START, name=name, cfg=cfg_hash(cfg), secs=round(time.time() - t1),
                     states={k: round(v, 3) for k, v in pd.Series(extra.get("states") or {}).value_counts(normalize=True).items()} if isinstance(extra.get("states"), dict) else None)
            r["parts"] = parts(cfg)
            out[cid] = r
            D.log("回测 %s：年化 %.1f%% · 最大回撤 %.1f%% · 累计 %.0f%%（%.0fs）" % (name, r["ann"] * 100, r["mdd"] * 100, r["cum"] * 100, time.time() - t1))
        except Exception as e:
            D.log("回测 %s 出错：%s" % (name, e))
            if cid in old:
                out[cid] = old[cid]
    if a.only:
        old.update(out); out = old
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    D.log("夜间回测完成：%d 个组合，总用时 %.0fs" % (len(out), time.time() - t0))
    if not a.no_push:
        push("%s 组合本地回测" % asof)


if __name__ == "__main__":
    main()
