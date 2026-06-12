"""
模块 3: 账户管理 (Account Manager Module)
功能: 监控双交易所账户的持仓、盈亏、净值等
可调教项: 同步频率、资产统计方式、风险敞口计算
"""

import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from loguru import logger
import ccxt


@dataclass
class Position:
    """持仓信息"""
    symbol: str
    side: str  # long / short
    amount: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    realized_pnl: float
    leverage: float = 1.0
    exchange: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def pnl_pct(self) -> float:
        """收益率"""
        if self.entry_price == 0:
            return 0.0
        return (self.mark_price - self.entry_price) / self.entry_price * (1 if self.side == 'long' else -1)
    
    @property
    def position_value(self) -> float:
        """持仓市值"""
        return abs(self.amount) * self.mark_price


@dataclass
class Balance:
    """余额信息"""
    asset: str
    free: float
    used: float
    total: float
    usd_value: float = 0.0
    exchange: str = ""
    
    @property
    def available_pct(self) -> float:
        """可用比例"""
        if self.total == 0:
            return 0.0
        return self.free / self.total


@dataclass
class AccountSummary:
    """账户汇总"""
    timestamp: datetime
    exchange: str
    total_equity_usdt: float = 0.0
    available_usdt: float = 0.0
    positions: List[Position] = field(default_factory=list)
    balances: List[Balance] = field(default_factory=list)
    daily_pnl: float = 0.0
    total_pnl: float = 0.0
    
    @property
    def margin_ratio(self) -> float:
        """保证金使用率"""
        if self.total_equity_usdt == 0:
            return 0.0
        return (self.total_equity_usdt - self.available_usdt) / self.total_equity_usdt
    
    @property
    def position_count(self) -> int:
        """持仓数量"""
        return len([p for p in self.positions if abs(p.amount) > 0])


@dataclass
class PortfolioSnapshot:
    """组合快照 - 双交易所聚合"""
    timestamp: datetime
    accounts: Dict[str, AccountSummary] = field(default_factory=dict)
    sync_errors: Dict[str, str] = field(default_factory=dict)
    stale: bool = False
    
    @property
    def total_equity(self) -> float:
        """总权益"""
        return sum(a.total_equity_usdt for a in self.accounts.values())
    
    @property
    def total_available(self) -> float:
        """总可用"""
        return sum(a.available_usdt for a in self.accounts.values())
    
    @property
    def total_positions(self) -> int:
        """总持仓数"""
        return sum(a.position_count for a in self.accounts.values())
    
    @property
    def total_unrealized_pnl(self) -> float:
        """总未实现盈亏"""
        return sum(
            sum(p.unrealized_pnl for p in a.positions)
            for a in self.accounts.values()
        )
    
    @property
    def total_daily_pnl(self) -> float:
        """当日总盈亏"""
        return sum(a.daily_pnl for a in self.accounts.values())
    
    def get_position_by_symbol(self, symbol: str) -> List[Position]:
        """按交易对获取持仓"""
        positions = []
        for account in self.accounts.values():
            for pos in account.positions:
                if pos.symbol == symbol:
                    positions.append(pos)
        return positions
    
    def to_text_summary(self) -> str:
        """文本摘要"""
        lines = [
            f"=== 账户概览 [{self.timestamp.strftime('%Y-%m-%d %H:%M')}] ===",
            f"总权益: ${self.total_equity:,.2f}",
            f"总可用: ${self.total_available:,.2f}",
            f"总持仓: {self.total_positions} 个",
            f"未实现盈亏: ${self.total_unrealized_pnl:,.2f}",
            f"当日盈亏: ${self.total_daily_pnl:,.2f}",
            "\n--- 各交易所 ---"
        ]
        for ex, account in self.accounts.items():
            lines.append(
                f"[{ex}] 权益: ${account.total_equity_usdt:,.2f} | "
                f"可用: ${account.available_usdt:,.2f} | "
                f"持仓: {account.position_count}"
            )
        
        if self.total_positions > 0:
            lines.append("\n--- 持仓明细 ---")
            for ex, account in self.accounts.items():
                for pos in account.positions:
                    if abs(pos.amount) > 0:
                        emoji = "🟢" if pos.unrealized_pnl > 0 else "🔴"
                        lines.append(
                            f"{emoji} [{ex}] {pos.symbol} {pos.side.upper()} | "
                            f"数量: {abs(pos.amount):.4f} | "
                            f"盈亏: ${pos.unrealized_pnl:,.2f} ({pos.pnl_pct:+.2%})"
                        )
        
        return "\n".join(lines)


class AccountManagerModule:
    """
    账户管理模块 - 可单独调教
    
    配置项：
    - sync_interval: 同步间隔（秒）
    - min_balance_value: 最小计入余额的美元价值
    - include_zero_balances: 是否包含零余额
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.exchanges: Dict[str, ccxt.Exchange] = {}
        self.sync_interval = config.get('sync_interval', 30)
        self.min_balance_value = config.get('min_balance_value', 1.0)
        self.include_zero = config.get('include_zero_balances', False)
        
        # 历史数据缓存
        self._history: List[PortfolioSnapshot] = []
        self._last_snapshot: Optional[PortfolioSnapshot] = None
        
        logger.info("[AccountManager] 账户管理模块初始化")
    
    def register_exchange(self, name: str, exchange: ccxt.Exchange):
        """注册交易所实例"""
        self.exchanges[name] = exchange
        logger.info("[AccountManager] 注册交易所: {}", name)
    
    async def sync_account(self, exchange_name: str, exchange: ccxt.Exchange) -> AccountSummary:
        """同步单个交易所账户"""
        summary = AccountSummary(
            timestamp=datetime.now(),
            exchange=exchange_name
        )
        
        try:
            # 1. 获取余额
            balance = exchange.fetch_balance()
            total_usdt = 0.0
            available_usdt = 0.0
            
            for asset, data in balance.get('total', {}).items():
                if data is None:
                    continue
                    
                free = balance.get('free', {}).get(asset, 0) or 0
                used = balance.get('used', {}).get(asset, 0) or 0
                total = data or 0
                
                # 获取USDT估值
                usd_value = 0.0
                if asset == 'USDT':
                    usd_value = total
                elif total > 0:
                    # 尝试获取价格
                    try:
                        ticker = exchange.fetch_ticker(f"{asset}/USDT")
                        usd_value = total * ticker['last']
                    except:
                        pass
                
                if usd_value >= self.min_balance_value or self.include_zero:
                    summary.balances.append(Balance(
                        asset=asset,
                        free=free,
                        used=used,
                        total=total,
                        usd_value=usd_value,
                        exchange=exchange_name
                    ))
                
                total_usdt += usd_value
                if asset == 'USDT':
                    available_usdt = free
            
            summary.total_equity_usdt = total_usdt
            summary.available_usdt = available_usdt
            
            # 2. 获取持仓（合约/杠杆）
            try:
                positions = exchange.fetch_positions()
                for pos in positions:
                    raw_amount = pos.get('contracts', 0) or pos.get('amount', 0)
                    info = pos.get('info') or {}
                    if info.get('positionAmt') not in (None, ''):
                        raw_amount = info.get('positionAmt')
                    contracts = float(raw_amount or 0)
                    if abs(contracts) > 0:
                        side = str(pos.get('side') or '').lower()
                        if side not in ('long', 'short'):
                            side = 'long' if contracts > 0 else 'short'
                        summary.positions.append(Position(
                            symbol=pos.get('symbol', ''),
                            side=side,
                            amount=abs(contracts),
                            entry_price=pos.get('entryPrice', 0) or pos.get('average', 0),
                            mark_price=pos.get('markPrice', 0) or pos.get('lastPrice', 0),
                            unrealized_pnl=pos.get('unrealizedPnl', 0) or pos.get('unrealisedPnl', 0),
                            realized_pnl=pos.get('realizedPnl', 0) or 0,
                            leverage=pos.get('leverage', 1),
                            exchange=exchange_name
                        ))
            except Exception as e:
                logger.debug("[AccountManager] {} 持仓获取失败（可能不支持）: {}", 
                           exchange_name, e)
            
            logger.info("[AccountManager] {} 账户同步完成 | 权益: ${:.2f} | 持仓: {}",
                       exchange_name, summary.total_equity_usdt, summary.position_count)
            
        except Exception as e:
            logger.error("[AccountManager] {} 账户同步失败: {}", exchange_name, e)
            raise
        
        return summary
    
    async def sync_all(self) -> PortfolioSnapshot:
        """同步所有交易所账户"""
        snapshot = PortfolioSnapshot(timestamp=datetime.now())
        
        tasks = []
        for name, exchange in self.exchanges.items():
            tasks.append(self.sync_account(name, exchange))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for name, result in zip(self.exchanges.keys(), results):
            if isinstance(result, AccountSummary):
                snapshot.accounts[name] = result
            elif result is not None:
                logger.error("[AccountManager] {} 同步异常: {}", name, result)
                snapshot.sync_errors[name] = str(result)
            else:
                logger.error("[AccountManager] {} 同步异常: {}", name, result)
        
        if not snapshot.accounts and self._last_snapshot is not None:
            self._last_snapshot.stale = True
            self._last_snapshot.sync_errors = snapshot.sync_errors
            logger.warning(
                "[AccountManager] 所有账户同步失败，保留上次有效快照 | 权益: ${:.2f} | 持仓: {}",
                self._last_snapshot.total_equity,
                self._last_snapshot.total_positions,
            )
            return self._last_snapshot

        self._last_snapshot = snapshot
        self._history.append(snapshot)
        
        # 保留最近100条历史
        if len(self._history) > 100:
            self._history = self._history[-100:]
        
        logger.info("[AccountManager] 全账户同步完成 | 总权益: ${:.2f} | 总持仓: {}",
                   snapshot.total_equity, snapshot.total_positions)
        
        return snapshot
    
    def get_snapshot(self) -> Optional[PortfolioSnapshot]:
        """获取最新快照"""
        return self._last_snapshot
    
    def get_daily_pnl(self) -> float:
        """获取当日盈亏"""
        if not self._history:
            return 0.0
        
        today = datetime.now().date()
        today_snapshots = [
            s for s in self._history 
            if s.timestamp.date() == today
        ]
        
        if len(today_snapshots) < 2:
            return self._history[-1].total_daily_pnl if self._history else 0.0
        
        first = today_snapshots[0]
        last = today_snapshots[-1]
        return last.total_equity - first.total_equity
    
    def check_risk_exposure(self) -> Dict[str, Any]:
        """检查风险敞口"""
        snapshot = self._last_snapshot
        if not snapshot:
            return {'error': 'No snapshot available'}
        
        return {
            'total_equity': snapshot.total_equity,
            'available_ratio': snapshot.total_available / snapshot.total_equity if snapshot.total_equity > 0 else 0,
            'position_count': snapshot.total_positions,
            'unrealized_pnl': snapshot.total_unrealized_pnl,
            'daily_pnl': snapshot.total_daily_pnl,
            'margin_usage': 1 - (snapshot.total_available / snapshot.total_equity) if snapshot.total_equity > 0 else 0,
            'is_healthy': snapshot.total_equity > 0
        }
