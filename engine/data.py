# -*- coding: utf-8 -*-
"""数据层：交易日历、全 A 历史（不复权 + 前复权因子）、实时行情、指数、申万行业、ETF、业绩报表。
缓存放 cache/（不进 git）。

价格口径：
  CLOSE   不复权收盘价（算市值、2 元门槛、涨跌停）
  FAC     前复权因子：前复权价 = CLOSE / FAC（最新一天 FAC = 1）
  每天收盘后用腾讯行情追加一行；若腾讯「昨收」≠ 缓存里的昨日收盘，说明今天除权除息，
  把之前所有 FAC 乘上 昨日收盘 / 腾讯昨收，前复权价就自动对齐。
"""
import os, time, json, pickle, warnings, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import numpy as np, pandas as pd, requests

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.environ.get("SM_CACHE") or os.path.join(ROOT, "cache")        # 回测用 cache/bt（见 bt_base.py）
os.makedirs(CACHE, exist_ok=True)
HIST_START = os.environ.get("SM_HIST_START") or "20240501"   # 历史从这里开始（回放约 300 个交易日 + 250 日回看）
FIN_N = int(os.environ.get("SM_FIN_N") or 8)                  # 业绩报表取最近几期
THREADS = 8


def log(*a):
    msg = "[%s] %s" % (bj_now().strftime("%m-%d %H:%M:%S"), " ".join(str(x) for x in a))
    print(msg, flush=True)
    with open(os.path.join(CACHE, "run.log"), "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def bj_now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).replace(tzinfo=None)


def _ak():
    import akshare as ak
    return ak


def _p(name):
    return os.path.join(CACHE, name)


def load(name, default=None):
    p = _p(name)
    if not os.path.exists(p):
        return default
    with open(p, "rb") as f:
        return pickle.load(f)


def save(name, obj):
    tmp = _p(name + ".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(obj, f)
    os.replace(tmp, _p(name))


def age_days(name):
    p = _p(name)
    return 9999 if not os.path.exists(p) else (time.time() - os.path.getmtime(p)) / 86400


def retry(f, n=3, wait=2):
    for i in range(n):
        try:
            return f()
        except Exception:
            if i == n - 1:
                raise
            time.sleep(wait * (i + 1))


def pmap(f, items, threads=THREADS):
    with ThreadPoolExecutor(threads) as ex:
        return list(ex.map(f, items))


# ---------------- 交易日历 ----------------
def calendar():
    cal = load("calendar.pkl")
    if cal is None or age_days("calendar.pkl") > 20:
        d = retry(lambda: _ak().tool_trade_date_hist_sina())
        cal = pd.DatetimeIndex(pd.to_datetime(d["trade_date"])).sort_values()
        save("calendar.pkl", cal)
    return cal


def is_trade_day(day):
    return pd.Timestamp(day) in set(calendar())


# ---------------- 实时行情（腾讯批量） ----------------
def tx_quote(codes):
    """codes 形如 sh600036；返回 DataFrame[index=code]：name price prev open high low amount fcap tcap pe pb turn"""
    def get(chunk):
        r = retry(lambda: requests.get("https://qt.gtimg.cn/q=" + ",".join(chunk), timeout=10))
        r.encoding = "gbk"
        out = []
        for line in r.text.strip().split(";"):
            if "~" not in line:
                continue
            x = line.split("~")
            code = line.split("=")[0].strip().replace("v_", "")
            try:
                out.append(dict(code=code, name=x[1], price=float(x[3] or 0), prev=float(x[4] or 0), open=float(x[5] or 0),
                                high=float(x[33] or 0), low=float(x[34] or 0), amount=float(x[37] or 0) * 1e4,
                                fcap=float(x[44] or 0) * 1e8, tcap=float(x[45] or 0) * 1e8,
                                pe=float(x[39] or 0) if x[39] else np.nan, pb=float(x[46] or 0) if x[46] else np.nan,
                                turn=float(x[38] or 0) / 100, ts=x[30]))
            except (ValueError, IndexError):
                pass
        return out
    rows = []
    for x in pmap(get, [codes[i:i + 60] for i in range(0, len(codes), 60)]):
        rows += x
    return pd.DataFrame(rows).drop_duplicates("code").set_index("code")


def stock_codes(refresh=False):
    """全 A 代码（不含北交所），每天刷新一次"""
    c = load("codes.pkl")
    if c is None or refresh or age_days("codes.pkl") > 0.5:
        try:
            d = retry(lambda: _ak().stock_zh_a_spot())
            c = sorted(x for x in d["代码"] if x[:2] in ("sh", "sz"))
            save("codes.pkl", c)
        except Exception as e:
            if c is None: raise
            log("股票代码表更新失败，沿用缓存：%s" % str(e)[-80:])
    return c


# ---------------- 个股历史 ----------------
def _fetch_one(code):
    ak = _ak()
    try:
        u = retry(lambda: ak.stock_zh_a_daily(symbol=code, start_date=HIST_START, end_date="20991231"))
        if u is None or len(u) == 0:
            return code, None
        f = retry(lambda: ak.stock_zh_a_daily(symbol=code, adjust="qfq-factor"))
        u["date"] = pd.to_datetime(u["date"]).astype("datetime64[ns]")
        u = u.set_index("date")
        fac = np.ones(len(u))
        if f is not None and len(f):
            fd = pd.to_datetime(f["date"]).astype("datetime64[ns]").values
            fv = pd.to_numeric(f["qfq_factor"]).values
            o = np.argsort(fd); fd, fv = fd[o], fv[o]
            i = np.searchsorted(fd, u.index.values, side="right") - 1          # 每天取「生效日 ≤ 当天」的最后一个因子
            fac = np.where(i >= 0, fv[np.clip(i, 0, None)], fv[0])
        u["fac"] = fac / fac[-1]
        return code, u[["open", "high", "low", "close", "amount", "outstanding_share", "fac"]]
    except Exception:
        return code, None


def build_history(codes=None):
    """首次建缓存：逐只抓新浪日线（不复权 + 前复权因子），约 15–20 分钟"""
    codes = codes or stock_codes()
    t0 = time.time()
    res = {}
    for i in range(0, len(codes), 400):
        part = dict(pmap(_fetch_one, codes[i:i + 400]))
        res.update({k: v for k, v in part.items() if v is not None})
        log("历史行情 %d/%d，用时 %.0fs" % (min(i + 400, len(codes)), len(codes), time.time() - t0))
    H = {k: pd.DataFrame({c: d[k] for c, d in res.items()}).sort_index() for k in
         ("open", "high", "low", "close", "amount", "outstanding_share", "fac")}
    H["fac"] = H["fac"].ffill().fillna(1.0)
    save("hist.pkl", H)
    log("历史行情缓存完成：%d 只，%s ~ %s" % (len(res), H["close"].index[0].date(), H["close"].index[-1].date()))
    return H


def history():
    H = load("hist.pkl")
    if H is None:
        H = build_history()
    return H


ADJ_MARK = "fac_adj.json"     # 记下当天已经做过的除权调整，同一天重跑时先撤销，避免重复累乘


def append_today(H, q, day, mark=True):
    """收盘后把今天的腾讯行情追加进缓存；处理除权（腾讯昨收 ≠ 缓存昨收）。同一天重复调用结果不变"""
    day = pd.Timestamp(day)
    if day in H["close"].index:
        H = {k: v.drop(day) for k, v in H.items()}
        try:
            m = json.load(open(os.path.join(CACHE, ADJ_MARK), encoding="utf-8"))
        except Exception:
            m = {}
        if m.get("day") == day.strftime("%Y-%m-%d") and m.get("ratio"):
            r = pd.Series(m["ratio"]).reindex(H["fac"].columns).dropna()
            H["fac"].loc[:, r.index] = H["fac"][r.index].mul(r, axis=1)          # 撤销上次的调整
            H["fac"] = H["fac"].div(H["fac"].iloc[-1].replace(0, 1), axis=1)
    last = H["close"].ffill().iloc[-1]
    q = q[q.price > 0]
    cols = H["close"].columns.union(q.index)
    H = {k: v.reindex(columns=cols) for k, v in H.items()}
    prev = q["prev"].reindex(cols)
    ratio = (prev / last.reindex(cols)).where(lambda r: (r - 1).abs() > 0.0005)       # 除权：昨收被调整
    adj = ratio.dropna()
    if len(adj):
        H["fac"].loc[:, adj.index] = H["fac"][adj.index].mul(1 / adj, axis=1)       # 之前的前复权价 × 腾讯昨收/缓存昨收
        log("今日除权除息 %d 只，已调整复权因子" % len(adj))
    if mark:
        json.dump({"day": day.strftime("%Y-%m-%d"), "ratio": {k: float(v) for k, v in adj.items()}},
                  open(os.path.join(CACHE, ADJ_MARK), "w", encoding="utf-8"))
    row = {"open": q["open"], "high": q["high"], "low": q["low"], "close": q["price"], "amount": q["amount"],
           "outstanding_share": (q["fcap"] / q["price"]).replace([np.inf], np.nan), "fac": pd.Series(1.0, q.index)}
    for k, v in row.items():
        H[k].loc[day] = v.reindex(cols)
    H["fac"] = H["fac"].ffill().fillna(1.0)
    H["fac"] = H["fac"].div(H["fac"].iloc[-1].replace(0, 1), axis=1)
    return H


def with_snapshot(H, q, day):
    """盘中：把实时价当作今天收盘拼到历史后面（不保存）"""
    return append_today({k: v.copy() for k, v in H.items()}, q, day, mark=False)


# ---------------- 指数 ----------------
def index_daily(sym):
    """新浪指数日线，sym 如 sh000852"""
    d = retry(lambda: _ak().stock_zh_index_daily(symbol=sym))
    return d.assign(date=pd.to_datetime(d["date"])).set_index("date")["close"].astype(float)


def index_spot(syms):
    """指数实时价；新浪失败时改用腾讯行情（同样的 sh000852 代码）"""
    try:
        d = retry(lambda: _ak().stock_zh_index_spot_sina())
        d = d.set_index("代码")
        out = d.reindex(syms)["最新价"].astype(float)
        if out.notna().all():
            return out
    except Exception as e:
        log("新浪指数行情失败，改用腾讯：%s" % e)
    q = tx_quote(list(syms))
    return q["price"].reindex(syms).astype(float)


# ---------------- 申万行业 ----------------
def sw_list(level):
    """申万一/二级行业代码表（缓存 30 天）"""
    name = "sw_l%d_list.pkl" % level
    x = load(name)
    if x is None or age_days(name) > 30:
        ak = _ak()
        d = retry(lambda: ak.sw_index_first_info() if level == 1 else ak.sw_index_second_info())
        x = [(str(c).split(".")[0], n) for c, n in zip(d["行业代码"], d["行业名称"])]
        save(name, x)
    return x


def sw_hist(codes, name, max_age=0.5, since=None):
    """申万指数日收盘 DataFrame[日期 × 代码]（收盘后更新）"""
    x = load(name)
    if x is not None and age_days(name) <= max_age:
        return x
    ak = _ak()
    def one(c):
        try:
            d = retry(lambda: ak.index_hist_sw(symbol=c, period="day"))
            return c, d.assign(日期=pd.to_datetime(d["日期"])).set_index("日期")["收盘"].astype(float)
        except Exception:
            return c, None
    res = dict(pmap(one, codes))
    got = {c: s for c, s in res.items() if s is not None}
    if len(got) < len(codes):
        log("申万行业指数 %s 只取到 %d/%d 个%s" % (name, len(got), len(codes), "，沿用缓存补齐" if x is not None else ""))
    if not got:
        if x is not None: return x
        raise RuntimeError("申万行业指数全部下载失败，且没有缓存")
    df = pd.DataFrame(got).sort_index()
    if x is not None:
        df = df.combine_first(x) if len(got) < len(codes) else df
    df = df[df.index >= (pd.Timestamp(since) if since else pd.Timestamp(HIST_START) - pd.Timedelta(days=400))]
    if len(got) == len(codes): save(name, df)
    return df


def sw_members(codes, name="sw_l2_members.pkl", max_age=7):
    """申万行业成分 {个股代码(6位): 行业代码}（缓存 7 天）"""
    x = load(name)
    if x is not None and age_days(name) <= max_age:
        return x
    ak = _ak()
    def one(c):
        try:
            d = retry(lambda: ak.index_component_sw(symbol=c))
            return c, [str(s).zfill(6) for s in d["证券代码"]]
        except Exception:
            return c, None
    m = {}
    for c, ss in pmap(one, codes):
        for s in ss or []:
            m[s] = c
    save(name, m)
    return m


# ---------------- 业绩报表 ----------------
def _periods(n=None):
    n = n or FIN_N
    y = bj_now().year
    out = []
    for yy in range(y, y - 2 - n // 4, -1):
        for md in ("1231", "0930", "0630", "0331"):
            p = "%d%s" % (yy, md)
            if pd.Timestamp(p) < pd.Timestamp(bj_now().date()):
                out.append(p)
    return sorted(out)[-n:]


def fin_reports():
    """近 8 个报告期的业绩报表（东方财富）；已结束很久的期不再重抓"""
    F = load("fin.pkl", {})
    ak = _ak()
    for p in _periods():
        old = p in F and len(F[p]) > 1000
        stale = (pd.Timestamp(bj_now().date()) - pd.Timestamp(p)).days < 200      # 披露期内每天更新
        if old and not (stale and age_days("fin.pkl") > 0.5):
            continue
        try:
            d = retry(lambda: ak.stock_yjbb_em(date=p))
            d["code"] = d["股票代码"].astype(str).str.zfill(6)
            F[p] = d.set_index("code")[["净利润-净利润", "营业总收入-营业总收入", "净资产收益率", "每股净资产", "最新公告日期"]]
            F[p].columns = ["np", "rev", "roe", "bps", "pub"]
            F[p]["pub"] = pd.to_datetime(F[p]["pub"], errors="coerce")
        except Exception as e:
            log("业绩报表 %s 获取失败：%s" % (p, e))
    save("fin.pkl", F)
    return F


def report_dates(years, name="report_dates.pkl"):
    """年报实际披露日 {年份: Series[6 位代码 → 日期]}（东财「预约披露时间」ACTUAL_PUBLISH_DATE）。
    业绩报表里的「最新公告日期」对往年会被后来的公告改写（全部晚于 5 月），回测判断「4/15 起年报未披露」必须用这个。
    已结束很久的年份不再重抓；当年（1–5 月披露期）每天更新一次。"""
    x = load(name, {})
    now = bj_now()
    for y in years:
        fresh = y in x and (y < now.year - 1 or (y == now.year - 1 and now.month > 5) or age_days(name) < 1)
        if fresh:
            continue
        URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        rows, page = [], 1
        try:
            while True:
                p = {"sortColumns": "SECURITY_CODE", "sortTypes": "1", "pageSize": "500", "pageNumber": str(page),
                     "reportName": "RPT_PUBLIC_BS_APPOIN", "columns": "SECURITY_CODE,ACTUAL_PUBLISH_DATE",
                     "filter": "(SECURITY_TYPE_CODE in (\"058001001\",\"058001008\"))(REPORT_DATE='%d-12-31')" % y}
                j = retry(lambda: requests.get(URL, params=p, timeout=20).json())
                r = j.get("result") or {}
                rows += r.get("data") or []
                if page >= (r.get("pages") or 1):
                    break
                page += 1
            d = pd.DataFrame(rows)
            if len(d):
                x[y] = pd.to_datetime(d.set_index("SECURITY_CODE")["ACTUAL_PUBLISH_DATE"], errors="coerce")
                save(name, x)
        except Exception as e:
            log("年报披露日 %d 获取失败：%s" % (y, str(e)[-80:]))
    return x


def fix_annual_pub(F, rd):
    """用年报实际披露日替换业绩报表里年报的 pub（有实际日期的才换）"""
    F = dict(F)
    for p in list(F):
        if p.endswith("1231") and int(p[:4]) in rd:
            df = F[p].copy()
            act = rd[int(p[:4])].reindex(df.index)
            df["pub"] = act.fillna(df["pub"])
            F[p] = df
    return F


def fin_asof(F, day):
    """day 当天已能看到的最新一期：返回 DataFrame[code]：np roe_a bps period"""
    day = pd.Timestamp(day)
    dl = {"0331": (4, 30), "0630": (8, 31), "0930": (10, 31), "1231": (4, 30)}
    rows = []
    for p in sorted(F):
        y, md = int(p[:4]), p[4:]
        m, d = dl[md]
        deadline = pd.Timestamp(y + (1 if md == "1231" else 0), m, d)
        df = F[p]
        df = df[~df.index.duplicated(keep="first")]        # 东财同一期偶有重复行（如更正公告）
        pub =df["pub"].where(df["pub"] > pd.Timestamp(p), deadline).clip(upper=deadline)
        ok = df[pub <= day]
        if len(ok):
            q = {"0331": 1, "0630": 2, "0930": 3, "1231": 4}[md]
            rows.append(pd.DataFrame({"np": ok["np"], "roe_a": pd.to_numeric(ok["roe"], errors="coerce") * 4 / q / 100,
                                      "bps": ok["bps"], "period": p}))
    if not rows:
        return pd.DataFrame(columns=["np", "roe_a", "bps", "period"])
    allr = pd.concat(rows)
    return allr[~allr.index.duplicated(keep="last")]


# ---------------- ETF ----------------
def etf_list():
    x = load("etf_list.pkl")
    if x is None or age_days("etf_list.pkl") > 7:
        try:
            d = retry(lambda: _ak().fund_etf_category_sina(symbol="ETF基金"))
            x = list(zip(d["代码"], d["名称"]))
            save("etf_list.pkl", x)
        except Exception as e:
            if x is None: raise
            log("ETF 列表更新失败，沿用缓存：%s" % str(e)[-80:])
    return x


def etf_spot(codes):
    """ETF 今日行情 DataFrame[index=代码]：最新价、昨收。新浪失败时改用腾讯"""
    try:
        es = retry(lambda: _ak().fund_etf_category_sina(symbol="ETF基金")).set_index("代码")
        return es[["最新价", "昨收"]]
    except Exception as e:
        log("新浪 ETF 行情失败，改用腾讯：%s" % str(e)[-80:])
    q = tx_quote(list(codes))
    return pd.DataFrame({"最新价": q["price"], "昨收": q["prev"]})


def etf_hist(codes, max_age=0.5):
    """ETF 日线（不复权，新浪）：返回 (close, amount) 两个 DataFrame；单日涨跌超 21% 视为份额折算"""
    x = load("etf_hist.pkl")
    if x is not None and age_days("etf_hist.pkl") <= max_age and set(codes) <= set(x[0].columns):
        return x
    ak = _ak()
    def one(c):
        try:
            d = retry(lambda: ak.fund_etf_hist_sina(symbol=c))
            d = d.assign(date=pd.to_datetime(d["date"])).set_index("date")
            d = d[d.index >= pd.Timestamp(HIST_START)]
            return c, d
        except Exception:
            return c, None
    res = {c: d for c, d in pmap(one, codes) if d is not None and len(d)}
    if len(res) < len(codes) * 0.8 and x is not None:
        log("ETF 日线只取到 %d/%d 只，沿用缓存" % (len(res), len(codes)))
        return x
    close = pd.DataFrame({c: d["close"] for c, d in res.items()}).sort_index().astype(float)
    amount = pd.DataFrame({c: d["amount"] for c, d in res.items()}).sort_index().astype(float)
    r = close.pct_change()
    split = r.abs() > 0.21
    fac = (1 + r.where(~split, 0)).cumprod() / (1 + r).cumprod()          # 折算日之前的价格按比例调整
    adj = close * fac.fillna(1).div(fac.fillna(1).iloc[-1], axis=1)
    x = (adj, amount)
    save("etf_hist.pkl", x)
    return x
