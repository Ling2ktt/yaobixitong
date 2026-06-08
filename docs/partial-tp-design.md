# 分批止盈方案设计文档

## 一、问题分析

### 当前状态
`order_executor.py` 的 `open_position()` 方法（第489-523行）：
- 只下1笔 TAKE_PROFIT_MARKET algo order，数量 = 全部仓位 `qty`
- `take_profit_quantities` 列表初始化为空，从未填充
- `has_exchange_tp` 为单一 bool，无法区分哪个级别成功
- TP1成交 = 全部仓位被平掉，TP2/TP3形同虚设

### 根因
现货路径 `_execute_spot_order()` 已有完善的分批TP逻辑（ratio分割+多笔订单），但合约路径从未同步该设计。

---

## 二、方案设计

### 2.1 仓位分割比例

| TP级别数 | ratios | 说明 |
|---------|--------|------|
| 1级 | `[1.0]` | 全量平仓（兼容旧逻辑） |
| 2级 | `[0.5, 0.5]` | 对半分 |
| 3级 | `[0.5, 0.3, 0.2]` | 快速回本+锁定利润+博尾段 |
| N级(N>3) | `[0.5] + [0.5/(N-1)] * (N-1)` | 首级50%，余均分 |

与现货路径保持一致的ratio规则。

### 2.2 数量对齐算法（核心）

```python
import math

def align_qty(raw_qty: float, step_size: float) -> float:
    """step_size 对齐：向下取整到最近步长"""
    if step_size <= 0:
        return raw_qty
    decimals = max(0, -int(math.log10(step_size)))
    return round(math.floor(raw_qty / step_size) * step_size, decimals)

# 分割+对齐+余量修复
def split_qty(total_qty: float, ratios: list, step_size: float, min_qty: float) -> list:
    aligned_qtys = []
    for ratio in ratios:
        raw = total_qty * ratio
        aligned = align_qty(raw, step_size)
        aligned_qtys.append(aligned)
    
    # 余量修复：对齐损耗补入最后一级
    diff = total_qty - sum(aligned_qtys)
    if diff > 0 and len(aligned_qtys) > 0:
        aligned_qtys[-1] = align_qty(aligned_qtys[-1] + diff, step_size)
    
    # 二次修复：如果补入后仍有微差（浮点精度），最终兜底
    final_diff = total_qty - sum(aligned_qtys)
    if abs(final_diff) > 1e-10 and len(aligned_qtys) > 0:
        aligned_qtys[-1] = round(aligned_qtys[-1] + final_diff, 
                                  max(0, -int(math.log10(step_size))))
    
    return aligned_qtys
```

### 2.3 仓位过小降级策略

```python
# 检查：分割后每级数量是否 >= min_qty
valid_levels = []
remaining_qty = total_qty

for i, (tp_price, qty_i) in enumerate(zip(take_profit_levels, aligned_qtys)):
    if qty_i >= min_qty:
        valid_levels.append(i)
    # 如果某级数量 < min_qty，将其份额合并到下一个有效级别

# 极端情况：如果连1级都凑不够 min_qty → 回退为全量TP1
if not valid_levels:
    # 仅下1笔全量TP订单（旧逻辑兼容）
    aligned_qtys = [total_qty]
    take_profit_levels = [take_profit_levels[0]]
```

**关键原则**：宁可少分批，也不让小额订单被交易所拒绝。

### 2.4 Order 数据结构变更

```python
@dataclass
class Order:
    # ... 保留原有字段 ...
    take_profit_levels: List[float]       # 不变
    take_profit_quantities: List[float]   # 填充实际对齐后数量
    tp_algo_ids: List[str] = field(default_factory=list)     # 新增：每级TP的algoId
    has_exchange_tp: bool = False         # 保留bool（任一级成功即为True，简化逻辑）
    # ... 其余不变 ...
```

**设计决策**：`has_exchange_tp` 保持 bool 而非改为 `List[bool]`，原因：
1. `_monitor_positions` 只需知道"交易所是否有TP单在执行"来做跳过判断
2. 某级失败时日志已记录，本地监控兜底即可
3. 减少序列化/反序列化改动量

### 2.5 open_position() 核心逻辑变更（第489-523行替换）

```
伪代码：
1. 计算ratios（根据n_levels）
2. 调用split_qty(total_qty=qty, ratios, step_size, min_qty)得到aligned_qtys
3. 如果aligned_qtys有效级别数 < n_levels，裁剪take_profit_levels到有效级别
4. for i, (tp_price, tp_qty) in enumerate(zip(take_profit_levels, aligned_qtys)):
     - 如果 tp_qty < min_qty → 跳过，日志警告
     - 调用 _send_algo_request(TAKE_PROFIT_MARKET, quantity=tp_qty, triggerPrice=tp_price, reduceOnly=true)
     - 成功 → tp_algo_ids.append(algoId), _has_exchange_tp=True
     - 失败 → 日志记录，继续下一级
5. tp_quantities = aligned_qtys
6. Order创建时填入 tp_algo_ids, tp_quantities, has_exchange_tp
```

### 2.6 _monitor_positions() 变更

**当前逻辑**（问题）：
- 遍历 `take_profit_levels`，只要价格命中任一级 → 全量平仓
- `has_exchange_tp=True` 时跳过所有TP本地处理

**新逻辑**：
```python
# 逐级检查TP命中
for i, tp in enumerate(pos.take_profit_levels):
    if direction_mult * (current_price - tp) >= 0:
        # 已命中TP级别i
        tp_qty = pos.take_profit_quantities[i] if i < len(pos.take_profit_quantities) else 0
        
        # 是否最后一级？
        is_last_level = (i == len(pos.take_profit_levels) - 1) or \
                        (pos.remaining_quantity - tp_qty <= min_threshold)
        
        if is_last_level:
            # 最后一级 → 全量平仓（close_position）
            close_reason = f"止盈{len(pos.take_profit_levels)}级 {tp}"
            # 正常平仓流程
        else:
            # 非最后一级 → 部分平仓
            # 方案A: 依赖交易所algo单自动执行（推荐，减少本地操作风险）
            # 方案B: 本地主动部分平仓（仅当交易所TP单不存在时）
            
            if getattr(pos, 'has_exchange_tp', False):
                # 交易所TP单存在 → 跳过，让交易所处理
                logger.info(f"[Monitor] {symbol} TP{i+1}命中, 交易所TP单存在, 跳过本地处理")
                continue
            else:
                # 交易所TP单不存在 → 本地部分平仓
                close_reason = f"部分止盈{i+1}级 {tp}"
                # 调用部分平仓方法
```

**部分平仓方法**（新增 `_partial_close_position`）：
```python
def _partial_close_position(self, order_id: str, close_qty: float, reason: str) -> bool:
    """部分平仓：减少remaining_quantity，不移除Order"""
    target_pos = # find order
    step_info = self._get_symbol_step_size(target_pos.symbol)
    aligned_qty = align_qty(close_qty, step_info['step_size'])
    
    if aligned_qty < step_info['min_qty']:
        logger.warning(f"部分平仓数量过小: {aligned_qty}")
        return False
    
    # 下市价减仓单
    result = self._send_request('POST', '/fapi/v1/order', 
        params={... 'quantity': str(aligned_qty), 'reduceOnly': 'true' ...})
    
    # 更新Order状态
    target_pos.remaining_quantity -= aligned_qty
    target_pos.filled_quantity += aligned_qty
    # 不移除Order，保持OPENED/PARTIAL_TP状态
```

### 2.7 close_position() 取消algo单逻辑

**当前逻辑已完善**：close_position 时会：
1. 取消普通open orders（STOP_MARKET/TAKE_PROFIT_MARKET）
2. 取消algo orders（通过 `/fapi/v1/openAlgoOrders` + DELETE）

**无需修改**：当部分TP成交后触发 close_position（止损或最后一级TP），现有的取消逻辑会自动清理剩余挂单。

### 2.8 SL订单数量联动

**当前**：止损单 quantity = 全部仓位 `qty`

**新方案**：止损单保持 `qty`（全量）不变。原因：
1. 止损触发 = 关闭全部剩余仓位，quantity应 = remaining_quantity
2. 但 algo order 下单时 remaining_quantity = qty（尚未TP），所以初始SL数量正确
3. 部分TP成交后，SL单的quantity可能 > remaining_quantity → 需要处理

**SL联动方案**：TP1/TP2成交后，应修改（取消+重下）SL单数量为当前remaining_quantity。
- 复杂度较高，且币安的STOP_MARKET在仓位不足时只平掉实际持仓（不会反向开仓，因为有reduceOnly）
- **简化方案**：SL单保持原数量，依赖 reduceOnly 保护。即使SL触发时仓位已减，交易所只平掉实际持仓量。

**结论**：SL数量不需要联动修改，reduceOnly 已提供安全保障。

---

## 三、改动文件清单

| 文件 | 改动点 | 优先级 |
|------|--------|--------|
| `modules/order_executor.py` | 1. Order新增 `tp_algo_ids` 字段 | P0 |
| | 2. `open_position()` 第489-523行替换为分批TP逻辑 | P0 |
| | 3. 新增 `_align_qty()` 和 `_split_qty()` 工具方法 | P0 |
| | 4. 新增 `_partial_close_position()` 方法 | P0 |
| | 5. `close_position()` 无需改动 | - |
| `core/engine.py` | 1. `_monitor_positions()` TP检查改为逐级处理 | P0 |
| | 2. 新增 `OrderStatus.PARTIAL_TP` 枚举值 | P1 |
| | 3. TP成交后更新 remaining_quantity/filled_quantity | P0 |

---

## 四、风险与回退

1. **仓位极小**（$10/1x → ~0.0001 BTC）：分割后每级可能 < min_qty
   - 降级为单级全量TP，已设计降级策略
   
2. **交易所algo单部分失败**：TP1成功但TP2/3失败
   - has_exchange_tp=True，本地监控跳过 → 本地监控作为兜底
   - 本地监控检查到价格已过TP2/3但仓位仍在 → 主动部分平仓
   
3. **SL触发时仓位已减**：reduceOnly保护，不会反向开仓
   
4. **回退方案**：如果分批TP导致问题，将ratios设为[1.0]即可回到旧逻辑

---

## 五、测试验证清单

- [ ] 3级TP：50%/30%/20% 数量计算正确
- [ ] step_size对齐：qty=0.010, step=0.001 → TP1=0.005, TP2=0.003, TP3=0.002
- [ ] 余量修复：qty=0.011, step=0.001 → TP1=0.005, TP2=0.003, TP3=0.003(补余量)
- [ ] 1级TP降级：ratio=[1.0], 全量平仓
- [ ] 极小仓位降级：qty < min_qty*2 → 合并为1级
- [ ] algo单失败：TP1成功+TP2失败 → 本地监控兜底
- [ ] SL触发时已部分TP → reduceOnly保护正常
- [ ] close_position取消剩余algo单正常工作
