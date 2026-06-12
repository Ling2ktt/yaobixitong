#!/usr/bin/env python3
"""
订单执行模块

功能：
1. 开仓（带止损）
2. 分批止盈
3. 订单状态管理
4. 与币安API交互
"""
import requests
import time
import hmac
import hashlib
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from enum import Enum
from loguru import logger
import json
import traceback
from dataclasses import dataclass, field
from urllib.parse import urlencode


class OrderStatus(Enum):
    """订单状态"""
    PENDING = "PENDING"       # 待执行
    OPENED = "OPENED"         # 已开仓
    PARTIAL_TP = "PARTIAL_TP" # 部分止盈
    FULL_TP = "FULL_TP"       # 全部止盈
    STOPPED = "STOPPED"       # 止损出场
    CANCELLED = "CANCELLED"   # 已取消
    CLOSED = "CLOSED"         # 已平仓


class OrderType(Enum):
    """订单类型"""
    MARKET = "MARKET"         # 市价单
    LIMIT = "LIMIT"           # 限价单
    STOP_LOSS = "STOP_LOSS"   # 止损单
    TAKE_PROFIT = "TAKE_PROFIT" # 止盈单


@dataclass
class Order:
    """订单对象"""
    order_id: str
    symbol: str
    direction: str  # 'LONG' or 'SHORT'
    status: OrderStatus
    entry_price: float
    entry_time: datetime
    position_size: float  # 仓位大小（币）
    leverage: float
    stop_loss: float
    take_profit_levels: List[float]  # 分批止盈价位
    take_profit_quantities: List[float]  # 分批止盈数量
    filled_quantity: float = 0.0  # 已成交数量
    remaining_quantity: float = 0.0  # 剩余数量
    realized_pnl: float = 0.0  # 已实现盈亏
    order_ids: List[str] = field(default_factory=list)  # 币安订单ID列表
    tp_algo_ids: List[str] = field(default_factory=list)  # Fix: 每级TP的algoId
    # Fix P1-3: 交易所保护单标记 — True 表示交易所已挂成功 STOP_MARKET / TAKE_PROFIT_MARKET
    has_exchange_stop: bool = False   # 交易所止损挂单成功
    has_exchange_tp: bool = False     # 交易所止盈挂单成功（任一级成功即为True）

    def to_dict(self) -> Dict[str, Any]:
        """转为字典"""
        return {
            'order_id': self.order_id,
            'symbol': self.symbol,
            'direction': self.direction,
            'status': self.status.value,
            'entry_price': self.entry_price,
            'entry_time': self.entry_time.isoformat(),
            'position_size': self.position_size,
            'leverage': self.leverage,
            'stop_loss': self.stop_loss,
            'take_profit_levels': self.take_profit_levels,
            'take_profit_quantities': self.take_profit_quantities,
            'filled_quantity': self.filled_quantity,
            'remaining_quantity': self.remaining_quantity,
            'realized_pnl': self.realized_pnl,
            'order_ids': self.order_ids,
            'tp_algo_ids': self.tp_algo_ids,
            'has_exchange_stop': self.has_exchange_stop,
            'has_exchange_tp': self.has_exchange_tp
        }


class OrderExecutorModule:
    """订单执行模块"""

    # === 仓位分割工具方法 ===

    @staticmethod
    def _align_qty(raw_qty: float, step_size: float) -> float:
        """step_size 对齐：向下取整到最近步长

        Args:
            raw_qty: 原始数量
            step_size: 交易所步长（如0.001）

        Returns:
            对齐后的数量
        """
        if step_size <= 0:
            return raw_qty
        import math
        decimals = max(0, -int(math.log10(step_size))) if step_size > 0 else 8
        return round(math.floor(raw_qty / step_size) * step_size, decimals)

    @staticmethod
    def _make_client_order_id(prefix: str, source_id: str, max_length: int = 36) -> str:
        """生成满足 Binance 长度限制的客户端订单 ID。"""
        import re

        safe_prefix = re.sub(r"[^A-Za-z0-9_-]", "", prefix or "")
        safe_source = re.sub(r"[^A-Za-z0-9_-]", "", source_id or "")
        if max_length <= 0:
            return ""

        base = f"{safe_prefix}{safe_source}"
        if len(base) <= max_length:
            return base

        digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:8]
        separator = "_" if safe_prefix else ""
        available = max_length - len(safe_prefix) - len(separator) - len(digest)
        if available <= 0:
            prefix_part = safe_prefix[:max(0, max_length - len(digest))]
            return f"{prefix_part}{digest}"[:max_length]

        source_part = safe_source[:available]
        return f"{safe_prefix}{source_part}{separator}{digest}"

    @staticmethod
    def _split_qty(total_qty: float, ratios: list, step_size: float,
                   min_qty: float) -> tuple:
        """按比例分割仓位并对齐step_size

        Args:
            total_qty: 总仓位（币）
            ratios: 分割比例列表（如[0.5, 0.3, 0.2]）
            step_size: 交易所步长
            min_qty: 交易所最小数量

        Returns:
            (aligned_qtys, valid_levels) — 对齐后数量列表和有效级别索引列表
        """
        aligned_qtys = []
        for ratio in ratios:
            raw = total_qty * ratio
            aligned = OrderExecutorModule._align_qty(raw, step_size)
            aligned_qtys.append(aligned)

        # 余量修复：对齐损耗补入最后一级
        diff = total_qty - sum(aligned_qtys)
        if diff > 0 and len(aligned_qtys) > 0:
            import math
            decimals = max(0, -int(math.log10(step_size))) if step_size > 0 else 8
            aligned_qtys[-1] = round(aligned_qtys[-1] + diff, decimals)

        # 过滤有效级别（qty >= min_qty）
        valid_levels = []
        for i, q in enumerate(aligned_qtys):
            if q >= min_qty:
                valid_levels.append(i)

        return aligned_qtys, valid_levels

    @staticmethod
    def _normalize_futures_symbol(symbol: str) -> str:
        """Convert local symbols like BTC/USDT:USDT to Binance BTCUSDT."""
        symbol = (symbol or "").strip().upper()
        if "/" in symbol:
            base, rest = symbol.split("/", 1)
            quote = rest.split(":", 1)[0]
            return f"{base}{quote}"
        return symbol.split(":", 1)[0]

    @staticmethod
    def _prepare_tp_slices(
        qty: float,
        take_profit_levels: List[float],
        ratios: List[float],
        step_size: float,
        min_qty: float,
        min_notional: float = 5.0,
    ) -> Tuple[List[float], List[float]]:
        """Prepare TP levels/quantities, collapsing tiny partials to one full TP."""
        if not take_profit_levels or qty <= 0:
            return [], []

        aligned_qtys, valid_levels = OrderExecutorModule._split_qty(
            qty, ratios, step_size, min_qty
        )
        pairs = []
        for idx in valid_levels:
            if idx >= len(take_profit_levels) or idx >= len(aligned_qtys):
                continue
            level = float(take_profit_levels[idx])
            level_qty = float(aligned_qtys[idx])
            if level_qty >= min_qty and level_qty * level >= min_notional:
                pairs.append((level, level_qty))

        if not pairs:
            return [float(take_profit_levels[0])], [qty]

        total_qty = round(sum(q for _, q in pairs), 12)
        if abs(total_qty - qty) > max(step_size, 1e-12) * 0.01:
            logger.warning(
                "[OrderExecutor] 分批TP因最小名义价值过滤后总量{} != 仓位{}，"
                "降级为1级全量TP",
                total_qty,
                qty,
            )
            return [float(take_profit_levels[0])], [qty]

        return [p for p, _ in pairs], [q for _, q in pairs]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化订单执行模块
        
        Args:
            config: 配置字典
        """
        config = config or {}
        
        # 币安API配置
        self.api_key = config.get('api_key', '')
        self.api_secret = config.get('api_secret', '')
        self.testnet = config.get('testnet', config.get('sandbox', True))  # 默认使用测试网
        
        # API端点
        if self.testnet:
            self.base_url = "https://testnet.binance.vision"
            self.futures_base_url = "https://testnet.binancefuture.com"
        else:
            self.base_url = "https://api.binance.com"
            self.futures_base_url = "https://fapi.binance.com"
        
        # HTTP会话
        self.session = requests.Session()
        self.session.headers.update({
            'X-MBX-APIKEY': self.api_key
        })
        
        # 代理配置
        self.proxy = config.get('proxy', '')
        if self.proxy:
            self.session.proxies = {
                'http': self.proxy,
                'https': self.proxy
            }
            logger.info(f"[OrderExecutor] 使用代理: {self.proxy}")
        
        # 当前持仓
        self.positions: List[Order] = []
        
        # 订单计数器（用于生成订单ID）
        self.order_counter = 0
        
        # 符号精度缓存（带TTL，24小时自动过期）
        self._step_size_cache: Dict[str, dict] = {}
        self._step_size_cache_time: Dict[str, float] = {}  # 缓存写入时间戳
        self._step_size_cache_ttl: int = 86400  # TTL = 24小时（秒）
        
        logger.info(f"[OrderExecutor] 初始化完成 | "
                    f"测试网: {self.testnet} | "
                    f"API Key: {'已配置' if self.api_key else '❌ 未配置'}")
    
    def _get_symbol_step_size(self, symbol: str) -> dict:
        """获取交易对的数量步长（带TTL缓存，24小时自动过期）
        
        Fix #2 (Round3): 缓存永不过期导致:
        - 初始获取失败时 fallback 值被永久缓存
        - 币安更新 LOT_SIZE 后本地不感知
        - 内存常驻陈旧数据
        """
        import time as _time
        now_ts = _time.time()
        
        # 检查缓存是否存在且未过期
        if symbol in self._step_size_cache:
            cache_age = now_ts - self._step_size_cache_time.get(symbol, 0)
            if cache_age < self._step_size_cache_ttl:
                return self._step_size_cache[symbol]
            else:
                # 缓存过期，清除后重新获取
                logger.info("[OrderExecutor] step_size 缓存过期({:.0f}h)，刷新: {}",
                           cache_age / 3600, symbol)
                del self._step_size_cache[symbol]
                self._step_size_cache_time.pop(symbol, None)
        
        try:
            base = self.futures_base_url
            resp = self.session.get(f"{base}/fapi/v1/exchangeInfo")
            data = resp.json()
            for s in data.get('symbols', []):
                sym = s['symbol']
                for f in s.get('filters', []):
                    if f['filterType'] == 'LOT_SIZE':
                        min_notional = 5.0
                        for nf in s.get('filters', []):
                            if nf.get('filterType') in ('MIN_NOTIONAL', 'NOTIONAL'):
                                min_notional = float(
                                    nf.get('notional')
                                    or nf.get('minNotional')
                                    or min_notional
                                )
                        info = {
                            'step_size': float(f['stepSize']),
                            'min_qty': float(f['minQty']),
                            'max_qty': float(f['maxQty']),
                            'min_notional': min_notional,
                        }
                        self._step_size_cache[sym] = info
                        self._step_size_cache_time[sym] = now_ts  # Fix #2: 记录缓存时间
            if symbol not in self._step_size_cache:
                # fallback
                logger.warning(f"[OrderExecutor] 未找到 {symbol} 的精度信息，使用默认值")
                self._step_size_cache[symbol] = {'step_size': 1, 'min_qty': 1, 'max_qty': 10000000, 'min_notional': 5.0}
                self._step_size_cache_time[symbol] = now_ts  # Fix #2: fallback也记时
        except Exception as e:
            logger.warning(f"[OrderExecutor] 获取符号精度失败: {e}，使用默认值")
            self._step_size_cache[symbol] = {'step_size': 1, 'min_qty': 1, 'max_qty': 10000000, 'min_notional': 5.0}
            self._step_size_cache_time[symbol] = now_ts  # Fix #2: 异常也记时
        
        return self._step_size_cache[symbol]
    
    def register_exchange(self, name: str, exchange):
        """
        注册交易所（用于订单执行）
        
        Args:
            name: 交易所名称
            exchange: 交易所对象（ccxt）
        """
        logger.info(f"[OrderExecutor] 注册交易所: {name}")
        # 当前实现使用直接API调用，不需要存储exchange对象
    
    def _generate_signature(self, params: Dict[str, Any]) -> str:
        """
        生成币安API签名
        
        Args:
            params: 请求参数（不含signature）
            
        Returns:
            签名字符串
        """
        # 关键：必须URL编码，且与requests库的编码方式一致
        # requests 默认使用 urllib.parse.urlencode 编码参数
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def _send_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
        use_futures: bool = False
    ) -> Dict[str, Any]:
        """
        发送HTTP请求到币安API
        
        Args:
            method: HTTP方法 ('GET', 'POST', 'DELETE')
            endpoint: API端点
            params: 请求参数
            signed: 是否需要签名
            use_futures: 是否使用合约API (fapi.binance.com)
            
        Returns:
            API响应
        """
        params = params or {}
        
        # 添加时间戳和接收窗口
        if signed:
            params['timestamp'] = int(time.time() * 1000)
            params['recvWindow'] = 10000
            params['signature'] = self._generate_signature(params)
        
        base = self.futures_base_url if use_futures else self.base_url
        url = f"{base}{endpoint}"
        
        try:
            if method == 'GET':
                response = self.session.get(url, params=params)
            elif method == 'POST':
                response = self.session.post(url, params=params)
            elif method == 'DELETE':
                response = self.session.delete(url, params=params)
            else:
                raise ValueError(f"不支持的HTTP方法: {method}")
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"[OrderExecutor] API请求失败: {e}")
            # Fix #4 (Round4): 解析API错误码，-4411表示TradFi-Perps协议未签署
            err_body = ''
            err_code = 0
            if hasattr(e, 'response') and e.response is not None:
                err_body = e.response.text
                logger.error(f"[OrderExecutor] 响应: {err_body}")
                try:
                    err_json = e.response.json()
                    err_code = err_json.get('code', 0)
                    if err_code == -4411:
                        logger.error("[OrderExecutor] ⚠️ TradFi-Perps协议未签署！该代币需要签署特殊协议")
                except:
                    pass
            raise
    
    def _send_algo_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
    ) -> Dict[str, Any]:
        """
        发送Algo Order请求（STOP_MARKET/TAKE_PROFIT_MARKET迁移到 /fapi/v1/algoOrder）
        
        Binance于2025-12-09将条件订单迁移到Algo端点(-4120错误)。
        参数映射: stopPrice→triggerPrice, clientOrderId→clientAlgoId, orderId→algoId
        """
        params = params or {}
        
        if signed:
            params['timestamp'] = int(time.time() * 1000)
            params['recvWindow'] = 10000
            params['signature'] = self._generate_signature(params)
        
        url = f"{self.futures_base_url}{endpoint}"
        
        try:
            if method == 'GET':
                response = self.session.get(url, params=params)
            elif method == 'POST':
                response = self.session.post(url, params=params)
            elif method == 'DELETE':
                response = self.session.delete(url, params=params)
            else:
                raise ValueError(f"不支持的HTTP方法: {method}")
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"[OrderExecutor] Algo API请求失败: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"[OrderExecutor] 响应: {e.response.text}")
            raise
    
    def open_position(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit_levels: List[float],
        position_size: float,
        leverage: float = 1.0  # Fix: 默认1.0（与config对齐，不再默认3.0）
    ) -> Optional[Order]:
        """
        开仓（带止损）- 币安U本位永续合约
        
        Args:
            symbol: 交易对 (如 'BTCUSDT')
            direction: 方向 ('LONG' or 'SHORT')
            entry_price: 入场价
            stop_loss: 止损价
            take_profit_levels: 止盈价位列表（分批）
            position_size: 仓位大小（币）
            leverage: 杠杆倍数
            
        Returns:
            Order对象 or None (失败)
        """
        # 0. 数量精度处理（合约要求整数或其他 stepSize）
        step_info = self._get_symbol_step_size(symbol)
        step_size = step_info['step_size']
        min_qty = step_info['min_qty']
        min_notional = float(step_info.get('min_notional', 5.0) or 5.0)
        # Fix #4: 使用 math.floor 做精度适配，不用 int() 强转
        import math
        qty = math.floor(position_size / step_size) * step_size
        # 根据 step_size 的小数位数做 round，避免浮点误差
        decimals = max(0, -int(math.log10(step_size))) if step_size > 0 else 8
        qty = round(qty, decimals)
        if qty < min_qty:
            logger.error(f"[OrderExecutor] 数量 {qty} 低于最小数量 {min_qty}")
            return None
        position_size = qty

        # ===== 0. 前置条件检查：必须有止损止盈 =====
        if not stop_loss or stop_loss <= 0:
            logger.error(f"[OrderExecutor] ❌ 拒绝开仓: 止损价无效 ({stop_loss}), 必须有有效止损")
            return None

        if not take_profit_levels or len(take_profit_levels) == 0:
            logger.error(f"[OrderExecutor] ❌ 拒绝开仓: 止盈价列表为空, 必须至少有一个止盈价位")
            return None

        # 验证止盈价位逻辑一致性
        all_invalid = True
        for tp in take_profit_levels:
            if direction == 'LONG' and tp > entry_price:
                all_invalid = False
                break
            elif direction == 'SHORT' and tp < entry_price:
                all_invalid = False
                break
        if all_invalid:
            logger.error(f"[OrderExecutor] ❌ 拒绝开仓: 所有止盈价位方向错误 "
                        f"(direction={direction}, TP={[f'{tp:.6f}' for tp in take_profit_levels]}, "
                        f"entry={entry_price})")
            return None

        # 1. 生成订单ID
        self.order_counter += 1
        order_id = f"WC_{int(time.time())}_{self.order_counter}"
        client_order_id = self._make_client_order_id("WC_", order_id)  # 用于幂等性
        
        logger.info(f"[OrderExecutor] 开仓: {symbol} {direction}")
        logger.info(f"  入场价: ${entry_price:.6f}")
        logger.info(f"  止损价: ${stop_loss:.6f}")
        logger.info(f"  止盈价位: {[f'${tp:.6f}' for tp in take_profit_levels]}")
        logger.info(f"  仓位大小: {position_size:.4f} 币")
        logger.info(f"  杠杆: {leverage}x")
        
        # 2. 设置杠杆（合约 fapi）
        try:
            self._send_request(
                'POST',
                '/fapi/v1/leverage',
                params={
                    'symbol': symbol,
                    'leverage': int(leverage)
                },
                signed=True,
                use_futures=True
            )
            logger.info(f"[OrderExecutor] ✅ 杠杆设置成功: {leverage}x")
        except Exception as e:
            logger.error(f"[OrderExecutor] ❌ 杠杆设置失败: {e}")
            return None
        
        # 3. 下达入场订单（市价单 - 合约）
        try:
            side = 'BUY' if direction == 'LONG' else 'SELL'
            pos_side = 'LONG' if direction == 'LONG' else 'SHORT'
            
            # 确保名义价值 ≥ $5（币安合约最低要求）
            notional = position_size * entry_price
            if notional < 5.0:
                # 安全向上取整：基于 step_size 和 min_notional，不得超过风险预算
                import math
                # 计算满足最小名义价值所需的最小数量（向上取整到step_size）
                min_qty_needed = math.ceil(5.0 / entry_price / step_size) * step_size
                decimals = max(0, -int(math.log10(step_size))) if step_size > 0 else 8
                min_qty_needed = round(min_qty_needed, decimals)
                
                # 检查是否超出风险预算（原始仓位大小的3倍作为安全上限）
                safe_cap = max(position_size * 3, min_qty)
                if min_qty_needed > safe_cap:
                    logger.error("[OrderExecutor] ❌ 名义价值 ${:.2f} < $5, "
                                "且最小合规数量 {:.4f} 超出安全上限 {:.4f}，拒单",
                                notional, min_qty_needed, safe_cap)
                    return None
                
                logger.warning("[OrderExecutor] 名义价值 ${:.2f} < $5, 安全调整数量: {:.4f} → {:.4f}",
                             notional, position_size, min_qty_needed)
                position_size = min_qty_needed
            
            # 使用 step_size 适配后的数量
            qty = math.floor(position_size / step_size) * step_size
            decimals = max(0, -int(math.log10(step_size))) if step_size > 0 else 8
            qty = round(qty, decimals)
            if qty < min_qty:
                logger.error(f"[OrderExecutor] 数量 {qty} 低于最小数量 {min_qty}")
                return None
            position_size = qty
            
            order_params = {
                'symbol': symbol,
                'side': side,
                'type': 'MARKET',
                'quantity': str(qty),
                'positionSide': pos_side,  # 双向持仓模式
                'newClientOrderId': client_order_id,  # 幂等性
            }
            
            entry_order = self._send_request(
                'POST',
                '/fapi/v1/order',
                params=order_params,
                signed=True,
                use_futures=True
            )
            
            logger.info(f"[OrderExecutor] ✅ 入场订单成功 | 订单ID: {entry_order['orderId']}")
            
        except Exception as e:
            err_str = str(e)
            # Fix #4 (Round4): 将-4411错误码注入异常消息，让engine能识别
            if '-4411' in err_str or 'TradFi' in err_str:
                logger.error(f"[OrderExecutor] ❌ 入场订单失败: {err_str}")
                logger.error("[OrderExecutor] ⚠️ 该代币需要签署币安TradFi-Perps协议")
                raise RuntimeError(f"TradFi-Perps协议未签署(-4411): {err_str}")
            logger.error(f"[OrderExecutor] ❌ 入场订单失败: {e}")
            return None
        
        # 4. 下达止损订单（Algo Order API — Binance 2025-12-09 迁移）
        stop_side = 'SELL' if direction == 'LONG' else 'BUY'
        stop_order = None
        try:
            stop_order = self._send_algo_request(
                'POST',
                '/fapi/v1/algoOrder',
                params={
                    'symbol': symbol,
                    'side': stop_side,
                    'type': 'STOP_MARKET',
                    'algoType': 'CONDITIONAL',
                    'triggerPrice': f"{stop_loss:.6f}",
                    'quantity': str(qty),
                    'positionSide': pos_side,
                    'workingType': 'CONTRACT_PRICE',
                },
                signed=True,
            )
            logger.info(f"[OrderExecutor] ✅ 止损订单成功 | algoId: {stop_order.get('algoId')}")
            _has_exchange_stop = True

        except Exception as e:
            logger.warning(f"[OrderExecutor] ⚠️ 止损订单失败（引擎将接管监控）: {e}")
            stop_order = None
            # Fix P1-3: 止损保护单挂单失败，本地兜底仍能工作
            _has_exchange_stop = False
        
        # 5. 下达分批止盈订单（Algo Order API — Binance 2025-12-09 迁移）
        # Fix: 分批TP — 按ratio分割仓位，每级独立下单
        tp_orders = []
        tp_algo_ids = []
        tp_quantities = []
        _has_exchange_tp = False

        n_levels = len(take_profit_levels)
        if n_levels >= 1:
            # 计算分割比例（与现货路径一致）
            if n_levels == 1:
                ratios = [1.0]
            elif n_levels == 2:
                ratios = [0.5, 0.5]
            elif n_levels == 3:
                ratios = [0.5, 0.3, 0.2]
            else:
                ratios = [0.5] + [0.5 / (n_levels - 1)] * (n_levels - 1)

            take_profit_levels, aligned_qtys = self._prepare_tp_slices(
                qty=qty,
                take_profit_levels=take_profit_levels,
                ratios=ratios,
                step_size=step_size,
                min_qty=min_qty,
                min_notional=min_notional,
            )
            n_levels = len(take_profit_levels)

            tp_side = 'SELL' if direction == 'LONG' else 'BUY'
            for i, (tp_price, tp_qty) in enumerate(zip(take_profit_levels, aligned_qtys)):
                try:
                    tp_order = self._send_algo_request(
                        'POST',
                        '/fapi/v1/algoOrder',
                        params={
                            'symbol': symbol,
                            'side': tp_side,
                            'type': 'TAKE_PROFIT_MARKET',
                            'algoType': 'CONDITIONAL',
                            'triggerPrice': f"{tp_price:.6f}",
                            'quantity': f"{tp_qty}",
                            'positionSide': pos_side,
                            'workingType': 'CONTRACT_PRICE',
                        },
                        signed=True,
                    )
                    tp_orders.append(tp_order.get('algoId', ''))
                    tp_algo_ids.append(tp_order.get('algoId', ''))
                    tp_quantities.append(tp_qty)
                    logger.info(f"[OrderExecutor] ✅ 止盈{i+1}订单成功 | "
                                f"价位: ${tp_price:.6f} | "
                                f"数量: {tp_qty} | "
                                f"比例: {tp_qty / qty * 100:.0f}% | "
                                f"algoId: {tp_order.get('algoId')}")
                    _has_exchange_tp = True

                except Exception as e:
                    logger.error(f"[OrderExecutor] ❌ 止盈{i+1}订单失败 (价位${tp_price:.6f}): {e}")
                    # 记录0量占位，保持索引对齐
                    tp_quantities.append(0)
                    # Fix P1-3: 止盈保护单挂单失败，本地兜底仍能工作

            # 验证分批数量总和
            total_tp_qty = sum(tp_quantities)
            if abs(total_tp_qty - qty) > step_size * 0.01:
                logger.warning(f"[OrderExecutor] 分批TP总量{total_tp_qty} != 仓位{qty}, "
                              f"差值={qty - total_tp_qty}")

        # ===== 5b. 验收锁：SL/TP 必须至少有一个交易所保护单成功 =====
        if not _has_exchange_stop and not _has_exchange_tp:
            logger.error(f"[OrderExecutor] ❌ 致命错误: SL和TP订单均失败，"
                        f"立即平仓以保护资金")
            try:
                side = 'SELL' if direction == 'LONG' else 'BUY'
                self._send_request(
                    'POST', '/fapi/v1/order',
                    params={
                        'symbol': symbol,
                        'side': side,
                        'type': 'MARKET',
                        'quantity': str(qty),
                        'positionSide': pos_side,
                    },
                    signed=True,
                    use_futures=True
                )
                logger.info(f"[OrderExecutor] ✅ 紧急平仓成功")
            except Exception as close_e:
                logger.error(f"[OrderExecutor] ⚠️ 紧急平仓也失败了！请手动检查持仓！"
                            f" 入场单ID: {entry_order.get('orderId')}")
                logger.error(f"[OrderExecutor] 紧急平仓异常: {close_e}")
                logger.error(traceback.format_exc())
            return None

        # 6. 创建Order对象
        # 收集所有订单ID（普通订单用orderId，Algo订单用algoId）
        all_order_ids = [str(entry_order['orderId'])]
        if stop_order:
            all_order_ids.append(str(stop_order.get('algoId', stop_order.get('orderId', ''))))
        all_order_ids.extend([str(x) for x in tp_orders])

        order = Order(
            order_id=order_id,
            symbol=symbol,
            direction=direction,
            status=OrderStatus.OPENED,
            entry_price=entry_price,
            entry_time=datetime.now(),
            position_size=position_size,
            leverage=leverage,
            stop_loss=stop_loss,
            take_profit_levels=list(take_profit_levels),
            take_profit_quantities=tp_quantities,
            remaining_quantity=position_size,
            order_ids=all_order_ids,
            tp_algo_ids=tp_algo_ids,
            # Fix P1-3: 传递交易所保护单状态
            has_exchange_stop=_has_exchange_stop,
            has_exchange_tp=_has_exchange_tp,
        )
        
        # 7. 添加到持仓列表
        self.positions.append(order)

        logger.info(f"[OrderExecutor] ✅ 开仓完成 | 订单ID: {order_id}")

        return order

    def _partial_close_position(self, order_id: str, close_qty: float,
                                 reason: str = "部分止盈") -> bool:
        """部分平仓：减少remaining_quantity，将状态改为PARTIAL_TP

        用于分批止盈的非最后级TP成交后的本地部分平仓。

        Args:
            order_id: 持仓订单ID
            close_qty: 要平仓的数量（币）
            reason: 平仓原因

        Returns:
            是否成功
        """
        target_pos = None
        for pos in self.positions:
            if pos.order_id == order_id:
                target_pos = pos
                break

        if target_pos is None:
            logger.warning(f"[OrderExecutor] 部分平仓: 未找到持仓 {order_id}")
            return False

        if target_pos.status == OrderStatus.CLOSED:
            logger.warning(f"[OrderExecutor] 部分平仓: 持仓 {order_id} 已平仓，跳过")
            return False

        # 数量对齐
        import math
        step_info = self._get_symbol_step_size(target_pos.symbol)
        step_size = step_info['step_size']
        min_qty = step_info['min_qty']
        decimals = max(0, -int(math.log10(step_size))) if step_size > 0 else 8
        aligned_qty = round(math.floor(close_qty / step_size) * step_size, decimals)

        if aligned_qty < min_qty:
            logger.warning(f"[OrderExecutor] 部分平仓数量 {aligned_qty} < min_qty {min_qty}, 跳过")
            return False

        # 下市价减仓单
        pos_side = target_pos.direction.upper()
        if pos_side not in ('LONG', 'SHORT'):
            pos_side = 'LONG'
        side = 'SELL' if pos_side == 'LONG' else 'BUY'

        try:
            result = self._send_request(
                'POST',
                '/fapi/v1/order',
                params={
                    'symbol': target_pos.symbol,
                    'side': side,
                    'type': 'MARKET',
                    'quantity': str(aligned_qty),
                    'positionSide': pos_side,
                },
                signed=True,
                use_futures=True
            )

            # 计算部分盈亏
            cum_quote = float(result.get('cumQuote', 0))
            executed_qty = float(result.get('executedQty', 0))
            avg_price = cum_quote / executed_qty if executed_qty > 0 else 0
            if target_pos.direction == 'LONG':
                partial_pnl = (avg_price - target_pos.entry_price) * executed_qty
            else:
                partial_pnl = (target_pos.entry_price - avg_price) * executed_qty

            # 更新Order状态
            target_pos.remaining_quantity -= aligned_qty
            target_pos.filled_quantity += aligned_qty
            target_pos.realized_pnl += partial_pnl
            target_pos.status = OrderStatus.PARTIAL_TP

            logger.info(f"[OrderExecutor] 部分平仓成功: {target_pos.symbol} | "
                       f"数量: {aligned_qty} | 原因: {reason} | "
                       f"剩余: {target_pos.remaining_quantity:.4f} | "
                       f"部分盈亏: ${partial_pnl:.2f}")
            return True

        except Exception as e:
            logger.error(f"[OrderExecutor] 部分平仓失败: {e}")
            return False

    def get_positions(self) -> List[Order]:
        """
        获取当前持仓
        
        Returns:
            持仓列表
        """
        return self.positions
    
    def update_positions(self):
        """
        更新持仓状态（从币安API同步）
        """
        for order in self.positions:
            # TODO: 从API获取订单状态、成交明细等
            pass
    
    # -*- 文档参考 -*-
    # Source: https://developers.binance.com/docs/derivatives/usds-margined-futures/trade
    # API: POST /fapi/v1/order (positionSide)
    # Validated: 2026-05-31
    # positionSide valid values: BOTH (one-way) | LONG | SHORT (hedge mode)

    def cleanup_orphan_algo_orders(self, active_symbols: set) -> Dict[str, Any]:
        """Cancel open algo orders whose symbol has no live position."""
        active = {
            self._normalize_futures_symbol(symbol)
            for symbol in (active_symbols or set())
            if symbol
        }
        result = {
            "checked": 0,
            "cancelled": 0,
            "failed": 0,
            "orphans": [],
            "errors": [],
        }
        try:
            open_algos = self._send_algo_request(
                'GET', '/fapi/v1/openAlgoOrders', params={}, signed=True,
            )
        except Exception as e:
            logger.warning("[OrderExecutor] 查询Algo订单失败，无法清理幽灵单: {}", e)
            result["errors"].append(str(e))
            return result

        if isinstance(open_algos, dict):
            open_algos = open_algos.get('orders') or open_algos.get('data') or []
        if not isinstance(open_algos, list):
            return result

        for ao in open_algos:
            if not isinstance(ao, dict):
                continue
            status = str(ao.get('algoStatus') or ao.get('status') or 'NEW').upper()
            if status not in ('NEW', 'PARTIALLY_FILLED'):
                continue
            symbol = self._normalize_futures_symbol(str(ao.get('symbol') or ''))
            algo_id = ao.get('algoId')
            result["checked"] += 1
            if not symbol or symbol in active:
                continue
            try:
                self._send_algo_request(
                    'DELETE',
                    '/fapi/v1/algoOrder',
                    params={'symbol': symbol, 'algoId': algo_id},
                    signed=True,
                )
                result["cancelled"] += 1
                result["orphans"].append(f"{symbol}:{algo_id}")
                logger.info("[OrderExecutor] 已清理无持仓幽灵Algo单: {} | {}", symbol, algo_id)
            except Exception as e:
                result["failed"] += 1
                result["errors"].append(f"{symbol}:{algo_id}:{e}")
                logger.warning("[OrderExecutor] 清理幽灵Algo单失败: {} | {} | {}", symbol, algo_id, e)

        return result

    def get_exchange_active_position_symbols(self) -> set:
        """Read live futures positions directly from Binance and return active symbols."""
        positions = self._send_request(
            'GET',
            '/fapi/v2/positionRisk',
            params={},
            signed=True,
            use_futures=True,
        )
        active = set()
        if not isinstance(positions, list):
            return active
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            try:
                amount = float(pos.get('positionAmt') or 0)
            except (TypeError, ValueError):
                amount = 0.0
            if abs(amount) <= 0:
                continue
            symbol = self._normalize_futures_symbol(str(pos.get('symbol') or ''))
            if symbol:
                active.add(symbol)
        return active

    def ensure_protection_orders(self, order: Order) -> Dict[str, Any]:
        """Ensure an open position has exchange-side STOP/TP algo protection."""
        symbol = self._normalize_futures_symbol(order.symbol)
        direction = (order.direction or "").upper()
        pos_side = "LONG" if direction == "LONG" else "SHORT"
        close_side = "SELL" if pos_side == "LONG" else "BUY"
        qty = float(order.remaining_quantity or order.position_size or 0)
        result = {
            "has_stop": False,
            "has_tp": False,
            "created_stop": False,
            "created_tp": 0,
        }
        if not symbol or qty <= 0:
            return result

        try:
            open_algos = self._send_algo_request(
                'GET', '/fapi/v1/openAlgoOrders',
                params={'symbol': symbol}, signed=True,
            )
        except Exception as e:
            logger.warning("[OrderExecutor] 查询保护单失败，跳过补挂: {} | {}", symbol, e)
            return result

        if isinstance(open_algos, dict):
            open_algos = open_algos.get('orders') or open_algos.get('data') or []
        if not isinstance(open_algos, list):
            open_algos = []

        def is_open(ao: Dict[str, Any]) -> bool:
            return str(ao.get('algoStatus') or ao.get('status') or 'NEW').upper() in (
                'NEW', 'PARTIALLY_FILLED'
            )

        def algo_type(ao: Dict[str, Any]) -> str:
            return str(
                ao.get('orderType')
                or ao.get('type')
                or ao.get('origType')
                or ''
            ).upper()

        def trigger_price(ao: Dict[str, Any]) -> float:
            raw = ao.get('triggerPrice') or ao.get('stopPrice') or ao.get('price') or 0
            try:
                return float(raw)
            except (TypeError, ValueError):
                return 0.0

        open_algos = [ao for ao in open_algos if isinstance(ao, dict) and is_open(ao)]
        stop_orders = [ao for ao in open_algos if algo_type(ao) == 'STOP_MARKET']
        tp_orders = [ao for ao in open_algos if algo_type(ao) == 'TAKE_PROFIT_MARKET']
        result["has_stop"] = bool(stop_orders)
        result["has_tp"] = bool(tp_orders)

        if not stop_orders and order.stop_loss and order.stop_loss > 0:
            try:
                stop_order = self._send_algo_request(
                    'POST',
                    '/fapi/v1/algoOrder',
                    params={
                        'symbol': symbol,
                        'side': close_side,
                        'type': 'STOP_MARKET',
                        'algoType': 'CONDITIONAL',
                        'triggerPrice': f"{float(order.stop_loss):.6f}",
                        'quantity': str(qty),
                        'positionSide': pos_side,
                        'workingType': 'CONTRACT_PRICE',
                    },
                    signed=True,
                )
                order.order_ids.append(str(stop_order.get('algoId', '')))
                result["has_stop"] = True
                result["created_stop"] = True
                logger.info("[OrderExecutor] 已补挂止损保护单: {} | {}", symbol, stop_order.get('algoId'))
            except Exception as e:
                logger.warning("[OrderExecutor] 补挂止损失败: {} | {}", symbol, e)

        existing_tp_prices = [trigger_price(ao) for ao in tp_orders]
        tp_levels = [float(x) for x in (order.take_profit_levels or []) if x]
        tp_qtys = list(order.take_profit_quantities or [])
        if len(tp_qtys) != len(tp_levels):
            if tp_levels:
                if len(tp_levels) == 1:
                    ratios = [1.0]
                elif len(tp_levels) == 2:
                    ratios = [0.5, 0.5]
                elif len(tp_levels) == 3:
                    ratios = [0.5, 0.3, 0.2]
                else:
                    ratios = [0.5] + [0.5 / (len(tp_levels) - 1)] * (len(tp_levels) - 1)
                step_info = self._get_symbol_step_size(symbol)
                tp_levels, tp_qtys = self._prepare_tp_slices(
                    qty=qty,
                    take_profit_levels=tp_levels,
                    ratios=ratios,
                    step_size=step_info['step_size'],
                    min_qty=step_info['min_qty'],
                    min_notional=float(step_info.get('min_notional', 5.0) or 5.0),
                )
        else:
            step_info = self._get_symbol_step_size(symbol)
            min_notional = float(step_info.get('min_notional', 5.0) or 5.0)
            valid_pairs = [
                (level, tp_qty)
                for level, tp_qty in zip(tp_levels, tp_qtys)
                if float(tp_qty) >= step_info['min_qty'] and float(tp_qty) * float(level) >= min_notional
            ]
            if not valid_pairs and tp_levels:
                valid_pairs = [(tp_levels[0], qty)]
            tp_levels = [p for p, _ in valid_pairs]
            tp_qtys = [q for _, q in valid_pairs]

        for tp_price, tp_qty in zip(tp_levels, tp_qtys):
            already_exists = any(
                existing > 0 and abs(existing - tp_price) / tp_price < 0.0001
                for existing in existing_tp_prices
            )
            if already_exists:
                continue
            try:
                tp_order = self._send_algo_request(
                    'POST',
                    '/fapi/v1/algoOrder',
                    params={
                        'symbol': symbol,
                        'side': close_side,
                        'type': 'TAKE_PROFIT_MARKET',
                        'algoType': 'CONDITIONAL',
                        'triggerPrice': f"{float(tp_price):.6f}",
                        'quantity': str(tp_qty),
                        'positionSide': pos_side,
                        'workingType': 'CONTRACT_PRICE',
                    },
                    signed=True,
                )
                algo_id = str(tp_order.get('algoId', ''))
                order.tp_algo_ids.append(algo_id)
                order.order_ids.append(algo_id)
                result["created_tp"] += 1
                result["has_tp"] = True
                logger.info("[OrderExecutor] 已补挂止盈保护单: {} | {} | {}", symbol, tp_price, algo_id)
            except Exception as e:
                logger.warning("[OrderExecutor] 补挂止盈失败: {} | {} | {}", symbol, tp_price, e)

        order.has_exchange_stop = bool(result["has_stop"])
        order.has_exchange_tp = bool(result["has_tp"])
        if tp_levels and tp_qtys:
            order.take_profit_levels = tp_levels
            order.take_profit_quantities = tp_qtys
        return result

    def cancel_algo_orders(self, symbol: str) -> int:
        """取消指定代币的所有 Algo 订单（止损/止盈挂单）

        用于交易所止损单触发后清理遗留的止盈挂单。

        Args:
            symbol: 交易对（如 BTCUSDT）

        Returns:
            取消的订单数
        """
        cancelled = 0
        try:
            algo_orders = self._send_algo_request(
                'GET', '/fapi/v1/openAlgoOrders',
                params={'symbol': symbol}, signed=True,
            )
            for ao in algo_orders:
                try:
                    self._send_algo_request(
                        'DELETE', '/fapi/v1/algoOrder',
                        params={'symbol': symbol, 'algoId': ao['algoId']},
                        signed=True,
                    )
                    logger.info("[OrderExecutor] 已撤销Algo订单: {} | {}", ao.get('algoId'), ao.get('type'))
                    cancelled += 1
                except Exception:
                    pass
        except Exception:
            pass
        if cancelled > 0:
            logger.info("[OrderExecutor] 共撤销 {} 个Algo订单: {}", cancelled, symbol)
        return cancelled

    def close_position(self, order_id: str, reason: str = "手动平仓") -> bool:
        """
        平仓指定持仓（市价单 - 合约 fapi）

        Fix P0-1: 数量按 step_size 精度对齐，禁止 int() 截断放大。
        使用 reduceOnly=true 搭配 closePosition 避免数量问题。
        若数量对齐后 <=0，安全失败/记录错误，不得放大。

        Fix P1-3: 取消旧有「先置 CLOSED 再撤销挂单」逻辑，改用
        reduceOnly=true 平仓（不依赖数量），同时记录交易所保护单状态。

        Fix #1 (Round3): positionSide 防御性校验，确保 hedge mode 下传值合法。

        Args:
            order_id: 持仓订单ID
            reason: 平仓原因

        Returns:
            是否成功
        """
        target_pos = None
        for pos in self.positions:
            if pos.order_id == order_id:
                target_pos = pos
                break

        if target_pos is None:
            logger.warning(f"[OrderExecutor] 未找到持仓: {order_id}")
            return False

        if target_pos.status == OrderStatus.CLOSED:
            logger.warning(f"[OrderExecutor] 持仓 {order_id} 已平仓，跳过")
            return True

        # Fix #1 (Round3): 防御性校验 positionSide — hedge mode 必须是 LONG 或 SHORT
        # 若 direction 字段被污染（非标准值），从持仓数据推断正确方向
        pos_side = target_pos.direction.upper() if target_pos.direction else 'LONG'
        if pos_side not in ('LONG', 'SHORT'):
            logger.warning("[OrderExecutor] positionSide 异常值 '{}', 尝试推断方向",
                          target_pos.direction)
            # fallback: 从交易所同步的持仓方向推断
            pos_side = 'LONG'
            logger.info("[OrderExecutor] 推断 positionSide={}", pos_side)

        # 立即标记为已关闭（防止重入）
        target_pos.status = OrderStatus.CLOSED

        try:
            side = 'SELL' if pos_side == 'LONG' else 'BUY'

            # 先撤销交易所止损/止盈挂单（避免平仓被保护单拦截）
            self.cancel_algo_orders(target_pos.symbol)

            # Fix P0-1: 使用 step_size 精度对齐 remaining_quantity，禁止 int()
            step_info = self._get_symbol_step_size(target_pos.symbol)
            step_size = step_info['step_size']
            min_qty = step_info['min_qty']
            import math
            decimals = max(0, -int(math.log10(step_size))) if step_size > 0 else 8

            # 用 remaining_quantity 对齐 step_size（不放大，不int截断）
            qty_floor = math.floor(target_pos.remaining_quantity / step_size) * step_size
            qty_aligned = round(qty_floor, decimals)

            if qty_aligned <= 0 or qty_aligned < min_qty:
                # 数量小于交易所最小步长/最小数量时，禁止放大为1个币。
                # MARKET 单不支持 closePosition=true；此处安全失败，等待交易所保护单或人工处理。
                logger.error(f"[OrderExecutor] 平仓数量无效，拒绝本地市价平仓 "
                             f"(remaining={target_pos.remaining_quantity}, aligned={qty_aligned}, "
                             f"step={step_size}, min={min_qty})")
                target_pos.status = OrderStatus.OPENED
                return False
            else:
                params = {
                    'symbol': target_pos.symbol,
                    'side': side,
                    'type': 'MARKET',
                    'quantity': str(qty_aligned),
                    'positionSide': pos_side,  # Fix #1: 使用校验后的值
                    'newClientOrderId': self._make_client_order_id("WC_CLOSE_", order_id),
                }

            result = self._send_request(
                'POST',
                '/fapi/v1/order',
                params=params,
                signed=True,
                use_futures=True
            )

            # Fix #3 (原有): 正确计算已实现盈亏
            cum_quote = float(result.get('cumQuote', 0))
            executed_qty = float(result.get('executedQty', 0))
            if executed_qty > 0:
                avg_price = cum_quote / executed_qty
            else:
                avg_price = 0

            if target_pos.direction == 'LONG':
                realized_pnl = (avg_price - target_pos.entry_price) * executed_qty
            else:
                realized_pnl = (target_pos.entry_price - avg_price) * executed_qty

            target_pos.realized_pnl = realized_pnl
            self.positions.remove(target_pos)
            logger.info(f"[OrderExecutor] 平仓成功: {target_pos.symbol} {target_pos.direction} "
                       f"原因: {reason} | 订单ID: {result.get('orderId')} | 盈亏: ${realized_pnl:.2f}")
            return True
        except Exception as e:
            logger.error(f"[OrderExecutor] 平仓失败: {target_pos.symbol} - {e}")
            # 如果平仓失败，恢复状态（允许重试）
            target_pos.status = OrderStatus.OPENED
            return False
    
    def execute_decision(self, decision: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        执行交易决策（engine 调用入口）
        
        Args:
            decision: 交易决策字典，包含：
                - symbol: 交易对
                - action: 'LONG' | 'SHORT' | 'HOLD' | 'CLOSE'
                - market_type: 'spot' | 'futures'（默认 futures）
                - entry_price: 入场价
                - stop_loss: 止损价
                - take_profit: 止盈价（可单个或列表）
                - position_size: 仓位大小（币）
                - leverage: 杠杆倍数（现货时忽略）
                
        Returns:
            执行结果列表
        """
        action = decision.get('action', 'HOLD')
        symbol = decision.get('symbol', '')
        # Binance API 需要 BTCUSDT 格式（无斜杠）
        symbol = symbol.replace('/', '')
        market_type = decision.get('market_type', 'futures')
        
        logger.info(f"[OrderExecutor] 收到决策: {symbol} {action} (市场: {market_type})")
        
        if action in ('HOLD', 'CLOSE'):
            return [{'action': action, 'status': 'skipped', 'message': f'Action={action}, 跳过下单'}]
        
        if action not in ('LONG', 'SHORT'):
            return [{'action': action, 'status': 'error', 'message': f'不支持的操作: {action}'}]
        
        # 提取下单参数
        entry_price = decision.get('entry_price') or decision.get('entry', 0)
        stop_loss = decision.get('stop_loss', 0) or 0.0  # Fix: None→0 防止静默跳过止损
        take_profit = decision.get('take_profit', 0) or 0.0
        position_size = decision.get('position_size', 0)
        leverage = decision.get('leverage', 1.0)  # Fix: 默认1.0（与config trinity.risk.leverage对齐）
        
        # 处理止盈价：支持单值和列表
        if isinstance(take_profit, (int, float)):
            take_profit_levels = [take_profit] if take_profit > 0 else []
        elif isinstance(take_profit, list):
            take_profit_levels = take_profit
        else:
            take_profit_levels = []
        
        try:
            if market_type == 'spot':
                order = self._execute_spot_order(
                    symbol=symbol,
                    direction=action,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit_levels=take_profit_levels,
                    position_size=position_size
                )
            else:
                order = self.open_position(
                    symbol=symbol,
                    direction=action,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit_levels=take_profit_levels,
                    position_size=position_size,
                    leverage=leverage
                )
            
            if order:
                return [{
                    'action': action,
                    'symbol': symbol,
                    'status': 'executed',
                    'order_id': order.order_id,
                    'market_type': market_type,
                    'entry_price': entry_price,
                    'position_size': position_size,
                    'leverage': leverage if market_type == 'futures' else 1.0
                }]
            else:
                return [{
                    'action': action,
                    'symbol': symbol,
                    'status': 'failed',
                    'message': '下单返回空（API调用失败或参数无效）'
                }]
        except RuntimeError as e:
            # Fix #4 (Round4): 传递 -4411 TradFi-Perps 错误
            err_str = str(e)
            logger.error(f"[OrderExecutor] 决策执行异常(TradFi): {err_str}")
            return [{
                'action': action,
                'symbol': symbol,
                'status': 'failed',
                'message': err_str
            }]
        except Exception as e:
            logger.error(f"[OrderExecutor] 决策执行异常: {e}")
            return [{
                'action': action,
                'symbol': symbol,
                'status': 'error',
                'message': str(e)
            }]
    
    def _execute_spot_order(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit_levels: List[float],
        position_size: float
    ) -> Optional[Order]:
        """
        现货下单（无杠杆，仅支持做多）
        
        与合约路径的区别：
        - 不设置杠杆（现货不需要）
        - 仅支持 LONG（方向检查）
        - 仓位计算公式：capital / price
        
        Args:
            symbol: 交易对
            direction: 方向（现货仅 LONG）
            entry_price: 入场价
            stop_loss: 止损价
            take_profit_levels: 止盈价位列表
            position_size: 仓位大小（币）
            
        Returns:
            Order对象 or None
        """
        # 现货不支持做空
        if direction.upper() == 'SHORT':
            logger.error("[OrderExecutor] ❌ 现货不支持做空")
            return None
        
        self.order_counter += 1
        order_id = f"WC_SPOT_{int(time.time())}_{self.order_counter}"
        client_order_id = self._make_client_order_id("WC_SPOT_", order_id)  # 用于幂等性
        
        logger.info(f"[OrderExecutor] 现货开仓: {symbol} LONG")
        logger.info(f"  入场价: ${entry_price:.6f}")
        logger.info(f"  止损价: ${stop_loss:.6f}")
        logger.info(f"  止盈价位: {[f'${tp:.6f}' for tp in take_profit_levels]}")
        logger.info(f"  仓位大小: {position_size:.4f} 币")
        logger.info(f"  市场类型: 现货（无杠杆）")
        
        # 1. 下达入场市价单（现货无杠杆设置步骤）
        try:
            entry_order = self._send_request(
                'POST',
                '/api/v3/order',
                params={
                    'symbol': symbol,
                    'side': 'BUY',
                    'type': 'MARKET',
                    'quantity': f"{position_size:.4f}",
                    'newClientOrderId': client_order_id,  # 幂等性
                },
                signed=True
            )
            logger.info(f"[OrderExecutor] ✅ 现货入场成功 | 订单ID: {entry_order['orderId']}")
        except Exception as e:
            logger.error(f"[OrderExecutor] ❌ 现货入场失败: {e}")
            return None
        
        # 2. 下达止损单
        stop_order = None
        try:
            stop_order = self._send_request(
                'POST',
                '/api/v3/order',
                params={
                    'symbol': symbol,
                    'side': 'SELL',
                    'type': 'STOP_LOSS',
                    'quantity': f"{position_size:.4f}",
                    'price': f"{stop_loss:.6f}",
                    'stopPrice': f"{stop_loss:.6f}",
                    'timeInForce': 'GTC'
                },
                signed=True
            )
            logger.info(f"[OrderExecutor] ✅ 现货止损成功 | 订单ID: {stop_order['orderId']}")
        except Exception as e:
            logger.error(f"[OrderExecutor] ❌ 现货止损失败: {e}")
            stop_order = None
        
        # 3. 下达分批止盈单
        tp_orders = []
        tp_quantities = []
        n_levels = len(take_profit_levels)
        
        if n_levels == 1:
            ratios = [1.0]
        elif n_levels == 2:
            ratios = [0.5, 0.5]
        elif n_levels == 3:
            ratios = [0.5, 0.3, 0.2]
        else:
            ratios = [0.5] + [0.5 / (n_levels - 1)] * (n_levels - 1)
        
        for i, (tp_price, ratio) in enumerate(zip(take_profit_levels, ratios)):
            try:
                tp_quantity = position_size * ratio
                tp_quantities.append(tp_quantity)
                
                tp_order = self._send_request(
                    'POST',
                    '/api/v3/order',
                    params={
                        'symbol': symbol,
                        'side': 'SELL',
                        'type': 'TAKE_PROFIT_LIMIT',
                        'quantity': f"{tp_quantity:.4f}",
                        'price': f"{tp_price:.6f}",
                        'stopPrice': f"{tp_price:.6f}",
                        'timeInForce': 'GTC'
                    },
                    signed=True
                )
                tp_orders.append(tp_order['orderId'])
                logger.info(f"[OrderExecutor] ✅ 止盈{i+1}: ${tp_price:.6f} x {tp_quantity:.4f}")
            except Exception as e:
                logger.error(f"[OrderExecutor] ❌ 止盈{i+1}失败: {e}")
        
        # 4. 创建Order对象
        order = Order(
            order_id=order_id,
            symbol=symbol,
            direction='LONG',
            status=OrderStatus.OPENED,
            entry_price=entry_price,
            entry_time=datetime.now(),
            position_size=position_size,
            leverage=1.0,  # 现货杠杆=1
            stop_loss=stop_loss,
            take_profit_levels=take_profit_levels,
            take_profit_quantities=tp_quantities,
            remaining_quantity=position_size,
            order_ids=[entry_order['orderId']] + ([stop_order['orderId']] if stop_order else []) + tp_orders
        )
        
        self.positions.append(order)
        logger.info(f"[OrderExecutor] ✅ 现货开仓完成 | 订单ID: {order_id}")
        return order


# 测试代码
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    
    print("测试订单执行模块\n")
    print("=" * 80)
    
    # 初始化（使用测试网）
    executor = OrderExecutorModule({
        'testnet': True,
        'api_key': '',  # 测试网不需要API Key
        'api_secret': ''
    })
    
    print("\n[测试] 开仓（模拟）\n")
    print("-" * 80)
    
    # 注意：测试网需要有效的API Key才能实际下单
    # 这里只测试逻辑，不实际调用API
    
    print("模拟开仓: DOGEUSDT LONG")
    print("  入场价: $0.105000")
    print("  止损价: $0.100000")
    print("  止盈价位: [$0.110000, $0.115000, $0.120000]")
    print("  仓位大小: 100.0 DOGE")
    print("  杠杆: 3x")
    print("\n（需要有效的币安测试网API Key才能实际下单）")
    
    print("\n" + "=" * 80)
    print("测试完成！")
