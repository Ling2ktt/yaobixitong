"""
模块 4: AI 综合决策 (AI Decision Module)
功能: 调用大模型API，综合分析行情、信息、持仓，输出交易决策
可调教项: 提示词模板、模型选择、温度参数、策略风格
"""

import json
import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from loguru import logger


class ActionType(Enum):
    """交易动作类型"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    CLOSE = "CLOSE"
    REDUCE = "REDUCE"


@dataclass
class TradeDecision:
    """交易决策"""
    action: ActionType
    symbol: str
    amount: float
    price: Optional[float] = None
    reason: str = ""
    confidence: float = 0.5
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    leverage: float = 1.0  # Fix: 添加leverage字段，默认1.0（与trinity.risk.leverage对齐）
    timeframe: str = "1h"
    strategy: str = "default"
    timestamp: datetime = field(default_factory=datetime.now)
    raw_response: str = ""

    @property
    def is_valid(self) -> bool:
        """决策是否有效"""
        return (
            self.confidence >= 0.3 and
            self.action in [ActionType.BUY, ActionType.SELL,
                          ActionType.HOLD, ActionType.CLOSE, ActionType.REDUCE]
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            'action': self.action.value,
            'symbol': self.symbol,
            'amount': self.amount,
            'price': self.price,
            'reason': self.reason,
            'confidence': self.confidence,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'leverage': self.leverage,  # Fix: leverage必须输出到dict
            'timeframe': self.timeframe,
            'timestamp': self.timestamp.isoformat()
        }


class AIDecisionModule:
    """
    AI 决策模块 - 可单独调教
    
    配置项：
    - provider: openai / anthropic
    - model: 模型名称
    - temperature: 随机性 (0-1)
    - max_tokens: 最大token数
    - system_prompt: 系统提示词
    - strategy_style: 策略风格 conservative / balanced / aggressive
    """
    
    # 预设策略风格
    STRATEGY_STYLES = {
        'conservative': {
            'description': '保守型：注重风险控制，低杠杆，严格止损',
            'risk_level': '低',
            'leverage_max': 1,
            'position_size': '小仓位（<2%净值）'
        },
        'balanced': {
            'description': '平衡型：风险收益均衡，中等仓位',
            'risk_level': '中',
            'leverage_max': 3,
            'position_size': '中等仓位（2-5%净值）'
        },
        'aggressive': {
            'description': '激进型：追求高收益，接受较大回撤',
            'risk_level': '高',
            'leverage_max': 10,
            'position_size': '大仓位（5-10%净值）'
        }
    }
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.provider = config.get('provider', 'openai')
        self.model = config.get('model', 'gpt-4')
        self.api_key = config.get('api_key', '')
        self.temperature = config.get('temperature', 0.3)
        self.max_tokens = config.get('max_tokens', 2000)
        self.strategy_style = config.get('strategy_style', 'balanced')
        
        # 初始化客户端
        self._client = None
        self._init_client()
        
        logger.info("[AIDecision] AI决策模块初始化 | 提供商: {} | 模型: {} | 风格: {}",
                   self.provider, self.model, self.strategy_style)
    
    def _init_client(self):
        """初始化AI客户端"""
        try:
            if self.provider == 'openai':
                import openai
                self._client = openai.AsyncOpenAI(api_key=self.api_key)
            elif self.provider == 'anthropic':
                import anthropic
                self._client = anthropic.AsyncAnthropic(api_key=self.api_key)
        except Exception as e:
            logger.error("[AIDecision] AI客户端初始化失败: {}", e)
    
    def _build_system_prompt(self) -> str:
        """构建系统提示词 - 可调教核心"""
        style = self.STRATEGY_STYLES.get(self.strategy_style, self.STRATEGY_STYLES['balanced'])
        
        return f"""你是一位专业的加密货币量化交易AI，名为"旺财"。

## 交易风格
{style['description']}
- 风险等级: {style['risk_level']}
- 最大杠杆: {style['leverage_max']}x
- 仓位建议: {style['position_size']}

## 决策原则
1. 趋势跟随：顺应大趋势，逆势减仓
2. 风险管理：单笔亏损不超过净值的2%
3. 分批操作：大额交易分多笔执行
4. 动态止损：盈利后上移止损位
5. 情绪冷静：市场极度贪婪时减仓，极度恐惧时寻找机会

## 输出格式
必须输出严格的JSON格式，不要有任何其他内容：
{{
    "action": "BUY|SELL|HOLD|CLOSE|REDUCE",
    "symbol": "交易对如 BTC/USDT",
    "amount": 交易数量（USDT计价或币数量），
    "price": 目标价格（可选，0表示市价），
    "reason": "详细决策理由（100字以内）",
    "confidence": 0.0-1.0,
    "stop_loss": 止损价格（可选），
    "take_profit": 止盈价格（可选），
    "timeframe": "决策周期如 1h|4h|1d",
    "strategy": "策略名称"
}}

## 置信度标准
- 0.8-1.0: 强烈信号，多重指标共振
- 0.6-0.8: 较明确信号，主要指标支持
- 0.4-0.6: 一般信号，存在不确定性
- 0.3-0.4: 弱信号，需进一步确认
- <0.3: 观望，不操作

## 重要提示
- 没有明确信号时选择 HOLD
- 不要频繁交易，耐心等待高胜率机会
- 同时考虑技术面和基本面
- 注意交易所之间的价差和流动性差异
"""
    
    def _build_user_prompt(self, 
                          market_data: Dict[str, Any],
                          info: Dict[str, Any],
                          account: Dict[str, Any]) -> str:
        """构建用户提示词 - 整合所有输入数据"""
        
        # 行情数据摘要
        market_summary = market_data.get('summary', '暂无行情数据')
        indicators = market_data.get('indicators', {})
        indicators_text = "\n".join([f"- {k}: {v:.4f}" for k, v in indicators.items()]) if indicators else "暂无"
        
        # 信息摘要
        info_summary = info.get('summary', '暂无市场信息')
        sentiment = info.get('overall_sentiment', 0)
        fear_greed = info.get('fear_greed_index', 'N/A')
        
        # 账户摘要
        account_summary = account.get('summary', '暂无账户数据')
        positions = account.get('positions', [])
        positions_text = "\n".join([
            f"- {p['symbol']} {p['side']}: {p['amount']} @ {p['entry_price']} "
            f"(盈亏: {p.get('pnl', 0):.2f})"
            for p in positions
        ]) if positions else "无持仓"
        
        return f"""请基于以下信息做出交易决策：

### 行情数据
{market_summary}

技术指标:
{indicators_text}

### 市场信息
{info_summary}

整体情绪: {sentiment:.2f}
恐惧贪婪指数: {fear_greed}

### 账户状态
{account_summary}

当前持仓:
{positions_text}

### 要求
请分析当前市场状况，给出明确的交易决策。如果没有好机会，请果断选择HOLD。
"""
    
    async def decide(self, 
                     market_data: Dict[str, Any],
                     info: Dict[str, Any], 
                     account: Dict[str, Any]) -> TradeDecision:
        """
        执行AI决策
        
        Args:
            market_data: 行情数据摘要
            info: 市场信息摘要
            account: 账户状态摘要
            
        Returns:
            TradeDecision: 交易决策
        """
        if not self._client:
            logger.error("[AIDecision] AI客户端未初始化")
            return TradeDecision(
                action=ActionType.HOLD,
                symbol="",
                amount=0,
                reason="AI客户端未初始化",
                confidence=0.0
            )
        
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(market_data, info, account)
        
        try:
            if self.provider == 'openai':
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=self.temperature,
                    max_tokens=self.max_tokens
                )
                raw_response = response.choices[0].message.content
                
            elif self.provider == 'anthropic':
                response = await self._client.messages.create(
                    model=self.model,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                    temperature=self.temperature,
                    max_tokens=self.max_tokens
                )
                raw_response = response.content[0].text
            else:
                raise ValueError(f"不支持的AI提供商: {self.provider}")
            
            # 解析JSON响应
            decision = self._parse_decision(raw_response)
            decision.raw_response = raw_response
            
            logger.info("[AIDecision] 决策完成 | 动作: {} | 标的: {} | 置信度: {:.2f}",
                       decision.action.value, decision.symbol, decision.confidence)
            
            return decision
            
        except Exception as e:
            logger.error("[AIDecision] AI决策失败: {}", e)
            return TradeDecision(
                action=ActionType.HOLD,
                symbol="",
                amount=0,
                reason=f"决策异常: {str(e)}",
                confidence=0.0
            )
    
    def _parse_decision(self, raw_response: str) -> TradeDecision:
        """解析AI响应为决策对象"""
        try:
            # 提取JSON部分
            json_str = raw_response
            if '```json' in raw_response:
                json_str = raw_response.split('```json')[1].split('```')[0]
            elif '```' in raw_response:
                json_str = raw_response.split('```')[1].split('```')[0]
            
            data = json.loads(json_str.strip())
            
            # 映射动作
            action_str = data.get('action', 'HOLD').upper()
            try:
                action = ActionType(action_str)
            except ValueError:
                action = ActionType.HOLD
            
            return TradeDecision(
                action=action,
                symbol=data.get('symbol', ''),
                amount=float(data.get('amount', 0)),
                price=float(data.get('price')) if data.get('price') else None,
                reason=data.get('reason', ''),
                confidence=float(data.get('confidence', 0.5)),
                stop_loss=float(data.get('stop_loss')) if data.get('stop_loss') else None,
                take_profit=float(data.get('take_profit')) if data.get('take_profit') else None,
                timeframe=data.get('timeframe', '1h'),
                strategy=data.get('strategy', 'default')
            )
            
        except json.JSONDecodeError as e:
            logger.warning("[AIDecision] JSON解析失败，使用默认决策: {}", e)
            return TradeDecision(
                action=ActionType.HOLD,
                symbol="",
                amount=0,
                reason="解析失败，选择观望",
                confidence=0.0,
                raw_response=raw_response[:500]
            )
        except Exception as e:
            logger.error("[AIDecision] 解析异常: {}", e)
            return TradeDecision(
                action=ActionType.HOLD,
                symbol="",
                amount=0,
                reason=f"解析异常: {str(e)}",
                confidence=0.0
            )
    
    def update_strategy(self, style: str):
        """更新策略风格"""
        if style in self.STRATEGY_STYLES:
            self.strategy_style = style
            logger.info("[AIDecision] 策略风格更新为: {}", style)
        else:
            logger.warning("[AIDecision] 未知策略风格: {}", style)
