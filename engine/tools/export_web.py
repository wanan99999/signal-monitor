# -*- coding: utf-8 -*-
"""给「组合配置」网页导出：策略插件说明（plugins.json）+ 切换规则即时统计要用的指数 / 行业收盘价（mkt.json）。
输出到 D:\\quant-strategies\\combo\\data\\。用法：python engine/tools/export_web.py [--no-mkt]"""
import os, sys, json
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # engine/
import data as D
import combo2 as C

OUT = r"D:\quant-strategies\combo\data"
SINCE = pd.Timestamp("2022-06-01")          # 2023 起统计 + 前面留 120 日均线 / 60 日宽度的回看


def plugins():
    ps = []
    for pid, P in C.PLUGINS.items():
        M = P.META
        ps.append(dict(id=pid, name=M["name"], slots=M["slots"], desc=M["desc"], rules=M["rules"],
                       reselect_on_enter=P.reselect_on_enter,
                       params=[dict(key=k, **v) for k, v in M["params"].items()]))
    return dict(plugins=ps, states=C.STATES, index_names=C.INDEX_NAMES, rule_default=C.Rule.DEFAULT, v14=C.V14)


def mkt():
    out = dict(index={})
    for sym in C.INDEX_NAMES:
        s = D.index_daily(sym)
        s = s[s.index >= SINCE]
        out["index"][sym] = {d.strftime("%Y-%m-%d"): round(float(v), 2) for d, v in s.items()}
    dates = sorted(set().union(*[set(v) for v in out["index"].values()]))
    l1 = D.sw_list(1)
    sw1 = D.sw_hist([c for c, _ in l1], "sw_l1_long.pkl", 1, since=SINCE)
    sw1 = sw1[sw1.index >= SINCE]
    out["dates"] = dates
    out["index"] = {k: [v.get(d) for d in dates] for k, v in out["index"].items()}
    sw1 = sw1.reindex(pd.to_datetime(dates))
    out["sw1"] = [[None if pd.isna(x) else round(float(x), 2) for x in sw1[c].values] for c in sw1.columns]
    return out


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    json.dump(plugins(), open(os.path.join(OUT, "plugins.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if "--no-mkt" not in sys.argv:
        m = mkt()
        json.dump(m, open(os.path.join(OUT, "mkt.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        print("mkt", len(m["dates"]), m["dates"][0], m["dates"][-1], "sw1", len(m["sw1"]))
    print("ok")
