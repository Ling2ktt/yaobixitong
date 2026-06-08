"""
模块 8: 每日复盘 (Daily Review Module)
功能: AI自动生成日报，总结当日交易、盈亏、决策质量
可调教项: 复盘模板、报告格式、分析维度、推送时间
"""

import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger
import json


@dataclass
class DailyStats:
    """每日统计"""
    date: str
    total_trades: int = 0
    profitable_trades: int = 0
    loss_trades: int = 0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    win_rate: float = 0.0
    avg_profit: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    best_trade: Optional[Dict] = None
    worst_trade: Optional[Dict] = None
    decisions: List[Dict] = field(default_factory=list)
    snapshots: List[Dict] = field(default_factory=list)


@dataclass
class DailyReport:
    """日报"""
    date: str
    generated_at: datetime
    stats: DailyStats
    summary: str = ""
    insights: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    raw_data: Dict[str, Any] = field(default_factory=dict)
    
    def to_text(self) -> str:
        """文本格式日报"""
        lines = [
            f"🐕 旺财日报 [{self.date}]",
            f"生成时间: {self.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 50,
            "",
            "📊 交易统计",
            f"总交易次数: {self.stats.total_trades}",
            f"盈利次数: {self.stats.profitable_trades}",
            f"亏损次数: {self.stats.loss_trades}",
            f"胜率: {self.stats.win_rate:.1%}",
            f"总盈亏: ${self.stats.total_pnl:,.2f}",
            f"总手续费: ${self.stats.total_fees:,.2f}",
            f"盈亏比: {self.stats.profit_factor:.2f}",
            f"最大回撤: {self.stats.max_drawdown:.2%}",
            "",
            "💡 关键洞察",
        ]
        for i, insight in enumerate(self.insights, 1):
            lines.append(f"{i}. {insight}")
        
        lines.extend([
            "",
            "🎯 明日建议",
        ])
        for i, rec in enumerate(self.recommendations, 1):
            lines.append(f"{i}. {rec}")
        
        lines.extend([
            "",
            "📝 总结",
            self.summary,
        ])
        
        return "\n".join(lines)
    
    def to_markdown(self) -> str:
        """Markdown格式"""
        return f"""# 🐕 旺财日报 - {self.date}

> 生成时间: {self.generated_at.strftime('%Y-%m-%d %H:%M:%S')}

## 📊 交易统计

| 指标 | 数值 |
|------|------|
| 总交易次数 | {self.stats.total_trades} |
| 盈利次数 | {self.stats.profitable_trades} |
| 亏损次数 | {self.stats.loss_trades} |
| 胜率 | {self.stats.win_rate:.1%} |
| 总盈亏 | ${self.stats.total_pnl:,.2f} |
| 总手续费 | ${self.stats.total_fees:,.2f} |
| 盈亏比 | {self.stats.profit_factor:.2f} |
| 最大回撤 | {self.stats.max_drawdown:.2%} |

## 💡 关键洞察

{chr(10).join(f"{i}. {insight}" for i, insight in enumerate(self.insights, 1))}

## 🎯 明日建议

{chr(10).join(f"{i}. {rec}" for i, rec in enumerate(self.recommendations, 1))}

## 📝 总结

{self.summary}
"""


class DailyReviewModule:
    """
    每日复盘模块 - 可单独调教
    
    配置项：
    - report_time: 日报生成时间 (HH:MM)
    - ai_provider: AI提供商
    - ai_model: AI模型
    - report_format: 报告格式 text / markdown / html
    - include_charts: 是否包含图表
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.report_time = config.get('report_time', '23:30')
        self.ai_provider = config.get('ai_provider', 'openai')
        self.ai_model = config.get('ai_model', 'gpt-4')
        self.report_format = config.get('report_format', 'markdown')
        self.include_charts = config.get('include_charts', False)
        
        self._client = None
        self._init_client()
        
        logger.info("[DailyReview] 每日复盘模块初始化 | 报告时间: {}", self.report_time)
    
    def _init_client(self):
        """初始化AI客户端"""
        try:
            if self.ai_provider == 'openai':
                import openai
                self._client = openai.AsyncOpenAI(
                    api_key=self.config.get('ai_api_key', '')
                )
            elif self.ai_provider == 'anthropic':
                import anthropic
                self._client = anthropic.AsyncAnthropic(
                    api_key=self.config.get('ai_api_key', '')
                )
        except Exception as e:
            logger.error("[DailyReview] AI客户端初始化失败: {}", e)
    
    async def generate_report(self, 
                             db_path: str,
                             date: Optional[str] = None) -> DailyReport:
        """
        生成日报
        
        Args:
            db_path: 数据库路径
            date: 指定日期（默认昨天）
            
        Returns:
            DailyReport: 日报对象
        """
        if date is None:
            date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        
        # 1. 统计数据
        stats = await self._calculate_stats(db_path, date)
        
        # 2. AI生成分析
        ai_analysis = await self._generate_ai_analysis(stats, date)
        
        # 3. 组装报告
        report = DailyReport(
            date=date,
            generated_at=datetime.now(),
            stats=stats,
            summary=ai_analysis.get('summary', ''),
            insights=ai_analysis.get('insights', []),
            recommendations=ai_analysis.get('recommendations', [])
        )
        
        # 4. 保存报告
        self._save_report(report)
        
        logger.info("[DailyReview] 日报生成完成 | 日期: {} | 交易: {} | 盈亏: ${:.2f}",
                   date, stats.total_trades, stats.total_pnl)
        
        return report
    
    async def _calculate_stats(self, db_path: str, date: str) -> DailyStats:
        """计算当日统计数据"""
        import sqlite3
        
        stats = DailyStats(date=date)
        
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # 查询当日交易
            cursor.execute('''
                SELECT * FROM trades 
                WHERE date(timestamp) = ?
                ORDER BY timestamp
            ''', (date,))
            
            trades = [dict(row) for row in cursor.fetchall()]
            stats.total_trades = len(trades)
            
            if trades:
                # 盈亏统计
                profits = []
                losses = []
                
                for trade in trades:
                    pnl = trade.get('pnl', 0) or 0
                    stats.total_pnl += pnl
                    stats.total_fees += trade.get('fee', 0) or 0
                    
                    if pnl > 0:
                        stats.profitable_trades += 1
                        profits.append(pnl)
                        if not stats.best_trade or pnl > (stats.best_trade.get('pnl', 0) or 0):
                            stats.best_trade = trade
                    elif pnl < 0:
                        stats.loss_trades += 1
                        losses.append(abs(pnl))
                        if not stats.worst_trade or pnl < (stats.worst_trade.get('pnl', 0) or 0):
                            stats.worst_trade = trade
                
                # 胜率
                if stats.total_trades > 0:
                    stats.win_rate = stats.profitable_trades / stats.total_trades
                
                # 平均盈亏
                if profits:
                    stats.avg_profit = sum(profits) / len(profits)
                if losses:
                    stats.avg_loss = sum(losses) / len(losses)
                
                # 盈亏比
                if stats.avg_loss > 0:
                    stats.profit_factor = stats.avg_profit / stats.avg_loss
                
                # 最大回撤（简化计算）
                equity_curve = []
                running_pnl = 0
                for trade in trades:
                    running_pnl += trade.get('pnl', 0) or 0
                    equity_curve.append(running_pnl)
                
                if equity_curve:
                    peak = equity_curve[0]
                    max_dd = 0
                    for eq in equity_curve:
                        if eq > peak:
                            peak = eq
                        dd = peak - eq
                        if dd > max_dd:
                            max_dd = dd
                    stats.max_drawdown = max_dd / abs(equity_curve[0]) if equity_curve[0] != 0 else 0
            
            # 查询当日决策
            cursor.execute('''
                SELECT * FROM decisions 
                WHERE date(timestamp) = ?
            ''', (date,))
            stats.decisions = [dict(row) for row in cursor.fetchall()]
            
            # 查询当日账户快照
            cursor.execute('''
                SELECT * FROM account_snapshots 
                WHERE date(timestamp) = ?
            ''', (date,))
            stats.snapshots = [dict(row) for row in cursor.fetchall()]
            
            conn.close()
            
        except Exception as e:
            logger.error("[DailyReview] 统计数据失败: {}", e)
        
        return stats
    
    async def _generate_ai_analysis(self, stats: DailyStats, 
                                    date: str) -> Dict[str, Any]:
        """使用AI生成分析"""
        if not self._client:
            return {
                'summary': 'AI客户端未初始化，仅提供基础统计。',
                'insights': ['今日共执行 {} 笔交易'.format(stats.total_trades)],
                'recommendations': ['请检查AI配置以获取更详细的分析。']
            }
        
        # 构建分析提示
        prompt = self._build_review_prompt(stats, date)
        
        try:
            if self.ai_provider == 'openai':
                response = await self._client.chat.completions.create(
                    model=self.ai_model,
                    messages=[
                        {"role": "system", "content": "你是一位专业的交易复盘分析师。请用中文提供简洁有力的分析。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.5,
                    max_tokens=1500
                )
                content = response.choices[0].message.content
            else:
                content = "暂不支持该AI提供商的分析"
            
            # 解析AI响应（期望JSON格式）
            return self._parse_analysis(content)
            
        except Exception as e:
            logger.error("[DailyReview] AI分析生成失败: {}", e)
            return {
                'summary': f'今日交易 {stats.total_trades} 笔，总盈亏 ${stats.total_pnl:.2f}',
                'insights': [f'胜率 {stats.win_rate:.1%}', f'盈亏比 {stats.profit_factor:.2f}'],
                'recommendations': ['继续观察市场走势。']
            }
    
    def _build_review_prompt(self, stats: DailyStats, date: str) -> str:
        """构建复盘提示"""
        best_trade_info = ""
        if stats.best_trade:
            best_trade_info = f"最佳交易: {stats.best_trade.get('symbol', '')} 盈利 ${stats.best_trade.get('pnl', 0):.2f}"
        
        worst_trade_info = ""
        if stats.worst_trade:
            worst_trade_info = f"最差交易: {stats.worst_trade.get('symbol', '')} 亏损 ${abs(stats.worst_trade.get('pnl', 0)):.2f}"
        
        return f"""请基于以下交易数据生成每日复盘报告：

日期: {date}

交易统计:
- 总交易次数: {stats.total_trades}
- 盈利次数: {stats.profitable_trades}
- 亏损次数: {stats.loss_trades}
- 胜率: {stats.win_rate:.1%}
- 总盈亏: ${stats.total_pnl:.2f}
- 总手续费: ${stats.total_fees:.2f}
- 平均盈利: ${stats.avg_profit:.2f}
- 平均亏损: ${stats.avg_loss:.2f}
- 盈亏比: {stats.profit_factor:.2f}
{best_trade_info}
{worst_trade_info}

请输出JSON格式：
{{
    "summary": "一句话总结今日表现",
    "insights": ["洞察1", "洞察2", "洞察3"],
    "recommendations": ["建议1", "建议2"]
}}
"""
    
    def _parse_analysis(self, content: str) -> Dict[str, Any]:
        """解析AI分析响应"""
        try:
            import json
            # 尝试提取JSON
            if '```json' in content:
                content = content.split('```json')[1].split('```')[0]
            elif '```' in content:
                content = content.split('```')[1].split('```')[0]
            
            data = json.loads(content.strip())
            return {
                'summary': data.get('summary', ''),
                'insights': data.get('insights', []),
                'recommendations': data.get('recommendations', [])
            }
        except:
            # 如果解析失败，使用文本作为总结
            return {
                'summary': content[:200] if content else '分析生成中...',
                'insights': ['AI返回非标准格式'],
                'recommendations': ['建议手动检查交易记录。']
            }
    
    def _save_report(self, report: DailyReport):
        """保存报告到文件"""
        try:
            report_dir = Path('reports')
            report_dir.mkdir(exist_ok=True)
            
            if self.report_format == 'markdown':
                ext = 'md'
                content = report.to_markdown()
            else:
                ext = 'txt'
                content = report.to_text()
            
            filepath = report_dir / f"daily_report_{report.date}.{ext}"
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            
            logger.info("[DailyReview] 报告已保存: {}", filepath)
        except Exception as e:
            logger.error("[DailyReview] 保存报告失败: {}", e)
    
    def should_generate_now(self) -> bool:
        """检查是否应该生成日报"""
        now = datetime.now()
        report_hour, report_minute = map(int, self.report_time.split(':'))
        
        return now.hour == report_hour and now.minute == report_minute
