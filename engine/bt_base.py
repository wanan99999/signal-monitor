# -*- coding: utf-8 -*-
"""一次性：搭回测底库 cache/bt/（2021-11 起的个股、申万、ETF、2021Q3 起业绩报表）。
之后每晚 bt_nightly.py 把实盘缓存（cache/）的新数据接到底库后面，不用重抓。
用法：python engine/bt_base.py        约 30–40 分钟（新浪逐只抓）
"""
import os, sys, shutil
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BT = os.path.join(ROOT, "cache", "bt")
os.environ["SM_CACHE"] = BT
os.environ["SM_HIST_START"] = "20211101"
os.environ["SM_FIN_N"] = "21"
os.makedirs(BT, exist_ok=True)
for f in ("calendar.pkl", "codes.pkl", "sw_l1_list.pkl", "sw_l2_list.pkl", "sw_l2_members.pkl", "sw_bank.pkl", "etf_list.pkl"):
    src = os.path.join(ROOT, "cache", f)
    if os.path.exists(src) and not os.path.exists(os.path.join(BT, f)):
        shutil.copy2(src, os.path.join(BT, f))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import socket; socket.setdefaulttimeout(30)
import data as D
import strategies as S

step = sys.argv[1] if len(sys.argv) > 1 else "all"
if step in ("all", "fin"):
    F = D.fin_reports(); D.log("业绩报表 %d 期：%s ~ %s" % (len(F), min(F), max(F)))
if step in ("all", "sw"):
    for lv, nm in ((1, "sw_l1.pkl"), (2, "sw_l2.pkl")):
        x = D.sw_hist([c for c, _ in D.sw_list(lv)], nm, 0)
        D.log("申万 %d 级 %s：%s ~ %s" % (lv, x.shape, x.index[0].date(), x.index[-1].date()))
if step in ("all", "etf"):
    cand = [c for c, n in D.etf_list() if not any(k in n for k in S.ETF_EXCLUDE)]
    ec, ea = D.etf_hist(cand, 0)
    D.log("ETF %s：%s ~ %s" % (ec.shape, ec.index[0].date(), ec.index[-1].date()))
if step in ("all", "hist"):
    D.build_history(D.load("codes.pkl"))
D.log("回测底库完成")
