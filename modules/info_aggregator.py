"""
模块 2: 信息聚合 (Info Aggregator Module)
功能: 聚合新闻、链上数据、社交媒体情绪等多维度信息
可调教项: 信息源权重、关键词过滤、情绪分析模型
"""

import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import aiohttp
from loguru import logger
import json


class InfoSource(Enum):
    """信息来源枚举"""
    NEWS = "news"           # 新闻
    ONCHAIN = "onchain"     # 链上数据
    SOCIAL = "social"       # 社交媒体
    WHALE = "whale"         # 巨鲸监控
    FUNDING = "funding"     # 资金费率


@dataclass
class InfoItem:
    """信息条目"""
    source: InfoSource
    title: str
    content: str
    url: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    sentiment: float = 0.0  # -1 到 1，负面情绪到正面情绪
    relevance: float = 1.0   # 0 到 1，相关度
    tags: List[str] = field(default_factory=list)
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketInfo:
    """市场信息聚合结果"""
    timestamp: datetime
    overall_sentiment: float = 0.0
    fear_greed_index: Optional[int] = None  # 0-100
    funding_rate: Optional[float] = None
    items: List[InfoItem] = field(default_factory=list)
    
    def to_text_summary(self, max_items: int = 10) -> str:
        """转换为文本摘要，供AI决策使用"""
        lines = [
            f"=== 市场信息摘要 [{self.timestamp.strftime('%Y-%m-%d %H:%M')}] ===",
            f"整体情绪: {'看多' if self.overall_sentiment > 0.2 else '看空' if self.overall_sentiment < -0.2 else '中性'} ({self.overall_sentiment:.2f})",
        ]
        if self.fear_greed_index:
            lines.append(f"恐惧贪婪指数: {self.fear_greed_index}")
        if self.funding_rate:
            lines.append(f"资金费率: {self.funding_rate:.4%}")
        
        lines.append("\n--- 关键信息 ---")
        for i, item in enumerate(self.items[:max_items]):
            sentiment_str = "🟢" if item.sentiment > 0.2 else "🔴" if item.sentiment < -0.2 else "⚪"
            lines.append(f"{sentiment_str} [{item.source.value}] {item.title}")
            if item.content:
                lines.append(f"   {item.content[:100]}...")
        
        return "\n".join(lines)


class InfoAggregatorModule:
    """
    信息聚合模块 - 可单独调教
    
    配置项：
    - sources: 启用的信息源列表
    - keywords: 监控关键词
    - sentiment_threshold: 情绪过滤阈值
    - max_items_per_source: 每个来源最大条目数
    """
    
    # 预定义的信息源API（免费/低成本）
    SOURCES = {
        'fear_greed': 'https://api.alternative.me/fng/?limit=1',
        'funding_rate': None,  # 通过CCXT获取
        'news': None,  # 可通过NewsAPI等
    }
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.enabled_sources = config.get('sources', [
            'fear_greed', 'funding', 'onchain', 'social'
        ])
        self.keywords = config.get('keywords', [
            'Bitcoin', 'BTC', 'Ethereum', 'ETH', 'crypto', 'federal reserve', 'ETF'
        ])
        self.sentiment_threshold = config.get('sentiment_threshold', 0.1)
        self.max_items = config.get('max_items_per_source', 5)
        self.request_timeout = min(float(config.get('request_timeout', 8)), 10.0)
        self.session: Optional[aiohttp.ClientSession] = None
        
        logger.info("[InfoAggregator] 信息聚合模块初始化 | 来源: {}", 
                    self.enabled_sources)
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建HTTP会话"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.request_timeout)
            )
        return self.session
    
    async def fetch_fear_greed(self) -> Optional[Dict]:
        """获取恐惧贪婪指数"""
        try:
            session = await self._get_session()
            async with session.get(self.SOURCES['fear_greed']) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('data'):
                        item = data['data'][0]
                        return {
                            'value': int(item['value']),
                            'classification': item['value_classification'],
                            'timestamp': datetime.fromtimestamp(int(item['timestamp']))
                        }
        except Exception as e:
            logger.warning("[InfoAggregator] 恐惧贪婪指数获取失败: {}", e)
        return None
    
    async def fetch_funding_rates(self, exchanges: Dict[str, Any]) -> List[InfoItem]:
        """获取资金费率信息"""
        items = []
        try:
            for ex_name, exchange in exchanges.items():
                try:
                    # 获取永续合约资金费率
                    markets = exchange.load_markets()
                    perp_symbols = [s for s in markets if ':USDT' in s or '-SWAP' in s]
                    
                    for symbol in perp_symbols[:5]:  # 限制数量
                        try:
                            funding = exchange.fetch_funding_rate(symbol)
                            rate = funding.get('fundingRate', 0)
                            items.append(InfoItem(
                                source=InfoSource.FUNDING,
                                title=f"{symbol} 资金费率",
                                content=f"当前费率: {rate:.4%}",
                                sentiment=1.0 if rate > 0.01 else -1.0 if rate < -0.01 else 0,
                                relevance=0.8,
                                tags=['funding', symbol.split('/')[0]],
                                raw_data=funding
                            ))
                        except:
                            continue
                except Exception as e:
                    logger.warning("[InfoAggregator] {} 资金费率获取失败: {}", ex_name, e)
        except Exception as e:
            logger.error("[InfoAggregator] 资金费率获取失败: {}", e)
        
        return items[:self.max_items]
    
    async def fetch_onchain_data(self) -> List[InfoItem]:
        """
        获取链上数据（简化版）
        实际可接入：
        - Glassnode API
        - CryptoQuant
        - Dune Analytics
        """
        items = []
        # 这里预留链上数据接口
        # 可通过第三方API获取交易所流入流出、巨鲸转账等
        logger.debug("[InfoAggregator] 链上数据获取（预留接口）")
        return items
    
    async def fetch_social_sentiment(self) -> List[InfoItem]:
        """
        获取社交媒体情绪
        实际可接入：
        - LunarCrush
        - Santiment
        - Twitter/X API
        """
        items = []
        logger.debug("[InfoAggregator] 社交情绪获取（预留接口）")
        return items
    
    async def aggregate(self, exchanges: Optional[Dict] = None) -> MarketInfo:
        """
        聚合所有信息源
        
        Returns:
            MarketInfo: 聚合后的市场信息
        """
        info = MarketInfo(timestamp=datetime.now())
        all_items = []
        
        # 1. 恐惧贪婪指数
        if 'fear_greed' in self.enabled_sources:
            fg = await self.fetch_fear_greed()
            if fg:
                info.fear_greed_index = fg['value']
                # 恐惧贪婪指数映射到情绪 (-1 到 1)
                info.overall_sentiment = (fg['value'] - 50) / 50
        
        # 2. 资金费率
        if 'funding' in self.enabled_sources and exchanges:
            funding_items = await self.fetch_funding_rates(exchanges)
            all_items.extend(funding_items)
            # 计算平均资金费率情绪
            if funding_items:
                avg_sentiment = sum(i.sentiment for i in funding_items) / len(funding_items)
                info.overall_sentiment = (info.overall_sentiment + avg_sentiment) / 2
        
        # 3. 链上数据
        if 'onchain' in self.enabled_sources:
            onchain_items = await self.fetch_onchain_data()
            all_items.extend(onchain_items)
        
        # 4. 社交情绪
        if 'social' in self.enabled_sources:
            social_items = await self.fetch_social_sentiment()
            all_items.extend(social_items)
        
        # 过滤低相关度信息
        info.items = [
            item for item in all_items 
            if item.relevance >= self.sentiment_threshold
        ]
        
        # 按相关度和时间排序
        info.items.sort(key=lambda x: (x.relevance, x.timestamp), reverse=True)
        
        logger.info("[InfoAggregator] 信息聚合完成 | 条目: {} | 情绪: {:.2f}",
                    len(info.items), info.overall_sentiment)
        
        return info
    
    async def close(self):
        """关闭会话"""
        if self.session and not self.session.closed:
            await self.session.close()
