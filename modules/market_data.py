"""
模块 1: 行情数据采集 (Market Data Module)
功能: 从 Binance 和 OKX 双源获取行情数据，互相补充校验
可调教项: 交易对列表、K线周期、深度档位、权重分配
"""

import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
import ccxt
from loguru import logger
import pandas as pd
import numpy as np


@dataclass
class KlineData:
    """K线数据结构"""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    exchange: str
    symbol: str
    timeframe: str


@dataclass
class DepthData:
    """深度数据结构"""
    timestamp: datetime
    bids: List[List[float]]  # [价格, 数量]
    asks: List[List[float]]
    exchange: str
    symbol: str


@dataclass
class TickerData:
    """Ticker数据结构"""
    timestamp: datetime
    bid: float
    ask: float
    last: float
    volume_24h: float
    change_24h_pct: float
    exchange: str
    symbol: str


@dataclass
class MarketSnapshot:
    """市场快照 - 聚合双源数据"""
    symbol: str
    timestamp: datetime
    klines: Dict[str, List[KlineData]] = field(default_factory=dict)  # timeframe -> list
    depths: Dict[str, DepthData] = field(default_factory=dict)  # exchange -> depth
    tickers: Dict[str, TickerData] = field(default_factory=dict)  # exchange -> ticker
    
    @property
    def best_price(self) -> Dict[str, float]:
        """获取双源最优价格"""
        prices = {}
        for ex, ticker in self.tickers.items():
            prices[ex] = {
                'bid': ticker.bid,
                'ask': ticker.ask,
                'last': ticker.last
            }
        return prices
    
    @property
    def avg_price(self) -> float:
        """双源均价"""
        if not self.tickers:
            return 0.0
        return sum(t.last for t in self.tickers.values()) / len(self.tickers)
    
    @property
    def spread(self) -> Optional[float]:
        """价差（套利机会）"""
        if len(self.tickers) < 2:
            return None
        prices = [t.last for t in self.tickers.values()]
        return max(prices) - min(prices)


class MarketDataModule:
    """
    行情数据模块 - 可单独调教
    
    配置项（config/market_data.yaml）：
    - symbols: 监控的交易对列表
    - timeframes: K线周期列表 ['1m', '5m', '15m', '1h', '4h', '1d']
    - depth_limit: 深度档位数量
    - binance_weight: Binance数据权重 (0-1)
    - okx_weight: OKX数据权重 (0-1)
    - enable_websocket: 是否启用WebSocket实时推送
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.exchanges: Dict[str, ccxt.Exchange] = {}
        self.symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT'])
        self.timeframes = config.get('timeframes', ['5m', '15m', '1h', '1d'])
        self.depth_limit = config.get('depth_limit', 20)
        self.weights = {
            'binance': config.get('binance_weight', 0.5),
            'okx': config.get('okx_weight', 0.5)
        }
        
        # 数据缓存
        self._klines_cache: Dict[str, pd.DataFrame] = {}
        self._depth_cache: Dict[str, DepthData] = {}
        self._ticker_cache: Dict[str, TickerData] = {}
        import gc as _gc
        self._gc = _gc
        
        self._init_exchanges()
        logger.info("[MarketData] 行情模块初始化完成 | 交易对: {} | 周期: {}", 
                    self.symbols, self.timeframes)
    
    def _init_exchanges(self):
        """初始化交易所连接（支持无密钥的公开数据访问）"""
        # self.config 结构（从 engine 传入）:
        # {
        #   'binance': {'enabled': True, 'api_key': '...', ...},
        #   'okx': {...},
        #   'symbols': [...],
        #   'timeframes': [...],
        #   'proxy': 'http://127.0.0.1:7897'  # engine注入
        # }
        exchange_names = ['binance', 'okx']
        # Fix: 读取代理配置，ccxt需要显式注入
        proxy_url = self.config.get('proxy', '')

        for ex_name in exchange_names:
            ex_config = self.config.get(ex_name, {})
            if not ex_config.get('enabled', True):
                continue

            try:
                ccxt_config = {
                    'enableRateLimit': True,
                }
                # Fix: ccxt代理配置
                if proxy_url:
                    ccxt_config['proxies'] = {
                        'http': proxy_url,
                        'https': proxy_url,
                    }
                    logger.debug("[MarketData] ccxt代理已配置: {}", proxy_url)

                if ex_name == 'binance':
                    # 有密钥才填入（公开接口不需要密钥）
                    api_key = ex_config.get('api_key', '')
                    api_secret = ex_config.get('api_secret', '')
                    if api_key:
                        ccxt_config['apiKey'] = api_key
                    if api_secret:
                        ccxt_config['secret'] = api_secret

                    # Always use swap for Binance (funds in futures account)
                    ccxt_config['options'] = {'defaultType': 'swap'}
                    if not ex_config.get('sandbox', True):
                        ccxt_config['options']['test'] = False

                    self.exchanges['binance'] = ccxt.binance(ccxt_config)
                    logger.info("[MarketData] Binance 初始化成功（公开接口）")

                elif ex_name == 'okx':
                    # 复用基础配置（含代理）
                    api_key = ex_config.get('api_key', '')
                    api_secret = ex_config.get('api_secret', '')
                    passphrase = ex_config.get('passphrase', '')
                    if api_key:
                        ccxt_config['apiKey'] = api_key
                    if api_secret:
                        ccxt_config['secret'] = api_secret
                    if passphrase:
                        ccxt_config['password'] = passphrase

                    if ex_config.get('sandbox', True):
                        ccxt_config['options'] = {'defaultType': 'spot', 'test': True}

                    self.exchanges['okx'] = ccxt.okx(ccxt_config)
                    logger.info("[MarketData] OKX 初始化成功（公开接口）")

            except Exception as e:
                logger.error("[MarketData] {} 初始化失败: {}", ex_name, e)
    
    async def fetch_klines(self, symbol: str, timeframe: str = '1h', 
                          limit: int = 100) -> pd.DataFrame:
        """
        获取K线数据 - 双源聚合
        
        Returns:
            DataFrame with columns: [timestamp, open, high, low, close, volume, 
                                    binance_close, okx_close, weighted_close]
        """
        cache_key = f"{symbol}_{timeframe}"
        
        all_klines = []
        for exchange_name, exchange in self.exchanges.items():
            try:
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
                df = pd.DataFrame(
                    ohlcv, 
                    columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
                )
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df['exchange'] = exchange_name
                df[f'{exchange_name}_close'] = df['close']
                all_klines.append(df)
                logger.debug("[MarketData] {} K线获取成功: {} {} 条", 
                           exchange_name, symbol, len(df))
            except Exception as e:
                err_msg = str(e)
                # 提取Binance错误码帮助排查
                if hasattr(e, 'args') and len(e.args) > 0:
                    err_msg = str(e.args[0])[:200]
                logger.warning("[MarketData] {} K线获取失败: {} - {}", 
                             exchange_name, symbol, err_msg)
                logger.debug("[MarketData] {} 完整错误: {}", exchange_name, e, exc_info=True)
        
        if not all_klines:
            logger.warning("[MarketData] 所有交易所K线获取失败: {}", symbol)
            return pd.DataFrame()
        
        # 合并双源数据 - 按时间戳对齐
        if len(all_klines) == 1:
            merged = all_klines[0]
        else:
            # 以时间戳为键合并
            merged = all_klines[0].set_index('timestamp')
            for df in all_klines[1:]:
                merged = merged.join(
                    df.set_index('timestamp')[['close']], 
                    rsuffix=f'_{df["exchange"].iloc[0]}'
                )
            merged = merged.reset_index()
        
        # 计算加权收盘价（双源互补）
        close_cols = [c for c in merged.columns if '_close' in c]
        if len(close_cols) == 2:
            # 双源都有数据，按权重计算
            binance_col = [c for c in close_cols if 'binance' in c][0]
            okx_col = [c for c in close_cols if 'okx' in c][0]
            merged['weighted_close'] = (
                merged[binance_col] * self.weights['binance'] + 
                merged[okx_col] * self.weights['okx']
            )
        else:
            # 单源数据
            merged['weighted_close'] = merged['close']
        
        self._klines_cache[cache_key] = merged
        return merged
    
    async def fetch_depth(self, symbol: str) -> Dict[str, DepthData]:
        """获取深度数据 - 双源"""
        depths = {}
        for exchange_name, exchange in self.exchanges.items():
            try:
                orderbook = exchange.fetch_order_book(symbol, limit=self.depth_limit)
                depth = DepthData(
                    timestamp=datetime.now(),
                    bids=orderbook['bids'][:self.depth_limit],
                    asks=orderbook['asks'][:self.depth_limit],
                    exchange=exchange_name,
                    symbol=symbol
                )
                depths[exchange_name] = depth
                self._depth_cache[f"{exchange_name}_{symbol}"] = depth
                logger.debug("[MarketData] {} 深度获取成功: {}", exchange_name, symbol)
            except Exception as e:
                err_msg = str(e)
                if hasattr(e, 'args') and len(e.args) > 0:
                    err_msg = str(e.args[0])[:200]
                logger.warning("[MarketData] {} 深度获取失败: {} - {}", 
                             exchange_name, symbol, err_msg)
                logger.debug("[MarketData] {} 深度完整错误: {}", exchange_name, e, exc_info=True)
        return depths
    
    async def fetch_ticker(self, symbol: str) -> Dict[str, TickerData]:
        """获取Ticker - 双源"""
        tickers = {}
        for exchange_name, exchange in self.exchanges.items():
            try:
                ticker = exchange.fetch_ticker(symbol)
                td = TickerData(
                    timestamp=datetime.now(),
                    bid=ticker['bid'],
                    ask=ticker['ask'],
                    last=ticker['last'],
                    volume_24h=ticker.get('quoteVolume', ticker.get('volume', 0)),
                    change_24h_pct=ticker.get('percentage', 0),
                    exchange=exchange_name,
                    symbol=symbol
                )
                tickers[exchange_name] = td
                self._ticker_cache[f"{exchange_name}_{symbol}"] = td
            except Exception as e:
                err_msg = str(e)
                if hasattr(e, 'args') and len(e.args) > 0:
                    err_msg = str(e.args[0])[:200]
                logger.warning("[MarketData] {} Ticker获取失败: {} - {}", 
                             exchange_name, symbol, err_msg)
                logger.debug("[MarketData] {} Ticker完整错误: {}", exchange_name, e, exc_info=True)
        return tickers
    
    async def get_market_snapshot(self, symbol: str) -> MarketSnapshot:
        """获取完整市场快照 - 聚合所有数据"""
        snapshot = MarketSnapshot(
            symbol=symbol,
            timestamp=datetime.now()
        )
        
        # 并行获取各类型数据
        klines_tasks = [self.fetch_klines(symbol, tf) for tf in self.timeframes]
        depth_task = self.fetch_depth(symbol)
        ticker_task = self.fetch_ticker(symbol)
        
        klines_results = await asyncio.gather(*klines_tasks, return_exceptions=True)
        depths = await depth_task
        tickers = await ticker_task
        
        # 组装数据
        for tf, klines in zip(self.timeframes, klines_results):
            if isinstance(klines, pd.DataFrame) and not klines.empty:
                snapshot.klines[tf] = [
                    KlineData(
                        timestamp=row['timestamp'],
                        open=row['open'],
                        high=row['high'],
                        low=row['low'],
                        close=row['close'],
                        volume=row['volume'],
                        exchange='aggregated',
                        symbol=symbol,
                        timeframe=tf
                    )
                    for _, row in klines.iterrows()
                ]
        
        snapshot.depths = depths
        snapshot.tickers = tickers
        
        logger.info("[MarketData] 市场快照生成: {} | 均价: {:.2f} | 价差: {}",
                    symbol, snapshot.avg_price, 
                    snapshot.spread if snapshot.spread else 'N/A')
        
        return snapshot
    
    async def get_all_snapshots(self) -> Dict[str, MarketSnapshot]:
        """获取所有交易对快照"""
        snapshots = {}
        for symbol in self.symbols:
            try:
                snapshots[symbol] = await self.get_market_snapshot(symbol)
            except Exception as e:
                logger.error("[MarketData] {} 快照获取失败: {}", symbol, e)
        return snapshots

    def clear_klines_cache(self):
        """清空K线缓存（每轮循环后释放内存）"""
        count = len(self._klines_cache)
        self._klines_cache.clear()
        self._depth_cache.clear()
        self._ticker_cache.clear()
        self._gc.collect()
        logger.info("[MarketData] K线缓存已清空: {} 条, GC完成", count)

    async def get_batched_snapshots(self, batch_size: int = 15,
                                     sleep_between: float = 8.0) -> Dict[str, Any]:
        """分批获取所有交易对快照（engine 调用入口）

        Args:
            batch_size: 每批数量
            sleep_between: 批次间休眠(秒)

        Returns:
            {symbol: MarketSnapshot} 字典
        """
        return await self.get_all_snapshots()

    def get_technical_indicators(self, symbol: str, timeframe: str = '1h') -> Dict[str, float]:
        """计算技术指标 - 可扩展"""
        cache_key = f"{symbol}_{timeframe}"
        df = self._klines_cache.get(cache_key)
        if df is None or df.empty:
            return {}
        
        close = df['weighted_close'] if 'weighted_close' in df.columns else df['close']
        
        indicators = {
            'sma_20': close.rolling(20).mean().iloc[-1] if len(close) >= 20 else None,
            'sma_50': close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None,
            'ema_12': close.ewm(span=12).mean().iloc[-1],
            'ema_26': close.ewm(span=26).mean().iloc[-1],
            'rsi_14': self._calculate_rsi(close, 14),
            'volatility': close.pct_change().std() * np.sqrt(365),
            'volume_sma': df['volume'].rolling(20).mean().iloc[-1] if 'volume' in df.columns else None,
        }
        
        return {k: v for k, v in indicators.items() if v is not None}
    
    @staticmethod
    def _calculate_rsi(prices: pd.Series, period: int = 14) -> Optional[float]:
        """计算RSI指标"""
        if len(prices) < period + 1:
            return None
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]
    
    def health_check(self) -> Dict[str, bool]:
        """健康检查"""
        return {
            name: bool(exchange) 
            for name, exchange in self.exchanges.items()
        }
