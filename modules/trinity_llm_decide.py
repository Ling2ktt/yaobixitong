"""
三位一体策略 - LLM 决策层

配合 TrinityEngine 代码层使用。
代码层做定量分析（Wyckoff阶段/SMC OB/PA H2L2），输出结构化JSON。
LLM层做定性判断：信号可信度、边缘场景、市场背景理解。

使用方式:
    from modules.trinity_llm_decide import TrinityLLMDecider
    
    decider = TrinityLLMDecider(api_key="...", model="gpt-4")
    decision = await decider.decide(trinity_signal, market_context)
"""

import json
import os
from typing import Dict, Optional, List
from dataclasses import dataclass, field
from datetime import datetime


# ============================================================
# 通用 Prompt 模板
# ============================================================

TRINITY_LLM_SYSTEM_PROMPT = """你是一位专业的加密货币合约交易分析师，精通以下三大交易体系：

1. **Wyckoff 威科夫操盘法** — 市场阶段判断（吸筹/上涨/派发/下跌），Spring/UTAD/SOS/SOW事件
2. **SMC 聪明钱概念** — 订单块(OB)、公允价值缺口(FVG)、流动性猎杀、OTE区间(62-79%)
3. **PA 价格行为学 (Al Brooks体系)** — "始终在场"方向、H2/L2入场信号、信号K线质量

你的职责：
- 接收代码层已经计算好的「结构化交易信号」
- 对信号进行定性审核，判断是否「真的可信」
- 你可以 APPROVE（批准）或 REJECT（拒绝）一个信号
- 你也可以 ADJUST（调整）仓位大小或止损位置

审核时请关注代码层可能漏掉的细节：
- 信号评分虽然高，但是否有高潮耗尽迹象？
- 市场是否处于重大新闻/事件中？
- 三层分析之间有没有矛盾？
- 成交量是否支持当前的判断？
- 资金费率是否过高（做多时如果资金费率>0.1%要警惕）？

输出格式必须是严格的JSON，不要包含任何额外文字。
"""

TRINITY_LLM_DECISION_PROMPT = """请审核以下三位一体策略信号，并给出最终决策。

## 市场背景
{market_context}

## 三层分析结果

### Wyckoff 结构层
- 阶段: {wyckoff_phase}
- 偏向: {wyckoff_bias}
- 置信度: {wyckoff_confidence}/100
- 关键事件: {wyckoff_events}
- 趋势结构: {wyckoff_trend}

### SMC 机构层
- 市场结构: {smc_structure}
- 流动性猎杀: {smc_liquidity_sweep}
- 订单块: {smc_ob}
- FVG: {smc_fvg}
- OTE甜点: {smc_ote}

### PA 执行层
- 始终在场方向: {pa_always_in}
- 趋势强度: {pa_trend_strength}/5
- 当前回调: {pa_legs}腿
- H2/L2信号: H2={pa_h2} / L2={pa_l2}
- 信号K线质量: {pa_signal_quality}/100 ({pa_signal_type})
- 铁丝网状态: {pa_barbwire}
- 高潮警告: {pa_climax}

## 策略评分
- 综合评分: {score}/160
- 信号等级: {grade}级
- 信号方向: {direction}
- 得分原因: {reasons}

## 风控参数
- 建议入场: {entry}
- 建议止损: {stop_loss}
- 建议止盈: {take_profit}
- 建议仓位: {position_pct}% (${position_usdt})

## 警告
{warnings}

---

请以JSON格式输出你的审核决定：

```json
{{
  "decision": "APPROVE" | "REJECT" | "ADJUST",
  "confidence": 0-100,
  "reason": "一句话简述理由（中文，30字以内）",
  "analysis": "详细分析（中文，100字以内）",
  "adjustment": {{
    "position_pct": 0-100,      // 调整后的仓位百分比（仅ADJUST时需要）
    "stop_loss": 0.0,           // 调整后的止损价（仅ADJUST时需要）
    "reason": "调整原因"
  }}
}}
```

注意：
1. 如果三层信号都强且方向一致 → APPROVE
2. 如果有矛盾或明显风险 → REJECT 或 ADJUST（降低仓位）
3. ADJUST 用于信号方向对但风控参数需要微调的情况
4. 如果 Wyckoff 偏向与 SMC 结构矛盾 → 必须 REJECT
5. 如果 PA 层是铁丝网状态 → 必须 REJECT
6. 如果有高潮警告 → 建议 ADJUST 减半仓位
"""

# 简化版 Prompt（当缺少某些层时使用）
TRINITY_LLM_SIMPLE_PROMPT = """请审核以下交易信号。

## 市场背景
{market_context}

## 信号摘要
{signal_summary}

## 风控参数
- 方向: {direction}
- 入场: {entry_price}
- 止损: {stop_loss}
- 止盈: {take_profit}
- 建议仓位: {position_pct}%

---

请以JSON格式输出审核决定：
```json
{{
  "decision": "APPROVE" | "REJECT" | "ADJUST",
  "confidence": 0-100,
  "reason": "一句话理由（中文，30字以内）",
  "adjustment": {{}}
}}
```
"""


# ============================================================
# 决策器实现
# ============================================================

@dataclass
class LLMDecision:
    """LLM 决策结果"""
    decision: str = "REJECT"       # APPROVE, REJECT, ADJUST
    confidence: int = 0            # 0-100
    reason: str = ""               # 简短理由
    analysis: str = ""             # 详细分析
    adjustment: Dict = field(default_factory=dict)  # 调整参数
    
    def is_approved(self) -> bool:
        return self.decision in ("APPROVE", "ADJUST")
    
    def to_dict(self) -> Dict:
        return {
            "decision": self.decision,
            "confidence": self.confidence,
            "reason": self.reason,
            "analysis": self.analysis,
            "adjustment": self.adjustment,
            "timestamp": datetime.now().isoformat()
        }


class TrinityLLMDecider:
    """三位一体LLM决策器"""
    
    def __init__(self, config: Dict = None):
        """
        Args:
            config: {
                "provider": "openai" | "deepseek" | "yuanbao",
                "api_key": "...",
                "api_base": "https://api.openai.com/v1",
                "model": "gpt-4",
                "enabled": True,
                "timeout": 30,
                "temperature": 0.3,     # 低温度=更确定性
                "max_tokens": 500
            }
        """
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.provider = self.config.get("provider", "openai")
        self.api_key = self.config.get("api_key", "")
        self.api_base = self.config.get("api_base", "https://api.openai.com/v1")
        self.model = self.config.get("model", "gpt-4")
        self.timeout = self.config.get("timeout", 30)
        self.temperature = self.config.get("temperature", 0.3)
        self.max_tokens = self.config.get("max_tokens", 500)
        
        # 是否在API不可用时降级为自动批准模式
        self.fallback_approve = self.config.get("fallback_approve", False)
    
    def build_market_context(self, symbol: str, price: float,
                             market_info: Dict = None) -> str:
        """构建市场背景描述"""
        lines = [
            f"交易对: {symbol}",
            f"当前价格: ${price:.2f}" if price else "价格: N/A",
        ]
        
        if market_info:
            if market_info.get("fear_greed"):
                fg = market_info["fear_greed"]
                lines.append(f"恐惧贪婪指数: {fg} (0=极度恐惧, 100=极度贪婪)")
            
            if market_info.get("funding_rate") is not None:
                fr = market_info["funding_rate"]
                lines.append(f"资金费率: {fr*100:.4f}%")
                if fr > 0.001:
                    lines.append("⚠ 资金费率偏高，做多需谨慎")
            
            if market_info.get("btc_dominance"):
                lines.append(f"BTC市值占比: {market_info['btc_dominance']}%")
            
            if market_info.get("news_summary"):
                lines.append(f"\n近期动态: {market_info['news_summary']}")
        
        return "\n".join(lines) if lines else f"交易对: {symbol}"
    
    def build_full_prompt(self, trinity_signal, 
                          market_context: str = "") -> str:
        """
        构建完整 LLM Prompt
        
        Args:
            trinity_signal: TrinityEngine.analyze() 返回的 TrinitySignal
            market_context: 市场背景文本
        """
        # 安全检查：如果是 HOLD 信号，不入 LLM
        if trinity_signal.signal == "HOLD":
            return ""
        
        # 提取各层信号
        wyckoff = trinity_signal.wyckoff or {}
        smc = trinity_signal.smc or {}
        pa = trinity_signal.pa or {}
        
        # 格式化参数
        wyckoff_events = ", ".join(wyckoff.get("key_events", ["无"]))
        
        ob = smc.get("order_block", {})
        ob_text = f"{ob.get('type', '无')} (近端:{ob.get('proximal', 'N/A')}, 远端:{ob.get('distal', 'N/A')}, 质量:{ob.get('quality', 0)})" if ob.get("type") else "无"
        
        fvg = smc.get("fvg", {})
        fvg_text = f"{fvg.get('type')} ({fvg.get('top', 'N/A')}-{fvg.get('bottom', 'N/A')})" if fvg.get("type") else "无"
        
        ote = smc.get("ote_zone", {})
        ote_text = f"${ote.get('sweet', 'N/A'):.2f}" if ote.get("sweet") else "无"
        
        # 格式化获利目标
        tp_text = ", ".join([f"${t:.2f}" for t in trinity_signal.take_profit[:3]]) if trinity_signal.take_profit else "无"
        
        # 填充模板
        prompt = TRINITY_LLM_DECISION_PROMPT.format(
            market_context=market_context or "无特殊背景",
            wyckoff_phase=wyckoff.get("phase", "未知"),
            wyckoff_bias=wyckoff.get("bias", "NEUTRAL"),
            wyckoff_confidence=wyckoff.get("confidence", 0),
            wyckoff_events=wyckoff_events,
            wyckoff_trend=wyckoff.get("trend_structure", "未知"),
            smc_structure=smc.get("structure", "未知"),
            smc_liquidity_sweep=smc.get("liquidity_sweep", "无"),
            smc_ob=ob_text,
            smc_fvg=fvg_text,
            smc_ote=ote_text,
            pa_always_in=pa.get("always_in", "未知"),
            pa_trend_strength=pa.get("trend_strength", 0),
            pa_legs=pa.get("callback_legs", 0),
            pa_h2=pa.get("h2_ready", False),
            pa_l2=pa.get("l2_ready", False),
            pa_signal_quality=pa.get("signal_bar_quality", 0),
            pa_signal_type=pa.get("signal_bar_type", "未知"),
            pa_barbwire="⚠ 是（不推荐交易）" if pa.get("is_barbwire") else "否",
            pa_climax="⚠ 是（建议减仓）" if pa.get("climax_warning") else "否",
            score=trinity_signal.score,
            grade=trinity_signal.grade,
            direction=trinity_signal.signal,
            reasons="\n".join([f"  + {r}" for r in trinity_signal.reasons]) if trinity_signal.reasons else "无",
            entry=f"${trinity_signal.entry.get('price', 'N/A')} ({trinity_signal.entry.get('type', 'N/A')})",
            stop_loss=f"${trinity_signal.stop_loss:.2f}" if trinity_signal.stop_loss else "未设置",
            take_profit=tp_text,
            position_pct=trinity_signal.position_pct * 100,
            position_usdt=f"{trinity_signal.max_position_usdt:.2f}",
            warnings="\n".join([f"  ⚠ {w}" for w in trinity_signal.warnings]) if trinity_signal.warnings else "无",
        )
        
        return prompt
    
    def build_simple_prompt(self, direction: str, entry_price: float,
                           stop_loss: float, take_profit: List[float],
                           position_pct: float, 
                           signal_summary: str = "",
                           market_context: str = "") -> str:
        """构建简化版 Prompt（当缺少某些层的分析时使用）"""
        tp_text = ", ".join([f"${t:.2f}" for t in take_profit[:3]]) if take_profit else "无"
        
        return TRINITY_LLM_SIMPLE_PROMPT.format(
            market_context=market_context or "无特殊背景",
            signal_summary=signal_summary or "技术信号摘要未提供",
            direction=direction,
            entry_price=f"${entry_price:.2f}" if entry_price else "N/A",
            stop_loss=f"${stop_loss:.2f}" if stop_loss else "未设置",
            take_profit=tp_text,
            position_pct=position_pct * 100,
        )
    
    async def _call_llm_api(self, messages: List[Dict]) -> Optional[str]:
        """调用 LLM API"""
        import aiohttp
        
        if not self.api_key:
            print("[LLM] 无API Key，跳过LLM调用")
            return None
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        
        url = f"{self.api_base.rstrip('/')}/chat/completions"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"]
                    else:
                        error = await resp.text()
                        print(f"[LLM] API错误 ({resp.status}): {error[:200]}")
                        return None
        except Exception as e:
            print(f"[LLM] 调用异常: {e}")
            return None
    
    def _parse_json_response(self, text: str) -> Optional[Dict]:
        """解析 LLM 返回的 JSON"""
        if not text:
            return None
        
        # 清理：去除 markdown 代码块标记
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试提取 JSON 块
            import re
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                try:
                    return json.loads(match.group())
                except:
                    pass
        return None
    
    async def decide(self, trinity_signal,
                     market_context: str = "",
                     force_skip_llm: bool = False) -> LLMDecision:
        """
        调用 LLM 对三位一体信号进行审核
        
        Args:
            trinity_signal: TrinityEngine 返回的信号
            market_context: 市场背景
            force_skip_llm: 强制跳过 LLM（用于测试或降级）
        
        Returns:
            LLMDecision
        """
        # 如果未启用或强制跳过，返回自动批准
        if not self.enabled or force_skip_llm:
            return LLMDecision(
                decision="APPROVE",
                confidence=trinity_signal.score,
                reason="LLM已禁用，自动批准",
                analysis="代码层评分通过，跳过LLM审核"
            )
        
        # HOLD 信号不需要 LLM
        if trinity_signal.signal == "HOLD":
            return LLMDecision(
                decision="REJECT",
                confidence=0,
                reason="信号为HOLD",
                analysis="代码层判定不交易"
            )
        
        # 构建 Prompt
        prompt = self.build_full_prompt(trinity_signal, market_context)
        if not prompt:
            return LLMDecision(decision="REJECT", reason="无法构建Prompt")
        
        messages = [
            {"role": "system", "content": TRINITY_LLM_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
        
        # 调用 LLM
        response = await self._call_llm_api(messages)
        
        if response is None:
            # API 调用失败
            if self.fallback_approve:
                print("[LLM] API不可用，降级为自动批准")
                return LLMDecision(
                    decision="APPROVE",
                    confidence=trinity_signal.score - 10,
                    reason="LLM不可用，自动批准（降级模式）",
                    analysis="API调用失败，信任代码层判断"
                )
            else:
                return LLMDecision(
                    decision="REJECT",
                    confidence=0,
                    reason="LLM API不可用",
                    analysis="无法调用LLM审核，保守拒绝"
                )
        
        # 解析响应
        parsed = self._parse_json_response(response)
        
        if parsed is None:
            print(f"[LLM] 无法解析响应: {response[:200]}")
            return LLMDecision(
                decision="REJECT",
                confidence=0,
                reason="LLM响应无法解析",
                analysis=f"原始响应: {response[:100]}"
            )
        
        return LLMDecision(
            decision=parsed.get("decision", "REJECT"),
            confidence=parsed.get("confidence", 50),
            reason=parsed.get("reason", ""),
            analysis=parsed.get("analysis", ""),
            adjustment=parsed.get("adjustment", {})
        )


# ============================================================
# 便捷函数：同步模式（用于不需要 async 的场景）
# ============================================================

def llm_decide_sync(trinity_signal, market_context: str = "",
                    config: Dict = None) -> LLMDecision:
    """
    llm_decide() 同步版本
    通过 asyncio.run() 包装异步调用
    """
    import asyncio
    
    decider = TrinityLLMDecider(config)
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    return loop.run_until_complete(
        decider.decide(trinity_signal, market_context)
    )


# ============================================================
# API 兼容适配器（对接现有 ai_decision.py 的接口）
# ============================================================

async def trinity_llm_decide_with_fallback(
    trinity_signal,
    market_context: str = "",
    llm_config: Dict = None,
    ai_decision_module=None
) -> LLMDecision:
    """
    使用三位一体 LLM 决策，如果不可用则降级到现有 ai_decision 模块
    
    Args:
        trinity_signal: TrinityEngine 信号
        market_context: 市场背景
        llm_config: LLM 配置
        ai_decision_module: 现有的 AIDecisionModule 实例（fallback）
    """
    decider = TrinityLLMDecider(llm_config or {})
    
    # 尝试三位一体专用 LLM
    if decider.enabled and decider.api_key:
        try:
            return await decider.decide(trinity_signal, market_context)
        except Exception as e:
            print(f"[TrinityLLM] 异常，降级: {e}")
    
    # 降级：返回代码层判断
    if trinity_signal.grade in ("A", "B"):
        return LLMDecision(
            decision="APPROVE",
            confidence=trinity_signal.score,
            reason=f"代码层{trinity_signal.grade}级信号，LLM不可用",
            analysis="降级为纯代码判断"
        )
    else:
        return LLMDecision(
            decision="REJECT",
            confidence=0,
            reason=f"信号等级{trinity_signal.grade}不满足，LLM不可用"
        )
