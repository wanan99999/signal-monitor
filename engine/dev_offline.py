# -*- coding: utf-8 -*-
"""开发用：只用本地缓存（截至最后一个收盘日）搭出 m / ctx，不追加今天的行情。
用法：from dev_offline import load; m, ctx = load()"""
import os, sys, pickle
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data as D
from market import Mkt
import strategies as S

PK = os.path.join(D.CACHE, "dev_ctx.pkl")


def load(refresh=False):
    if os.path.exists(PK) and not refresh:
        return pickle.load(open(PK, "rb"))
    H = D.history()
    today = H["close"].index[-1]
    q = D.tx_quote(D.stock_codes())
    cal = D.calendar()
    i1000 = D.index_daily("sh000852"); i300 = D.index_daily("sh000300")
    i1000, i300 = i1000[i1000.index <= today], i300[i300.index <= today]
    l1 = D.sw_list(1); l2 = D.sw_list(2)
    sw1 = D.sw_hist([c for c, _ in l1], "sw_l1.pkl", 99); sw2 = D.sw_hist([c for c, _ in l2], "sw_l2.pkl", 99)
    sw1, sw2 = sw1[sw1.index <= today], sw2[sw2.index <= today]
    ind = D.sw_members([c for c, _ in l2])
    banks = D.sw_members(["801780"], "sw_bank.pkl")
    bank_codes = [("sh" if c.startswith("6") else "sz") + c for c in banks]
    F = D.load("fin.pkl")
    elist = D.etf_list()
    cand = [c for c, n in elist if not any(k in n for k in S.ETF_EXCLUDE)]
    ec, ea = D.etf_hist(cand, 99)
    ec, ea = ec[ec.index <= today], ea[ea.index <= today]
    m = Mkt(H, q["name"], cal, today)
    ctx = S.Ctx(F=F, ind=ind, q=q, bank_codes=bank_codes, i1000=i1000, i300=i300, sw1=sw1, sw2=sw2,
                sw2_names=dict(l2), etf_close=ec, etf_amt=ea, etf_list=elist)
    pickle.dump((m, ctx), open(PK, "wb"))
    return m, ctx
