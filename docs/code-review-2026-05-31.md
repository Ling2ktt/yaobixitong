# 妖币交易系统 代码审查报告
**日期**: 2026-05-31 | **范围**: 全部核心模块 | **审查人**: CodeReviewExpert

---

## 总体评估: B+ (系统可运行, 存在需修复的关键问题)

本轮审查覆盖 6 个核心文件。系统核心流程正确（Trinity策略→风控→开仓→SL/TP挂单→监控），分批止盈和杠杆修复已落地。发现 3 个 P0 问题和若干 P1/P2 优化项。

---

## 🔴 P0 — 必须修复

### P0-1: `strategy_trinity.py` — 1H K线预获取被破坏

**位置**: `strategy_trinity.py:1341`
```python
df_1h = None  # <-- 覆盖了函数参数!
```
**影响**: `generate_signal(df_1h=pre_fetched_data)` 传入的预取数据被静默丢弃，导致策略每次内部重新拉取 1H K线，浪费API调用，增加延迟。
**修复**: 删除 `df_1h = None` 这一行。`df_1h` 的默认值已在函数签名中为 `None`。

### P0-2: `engine.py` — `_execute_decision` 中 `to_dict()` 被调用两次导致数据丢

**位置**: `engine.py:1015` + `engine.py:1080`
**影响**: 第一次 `to_dict()` 在风控前（line 1015），风控可能修改 price→entry_price 的映射。第二次 `to_dict()` 在平仓分支（line 1080）重建了 dict，丢失了第一次的映射。
**修复**: CLOSE 分支直接使用第一次的 `decision_dict`，不需要重新调用 `to_dict()`。

### P0-3: `engine.py` — `_monitor_positions` 中 `close_position` 缺少异常保护

**位置**: `engine.py:1623, 1654`
**影响**: `close_position()` 和 `record_trade_result()` 没有 try/except。如果平仓失败但 PnL 已记录，风控的 daily_pnl 统计会失真，可能造成错误熔断。
**修复**: 包裹 try/except，仅在 `close_position` 返回 True 时记录 PnL。

---

## 🟡 P1 — 应该修复

### P1-1: `engine.py` — 多处硬编码默认值应移入配置

| 位置 | 硬编码值 | 应从哪里读取 |
|------|---------|-------------|
| `_init_modules:137` | `timeframes=['1h','4h']` | config.system.yaml |
| `_init_modules:261-264` | `notifier_config` 全部硬编码 | config |
| `_init_modules:276-282` | `report_time: '23:30'` 等 | config |
| `_decision_trinity:1337` | `asyncio.Semaphore(5)` | config |
| `_decision_trinity:1298` | symbols fallback `['BTC','ETH','SOL']` | config |

### P1-2: `engine.py` — skip_ai 和 AI回退 的 TradeDecision 构造代码重复

**位置**: `engine.py:1447-1460` 和 `engine.py:1499-1512`
**影响**: 两段代码100%相同，改动时必须同步修改，容易遗漏。
**修复**: 提取为 `_build_trade_decision_from_candidate()` 公共方法。

### P1-3: `strategy_trinity.py` — `max_notional` 语义歧义

**位置**: `strategy_trinity.py:1437`
```python
max_notional = self.max_single_order_usdt * self.leverage
```
**影响**: 如果 `max_single_order_usdt=10` 且杠杆=1x，`max_notional=10`(名义价值)。但如果杠杆=5x，`max_notional=50`——这取决于 `max_single_order_usdt` 本意是"保证金上限"还是"名义价值上限"。当前 config 中 `risk.max_single_order_usdt: 10` 的语义需明确。
**建议**: 统一为名义价值上限，即 `max_notional = self.max_single_order_usdt`（不乘杠杆），或明确文档化当前行为。

### P1-4: `strategy_trinity.py` — `generate_signal` 无顶层 try/except

**位置**: `strategy_trinity.py:1311-1478`
**影响**: 如果分析器内部异常（如 KeyError、AttributeError），会直接崩溃到调用方，导致整个循环中断。
**修复**: 包裹 try/except，返回 HOLD 信号 + error_reason。

---

## 💭 P2 — 建议优化

### P2-1: TP 比率硬编码
**位置**: `strategy_trinity.py:1271,1286`
TP 等级始终为 2R/3R/4R，不可通过配置调整。建议添加 `tp1_rr_ratio`, `tp2_rr_ratio`, `tp3_rr_ratio` 配置项。

### P2-2: 重复的延迟导入
**位置**: `engine.py:732,1251`
`import json as _json` 在两个方法内部重复。应提升为模块级导入。

### P2-3: `_monitor_positions` 中 TP 级别处理
**位置**: `engine.py:1660-1669`
当 `tp_qty=0` 时，`_partial_close_position` 被跳过，但 `break` 仍执行，退出循环。如果后续还有有效TP级别会被跳过。

### P2-4: emergency close 失败后位置处于裸仓
**位置**: `order_executor.py:654-680`
验收锁触发紧急平仓失败后，方法返回 None 但仓位已开。日志虽告警，但引擎不知道这笔仓位存在。重启后 `_recover_positions_from_exchange` 可以捞回来。

---

## ✅ 已确认正确的项目

| 项目 | 状态 |
|------|------|
| Algo API 迁移: STOP/TAKE_PROFIT_MARKET 正确使用 `/fapi/v1/algoOrder` | ✅ |
| 分批止盈: 50%/30%/20% 分割+step_size对齐+余量修复 | ✅ |
| 三重防护锁: 前置条件+验收锁+None防护 | ✅ |
| 杠杆: 全局默认 1.0x，TradeDecision/StrategySignal/PositionSizing 全部对齐 | ✅ |
| 仓位上限: max_single_order 硬限制 | ✅ |
| Symbol 格式兼容: BTCUSDT ↔ BTC/USDT | ✅ |
| step_size 缓存 TTL 24h | ✅ |
| UTF-8 编码: stdout + 文件 | ✅ |
| -4411 TradFi-Perps 错误检测 | ✅ |
| reduceOnly 从 Algo API 参数中移除 (-1106 修复) | ✅ |

---

## 修复优先级排序

```
1. P0-1: df_1h=None 删除 (1行改动)
2. P0-3: _monitor_positions 平仓加 try/except
3. P0-2: _execute_decision to_dict() 重复调用
4. P1-4: generate_signal 加 try/except
5. P1-2: TradeDecision 构造去重
6. P1-1: 硬编码默认值移到 config (可分批)
7. P2-1: TP比率可配置化
```
