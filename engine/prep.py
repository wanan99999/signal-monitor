# -*- coding: utf-8 -*-
"""把准备好的各类数据组装成策略要用的 (Mkt, Ctx)。
run.py（每天盘中/收盘）和 bt_nightly.py（夜间回测）都只调这一个函数，组装方式只有一份；
两边不同的只是数据从哪来（实时抓 / 回测底库拼接），由调用方准备好传进来。"""
import data as D
from market import Mkt
import strategies as S


def assemble(H, q, cal, today, *, F, sw1, sw2, i1000, i300, etf_close, etf_amt, etf_list, ind=None, banks=None):
    """H 历史行情宽表；q 腾讯行情（取股票名称）；F 业绩报表；sw1/sw2 申万一/二级指数；
    i1000/i300 中证1000、沪深300 日线；etf_* ETF 收盘/成交额/名单；
    ind 申万二级成分（默认读缓存）；banks 银行股代码（默认读缓存）"""
    l2 = D.sw_list(2)
    if ind is None:
        ind = D.load("sw_l2_members.pkl")
    if banks is None:
        banks = D.load("sw_bank.pkl")
    bank_codes = [("sh" if c.startswith("6") else "sz") + c for c in banks]
    m = Mkt(H, q["name"], cal, today)
    ctx = S.Ctx(F=F, ind=ind, q=q, bank_codes=bank_codes, i1000=i1000, i300=i300, sw1=sw1, sw2=sw2,
                sw2_names=dict(l2), etf_close=etf_close, etf_amt=etf_amt, etf_list=etf_list)
    return m, ctx
