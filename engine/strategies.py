# -*- coding: utf-8 -*-
"""三个策略的逐日回放。规则与聚宽代码一致（见各函数说明），差异在 README 里列明。
每次运行都从回放起点把模拟账户重新跑到今天，所以结果只取决于行情数据，不依赖上一次运行留下的状态。"""
import numpy as np, pandas as pd
from market import Book, er_ma_ok, er_value

ETF_EXCLUDE = ["300", "500", "1000", "2000", "50", "180", "100", "800", "200", "A股", "A50", "A100", "A500", "中证A", "创业", "科创50",
               "科创100", "科创200", "科创板", "科综", "双创", "深证", "上证", "深成", "综指", "中小", "国债", "债", "货币", "现金", "日利",
               "添益", "快线", "快钱", "财富宝", "添利", "天天", "日日", "短融", "黄金", "金ETF", "白银", "豆粕", "原油", "能源化工", "期货",
               "纳指", "纳斯达克", "标普", "日经", "日本", "德国", "法国", "恒生", "港股", "中概", "H股", "沪港深", "香港", "美国", "亚太",
               "东南亚", "沙特", "巴西", "MSCI", "道琼斯", "海外", "全球", "中韩", "AH", "等权", "央企", "国企", "民企", "ESG", "红利", "价值",
               "成长", "质量", "低波", "基本面", "动量", "现金流", "股息", "大盘", "中盘", "小盘", "微盘", "龙头", "核心", "宽基", "长三角",
               "大湾区", "粤港澳", "湾创", "湖北", "浙江", "浙商", "上海", "北京", "四川", "江苏", "福建", "山东", "安徽", "湖南", "广东",
               "京津冀", "成渝", "杭州湾", "张江", "G60", "长江保护", "中创400", "一带一路", "央视", "中关村", "新兴", "战略", "产业升级",
               "新经济", "高端装备", "科技100", "科技50", "TMT", "互联网+"]


def replay_days(m, n_days=300, align=20):
    """回放区间：约 n_days 个交易日前、且交易日编号是 align 倍数的那天起，到今天"""
    d = m.dates[m.dates >= m.cal[0]]
    if len(d) <= n_days:
        return d
    start = d[-n_days]
    for x in d[d >= start]:
        if m.n(x) % align == 0:
            return d[d >= x]
    return d[d >= start]


class Ctx:
    """一次运行共享的数据：fin 业绩报表、ind 申万二级行业、q 腾讯行情、指数/行业/ETF 行情"""
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self._fin = {}

    def fin(self, d):
        import data as D
        d = pd.Timestamp(d)
        if d not in self._fin:
            self._fin[d] = D.fin_asof(self.F, d)
        return self._fin[d]


def _prev(m, d):
    i = m.dates.get_loc(d)
    return m.dates[i - 1] if i > 0 else d


# ======================= 银行股顺势 =======================
def bank(m, ctx, hold=4, keep_rank=6, chase=0.10, every=5, pool_n=20):
    """聚宽「银行股顺势策略 最终版」：上年末总市值前 20 的申万银行股；距 250 日高点排名；持 4 只，掉出前 6 才卖；
    新买入要求近 20 日涨幅 ≤10%；每 5 个交易日调仓并把留下的调回 1/4"""
    banks = [c for c in ctx.bank_codes if c in m.P.columns]
    tsh = (ctx.q["tcap"] / ctx.q["price"]).reindex(banks)                     # 总股本（按今天的总市值反推，年内视为不变）
    pools = {}
    def pool(y):
        if y not in pools:
            ye = m.dates[m.dates.year == y - 1]
            d = ye[-1] if len(ye) else m.dates[0]
            cap = (m.U.loc[d, banks] * tsh).dropna().sort_values(ascending=False)
            pools[y] = list(cap.index[:pool_n])
        return pools[y]
    b = Book(m)
    diag = {}
    pos = {d: i for i, d in enumerate(m.dates)}
    for d in replay_days(m):
        if m.n(d) % every == 0:
            i = pos[d]
            rows = []
            for c in pool(d.year):
                if i < 249 or m.first[c] > m.dates[i - 249]:
                    continue
                w = m.P[c].iloc[i - 249:i + 1]
                rows.append(dict(code=c, dd=w.iloc[-1] / w.max() - 1, ret=w.iloc[-1] / m.P[c].iloc[i - 20] - 1))
            sig = pd.DataFrame(rows).sort_values("dd", ascending=False).reset_index(drop=True)
            rank = {c: k for k, c in enumerate(sig.code)}
            held = list(b.weights(d))
            keep = [c for c in held if c in rank and rank[c] < keep_rank]
            for c in held:
                if c in keep:
                    continue
                if not b.order_value(c, d, 0, "不在股票池" if c not in rank else "排名掉到第 %d" % (rank[c] + 1)):
                    keep.append(c)
            need, new = hold - len(keep), []
            for _, r in sig.iterrows():
                if need <= 0:
                    break
                if r.code in keep or not m.can_buy(r.code, d) or r.ret > chase:
                    continue
                new.append(r.code); need -= 1
            tv = b.value(d) / hold
            for c in sorted(keep, key=lambda c: -b.pos_value(c, d)):
                b.order_value(c, d, tv, "调回 1/4")
            for c in new:
                b.order_value(c, d, min(tv, b.cash / 1.0015), "排名第 %d" % (rank[c] + 1))
            diag = dict(day=d, sig=sig)
        b.mark(d)
    sig = diag.get("sig")
    cond = []
    if sig is not None:
        for k, r in sig.head(8).iterrows():
            cond.append(dict(text="第 %d 名 %s：离一年最高价 %.1f%%，近 20 日涨 %.1f%%%s" % (
                k + 1, m.name(r.code), r.dd * 100, r.ret * 100, "（涨超 10%%，不新买；已持有的照拿）" if r.ret > chase else ""),
                ok=bool(r.ret <= chase)))
    return b, dict(conditions=cond, rank_day=diag.get("day"), every=every,
                   rule="上年末市值前 20 的银行股，持有离一年最高价最近的 4 只；掉出前 6 名才卖；近 20 天涨超 10% 的不新买；每 5 个交易日检查一次")


# ======================= 稳健选股（大盘 800 盈利股） =======================
class Steady:
    """聚宽「大盘股熊市震荡市稳健策略 V1.1」的选股 + 波动率控仓 + 影子净值回撤风控"""
    def __init__(self, m, ctx, hold, buffer, ind_max, pool=800, roe_min=0.05, stop=0.10, vol_target=0.15,
                 vol_win=40, dd_half=0.15, dd_out=0.25, dd_look=250):
        self.m, self.ctx = m, ctx
        self.hold, self.buffer, self.ind_max, self.pool_n, self.roe_min, self.stop = hold, buffer, ind_max, pool, roe_min, stop
        self.vt, self.vw, self.dh, self.do, self.dl = vol_target, vol_win, dd_half, dd_out, dd_look
        self.picks, self.sleeve = [], []
        P = m.P
        self.HI = P / P.rolling(250, min_periods=1).max()
        self.CNT = m.U_raw.notna().rolling(250, min_periods=1).sum()
        self.VOL = P.pct_change().rolling(60, min_periods=20).std()
        self.last = {}

    def exposure(self):
        r = np.array(self.sleeve[-self.vw:])
        e = 1.0
        vol = None
        if len(r) >= 20:
            vol = np.std(r, ddof=1) * np.sqrt(252)
            e = 1.0 if vol <= 0 else min(1.0, self.vt / vol)
        dd = 0.0
        if self.sleeve:
            nav = np.cumprod(1 + np.array(self.sleeve)); dd = nav[-1] / np.max(nav[-self.dl:]) - 1
            if dd < -self.do:
                e = 0.0
            elif dd < -self.dh:
                e *= 0.5
        self.last.update(vol=vol, dd=dd, expo=e)
        return e

    def select(self, d, held):
        m = self.m
        pd_ = _prev(m, d)
        ok = list(m.P.columns[(~m.st & ~m.paused.loc[d] & (m.first <= pd_ - pd.Timedelta(days=365))).values])
        cap = m.FCAP.loc[pd_, ok].dropna().sort_values(ascending=False).head(self.pool_n)
        f = self.ctx.fin(pd_)
        f6 = f.reindex([c[2:] for c in cap.index])
        good = [c for c, (npv, roe) in zip(cap.index, f6[["np", "roe_a"]].values) if npv > 0 and roe > self.roe_min]
        if len(good) < self.hold:
            return self.picks
        good = [c for c in good if self.CNT.at[d, c] >= 200]
        sc = (self.HI.loc[d, good].rank(pct=True) + (-self.VOL.loc[d, good]).rank(pct=True)).dropna().sort_values(ascending=False)
        codes = list(sc.index); rank = {s: i for i, s in enumerate(codes)}
        ind = self.ctx.ind
        def ind_of(s): return ind.get(s[2:], "未知")
        cnt, pick = {}, []
        def add(s): pick.append(s); cnt[ind_of(s)] = cnt.get(ind_of(s), 0) + 1
        for s in sorted(held, key=lambda x: rank.get(x, 10 ** 9)):
            if rank.get(s, 10 ** 9) < self.buffer and len(pick) < self.hold and cnt.get(ind_of(s), 0) < self.ind_max:
                add(s)
        for s in codes:
            if len(pick) >= self.hold:
                break
            if s not in pick and cnt.get(ind_of(s), 0) < self.ind_max:
                add(s)
        self.last.update(pool=len(good), top=[(s, round(float(sc[s]), 3)) for s in codes[:5]])
        return pick

    def after_close(self, d):
        if not self.picks:
            self.sleeve.append(0.0); return
        r = self.m.RET.loc[d, [s for s in self.picks if s in self.m.RET.columns]]
        self.sleeve.append(float(r.mean()) if len(r) else 0.0)


def steady(m, ctx, hold=30, buffer=60, ind_max=5, every=5, band=0.10):
    """独立运行的稳健策略：持仓 30 只（聚宽 V1.1 默认）"""
    S = Steady(m, ctx, hold, buffer, ind_max)
    b = Book(m)
    g_expo = 0.0
    def rebalance_to(d, picks, expo):
        tv = b.value(d) * 0.99 * expo / hold
        for s in list(b.weights(d)):
            if s not in picks:
                b.order_value(s, d, 0, "不在前 %d 名" % buffer)
            elif b.pos_value(s, d) > tv * 1.05:
                b.order_value(s, d, tv, "仓位调低")
        for s in picks:
            if b.pos_value(s, d) < tv * 0.95:
                b.order_value(s, d, tv, "新选入" if b.pos_value(s, d) == 0 else "仓位调高")
    for d in replay_days(m):
        for s in list(b.weights(d)):
            if b.cost.get(s) and b.px(s, d) / b.cost[s] - 1 < -S.stop and m.can_sell(s, d):
                b.order_value(s, d, 0, "亏损超过 10% 止损")
                if s in S.picks:
                    S.picks.remove(s)
        expo = S.exposure()
        if m.n(d) % every == 0:
            S.picks = S.select(d, list(b.weights(d)))
            rebalance_to(d, S.picks if expo > 0 else [], expo)
            g_expo = expo
        elif abs(expo - g_expo) >= band or (expo == 0 and g_expo > 0):
            rebalance_to(d, [s for s in S.picks if s in b.sh] if expo > 0 else [], expo)
            g_expo = expo
        S.after_close(d)
        b.mark(d)
    L = S.last
    cond = [dict(text="选股范围：流通市值前 800、盈利且年化净资产收益率 > 5%% 的公司，本次 %d 家" % L.get("pool", 0), ok=True),
            dict(text="组合近 40 天波动 %s（目标 15%%，越高仓位越低）" % ("%.1f%%" % (L["vol"] * 100) if L.get("vol") else "—"),
                 ok=bool(L.get("vol") is None or L["vol"] <= 0.15)),
            dict(text="选股组合从一年高点回落 %.1f%%（超过 15%% 减半仓，超过 25%% 空仓）" % (-L.get("dd", 0) * 100),
                 ok=bool(L.get("dd", 0) >= -0.15)),
            dict(text="建议总仓位 %.0f%%" % (L.get("expo", 1) * 100), ok=bool(L.get("expo", 1) >= 0.99))]
    return b, dict(conditions=cond, every=every,
                   rule="大盘 800 家盈利公司里，挑离一年最高价近、走势平稳的 30 只；每 5 个交易日换一次；单只亏 10% 卖出；组合波动大或回撤大时降仓")


# ======================= 全行情切换组合 V1.4（三合一） =======================
def combo(m, ctx, a_hold=10, a_buffer=20, a_ind_max=3, a_every=5, s_hold=10, s_hold_mix=5, s_buffer=20, s_every=5,
          b_n=3, b_every=10, b_map_days=20, tol=0.25, band=0.10):
    """聚宽「全行情切换组合·个股+板块+稳健 V1.4」"""
    I1000, I300 = ctx.i1000, ctx.i300
    L1, L2 = ctx.sw1, ctx.sw2
    ETF_C, ETF_A = ctx.etf_close, ctx.etf_amt
    etf_names = dict(ctx.etf_list)
    A = Steady(m, ctx, a_hold, a_buffer, a_ind_max)
    b = Book(m); b.ext_px = {c: ETF_C[c].reindex(m.dates).ffill() for c in ETF_C.columns}
    st = dict(s_on=False, s_picks=[], b_on=False, b_inds=[], b_stocks={}, b_map={}, b_map_day=-999,
              a_expo=0.0, a_share=None, a_tv={}, alloc=None)
    AMT20 = m.AMT.rolling(20, min_periods=1).mean().shift(1)
    R20 = m.P / m.P.shift(20) - 1
    info = {}

    def gates(d):
        c = I1000[I1000.index <= d].values
        g1 = er_ma_ok(c[-22:])
        h = I300[I300.index < d].values
        up = False; br = None
        if len(h) >= 140:
            ma_now, ma_old = h[-120:].mean(), h[-140:-20].mean()
            px = L1[L1.index < d].iloc[-130:]
            px = px.loc[:, px.notna().sum() >= 60]
            br = float((px.iloc[-1] > px.iloc[-60:].mean()).mean()) if px.shape[1] else 0
            up = bool(h[-1] > ma_now and ma_now > ma_old and br >= 0.5)
            info.update(i300=h[-1], ma120=ma_now, ma120_old=ma_old)
        info.update(er=er_value(c[-21:]), i1000=c[-1], ma20=c[-20:].mean(), br=br, g1=g1)
        return g1, g1 and up

    def s_select(d, n):
        pd_ = _prev(m, d)
        base = list(m.P.columns[(~m.st & ~m.paused.loc[d] & (m.first <= pd_ - pd.Timedelta(days=365))).values])
        amt = AMT20.loc[d, base]; amt = amt[amt > 0]
        pool = list(amt[amt.rank(pct=True) > 0.30].index)
        f = ctx.fin(pd_).reindex([c[2:] for c in pool]); f.index = pool
        bad = set(c for c in pool if m.U.at[d, c] < 2.0) | set(f.index[f["bps"] <= 0])
        if d.month <= 4:
            bad |= set(f.index[f["np"] < 0])
        if d.month == 4 and d.day >= 15:
            p = "%d1231" % (d.year - 1)
            if p in ctx.F:
                done = ctx.F[p]["pub"]
                pub_ok = set("sh" + c for c in done.index[done <= d]) | set("sz" + c for c in done.index[done <= d])
                bad |= set(pool) - pub_ok
        pool = [c for c in pool if c not in bad]
        cap = m.FCAP.loc[pd_, pool]; cap = cap[cap > 0]
        df = pd.DataFrame({"cap": cap, "ret": R20.loc[d, cap.index]}).dropna()
        sc = ((-np.log(df["cap"])).rank(pct=True) + (-df["ret"]).rank(pct=True)).sort_values(ascending=False)
        codes = list(sc.index); rank = {s: i for i, s in enumerate(codes)}
        pick = [s for s in sorted(st["s_picks"], key=lambda x: rank.get(x, 10 ** 9)) if rank.get(s, 10 ** 9) < s_buffer][:n]
        for s in codes:
            if len(pick) >= n:
                break
            if s not in pick:
                pick.append(s)
        info["s_filter"] = (len(bad), len(pool))
        return pick

    def s_update(d, share, n):
        if share <= 0:
            st["s_on"] = False; st["s_picks"] = []
            return {}
        st["s_picks"] = [s for s in st["s_picks"] if not m.st[s]]
        if (not st["s_on"]) or m.n(d) % s_every == 0 or not st["s_picks"] or len(st["s_picks"]) != n:
            st["s_picks"] = s_select(d, n)
        st["s_on"] = True
        tv = b.value(d) * 0.99 * share / n
        return {s: tv for s in st["s_picks"]}

    def b_mapping(d):
        pd_ = _prev(m, d)
        ok = []
        for c, nm in ctx.etf_list:
            if c not in ETF_C.columns or any(k in nm for k in ETF_EXCLUDE):
                continue
            s = ETF_C[c].dropna()
            if len(s) == 0 or s.index[0] > pd_ - pd.Timedelta(days=120):
                continue
            a = ETF_A[c][ETF_A.index <= pd_].iloc[-20:].mean()
            if a >= 3e7:
                ok.append(c)
        if not ok:
            return {}
        er = ETF_C[ok][ETF_C.index <= pd_].iloc[-121:].pct_change().iloc[1:]
        er = er.loc[:, er.notna().sum() >= 96]
        ir = L2[L2.index <= pd_].pct_change().iloc[-120:]
        ir, er = ir.align(er, join="inner", axis=0)
        if len(ir) < 96 or er.shape[1] == 0:
            return {}
        out = {}
        for c in ir.columns:
            cc = er.corrwith(ir[c]).dropna()
            if len(cc) and cc.max() >= 0.85:
                out[c] = (cc.idxmax(), float(cc.max()))
        info["map_n"] = (len(out), er.shape[1])
        return out

    def b_update(d, share):
        if share <= 0:
            st["b_on"] = False; st["b_inds"] = []; st["b_stocks"] = {}
            return {}
        px = L2[L2.index < d]
        okset, mom = set(), {}
        for c in px.columns:
            s = px[c].dropna()
            if len(s) >= 61 and er_ma_ok(s.values):
                okset.add(c); mom[c] = s.iloc[-1] / s.iloc[-61] - 1
        for ind in list(st["b_inds"]):
            if ind not in okset:
                st["b_inds"].remove(ind); st["b_stocks"].pop(ind, None)
        if (not st["b_on"]) or m.n(d) % b_every == 0:
            if m.n(d) - st["b_map_day"] >= b_map_days or not st["b_map"]:
                st["b_map"] = b_mapping(d); st["b_map_day"] = m.n(d)
            pick, used = [], set()
            for c in sorted(mom, key=lambda c: -mom[c]):
                if len(pick) >= b_n:
                    break
                if c in st["b_map"] and st["b_map"][c][0] not in used:
                    pick.append(c); used.add(st["b_map"][c][0])
            st["b_inds"] = pick
            st["b_stocks"] = {c: [st["b_map"][c][0]] for c in pick}
        st["b_on"] = True
        info["b_pick"] = [(c, st["b_map"].get(c, ("", 0))[0], mom.get(c)) for c in st["b_inds"]]
        tv = b.value(d) * 0.99 * share / b_n
        out = {}
        for c in st["b_inds"]:
            for e in st["b_stocks"].get(c, []):
                out[e] = out.get(e, 0.0) + tv
        return out

    def a_update(d, share):
        up = set(st["s_picks"]) | set(s for v in st["b_stocks"].values() for s in v)
        for s in list(A.picks):
            if s in b.sh and s not in up and b.cost.get(s) and b.px(s, d) / b.cost[s] - 1 < -A.stop and m.can_sell(s, d):
                A.picks.remove(s); st["a_tv"].pop(s, None)
        expo = A.exposure()
        rebal = m.n(d) % a_every == 0
        if rebal:
            A.picks = A.select(d, A.picks)
        if rebal or abs(expo - st["a_expo"]) >= band or (expo == 0 and st["a_expo"] > 0) or share != st["a_share"]:
            st["a_expo"] = expo; st["a_share"] = share
            tv = b.value(d) * 0.99 * share * expo / a_hold
            st["a_tv"] = {s: tv for s in A.picks} if share > 0 and expo > 0 else {}
        return dict(st["a_tv"])

    alloc_hist = {}
    for d in replay_days(m):
        g1, g2 = gates(d)
        a_sh, s_sh, b_sh = (0.0, 0.5, 0.5) if g2 else ((0.0, 1.0, 0.0) if g1 else (1.0, 0.0, 0.0))
        alloc_hist[d] = (a_sh, s_sh, b_sh)
        mix = s_sh > 0 and b_sh > 0
        tgt = {}
        for part in (s_update(d, s_sh, s_hold_mix if mix else s_hold), b_update(d, b_sh), a_update(d, a_sh)):
            for s, v in part.items():
                tgt[s] = tgt.get(s, 0.0) + v
        why = {}
        for s in st["s_picks"]: why[s] = "个股块"
        for ind, es in st["b_stocks"].items():
            for e in es: why[e] = "板块块 · " + ctx.sw2_names.get(ind, ind)
        for s in A.picks: why.setdefault(s, "稳健块")
        for s in list(b.weights(d)):
            if s not in tgt:
                b.order_value(s, d, 0, "不再持有")
            elif b.pos_value(s, d) > tgt[s] * (1 + tol):
                b.order_value(s, d, tgt[s], why.get(s, "") + " 仓位调低")
        for s, v in tgt.items():
            if b.pos_value(s, d) < v * (1 - tol):
                b.order_value(s, d, v, why.get(s, ""))
        A.after_close(d)
        b.mark(d)
    a_sh, s_sh, b_sh = alloc_hist[d]
    who = "稳健块" if a_sh else ("个股块 50% + 板块块 50%" if b_sh else "个股块")
    cond = [dict(text="中证1000 近 20 日净涨幅占来回波动的 %.0f%%（要超过 20%%）" % (info["er"] * 100), ok=bool(info["er"] > 0.2)),
            dict(text="中证1000 %.0f 点，20 日均价 %.0f 点（要在均价上方）" % (info["i1000"], info["ma20"]), ok=bool(info["i1000"] > info["ma20"])),
            dict(text="沪深300 昨收 %.0f，半年均价 %.0f（要在上方）" % (info.get("i300", 0), info.get("ma120", 0)),
                 ok=bool(info.get("i300", 0) > info.get("ma120", 1e9))),
            dict(text="沪深300 半年均价在上升（一个月前 %.0f）" % info.get("ma120_old", 0), ok=bool(info.get("ma120", 0) > info.get("ma120_old", 1e9))),
            dict(text="申万一级行业站上 60 日均价的占 %.0f%%（要 ≥ 50%%）" % ((info.get("br") or 0) * 100), ok=bool((info.get("br") or 0) >= 0.5)),
            dict(text="今天的钱交给：%s" % who, ok=True)]
    if b_sh and info.get("b_pick"):
        for c, e, mo in info["b_pick"]:
            cond.append(dict(text="板块块：%s（60 日涨 %.1f%%）→ %s" % (ctx.sw2_names.get(c, c), (mo or 0) * 100, etf_names.get(e, e)), ok=True))
    A.last.setdefault("expo", 1)
    if a_sh:
        cond.append(dict(text="稳健块建议仓位 %.0f%%（按组合波动和回撤自动调）" % (A.last["expo"] * 100), ok=bool(A.last["expo"] >= 0.99)))
    return b, dict(conditions=cond, every=5, alloc=dict(a=a_sh, s=s_sh, b=b_sh), etf_names=etf_names,
                   alloc_hist={k.strftime("%Y-%m-%d"): v for k, v in alloc_hist.items()},
                   rule="平时用稳健块（大盘盈利股 10 只）；中小盘涨起来时换成个股块（小市值 + 近期跌得多的 10 只）；"
                        "大盘也在上涨期时个股 5 只 + 行业 ETF 3 只各一半；个股块避开有退市、ST 风险的股票")
