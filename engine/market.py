# -*- coding: utf-8 -*-
"""把缓存数据整理成回放用的宽表，并提供模拟账户（按目标市值下单，记录每天的买卖）。"""
import numpy as np, pandas as pd

ANCHOR = pd.Timestamp("2023-01-03")      # 交易日计数起点：与聚宽回测（2023-01-01 开始）一致，调仓日对得上
COST = 0.0015                            # 单边成本（佣金 + 印花税 + 滑点的近似）


class Mkt:
    def __init__(self, H, names, cal, today):
        """H: data.history() 的字典（可能已拼上今天的快照）；names: 代码→名称（当前）；cal: 交易日历"""
        self.today = pd.Timestamp(today)
        U = H["close"].sort_index()
        U = U[U.index <= self.today]
        self.dates = U.index
        self.U_raw = U
        self.U = U.ffill()
        fac = H["fac"].reindex(U.index).ffill().fillna(1.0)
        self.P = self.U / fac                                         # 前复权
        self.AMT = H["amount"].reindex(U.index)
        sh = H["outstanding_share"].reindex(U.index).ffill()
        self.FCAP = self.U * sh                                       # 流通市值（元）
        self.names = names.reindex(U.columns).fillna("")
        self.st = self.names.str.contains("ST") | self.names.str.contains("退")
        self.paused = U.isna() | (self.AMT.fillna(0) <= 0)
        first = U.notna().idxmax()
        self.first = first.where(first > U.index[0], pd.Timestamp("1990-01-01"))   # 缓存开始前就上市的视为老股
        pct = self.U / self.U.shift(1) - 1
        lim = pd.Series(0.10, U.columns)
        lim[U.columns.str[2:].str.startswith(("300", "301", "688", "689"))] = 0.20
        lim[self.st] = 0.05
        self.up = pct.ge(lim - 0.002, axis=1)
        self.dn = pct.le(-lim + 0.002, axis=1)
        cal = pd.DatetimeIndex(cal)
        self.cal = cal[(cal >= ANCHOR)]
        self.nidx = {d: i for i, d in enumerate(self.cal)}
        self.RET = self.P.pct_change().where(~self.paused, 0).fillna(0)

    def n(self, d):
        """d 是从 2023-01-03 起的第几个交易日（0 起）"""
        return self.nidx.get(pd.Timestamp(d), -1)

    def next_rebal(self, d, every):
        """d 之后（含 d 的下一天）第一个满足 n % every == 0 的交易日"""
        fut = self.cal[self.cal > pd.Timestamp(d)]
        for x in fut[:every + 2]:
            if self.n(x) % every == 0:
                return x
        return None

    def can_buy(self, s, d):
        return s in self.U.columns and not self.paused.at[d, s] and not self.up.at[d, s]

    def can_sell(self, s, d):
        return s in self.U.columns and not self.paused.at[d, s] and not self.dn.at[d, s]

    def listed_days(self, s, d):
        return (pd.Timestamp(d) - self.first[s]).days

    def name(self, s):
        return self.names.get(s, s)


class Book:
    """模拟账户：持仓按前复权价计股数；每次下单按单边 COST 扣费；记录每天成交"""
    def __init__(self, mkt, cash=1_000_000.0):
        self.m = mkt; self.cash = cash; self.sh = {}; self.cost = {}
        self.trades = {}          # 日期 → [dict(code, side, w0, w1)]
        self.nav = {}

    def px(self, s, d):
        if s in self.m.P.columns:
            return float(self.m.P.at[d, s])
        return float(self.ext_px[s].get(d, np.nan)) if hasattr(self, "ext_px") and s in self.ext_px else np.nan

    def value(self, d):
        return self.cash + sum(n * self.px(s, d) for s, n in self.sh.items() if n > 0)

    def pos_value(self, s, d):
        return self.sh.get(s, 0) * self.px(s, d)

    def _can(self, s, d, buy):
        if s not in self.m.U.columns:
            return not np.isnan(self.px(s, d))                     # ETF：有价格就能交易
        return self.m.can_buy(s, d) if buy else self.m.can_sell(s, d)

    def order_value(self, s, d, target, tag=""):
        """调到目标市值；返回是否成交"""
        p = self.px(s, d)
        if not p or np.isnan(p) or p <= 0:
            return False
        tot = self.value(d)
        cur = self.pos_value(s, d)
        delta = target - cur
        if abs(delta) < 1:
            return False
        if delta > 0:
            if not self._can(s, d, True):
                return False
            delta = min(delta, self.cash / (1 + COST))
            if delta < 1000:
                return False
            n = delta / p
            self.cost[s] = (self.cost.get(s, 0) * self.sh.get(s, 0) + delta) / (self.sh.get(s, 0) + n)
            self.sh[s] = self.sh.get(s, 0) + n
            self.cash -= delta * (1 + COST)
        else:
            if not self._can(s, d, False):
                return False
            n = min(-delta / p, self.sh.get(s, 0))
            self.sh[s] -= n
            self.cash += n * p * (1 - COST)
            if self.sh[s] * p < 1:
                self.sh.pop(s, None); self.cost.pop(s, None)
        w0, w1 = cur / tot, self.pos_value(s, d) / tot
        if delta > 0:
            side = "买入" if w0 < 0.001 else "加仓"
        else:
            side = "卖出" if w1 < 0.001 else "减仓"
        self.trades.setdefault(d, []).append(dict(code=s, side=side, w0=round(w0, 4), w1=round(w1, 4), tag=tag))
        return True

    def weights(self, d):
        tot = self.value(d)
        return {s: self.pos_value(s, d) / tot for s, n in self.sh.items() if n > 0}

    def mark(self, d):
        self.nav[d] = self.value(d)


def er_ma_ok(s, er_n=20, er_min=0.2, ma_n=20):
    s = np.asarray(s, dtype=float)
    if len(s) < max(er_n + 1, ma_n) or np.isnan(s[-(er_n + 1):]).any():
        return False
    path = np.abs(np.diff(s[-(er_n + 1):])).sum()
    er = (s[-1] - s[-(er_n + 1)]) / path if path > 0 else 0.0
    return bool(er > er_min and s[-1] > s[-ma_n:].mean())


def er_value(s, er_n=20):
    s = np.asarray(s, dtype=float)[-(er_n + 1):]
    path = np.abs(np.diff(s)).sum()
    return (s[-1] - s[0]) / path if path > 0 else 0.0
