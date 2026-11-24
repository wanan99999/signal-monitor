# 策略信号

https://wanan99999.github.io/signal-monitor/

每个交易日提示所选策略今天该买、该卖、调仓还是不动：

- **14:30 左右**：盘中提示，用当时价格估算 14:50 的操作
- **15:35 以后**：收盘复核，用收盘价重算，并和盘中提示对照

## 运行方式

本机 Windows 任务计划 `SignalMonitor` 每 5 分钟调一次 `tasks/run_task.ps1`（北京时间 13:25 起 4 小时），
`engine/run.py` 按北京时间决定跑盘中还是收盘，当天跑过就跳过，非交易日直接退出。算完写 `data/signals.json` 并推送到 GitHub。
不需要打开 Claude，只需要电脑开机（睡眠可唤醒）。

```
python engine/run.py --mode intraday --force --no-push   # 手动跑一次盘中（不推送）
python engine/run.py --mode close --force                # 手动跑收盘并推送
powershell -ExecutionPolicy Bypass -File tasks/setup_tasks.ps1   # 注册定时任务
```

## 数据

| 用途 | 来源 |
|---|---|
| 全 A 实时（价格、昨收、流通/总市值、PE、PB） | 腾讯 qt.gtimg.cn 批量行情 |
| 全 A 代码表、指数实时、ETF 实时、个股日线 + 前复权因子、ETF 日线 | 新浪（akshare） |
| 申万一/二级行业指数、成分股 | 申万研究所（akshare） |
| 净利润、ROE、每股净资产 | 东方财富业绩报表（akshare） |

`cache/`（不进 git）：个股历史首次建约 20 分钟（`python -c "import data; data.build_history()"`，在 engine 目录下），
之后每天收盘用腾讯行情追加一行；腾讯「昨收」和缓存不一致即视为除权除息，自动调整前复权因子。

## 策略（engine/config.json 开关）

| id | 策略 | 规则来源 |
|---|---|---|
| combo | 全行情切换组合（三合一 V1.4） | 聚宽 `全行情切换组合_个股+板块+稳健_V1.4.py` |
| steady | 大盘股熊市震荡市稳健策略（30 只） | 聚宽 `大盘股熊市震荡市稳健策略_V1.1.py` |
| bank | 银行股顺势策略 | 聚宽 `银行股顺势策略.py` |

每个策略按「起点」分别算：最近 60 个交易日里每一天都可以当起点，起点当天空仓建仓，调仓日从起点开始数（和聚宽从这天开始回测一致）。
结果写在 data/s/<策略>/<起点>.json，data/signals.json 只放各起点的买卖笔数汇总；网页上每个策略单独选起点，存在浏览器里。
与起点无关的计算（选股排名、市场条件、行业→ETF 匹配）按日期缓存共用，60 个起点 × 3 个策略一次约 1 分钟。与聚宽的差异：财务数据口径（东财累计值 vs 聚宽单季）、ST 用当前名称判断、
板块块用申万官方二级行业指数、不按 100 股取整，个别选股可能不同。
