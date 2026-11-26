# -*- coding: utf-8 -*-
"""可配置组合（V2）：每个策略是一个「插件」，各自在自己的影子账户里按自己的规则买卖；
组合层只按切换规则决定每个影子账户分到多少真实的钱，真实持仓 = 各影子账户持仓按比例放大后相加。

配置（JSON）：
  rule    切换规则：type = three_state（原方案）| two_state（不要大盘上涨期）| fixed（不切换）；各项参数见 Rule.DEFAULT
  sleeves 子账户：{id: {plugin, name, slot, params}}；同一个策略可以开多个子账户（例如持 10 只 / 持 5 只）
  alloc   每种行情下各子账户分多少钱：{normal|small|both: {sleeve_id: 比例}}，每行合计 ≤ 1，剩下的买货币基金
  max_hold 总持股上限（只在配置时检查）；tol 真实账户偏离目标多少才调（默认 25%，与 V1.4 一致）
"""
import copy
import numpy as np, pandas as pd
from market import Book, er_ma_ok, er_value
from strategies import Clock, Steady, pre, _prev, ETF_EXCLUDE

STATES = {"normal": "平常", "small": "中小盘走强", "both": "中小盘走强 + 大盘上涨期"}
# 三个位置：id（配置里用）、key（信号数据里的简写）、名称、什么时候用
SLOTS = [dict(id="steady", key="a", name="稳健位", desc="市场没起色时用"),
         dict(id="stock", key="s", name="个股位", desc="中小盘涨起来时用"),
         dict(id="sector", key="b", name="板块位", desc="大盘也在上涨期时用")]
INDEX_NAMES = {"sh000852": "中证1000", "sh000905": "中证500", "sz399006": "创业板指", "sz399303": "国证2000", "sh000300": "沪深300",
               "sh000016": "上证50", "sh000001": "上证指数"}


# ============================== 切换规则 ==============================
class Rule:
    DEFAULT = dict(type="three_state",
                   small=dict(index="sh000852", er_n=20, er_min=0.2, ma_n=20),
                   big=dict(index="sh000300", ma_n=120, slope_lag=20, breadth_min=0.5, breadth_ma=60),
                   confirm_days=0)

    def __init__(self, ctx, cfg):
        self.ctx = ctx
        c = copy.deepcopy(self.DEFAULT)
        for k, v in (cfg or {}).items():
            if isinstance(v, dict):
                c[k].update(v)
            else:
                c[k] = v
        self.c = c
        self.state, self.pending, self.cnt = None, None, 0
        self.info = {}

    def idx(self, sym):
        ctx = self.ctx
        if sym == "sh000852" and hasattr(ctx, "i1000"):
            return ctx.i1000
        if sym == "sh000300" and hasattr(ctx, "i300"):
            return ctx.i300
        def f():
            import data as D
            s = D.index_daily(sym)
            if hasattr(ctx, "today_spot"):                     # 盘中：今天用实时价补上
                s = s[s.index < ctx.today_spot[0]]
                v = ctx.today_spot[1](sym)
                if v:
                    s.loc[ctx.today_spot[0]] = float(v)
            return s
        return ctx.cached(("index", sym), f)

    def raw(self, d):
        """当天不考虑确认天数的行情状态；与 V1.4 一致：中小盘用含今天的价格，大盘用昨天收盘"""
        c = self.c
        key = ("rule_raw", d, repr(c["type"]), repr(sorted(c["small"].items())), repr(sorted(c["big"].items())))
        return self.ctx.cached(key, lambda: self._raw(d))

    def _raw(self, d):
        c, info = self.c, {}
        if c["type"] == "fixed":
            return "normal", info
        s = c["small"]
        x = self.idx(s["index"]); x = x[x.index <= d].values
        g1 = er_ma_ok(x[-(max(s["er_n"] + 1, s["ma_n"]) + 1):], s["er_n"], s["er_min"], s["ma_n"])
        info.update(small_name=INDEX_NAMES.get(s["index"], s["index"]), er=er_value(x[-(s["er_n"] + 1):], s["er_n"]) if len(x) > s["er_n"] else 0,
                    small_px=x[-1] if len(x) else None, small_ma=x[-s["ma_n"]:].mean() if len(x) else None, g1=g1)
        if c["type"] == "two_state":
            return ("small" if g1 else "normal"), info
        b = c["big"]
        h = self.idx(b["index"]); h = h[h.index < d].values
        up = False
        if len(h) >= b["ma_n"] + b["slope_lag"]:
            ma_now, ma_old = h[-b["ma_n"]:].mean(), h[-(b["ma_n"] + b["slope_lag"]):-b["slope_lag"]].mean()
            L1 = self.ctx.sw1
            px = L1[L1.index < d].iloc[-(b["breadth_ma"] + 70):]
            px = px.loc[:, px.notna().sum() >= b["breadth_ma"]]
            br = float((px.iloc[-1] > px.iloc[-b["breadth_ma"]:].mean()).mean()) if px.shape[1] else 0
            up = bool(h[-1] > ma_now and ma_now > ma_old and br >= b["breadth_min"])
            info.update(big_name=INDEX_NAMES.get(b["index"], b["index"]), big_px=h[-1], big_ma=ma_now, big_ma_old=ma_old, br=br)
        info["up"] = up
        return ("both" if (g1 and up) else ("small" if g1 else "normal")), info

    def step(self, d):
        r, self.info = self.raw(d)
        n = int(self.c.get("confirm_days") or 0)
        if self.state is None or n <= 0:
            self.state = r
            return r
        if r == self.state:
            self.pending, self.cnt = None, 0
        elif r == self.pending:
            self.cnt += 1
        else:
            self.pending, self.cnt = r, 1
        if self.pending is not None and self.cnt >= n:
            self.state, self.pending, self.cnt = r, None, 0
        return self.state


# ============================== 插件 ==============================
class Plugin:
    """插件基类：自己的影子账户 self.b（100 万模拟钱），自己的调仓日历（以起点为第 0 天）"""
    META = {}
    reselect_on_enter = False

    def __init__(self, m, ctx, start, **p):
        self.m, self.ctx = m, ctx
        self.p = {k: v["default"] for k, v in self.META.get("params", {}).items()}
        self.p.update({k: v for k, v in p.items() if v is not None})
        self.b = Book(m)
        self.ck = Clock(m, start)
        self.notes = []

    def step(self, d, force=False):
        raise NotImplementedError

    def conditions(self):
        return []

    @classmethod
    def max_hold(cls, p):
        return int(p.get("hold", cls.META["params"]["hold"]["default"]))


class SteadyP(Plugin):
    META = dict(id="steady", name="大盘股熊市震荡市稳健", slots=["steady"],
                desc="大盘 800 家盈利公司里，挑离一年最高价近、走势平稳的股票；组合波动大或回撤大时自己降仓",
                rules=dict(risk="单只亏 10% 当天卖；组合近 40 天波动超过目标就按比例降仓；从高点亏 15% 半仓、亏 25% 清空",
                           rotate="每 5 个交易日重排，跌出缓冲名次才卖；同一行业有上限", size="每只一样多的钱 × 建议总仓位"),
                params=dict(hold=dict(default=10, label="持股数", min=3, max=30),
                            buffer=dict(default=20, label="跌出前几名才卖", min=5, max=80),
                            ind_max=dict(default=3, label="同一行业最多几只", min=1, max=10),
                            every=dict(default=5, label="几天调仓一次", min=1, max=20),
                            stop=dict(default=0.10, label="单只亏多少止损", min=0.03, max=0.5),
                            vol_target=dict(default=0.15, label="波动目标", min=0.05, max=1.0),
                            dd_half=dict(default=0.15, label="回撤多少减半仓", min=0.05, max=0.5),
                            dd_out=dict(default=0.25, label="回撤多少清空", min=0.05, max=0.8)))

    def __init__(self, m, ctx, start, **p):
        super().__init__(m, ctx, start, **p)
        q = self.p
        self.S = Steady(m, ctx, q["hold"], q["buffer"], q["ind_max"], stop=q["stop"], vol_target=q["vol_target"],
                        dd_half=q["dd_half"], dd_out=q["dd_out"])
        self.g_expo, self.band = 0.0, 0.10

    def _to(self, d, picks, expo):
        b, q = self.b, self.p
        tv = b.value(d) * 0.99 * expo / q["hold"]
        for s in list(b.weights(d)):
            if s not in picks:
                b.order_value(s, d, 0, "跌出前 %d 名" % q["buffer"])
            elif b.pos_value(s, d) > tv * 1.05:
                b.order_value(s, d, tv, "仓位调低")
        for s in picks:
            if b.pos_value(s, d) < tv * 0.95:
                b.order_value(s, d, tv, "新选入" if b.pos_value(s, d) == 0 else "仓位调高")

    def step(self, d, force=False):
        S, b, m = self.S, self.b, self.m
        for s in list(b.weights(d)):
            if b.cost.get(s) and b.px(s, d) / b.cost[s] - 1 < -S.stop and m.can_sell(s, d):
                b.order_value(s, d, 0, "亏损超过 %.0f%% 止损" % (S.stop * 100))
                if s in S.picks:
                    S.picks.remove(s)
        expo = S.exposure()
        if self.ck.n(d) % self.p["every"] == 0 or force:
            S.picks = S.select(d, list(b.weights(d)))
            self._to(d, S.picks if expo > 0 else [], expo); self.g_expo = expo
        elif abs(expo - self.g_expo) >= self.band or (expo == 0 and self.g_expo > 0):
            self._to(d, [s for s in S.picks if s in b.sh] if expo > 0 else [], expo); self.g_expo = expo
        S.after_close(d)
        b.mark(d)

    def conditions(self):
        L = self.S.last; vol = L.get("vol")
        return [dict(text="建议仓位 %.0f%%（近 40 天波动 %s，高点回落 %.1f%%）" % (L.get("expo", 1) * 100, "%.1f%%" % (vol * 100) if vol else "—",
                                                                       -L.get("dd", 0) * 100), ok=bool(L.get("expo", 1) >= 0.99))]


class BankP(Plugin):
    META = dict(id="bank", name="银行股顺势", slots=["steady", "stock"],
                desc="上年末市值前 20 的银行股里，持有离一年最高价最近的几只；不追近期大涨的",
                rules=dict(risk="不设止损（测试过止损都更差）", rotate="每 5 个交易日重排，掉出缓冲名次才卖；近 20 天涨超 10% 的不新买",
                           size="每只一样多的钱，调仓日调回等权"),
                params=dict(hold=dict(default=4, label="持股数", min=1, max=10),
                            keep_rank=dict(default=6, label="掉出前几名才卖", min=1, max=20),
                            chase=dict(default=0.10, label="近 20 天涨超多少不新买", min=0.0, max=1.0),
                            every=dict(default=5, label="几天调仓一次", min=1, max=20),
                            pool_n=dict(default=20, label="股票池（市值前几家）", min=5, max=42)))

    def __init__(self, m, ctx, start, **p):
        super().__init__(m, ctx, start, **p)
        self.banks = [c for c in ctx.bank_codes if c in m.P.columns]
        self.tsh = (ctx.q["tcap"] / ctx.q["price"]).reindex(self.banks)
        self.pos = {d: i for i, d in enumerate(m.dates)}
        self.sig = None

    def pool(self, y):
        m, n = self.m, self.p["pool_n"]
        def f():
            ye = m.dates[m.dates.year == y - 1]
            d = ye[-1] if len(ye) else m.dates[0]
            return list((m.U.loc[d, self.banks] * self.tsh).dropna().sort_values(ascending=False).index[:n])
        return self.ctx.cached(("bank_pool", y, n), f)

    def signal(self, d):
        m, i = self.m, self.pos[d]
        def f():
            rows = []
            for c in self.pool(d.year):
                if i < 249 or m.first[c] > m.dates[i - 249]:
                    continue
                w = m.P[c].iloc[i - 249:i + 1]
                rows.append(dict(code=c, dd=w.iloc[-1] / w.max() - 1, ret=w.iloc[-1] / m.P[c].iloc[i - 20] - 1))
            return pd.DataFrame(rows, columns=["code", "dd", "ret"]).sort_values("dd", ascending=False).reset_index(drop=True)
        return self.ctx.cached(("bank_sig", d, self.p["pool_n"]), f)

    def step(self, d, force=False):
        b, q, m = self.b, self.p, self.m
        if self.ck.n(d) % q["every"] == 0 or force:
            sig = self.signal(d); self.sig = sig
            rank = {c: k for k, c in enumerate(sig.code)}
            held = list(b.weights(d))
            keep = [c for c in held if c in rank and rank[c] < q["keep_rank"]]
            for c in held:
                if c not in keep and not b.order_value(c, d, 0, "不在股票池" if c not in rank else "排名掉到第 %d" % (rank[c] + 1)):
                    keep.append(c)
            need, new = q["hold"] - len(keep), []
            for _, r in sig.iterrows():
                if need <= 0:
                    break
                if r.code in keep or not m.can_buy(r.code, d) or r.ret > q["chase"]:
                    continue
                new.append(r.code); need -= 1
            tv = b.value(d) / q["hold"]
            for c in sorted(keep, key=lambda c: -b.pos_value(c, d)):
                b.order_value(c, d, tv, "调回等权")
            for c in new:
                b.order_value(c, d, min(tv, b.cash / 1.0015), "排名第 %d" % (rank[c] + 1))
        b.mark(d)


class SmallCapP(Plugin):
    META = dict(id="smallcap", name="小市值反转（V1.4 个股块）", slots=["stock"],
                desc="去掉成交额最小的 30%，挑公司越小、最近 20 天跌得越多的股票；避开退市 / ST 风险股",
                rules=dict(risk="股价 <2 元、资不抵债不买；1–4 月亏损股不买；4/15 起年报未发不买；被 ST 当天换掉",
                           rotate="每 5 个交易日重排，跌出缓冲名次才卖；上场当天重新选", size="每只一样多的钱"),
                params=dict(hold=dict(default=10, label="持股数", min=3, max=30),
                            buffer=dict(default=20, label="跌出前几名才卖", min=3, max=80),
                            every=dict(default=5, label="几天调仓一次", min=1, max=20),
                            risk_filter=dict(default=True, label="退市 / ST 风险过滤")))
    reselect_on_enter = True

    def __init__(self, m, ctx, start, **p):
        super().__init__(m, ctx, start, **p)
        self.picks = []
        x = pre(m); self.AMT20, self.R20 = x["AMT20"], x["R20"]

    def rank(self, d):
        m, ctx, rf = self.m, self.ctx, self.p["risk_filter"]
        def f():
            pd_ = _prev(m, d)
            base = list(m.P.columns[(~m.st & ~m.paused.loc[d] & (m.first <= pd_ - pd.Timedelta(days=365))).values])
            amt = self.AMT20.loc[d, base]; amt = amt[amt > 0]
            pool = list(amt[amt.rank(pct=True) > 0.30].index)
            if rf:
                fi = ctx.fin(pd_).reindex([c[2:] for c in pool]); fi.index = pool
                bad = set(c for c in pool if m.U.at[d, c] < 2.0) | set(fi.index[fi["bps"] <= 0])
                if d.month <= 4:
                    bad |= set(fi.index[fi["np"] < 0])
                if d.month == 4 and d.day >= 15:
                    p = "%d1231" % (d.year - 1)
                    if p in ctx.F:
                        done = ctx.F[p]["pub"]; ok = done.index[done <= d]
                        bad |= set(pool) - (set("sh" + c for c in ok) | set("sz" + c for c in ok))
                pool = [c for c in pool if c not in bad]
            cap = m.FCAP.loc[pd_, pool]; cap = cap[cap > 0]
            df = pd.DataFrame({"cap": cap, "ret": self.R20.loc[d, cap.index]}).dropna()
            return list(((-np.log(df["cap"])).rank(pct=True) + (-df["ret"]).rank(pct=True)).sort_values(ascending=False).index)
        return ctx.cached(("s_rank", d, rf), f)

    def step(self, d, force=False):
        b, q, m = self.b, self.p, self.m
        n = q["hold"]
        st_out = [s for s in self.picks if m.st[s]]
        self.picks = [s for s in self.picks if not m.st[s]]
        if self.ck.n(d) % q["every"] == 0 or force or len(self.picks) != n:
            codes = self.rank(d); rk = {s: i for i, s in enumerate(codes)}
            pick = [s for s in sorted(self.picks, key=lambda x: rk.get(x, 10 ** 9)) if rk.get(s, 10 ** 9) < q["buffer"]][:n]
            for s in codes:
                if len(pick) >= n:
                    break
                if s not in pick:
                    pick.append(s)
            self.picks = pick
        tv = b.value(d) * 0.99 / n
        for s in list(b.weights(d)):
            if s not in self.picks:
                b.order_value(s, d, 0, "被 ST，换掉" if s in st_out else "跌出前 %d 名" % q["buffer"])
            elif b.pos_value(s, d) > tv * 1.25:
                b.order_value(s, d, tv, "仓位调低")
        for s in self.picks:
            if b.pos_value(s, d) < tv * 0.75:
                b.order_value(s, d, tv, "新选入" if b.pos_value(s, d) == 0 else "仓位调高")
        b.mark(d)


class SectorEtfP(Plugin):
    META = dict(id="sector_etf", name="行业 ETF（二级行业动量）", slots=["sector"],
                desc="124 个申万二级行业里挑自身顺势、60 天涨幅最大的几个，各买一只走势最像它的 ETF",
                rules=dict(risk="行业自身走弱当天卖", rotate="每 10 个交易日重排；行业 → ETF 对应每 20 个交易日更新；上场当天重新选",
                           size="每只 ETF 一样多的钱"),
                params=dict(hold=dict(default=3, label="买几个行业", min=1, max=10),
                            every=dict(default=10, label="几天调仓一次", min=1, max=40),
                            map_days=dict(default=20, label="几天更新一次行业→ETF 对应", min=5, max=120),
                            corr_min=dict(default=0.85, label="ETF 和行业的相关度至少", min=0.5, max=0.99)))
    reselect_on_enter = True

    def __init__(self, m, ctx, start, **p):
        super().__init__(m, ctx, start, **p)
        self.inds, self.etf, self.map, self.map_day, self.started = [], {}, {}, -999, False
        self.b.ext_px = ctx.cached(("etf_px",), lambda: {c: ctx.etf_close[c].reindex(m.dates).ffill() for c in ctx.etf_close.columns})

    def mapping(self, d):
        ctx, m, cmin = self.ctx, self.m, self.p["corr_min"]
        def f():
            EC, EA = ctx.etf_close, ctx.etf_amt
            pd_ = _prev(m, d); ok = []
            for c, nm in ctx.etf_list:
                if c not in EC.columns or any(k in nm for k in ETF_EXCLUDE):
                    continue
                s = EC[c].dropna()
                if len(s) == 0 or s.index[0] > pd_ - pd.Timedelta(days=120):
                    continue
                if EA[c][EA.index <= pd_].iloc[-20:].mean() >= 3e7:
                    ok.append(c)
            if not ok:
                return {}
            er = EC[ok][EC.index <= pd_].iloc[-121:].pct_change().iloc[1:]
            er = er.loc[:, er.notna().sum() >= 96]
            ir = ctx.sw2[ctx.sw2.index <= pd_].pct_change().iloc[-120:]
            ir, er = ir.align(er, join="inner", axis=0)
            if len(ir) < 96 or er.shape[1] == 0:
                return {}
            out = {}
            for c in ir.columns:
                cc = er.corrwith(ir[c]).dropna()
                if len(cc) and cc.max() >= cmin:
                    out[c] = (cc.idxmax(), float(cc.max()))
            return out
        return ctx.cached(("b_map", d, cmin), f)

    def mom(self, d):
        def f():
            px = self.ctx.sw2[self.ctx.sw2.index < d]; okset, mom = set(), {}
            for c in px.columns:
                s = px[c].dropna()
                if len(s) >= 61 and er_ma_ok(s.values):
                    okset.add(c); mom[c] = s.iloc[-1] / s.iloc[-61] - 1
            return okset, mom
        return self.ctx.cached(("l2_mom", d), f)

    def step(self, d, force=False):
        b, q, n = self.b, self.p, self.ck.n(d)
        okset, mom = self.mom(d)
        weak = [c for c in self.inds if c not in okset]
        self.inds = [c for c in self.inds if c in okset]
        if n % q["every"] == 0 or force or not self.started:
            if n - self.map_day >= q["map_days"] or not self.map:
                self.map = self.mapping(d); self.map_day = n
            pick, used = [], set()
            for c in sorted(mom, key=lambda c: -mom[c]):
                if len(pick) >= q["hold"]:
                    break
                if c in self.map and self.map[c][0] not in used:
                    pick.append(c); used.add(self.map[c][0])
            self.inds = pick; self.etf = {c: self.map[c][0] for c in pick}; self.started = True
        self.mom_now = mom
        tgt, why = {}, {}
        tv = b.value(d) * 0.99 / q["hold"]
        for c in self.inds:
            e = self.etf[c]; tgt[e] = tgt.get(e, 0) + tv; why[e] = self.ctx.sw2_names.get(c, c)
        for s in list(b.weights(d)):
            if s not in tgt:
                b.order_value(s, d, 0, "行业走弱" if any(self.etf.get(c) == s for c in weak) else "跌出前 %d 个行业" % q["hold"])
            elif b.pos_value(s, d) > tgt[s] * 1.25:
                b.order_value(s, d, tgt[s], why[s] + " 仓位调低")
        for s, v in tgt.items():
            if b.pos_value(s, d) < v * 0.75:
                b.order_value(s, d, v, why[s])
        b.mark(d)

    def conditions(self):
        en = dict(self.ctx.etf_list)
        return [dict(text="%s（60 日涨 %.1f%%）→ %s" % (self.ctx.sw2_names.get(c, c), self.mom_now.get(c, 0) * 100, en.get(self.etf[c], self.etf[c])), ok=True)
                for c in self.inds]


PLUGINS = {c.META["id"]: c for c in (SteadyP, BankP, SmallCapP, SectorEtfP)}


# ============================== 预设：原方案（与 V1.4 三合一对应） ==============================
V14 = dict(name="原方案（V1.4 三合一）", max_hold=10, tol=0.25, rule=Rule.DEFAULT,
           sleeves=dict(steady=dict(plugin="steady", slot="steady", params=dict(hold=10, buffer=20, ind_max=3)),
                        small=dict(plugin="smallcap", slot="stock", params=dict(hold=10, buffer=20)),
                        small_mix=dict(plugin="smallcap", slot="stock", name="小市值反转（上涨期 5 只）", params=dict(hold=5, buffer=20)),
                        etf=dict(plugin="sector_etf", slot="sector", params=dict(hold=3))),
           alloc=dict(normal=dict(steady=1.0), small=dict(small=1.0), both=dict(small_mix=0.5, etf=0.5)))


def meta():
    """给网页 / 小程序的说明（data/meta.json）：行情名、指数名、策略插件及参数、默认切换规则、原方案 V1.4。
    这些名称只在引擎里定义一次，前端都读这份，不再各自手写。"""
    ps = [dict(id=pid, name=P.META["name"], slots=P.META["slots"], desc=P.META["desc"], rules=P.META["rules"],
               reselect_on_enter=P.reselect_on_enter, params=[dict(key=k, **v) for k, v in P.META["params"].items()])
          for pid, P in PLUGINS.items()]
    return dict(plugins=ps, states=STATES, slots=SLOTS, index_names=INDEX_NAMES, rule_default=Rule.DEFAULT, v14=V14)


def check(cfg):
    """配置检查：返回问题列表（空 = 通过）"""
    errs = []
    sl = cfg.get("sleeves", {})
    for sid, s in sl.items():
        if s.get("plugin") not in PLUGINS:
            errs.append("子账户 %s 的策略 %s 不存在" % (sid, s.get("plugin")))
    for st, row in cfg.get("alloc", {}).items():
        if st not in STATES:
            errs.append("未知行情 %s" % st)
        tot = sum(row.values())
        if tot > 1.0001:
            errs.append("「%s」时分钱合计 %.0f%%，超过 100%%" % (STATES.get(st, st), tot * 100))
        n = sum(PLUGINS[sl[i]["plugin"]].max_hold(sl[i].get("params", {})) for i, v in row.items() if v > 0 and i in sl and sl[i].get("plugin") in PLUGINS)
        if n > cfg.get("max_hold", 10):
            errs.append("「%s」时最多可能同时持 %d 只，超过上限 %d" % (STATES.get(st, st), n, cfg.get("max_hold", 10)))
        for i in row:
            if i not in sl:
                errs.append("「%s」里的子账户 %s 没有定义" % (STATES.get(st, st), i))
    return errs


# ============================== 组合核心 ==============================
def run(m, ctx, start, cfg):
    """返回 (真实账户 Book, extra)；extra 与 strategies.* 的返回格式一致，可直接交给 run.build_output"""
    cfg = copy.deepcopy(cfg)
    rule = Rule(ctx, cfg.get("rule"))
    alloc = cfg["alloc"]
    sl = {sid: PLUGINS[s["plugin"]](m, ctx, start, **s.get("params", {})) for sid, s in cfg["sleeves"].items()}
    sname = {sid: s.get("name") or PLUGINS[s["plugin"]].META["name"] for sid, s in cfg["sleeves"].items()}
    tol = cfg.get("tol", 0.25)
    b = Book(m)
    b.ext_px = ctx.cached(("etf_px",), lambda: {c: ctx.etf_close[c].reindex(m.dates).ffill() for c in ctx.etf_close.columns})
    ck = Clock(m, start)
    k = {sid: 0.0 for sid in sl}
    prev = None
    state_hist = {}
    for d in ck.days:
        state = rule.step(d)
        a = {sid: float(alloc.get(state, {}).get(sid, 0)) for sid in sl}
        state_hist[d] = state
        enter = {sid for sid in sl if a[sid] > 0 and (prev is None or prev[sid] == 0)}
        for sid, p in sl.items():
            p.step(d, force=(sid in enter and p.reselect_on_enter))
        if a != prev:                                   # 行情切换：按新比例重新分钱
            tot = b.value(d) * 0.99
            for sid, p in sl.items():
                v = p.b.value(d)
                k[sid] = tot * a[sid] / v if v > 0 else 0.0
            prev = a
        tgt, why = {}, {}
        for sid, p in sl.items():
            if k[sid] <= 0:
                continue
            tags = {t["code"]: t.get("tag", "") for t in p.b.trades.get(d, [])}
            for s, n in p.b.sh.items():
                if n > 0:
                    tgt[s] = tgt.get(s, 0.0) + k[sid] * p.b.pos_value(s, d)
                    r = sname[sid] + ("：" + tags[s] if tags.get(s) else "")
                    why[s] = why[s] + " ＋ " + r if s in why else r          # 两个子账户都持有：两边原因都写上
        gone = {}
        for sid, p in sl.items():
            for t in p.b.trades.get(d, []):
                if t["side"] in ("卖出", "减仓"):
                    gone[t["code"]] = sname[sid] + "：" + t.get("tag", "")
        for s in list(b.weights(d)):
            if s not in tgt:
                b.order_value(s, d, 0, gone.get(s, "行情切换，%s 不再分到钱" % "这个子账户"))
            elif b.pos_value(s, d) > tgt[s] * (1 + tol):
                b.order_value(s, d, tgt[s], why.get(s, "") + "（仓位调低）")
        for s, v in tgt.items():
            if b.pos_value(s, d) < v * (1 - tol):
                b.order_value(s, d, v, why.get(s, ""))
        b.mark(d)
    st = state_hist[ck.days[-1]]
    info = rule.info
    cond = []
    if rule.c["type"] != "fixed":
        cond += [dict(text="%s 近 %d 日净涨幅占来回波动的 %.0f%%（要超过 %.0f%%）" % (info["small_name"], rule.c["small"]["er_n"], info["er"] * 100, rule.c["small"]["er_min"] * 100),
                      ok=bool(info["er"] > rule.c["small"]["er_min"])),
                 dict(text="%s %.0f 点，%d 日均价 %.0f 点（要在均价上方）" % (info["small_name"], info["small_px"], rule.c["small"]["ma_n"], info["small_ma"]),
                      ok=bool(info["small_px"] > info["small_ma"]))]
    if rule.c["type"] == "three_state" and "big_px" in info:
        bg = rule.c["big"]
        cond += [dict(text="%s 昨收 %.0f，%d 日均价 %.0f（要在上方）" % (info["big_name"], info["big_px"], bg["ma_n"], info["big_ma"]), ok=bool(info["big_px"] > info["big_ma"])),
                 dict(text="%s %d 日均价在上升（%d 天前 %.0f）" % (info["big_name"], bg["ma_n"], bg["slope_lag"], info["big_ma_old"]), ok=bool(info["big_ma"] > info["big_ma_old"])),
                 dict(text="申万一级行业站上 %d 日均价的占 %.0f%%（要 ≥ %.0f%%）" % (bg["breadth_ma"], info["br"] * 100, bg["breadth_min"] * 100), ok=bool(info["br"] >= bg["breadth_min"]))]
    who = " + ".join("%s %.0f%%" % (sname[s], v * 100) for s, v in alloc.get(st, {}).items() if v > 0) or "全部空仓"
    cond.append(dict(text="今天是「%s」，钱交给：%s" % (STATES[st], who), ok=True))
    for sid, p in sl.items():
        if alloc.get(st, {}).get(sid, 0) > 0:
            for c in p.conditions():
                cond.append(dict(text=sname[sid] + "：" + c["text"], ok=c["ok"]))
    every = min(p.p.get("every", 5) for p in sl.values())
    return b, dict(conditions=cond, every=every, clock=ck, state=st, states=state_hist, sleeves=sl,
                   alloc={k: round(sum(v for sid, v in alloc.get(st, {}).items() if cfg["sleeves"][sid].get("slot") == slot), 4)
                          for k, slot in ((x["key"], x["id"]) for x in SLOTS)},
                   etf_names=dict(ctx.etf_list), rule="可配置组合：" + cfg.get("name", ""))


def summary(cfg):
    """一句话说明（信号页卡片用）"""
    sl, parts = cfg["sleeves"], []
    for st in ("normal", "small", "both"):
        row = {k: v for k, v in cfg["alloc"].get(st, {}).items() if v > 0}
        if st != "normal" and cfg["rule"].get("type") == "fixed":
            continue
        if st == "both" and cfg["rule"].get("type") == "two_state":
            continue
        names = "＋".join("%s%s" % ((sl[k].get("name") or PLUGINS[sl[k]["plugin"]].META["name"]).split("（")[0], "" if len(row) == 1 else " %.0f%%" % (v * 100))
                         for k, v in row.items()) or "货币基金"
        parts.append("%s：%s" % (STATES[st], names))
    return "；".join(parts)
