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
    journal: List[Dict] = field(default_factory=list)
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


def _daily_report_to_text(self) -> str:
    """Readable text report."""
    lines = [
        f"旺财交易复盘 [{self.date}]",
        f"生成时间: {self.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 50,
        "",
        "交易统计",
        f"总交易次数: {self.stats.total_trades}",
        f"盈利次数: {self.stats.profitable_trades}",
        f"亏损次数: {self.stats.loss_trades}",
        f"胜率: {self.stats.win_rate:.1%}",
        f"总盈亏: ${self.stats.total_pnl:,.2f}",
        f"总手续费: ${self.stats.total_fees:,.2f}",
        f"盈亏比: {self.stats.profit_factor:.2f}",
        f"最大回撤: {self.stats.max_drawdown:.2%}",
        "",
        "关键观察",
    ]
    lines.extend(f"{i}. {insight}" for i, insight in enumerate(self.insights, 1))
    lines.extend(["", "后续建议"])
    lines.extend(f"{i}. {rec}" for i, rec in enumerate(self.recommendations, 1))
    lines.extend(["", "总结", self.summary])
    return "\n".join(lines)


def _daily_report_to_markdown(self) -> str:
    """Readable markdown report."""
    trade_lines = []
    for item in self.stats.journal[:20]:
        symbol = item.get('symbol', '')
        direction = item.get('direction', '')
        entry = item.get('entry_price', '')
        stop = item.get('stop_loss', '')
        tps = item.get('take_profit_levels', '')
        risk_level = item.get('risk_level', '')
        reason = (item.get('setup_reason') or item.get('signal_reason') or '')[:120]
        trade_lines.append(
            f"| {symbol} | {direction} | {entry} | {stop} | {tps} | {risk_level} | {reason} |"
        )
    trade_table = "\n".join(trade_lines) if trade_lines else "| 暂无 | - | - | - | - | - | - |"

    return f"""# 旺财交易复盘 - {self.date}

> 生成时间: {self.generated_at.strftime('%Y-%m-%d %H:%M:%S')}

## 交易统计

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

## 交易明细

| 标的 | 方向 | 入场 | 止损 | 止盈 | 风控 | 信号原因 |
|------|------|------|------|------|------|----------|
{trade_table}

## 关键观察

{chr(10).join(f"{i}. {insight}" for i, insight in enumerate(self.insights, 1))}

## 后续建议

{chr(10).join(f"{i}. {rec}" for i, rec in enumerate(self.recommendations, 1))}

## 总结

{self.summary}
"""


DailyReport.to_text = _daily_report_to_text
DailyReport.to_markdown = _daily_report_to_markdown


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

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trade_journal'")
            if cursor.fetchone():
                cursor.execute('''
                    SELECT * FROM trade_journal
                    WHERE date(entry_time) = ?
                    ORDER BY entry_time
                ''', (date,))
                stats.journal = [dict(row) for row in cursor.fetchall()]
            
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
            return self._generate_local_analysis(stats, date)
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
    
    def _generate_local_analysis(self, stats: DailyStats, date: str) -> Dict[str, Any]:
        """Generate a useful review without calling an AI provider."""
        trades = stats.journal
        summary = (
            f"{date} 共记录 {stats.total_trades} 笔交易，"
            f"交易复盘表 {len(trades)} 笔，当前已记录净盈亏 ${stats.total_pnl:.2f}。"
        )

        insights = []
        if trades:
            symbols = ", ".join(str(t.get('symbol', '')) for t in trades[:8])
            directions = {}
            missing_protection = []
            failed_risk = []
            for trade in trades:
                direction = str(trade.get('direction') or 'UNKNOWN')
                directions[direction] = directions.get(direction, 0) + 1
                if trade.get('stop_loss') in (None, '') or trade.get('take_profit_levels') in (None, '', '[]'):
                    missing_protection.append(str(trade.get('symbol', '')))
                if not bool(trade.get('risk_passed')):
                    failed_risk.append(str(trade.get('symbol', '')))

            direction_text = ", ".join(f"{k} {v} 笔" for k, v in sorted(directions.items()))
            insights.append(f"今日开仓标的: {symbols}。方向分布: {direction_text or '暂无'}。")
            if missing_protection:
                insights.append(f"以下记录缺少止盈/止损字段，需要核对保护单: {', '.join(missing_protection)}。")
            else:
                insights.append("交易复盘表中的开仓记录均带有止损与止盈计划，便于后续按计划复盘。")
            if failed_risk:
                insights.append(f"存在未通过风控但进入复盘表的记录: {', '.join(failed_risk)}。")
            else:
                insights.append("复盘表记录的风控结果均为通过。")
        else:
            insights.append("当天没有交易复盘记录，无法评估策略入场质量。")

        if stats.snapshots:
            first = stats.snapshots[0]
            last = stats.snapshots[-1]
            insights.append(
                "账户快照从 "
                f"${float(first.get('total_equity') or 0):.2f} "
                f"到 ${float(last.get('total_equity') or 0):.2f}，"
                f"最新持仓数 {int(last.get('position_count') or 0)}。"
            )

        recommendations = [
            "继续观察每笔颜驰信号触发后的 1R、2R 到达情况，把未到目标的原因记录到复盘表。",
            "新开仓已放宽到 10 单上限，建议重点关注相关性过高的同向持仓，避免行情单边反抽时集中回撤。",
            "如果要做精确胜率和 R 倍数统计，需要在平仓时回写实际成交盈亏、手续费和出场原因。",
        ]
        return {
            'summary': summary,
            'insights': insights,
            'recommendations': recommendations,
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


async def _daily_review_generate_ai_analysis(self, stats: DailyStats, date: str) -> Dict[str, Any]:
    """Generate review analysis, falling back to local rules when AI is unavailable."""
    if not self._client:
        return self._generate_local_analysis(stats, date)

    try:
        if self.ai_provider != 'openai':
            return self._generate_local_analysis(stats, date)

        response = await self._client.chat.completions.create(
            model=self.ai_model,
            messages=[
                {"role": "system", "content": "你是一位专业的交易复盘分析师。请用中文提供简洁有力的分析。"},
                {"role": "user", "content": self._build_review_prompt(stats, date)},
            ],
            temperature=0.5,
            max_tokens=1500,
        )
        content = response.choices[0].message.content
        return self._parse_analysis(content)
    except Exception as e:
        logger.error("[DailyReview] AI分析生成失败: {}", e)
        return self._generate_local_analysis(stats, date)


DailyReviewModule._generate_ai_analysis = _daily_review_generate_ai_analysis


def _wc_review_cell(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", " ")


def _wc_report_to_text(self) -> str:
    lines = [
        f"\u65fa\u8d22\u4ea4\u6613\u590d\u76d8 [{self.date}]",
        f"\u751f\u6210\u65f6\u95f4: {self.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 50,
        "",
        "\u4ea4\u6613\u7edf\u8ba1",
        f"\u603b\u4ea4\u6613\u6b21\u6570: {self.stats.total_trades}",
        f"\u76c8\u5229\u6b21\u6570: {self.stats.profitable_trades}",
        f"\u4e8f\u635f\u6b21\u6570: {self.stats.loss_trades}",
        f"\u80dc\u7387: {self.stats.win_rate:.1%}",
        f"\u603b\u76c8\u4e8f: ${self.stats.total_pnl:,.2f}",
        f"\u603b\u624b\u7eed\u8d39: ${self.stats.total_fees:,.2f}",
        f"\u76c8\u4e8f\u6bd4: {self.stats.profit_factor:.2f}",
        f"\u6700\u5927\u56de\u64a4: {self.stats.max_drawdown:.2%}",
        "",
        "\u5173\u952e\u89c2\u5bdf",
    ]
    lines.extend(f"{i}. {insight}" for i, insight in enumerate(self.insights, 1))
    lines.extend(["", "\u540e\u7eed\u5efa\u8bae"])
    lines.extend(f"{i}. {rec}" for i, rec in enumerate(self.recommendations, 1))
    lines.extend(["", "\u603b\u7ed3", self.summary])
    return "\n".join(lines)


def _wc_report_to_markdown(self) -> str:
    trade_lines = []
    for item in self.stats.journal[:20]:
        reason = (item.get('setup_reason') or item.get('signal_reason') or '')[:120]
        cells = [
            item.get('symbol', ''),
            item.get('direction', ''),
            item.get('entry_price', ''),
            item.get('stop_loss', ''),
            item.get('take_profit_levels', ''),
            item.get('risk_level', ''),
            reason,
        ]
        trade_lines.append("| " + " | ".join(_wc_review_cell(c) for c in cells) + " |")
    trade_table = "\n".join(trade_lines) if trade_lines else "| \u6682\u65e0 | - | - | - | - | - | - |"

    return f"""# \u65fa\u8d22\u4ea4\u6613\u590d\u76d8 - {self.date}

> \u751f\u6210\u65f6\u95f4: {self.generated_at.strftime('%Y-%m-%d %H:%M:%S')}

## \u4ea4\u6613\u7edf\u8ba1

| \u6307\u6807 | \u6570\u503c |
|------|------|
| \u603b\u4ea4\u6613\u6b21\u6570 | {self.stats.total_trades} |
| \u76c8\u5229\u6b21\u6570 | {self.stats.profitable_trades} |
| \u4e8f\u635f\u6b21\u6570 | {self.stats.loss_trades} |
| \u80dc\u7387 | {self.stats.win_rate:.1%} |
| \u603b\u76c8\u4e8f | ${self.stats.total_pnl:,.2f} |
| \u603b\u624b\u7eed\u8d39 | ${self.stats.total_fees:,.2f} |
| \u76c8\u4e8f\u6bd4 | {self.stats.profit_factor:.2f} |
| \u6700\u5927\u56de\u64a4 | {self.stats.max_drawdown:.2%} |

## \u4ea4\u6613\u660e\u7ec6

| \u6807\u7684 | \u65b9\u5411 | \u5165\u573a | \u6b62\u635f | \u6b62\u76c8 | \u98ce\u63a7 | \u4fe1\u53f7\u539f\u56e0 |
|------|------|------|------|------|------|----------|
{trade_table}

## \u5173\u952e\u89c2\u5bdf

{chr(10).join(f"{i}. {insight}" for i, insight in enumerate(self.insights, 1))}

## \u540e\u7eed\u5efa\u8bae

{chr(10).join(f"{i}. {rec}" for i, rec in enumerate(self.recommendations, 1))}

## \u603b\u7ed3

{self.summary}
"""


def _wc_generate_local_analysis(self, stats: DailyStats, date: str) -> Dict[str, Any]:
    trades = stats.journal
    summary = (
        f"{date} \u5171\u8bb0\u5f55 {stats.total_trades} \u7b14\u4ea4\u6613\uff0c"
        f"\u4ea4\u6613\u590d\u76d8\u8868 {len(trades)} \u7b14\uff0c"
        f"\u5f53\u524d\u5df2\u8bb0\u5f55\u51c0\u76c8\u4e8f ${stats.total_pnl:.2f}\u3002"
    )
    insights = []
    if trades:
        symbols = ", ".join(str(t.get('symbol', '')) for t in trades[:8])
        directions = {}
        missing_protection = []
        failed_risk = []
        for trade in trades:
            direction = str(trade.get('direction') or 'UNKNOWN')
            directions[direction] = directions.get(direction, 0) + 1
            if trade.get('stop_loss') in (None, '') or trade.get('take_profit_levels') in (None, '', '[]'):
                missing_protection.append(str(trade.get('symbol', '')))
            if not bool(trade.get('risk_passed')):
                failed_risk.append(str(trade.get('symbol', '')))

        direction_text = ", ".join(f"{k} {v} \u7b14" for k, v in sorted(directions.items()))
        insights.append(f"\u4eca\u65e5\u5f00\u4ed3\u6807\u7684: {symbols}\u3002\u65b9\u5411\u5206\u5e03: {direction_text or '\u6682\u65e0'}\u3002")
        if missing_protection:
            insights.append(f"\u4ee5\u4e0b\u8bb0\u5f55\u7f3a\u5c11\u6b62\u76c8/\u6b62\u635f\u5b57\u6bb5\uff0c\u9700\u8981\u6838\u5bf9\u4fdd\u62a4\u5355: {', '.join(missing_protection)}\u3002")
        else:
            insights.append("\u4ea4\u6613\u590d\u76d8\u8868\u4e2d\u7684\u5f00\u4ed3\u8bb0\u5f55\u5747\u5e26\u6709\u6b62\u635f\u4e0e\u6b62\u76c8\u8ba1\u5212\uff0c\u4fbf\u4e8e\u540e\u7eed\u6309\u8ba1\u5212\u590d\u76d8\u3002")
        if failed_risk:
            insights.append(f"\u5b58\u5728\u672a\u901a\u8fc7\u98ce\u63a7\u4f46\u8fdb\u5165\u590d\u76d8\u8868\u7684\u8bb0\u5f55: {', '.join(failed_risk)}\u3002")
        else:
            insights.append("\u590d\u76d8\u8868\u8bb0\u5f55\u7684\u98ce\u63a7\u7ed3\u679c\u5747\u4e3a\u901a\u8fc7\u3002")
    else:
        insights.append("\u5f53\u5929\u6ca1\u6709\u4ea4\u6613\u590d\u76d8\u8bb0\u5f55\uff0c\u65e0\u6cd5\u8bc4\u4f30\u7b56\u7565\u5165\u573a\u8d28\u91cf\u3002")

    if stats.snapshots:
        first = stats.snapshots[0]
        last = stats.snapshots[-1]
        insights.append(
            "\u8d26\u6237\u5feb\u7167\u4ece "
            f"${float(first.get('total_equity') or 0):.2f} "
            f"\u5230 ${float(last.get('total_equity') or 0):.2f}\uff0c"
            f"\u6700\u65b0\u6301\u4ed3\u6570 {int(last.get('position_count') or 0)}\u3002"
        )

    recommendations = [
        "\u7ee7\u7eed\u89c2\u5bdf\u6bcf\u7b14\u989c\u9a70\u4fe1\u53f7\u89e6\u53d1\u540e\u7684 1R\u30012R \u5230\u8fbe\u60c5\u51b5\uff0c\u628a\u672a\u5230\u76ee\u6807\u7684\u539f\u56e0\u8bb0\u5f55\u5230\u590d\u76d8\u8868\u3002",
        "\u65b0\u5f00\u4ed3\u5df2\u653e\u5bbd\u5230 10 \u5355\u4e0a\u9650\uff0c\u5efa\u8bae\u91cd\u70b9\u5173\u6ce8\u76f8\u5173\u6027\u8fc7\u9ad8\u7684\u540c\u5411\u6301\u4ed3\uff0c\u907f\u514d\u884c\u60c5\u5355\u8fb9\u53cd\u62bd\u65f6\u96c6\u4e2d\u56de\u64a4\u3002",
        "\u5982\u679c\u8981\u505a\u7cbe\u786e\u80dc\u7387\u548c R \u500d\u6570\u7edf\u8ba1\uff0c\u9700\u8981\u5728\u5e73\u4ed3\u65f6\u56de\u5199\u5b9e\u9645\u6210\u4ea4\u76c8\u4e8f\u3001\u624b\u7eed\u8d39\u548c\u51fa\u573a\u539f\u56e0\u3002",
    ]
    return {'summary': summary, 'insights': insights, 'recommendations': recommendations}


DailyReport.to_text = _wc_report_to_text
DailyReport.to_markdown = _wc_report_to_markdown
DailyReviewModule._generate_local_analysis = _wc_generate_local_analysis


def _wc_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _wc_parse_json(value: Any, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _wc_symbol_key(symbol: Any) -> str:
    import re
    text = re.sub(r"[^A-Z0-9]", "", str(symbol or "").upper())
    while text.endswith("USDTUSDT"):
        text = text[:-4]
    return text


def _wc_latest_position_map(stats: DailyStats) -> Dict[str, Dict[str, Any]]:
    if not stats.snapshots:
        return {}
    latest = stats.snapshots[-1]
    raw = _wc_parse_json(latest.get("raw_data"), {}) or {}
    positions = raw.get("positions", []) if isinstance(raw, dict) else []
    return {_wc_symbol_key(pos.get("symbol")): pos for pos in positions}


def _wc_pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.2f}%"


def _wc_num(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "-"
    if value == float("inf"):
        return "∞"
    return f"{value:.{digits}f}"


def _wc_rate(wins: int, total: int) -> str:
    return "-" if total <= 0 else f"{wins}/{total} ({wins / total:.1%})"


def _wc_review_records(stats: DailyStats) -> List[Dict[str, Any]]:
    position_map = _wc_latest_position_map(stats)
    records = []
    for trade in stats.journal:
        symbol = str(trade.get("symbol") or "")
        key = _wc_symbol_key(symbol)
        pos = position_map.get(key)
        direction = str(trade.get("direction") or "").upper()
        entry = _wc_float(trade.get("entry_price"), None)
        stop = _wc_float(trade.get("stop_loss"), None)
        tps = _wc_parse_json(trade.get("take_profit_levels"), []) or []
        tp1 = _wc_float(tps[0], None) if tps else None
        mark = _wc_float(pos.get("mark_price"), None) if pos else None
        unrealized = _wc_float(pos.get("unrealized_pnl"), None) if pos else None
        net_pnl = _wc_float(trade.get("net_pnl"), 0.0)
        is_open = pos is not None
        settled = (not is_open) and abs(net_pnl) > 0

        risk_unit = abs(entry - stop) if entry is not None and stop is not None else None
        tp1_unit = abs(tp1 - entry) if entry is not None and tp1 is not None else None
        plan_rr = (tp1_unit / risk_unit) if risk_unit and tp1_unit is not None else None
        risk_pct = (risk_unit / entry) if risk_unit and entry else None
        tp1_pct = (tp1_unit / entry) if tp1_unit is not None and entry else None

        current_r = None
        move_pct = None
        if entry is not None and mark is not None:
            favorable_move = (mark - entry) if direction == "LONG" else (entry - mark)
            move_pct = favorable_move / entry if entry else None
            current_r = favorable_move / risk_unit if risk_unit else None

        records.append({
            "symbol": symbol,
            "direction": direction or "-",
            "entry": entry,
            "stop": stop,
            "tp1": tp1,
            "tps": tps,
            "risk_level": trade.get("risk_level", ""),
            "reason": (trade.get("setup_reason") or trade.get("signal_reason") or "")[:120],
            "mark": mark,
            "unrealized": unrealized,
            "net_pnl": net_pnl,
            "is_open": is_open,
            "settled": settled,
            "risk_pct": risk_pct,
            "tp1_pct": tp1_pct,
            "plan_rr": plan_rr,
            "current_r": current_r,
            "move_pct": move_pct,
            "entry_location": (
                f"距止损 {_wc_pct(risk_pct)}，距TP1 {_wc_pct(tp1_pct)}，计划RR {_wc_num(plan_rr)}"
                if entry is not None else "-"
            ),
        })
    return records


def _wc_metrics_for(records: List[Dict[str, Any]], direction: Optional[str] = None) -> Dict[str, Any]:
    subset = [r for r in records if direction is None or r["direction"] == direction]
    settled = [r for r in subset if r["settled"]]
    settled_wins = [r for r in settled if r["net_pnl"] > 0]
    settled_losses = [r for r in settled if r["net_pnl"] < 0]
    open_rows = [r for r in subset if r["is_open"] and r["unrealized"] is not None]
    open_wins = [r for r in open_rows if r["unrealized"] > 0]
    open_losses = [r for r in open_rows if r["unrealized"] < 0]
    settled_profit = sum(r["net_pnl"] for r in settled_wins)
    settled_loss = abs(sum(r["net_pnl"] for r in settled_losses))
    open_profit = sum(r["unrealized"] for r in open_wins)
    open_loss = abs(sum(r["unrealized"] for r in open_losses))
    return {
        "total": len(subset),
        "settled_total": len(settled),
        "settled_wins": len(settled_wins),
        "settled_pf": (float("inf") if settled_profit > 0 and settled_loss == 0 else settled_profit / settled_loss if settled_loss else None),
        "open_total": len(open_rows),
        "open_wins": len(open_wins),
        "open_pf": (float("inf") if open_profit > 0 and open_loss == 0 else open_profit / open_loss if open_loss else None),
        "open_pnl": sum(r["unrealized"] or 0.0 for r in open_rows),
    }


def _wc_build_review_payload(stats: DailyStats) -> Dict[str, Any]:
    records = _wc_review_records(stats)
    metrics = {
        "ALL": _wc_metrics_for(records),
        "LONG": _wc_metrics_for(records, "LONG"),
        "SHORT": _wc_metrics_for(records, "SHORT"),
    }
    stats.review_records = records
    stats.review_metrics = metrics
    return {"records": records, "metrics": metrics}


def _wc_generate_local_analysis_v2(self, stats: DailyStats, date: str) -> Dict[str, Any]:
    payload = _wc_build_review_payload(stats)
    records = payload["records"]
    metrics = payload["metrics"]
    all_m = metrics["ALL"]
    long_m = metrics["LONG"]
    short_m = metrics["SHORT"]

    summary = (
        f"{date} 共记录 {len(records)} 笔复盘交易；已平仓样本 {all_m['settled_total']} 笔，"
        f"当前持仓样本 {all_m['open_total']} 笔，浮动盈亏合计 ${all_m['open_pnl']:.4f}。"
    )
    insights = [
        "复盘口径已统一：真实胜率/真实盈亏比只统计已平仓订单；未平仓订单单独统计当前浮动表现。",
        f"真实胜率: {_wc_rate(all_m['settled_wins'], all_m['settled_total'])}；真实盈亏比: {_wc_num(all_m['settled_pf'])}。",
        f"当前浮动胜率: {_wc_rate(all_m['open_wins'], all_m['open_total'])}；当前浮动盈亏比: {_wc_num(all_m['open_pf'])}。",
        f"多单浮动胜率: {_wc_rate(long_m['open_wins'], long_m['open_total'])}；空单浮动胜率: {_wc_rate(short_m['open_wins'], short_m['open_total'])}。",
    ]
    if records:
        best = max((r for r in records if r["current_r"] is not None), key=lambda r: r["current_r"], default=None)
        worst = min((r for r in records if r["current_r"] is not None), key=lambda r: r["current_r"], default=None)
        if best:
            insights.append(f"当前表现最好: {best['symbol']}，{_wc_num(best['current_r'])}R，浮盈亏 ${_wc_num(best['unrealized'], 4)}。")
        if worst and worst is not best:
            insights.append(f"当前表现最弱: {worst['symbol']}，{_wc_num(worst['current_r'])}R，浮盈亏 ${_wc_num(worst['unrealized'], 4)}。")

    recommendations = [
        "每次复盘先看真实已平仓指标，再看未平仓浮动指标，避免把运行中的订单提前计入胜率。",
        "重点跟踪入场后是否快速接近 1R；如果多笔订单长期停在 0R 附近，要复查入场位置是否追得太晚。",
        "平仓时需要回写实际出场价、出场原因、手续费和净盈亏，这样真实多空胜率与真实盈亏比才会越来越准。",
    ]
    return {"summary": summary, "insights": insights, "recommendations": recommendations}


def _wc_report_to_markdown_v2(self) -> str:
    payload = _wc_build_review_payload(self.stats)
    records = payload["records"]
    metrics = payload["metrics"]
    all_m = metrics["ALL"]
    long_m = metrics["LONG"]
    short_m = metrics["SHORT"]

    metric_rows = [
        ("已平仓样本", all_m["settled_total"]),
        ("真实胜率", _wc_rate(all_m["settled_wins"], all_m["settled_total"])),
        ("真实盈亏比", _wc_num(all_m["settled_pf"])),
        ("当前持仓样本", all_m["open_total"]),
        ("当前浮动胜率", _wc_rate(all_m["open_wins"], all_m["open_total"])),
        ("当前浮动盈亏比", _wc_num(all_m["open_pf"])),
        ("多单浮动胜率", _wc_rate(long_m["open_wins"], long_m["open_total"])),
        ("空单浮动胜率", _wc_rate(short_m["open_wins"], short_m["open_total"])),
        ("当前浮动盈亏合计", f"${all_m['open_pnl']:.4f}"),
    ]
    metrics_table = "\n".join(f"| {name} | {value} |" for name, value in metric_rows)

    trade_lines = []
    for r in records[:30]:
        performance = "-"
        if r["is_open"] and r["unrealized"] is not None:
            performance = f"{_wc_pct(r['move_pct'])} / {_wc_num(r['current_r'])}R / ${_wc_num(r['unrealized'], 4)}"
        elif r["settled"]:
            performance = f"已平仓 / ${_wc_num(r['net_pnl'], 4)}"
        cells = [
            r["symbol"],
            r["direction"],
            _wc_num(r["entry"], 6),
            r["entry_location"],
            _wc_num(r["mark"], 6),
            performance,
            _wc_num(r["stop"], 6),
            _wc_num(r["tp1"], 6),
            r["risk_level"],
            r["reason"],
        ]
        trade_lines.append("| " + " | ".join(_wc_review_cell(c) for c in cells) + " |")
    trade_table = "\n".join(trade_lines) if trade_lines else "| 暂无 | - | - | - | - | - | - | - | - | - |"

    return f"""# 旺财交易复盘 - {self.date}

> 生成时间: {self.generated_at.strftime('%Y-%m-%d %H:%M:%S')}

## 统一指标

| 指标 | 数值 |
|------|------|
{metrics_table}

## 订单表现

| 标的 | 方向 | 入场价 | 入场位置 | 当前价 | 入场后表现 | 止损 | TP1 | 风控 | 信号原因 |
|------|------|--------|----------|--------|------------|------|-----|------|----------|
{trade_table}

## 关键观察

{chr(10).join(f"{i}. {insight}" for i, insight in enumerate(self.insights, 1))}

## 后续建议

{chr(10).join(f"{i}. {rec}" for i, rec in enumerate(self.recommendations, 1))}

## 总结

{self.summary}
"""


def _wc_report_to_text_v2(self) -> str:
    payload = _wc_build_review_payload(self.stats)
    all_m = payload["metrics"]["ALL"]
    lines = [
        f"旺财交易复盘 [{self.date}]",
        f"生成时间: {self.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"真实胜率: {_wc_rate(all_m['settled_wins'], all_m['settled_total'])}",
        f"真实盈亏比: {_wc_num(all_m['settled_pf'])}",
        f"当前浮动胜率: {_wc_rate(all_m['open_wins'], all_m['open_total'])}",
        f"当前浮动盈亏比: {_wc_num(all_m['open_pf'])}",
        "",
        "关键观察",
    ]
    lines.extend(f"{i}. {insight}" for i, insight in enumerate(self.insights, 1))
    lines.extend(["", "后续建议"])
    lines.extend(f"{i}. {rec}" for i, rec in enumerate(self.recommendations, 1))
    lines.extend(["", "总结", self.summary])
    return "\n".join(lines)


DailyReviewModule._generate_local_analysis = _wc_generate_local_analysis_v2
DailyReport.to_markdown = _wc_report_to_markdown_v2
DailyReport.to_text = _wc_report_to_text_v2
