# -*- coding: utf-8 -*-
"""本地「假聚宽」：用本地缓存数据模拟聚宽 API，离线跑生成的聚宽代码，和 combo2 引擎对账。
只实现组合模板用到的接口；成交按当天收盘价（14:50 ≈ 收盘），单边 0.15%（与本地 Book 一致）。
用法：
  python engine/jqsim.py <聚宽代码.py> <配置.json> 2024-01-02 2025-06-30 [--official-l2]
  --official-l2：板块用申万官方二级行业指数（和本地引擎一致，只对账逻辑）；默认走模板自己的成分股合成
"""
import os, sys, json, types, time, datetime as dt
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
COST = 0.0015


def J(c):
    return c if "." in c else c[2:] + (".XSHG" if c.startswith("sh") else ".XSHE")


def L(c):
    return c if "." not in c else ("sh" if c.endswith("XSHG") else "sz") + c[:6]


class Field:
    def __init__(s, t, n): s.t, s.n = t, n
    def in_(s, x): return ("in", s.n, [L(c) if "." in c else c for c in x])
    def __ge__(s, x): return ("ge", s.n, x)
    def __le__(s, x): return ("le", s.n, x)


class Table:
    def __init__(s, name): s._name = name
    def __getattr__(s, k):
        if k.startswith("_"): raise AttributeError(k)
        return Field(s._name, k)


class Query:
    def __init__(s, *f): s.f = [(x.t, x.n) for x in f]; s.flt = []
    def filter(s, *a): s.flt = list(a); return s


class Sim:
    def __init__(self, m, ctx, official_l2=False):
        self.m, self.ctx = m, ctx
        self.official_l2 = official_l2
        self.d = None
        self.cash = 0.0; self.sh = {}; self.avg = {}
        self.funcs = []
        self.rec = {}
        self.nav = {}
        self.ntrade = 0
        self.ec = ctx.etf_close; self.ea = ctx.etf_amt
        self.enames = {J(c): n for c, n in ctx.etf_list}
        self.tsh = (ctx.q["tcap"] / ctx.q["price"])
        self.idx = {}
        self.inv = {}
        for c6, l2 in ctx.ind.items():
            self.inv.setdefault(l2, []).append(c6)
        self.cal = m.dates

    # ---------- 价格 ----------
    def is_etf(self, c):
        return c in self.ec.columns

    def close_raw(self, c, d):
        if c == "sh511880": return 100.0
        if self.is_etf(c):
            v = self.ec.at[d, c] if d in self.ec.index else np.nan
            return float(v)
        return float(self.m.U.at[d, c]) if c in self.m.U.columns else np.nan

    def index_series(self, sym):
        sym = L(sym)
        if sym not in self.idx:
            if sym == "sh000852": s = self.ctx.i1000
            elif sym == "sh000300": s = self.ctx.i300
            else:
                import data as D
                s = D.index_daily(sym)
            self.idx[sym] = s
        return self.idx[sym]

    # ---------- 聚宽接口 ----------
    def api(self):
        sim = self
        jq = types.ModuleType("jqdata")
        jq.valuation, jq.indicator, jq.income = Table("valuation"), Table("indicator"), Table("income")
        jq.query = lambda *f: Query(*f)
        jq.finance = types.SimpleNamespace(SW1_DAILY_PRICE=Table("sw1"), run_query=self.run_query)
        jq.g = types.SimpleNamespace()
        jq.log = types.SimpleNamespace(info=lambda m: None, warn=lambda m: None, set_level=lambda *a: None)
        for k in ("set_benchmark", "set_option", "set_order_cost", "set_slippage"):
            setattr(jq, k, lambda *a, **kw: None)
        jq.OrderCost = lambda **kw: None
        jq.PriceRelatedSlippage = lambda x: None
        jq.MarketOrderStyle = lambda p=None: None
        jq.run_daily = lambda f, time=None: sim.funcs.append((time, f))
        jq.record = lambda **kw: sim.rec.setdefault(sim.d, kw)
        jq.get_current_data = self.get_current_data
        jq.get_all_securities = self.get_all_securities
        jq.get_price = self.get_price
        jq.history = self.history
        jq.attribute_history = self.attribute_history
        jq.get_fundamentals = self.get_fundamentals
        jq.get_industry = self.get_industry
        jq.get_industries = self.get_industries
        jq.get_industry_stocks = self.get_industry_stocks
        jq.get_trade_days = self.get_trade_days
        jq.get_security_info = lambda c: types.SimpleNamespace(display_name=sim.enames.get(c, c))
        jq.order = lambda s, n, style=None: sim._order(s, n)
        jq.order_target = lambda s, n, style=None: sim._order(s, n - sim.sh.get(s, 0))
        jq.order_value = lambda s, v, style=None: sim._order(s, int(v / sim.close_raw(L(s), sim.d) / 100) * 100)
        jq.__all__ = [k for k in dir(jq) if not k.startswith("_")]
        return jq

    def get_current_data(self):
        sim, d, m = self, self.d, self.m
        class CD(dict):
            def __missing__(cd, s):
                c = L(s)
                if c == "sh511880":
                    o = types.SimpleNamespace(last_price=100.0, high_limit=1e9, low_limit=0, paused=False, is_st=False, name="银华日利")
                elif sim.is_etf(c):
                    p = sim.close_raw(c, d)
                    o = types.SimpleNamespace(last_price=None if np.isnan(p) else p, high_limit=1e9, low_limit=0, paused=bool(np.isnan(p)),
                                              is_st=False, name=sim.enames.get(s, s))
                elif c in m.U.columns:
                    p = float(m.U.at[d, c]); pz = bool(m.paused.at[d, c])
                    up, dn = bool(m.up.at[d, c]), bool(m.dn.at[d, c])
                    o = types.SimpleNamespace(last_price=None if np.isnan(p) else p, high_limit=p if up else 1e9, low_limit=p if dn else 0,
                                              paused=pz, is_st=bool(m.st[c]), name=str(m.names.get(c, "")))
                else:
                    o = types.SimpleNamespace(last_price=None, high_limit=1e9, low_limit=0, paused=True, is_st=False, name="")
                cd[s] = o
                return o
        return CD()

    def get_all_securities(self, types_, date=None):
        if types_ == ["etf"]:
            rows = {}
            for c, n in self.ctx.etf_list:
                if c in self.ec.columns:
                    s = self.ec[c].dropna()
                    if len(s):
                        rows[J(c)] = dict(display_name=n, start_date=s.index[0].date())
            return pd.DataFrame.from_dict(rows, orient="index")
        first = self.m.first
        df = pd.DataFrame({"display_name": self.m.names.values, "start_date": [x.date() for x in first.reindex(self.m.names.index)]},
                          index=[J(c) for c in self.m.names.index])
        return df

    def _dates_upto(self, end, count, strict=False):
        end = pd.Timestamp(end)
        if isinstance(end, pd.Timestamp) and end.hour >= 9 and not strict:
            end = end.normalize()
        ds = self.cal[self.cal < end] if strict else self.cal[self.cal <= end]
        return ds[-count:]

    def _wide(self, codes, ds, field, fq):
        lc = [L(c) for c in codes]
        st = [c for c in lc if c in self.m.U.columns]; et = [c for c in lc if c not in self.m.U.columns and c in self.ec.columns]
        parts = []
        if st:
            src = {"close": self.m.P if fq == "pre" else self.m.U_raw, "money": self.m.AMT}[field]
            x = src.loc[ds, st]
            if field == "close" and fq == "pre":           # 聚宽的前复权以「回测当天」为基准：当天价 = 不复权价
                x = x * (self.m.U.loc[self.d, st] / self.m.P.loc[self.d, st])
            parts.append(x)
        if et:
            src = self.ec if field == "close" else self.ea
            parts.append(src.reindex(ds)[et])
        w = pd.concat(parts, axis=1) if parts else pd.DataFrame(index=ds)
        w.columns = [J(c) for c in w.columns]
        return w

    def get_price(self, codes, end_date=None, count=1, fields=("close",), fq="pre", panel=False, frequency="1d", **kw):
        if frequency == "1m":
            s = self.index_series(codes)
            v = s[s.index <= self.d]
            return pd.DataFrame({"close": [float(v.iloc[-1])]})
        ds = self._dates_upto(end_date, count)
        f = fields[0]
        w = self._wide(list(codes), ds, f, fq)
        w.index.name = "time"
        out = w.stack(future_stack=True).rename(f).reset_index()
        out.columns = ["time", "code", f]
        return out

    def history(self, count, unit="1d", field="close", security_list=None, df=True, skip_paused=False, fq="pre"):
        ds = self._dates_upto(self.d, count, strict=True)
        w = self._wide(security_list, ds, field, fq)
        return {c: w[c].values for c in w.columns} if not df else w

    def attribute_history(self, code, count, unit="1d", fields=("close",), skip_paused=True, df=True, fq="pre"):
        s = self.index_series(code)
        v = s[s.index < self.d].iloc[-count:].values
        return {"close": v}

    def get_fundamentals(self, q, date=None):
        d = pd.Timestamp(date)
        codes = []
        for f in q.flt:
            if f[0] == "in":
                codes = f[2]
        codes = [c for c in codes if c in self.m.U.columns]
        fi = self.ctx.fin(d).reindex([c[2:] for c in codes]); fi.index = codes
        out = pd.DataFrame(index=codes)
        for t, n in q.f:
            if n == "code": continue
            if n == "circulating_market_cap": out[n] = self.m.FCAP.loc[d, codes].values / 1e8
            elif n == "market_cap": out[n] = (self.m.U.loc[d, codes] * self.tsh.reindex(codes)).values / 1e8
            elif n == "pe_ratio": out[n] = np.where(fi["np"].values < 0, -10.0, 10.0)
            elif n == "pb_ratio": out[n] = np.where(fi["bps"].values <= 0, -1.0, 1.0)
            elif n == "roe": out[n] = fi["roe_a"].values * 100 / 4
            elif n == "net_profit": out[n] = fi["np"].values
            elif n == "statDate":
                p = "%d1231" % (d.year - 1)
                F = self.ctx.F
                done = set()
                if p in F:
                    pub = F[p]["pub"]; done = set(pub.index[pub <= d])
                out[n] = ["%d-12-31" % (d.year - 1) if c[2:] in done else "%d-09-30" % (d.year - 1) for c in codes]
        out.index = [J(c) for c in out.index]
        out.index.name = "code"
        return out.reset_index()

    def get_industry(self, codes, date=None):
        return {c: {"sw_l2": {"industry_name": self.ctx.ind.get(L(c)[2:], "未知")}} for c in codes}

    def get_industries(self, name, date=None):
        cols = self.ctx.sw1.columns if name == "sw_l1" else self.ctx.sw2.columns
        names = {} if name == "sw_l1" else self.ctx.sw2_names
        return pd.DataFrame({"name": [names.get(c, c) for c in cols], "start_date": [dt.date(2000, 1, 1)] * len(cols)}, index=list(cols))

    def get_industry_stocks(self, code, date=None):
        if code == "801780":
            return [J(c) for c in self.ctx.bank_codes]
        return [J(("sh" if c.startswith("6") else "sz") + c) for c in self.inv.get(code, [])]

    def get_trade_days(self, start_date=None, end_date=None, count=None):
        full = pd.DatetimeIndex(self.m.cal) if len(self.m.cal) else self.cal
        allc = self.cal.union(full)
        e = pd.Timestamp(end_date)
        if count:
            return [x.date() for x in allc[allc <= e][-count:]]
        return [x.date() for x in allc[(allc >= pd.Timestamp(start_date)) & (allc <= e)]]

    def run_query(self, q):
        codes, lo, hi = [], None, None
        for f in q.flt:
            if f[0] == "in": codes = f[2]
            elif f[0] == "ge": lo = pd.Timestamp(f[2])
            elif f[0] == "le": hi = pd.Timestamp(f[2])
        s = self.ctx.sw1
        s = s[(s.index >= lo) & (s.index <= hi)][[c for c in codes if c in s.columns]]
        s.index.name = "date"
        out = s.stack(future_stack=True).dropna().rename("close").reset_index(); out.columns = ["date", "code", "close"]
        return out

    # ---------- 账户 ----------
    def _order(self, s, n):
        c = L(s); p = self.close_raw(c, self.d)
        if not n or np.isnan(p) or p <= 0:
            return
        n = int(n)
        if n < 0: n = -min(-n, self.sh.get(s, 0))
        amt = n * p
        if n > 0 and amt * (1 + COST) > self.cash + 1e-6:
            return
        self.cash -= amt + abs(amt) * COST
        self.sh[s] = self.sh.get(s, 0) + n
        if self.sh[s] == 0: self.sh.pop(s)
        self.ntrade += 1

    def portfolio(self):
        sim = self
        pos = {}
        for s, n in self.sh.items():
            p = self.close_raw(L(s), self.d)
            pos[s] = types.SimpleNamespace(total_amount=n, closeable_amount=n, value=n * p, avg_cost=p)
        tv = self.cash + sum(x.value for x in pos.values())
        return types.SimpleNamespace(total_value=tv, available_cash=self.cash, positions=pos)

    def apply_divs(self, prev, d):
        """股票除权：按前复权价变化调整持股数（聚宽送转/分红自动处理，这里近似成股数变化）"""
        for s in list(self.sh):
            c = L(s)
            if c in self.m.U.columns:
                f0 = self.m.P.at[prev, c] / self.m.U.at[prev, c]; f1 = self.m.P.at[d, c] / self.m.U.at[d, c]
                if np.isfinite(f0) and np.isfinite(f1) and f1 > 0 and abs(f0 / f1 - 1) > 1e-6:
                    n0 = self.sh[s]; n1 = n0 * f1 / f0                 # 送转/分红折成股数；零头按现价换成现金
                    keep = int(n1)
                    self.cash += (n1 - keep) * float(self.m.U.at[d, c])
                    if keep > 0:
                        self.sh[s] = keep
                    else:
                        self.sh.pop(s)


def run(code_path, cfg, start, end, official_l2=False, m=None, ctx=None):
    if m is None:
        import bt_nightly as B
        m, ctx = B.build_ctx()
    sim = Sim(m, ctx, official_l2)
    sim.cash = 1e7
    jq = sim.api()
    sys.modules["jqdata"] = jq
    src = open(code_path, encoding="utf-8").read() if code_path.endswith(".py") else code_path
    ns = {"__name__": "strat"}
    exec(compile(src, "strat", "exec"), ns)
    if cfg is not None:
        ns["CONFIG"] = cfg
    for k in ("g",):
        ns[k] = jq.g
    if official_l2:
        def l2_official(context):
            s = ctx.sw2[ctx.sw2.index < pd.Timestamp(context.current_dt.date())].iloc[-186:]
            return s
        ns["l2_close"] = l2_official
    days = m.dates[(m.dates >= pd.Timestamp(start)) & (m.dates <= pd.Timestamp(end))]
    context = types.SimpleNamespace()
    sim.d = days[0]
    context.current_dt = days[0].to_pydatetime(); context.previous_date = m.dates[m.dates < days[0]][-1].date()
    context.portfolio = sim.portfolio()
    ns["initialize"](context)
    trade = dict((t, f) for t, f in sim.funcs)
    t0 = time.time()
    prev = None
    for d in days:
        sim.d = d
        if prev is not None:
            sim.apply_divs(prev, d)
        context.current_dt = (d + pd.Timedelta(hours=14, minutes=50)).to_pydatetime()
        context.previous_date = m.dates[m.dates < d][-1].date()
        class PF:
            pass
        context.portfolio = LivePF(sim)
        trade["14:50"](context)
        trade["after_close"](context)
        sim.nav[d] = sim.portfolio().total_value
        prev = d
    return pd.Series(sim.nav) / 1e7, sim, ns, (m, ctx), time.time() - t0


class LivePF:
    """每次访问都按当前持仓重新计算（下单后马上生效）"""
    def __init__(self, sim): self.sim = sim
    @property
    def positions(self): return self.sim.portfolio().positions
    @property
    def total_value(self): return self.sim.portfolio().total_value
    @property
    def available_cash(self): return self.sim.cash


if __name__ == "__main__":
    code, cfgp, s, e = sys.argv[1:5]
    cfg = json.load(open(cfgp, encoding="utf-8")) if cfgp != "-" else None
    nav, sim, ns, _, secs = run(code, cfg, s, e, "--official-l2" in sys.argv)
    print("净值 %.4f  最大回撤 %.2f%%  成交 %d 笔  用时 %.0fs" % (nav.iloc[-1], (nav / nav.cummax() - 1).min() * 100, sim.ntrade, secs))
