"""
模块 7: 记录与通知 (Logger & Notifier Module)
功能: 交易记录写入数据库 + 多渠道告警推送
可调教项: 通知渠道、告警级别、记录字段、推送模板
"""

import asyncio
import json
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from loguru import logger
import sqlite3


class AlertLevel(Enum):
    """告警级别"""
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class NotifyChannel(Enum):
    """通知渠道"""
    CONSOLE = "console"
    TELEGRAM = "telegram"
    WEBHOOK = "webhook"
    FILE = "file"


@dataclass
class AlertMessage:
    """告警消息"""
    level: AlertLevel
    title: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    tags: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    
    def to_text(self) -> str:
        """文本格式"""
        emoji = {
            AlertLevel.INFO: "ℹ️",
            AlertLevel.WARNING: "⚠️",
            AlertLevel.ERROR: "❌",
            AlertLevel.CRITICAL: "🚨"
        }
        return f"{emoji.get(self.level, '📌')} [{self.level.value}] {self.title}\n{self.content}"
    
    def to_markdown(self) -> str:
        """Markdown格式"""
        return f"""**[{self.level.value}] {self.title}**
_{self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}_

{self.content}

{', '.join(f'`#{tag}`' for tag in self.tags)}
"""


class LoggerNotifierModule:
    """
    记录与通知模块 - 可单独调教
    
    配置项：
    - database: 数据库配置
    - channels: 启用的通知渠道
    - alert_levels: 推送的最低告警级别
    - templates: 消息模板
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.db_config = config.get('database', {})
        self.channels = config.get('channels', ['console'])
        self.min_level = AlertLevel(config.get('min_alert_level', 'INFO'))
        
        # 初始化数据库
        self._init_database()
        
        # 初始化通知渠道
        self._telegram_bot = None
        self._webhook_url = config.get('webhook', {}).get('url', '')
        
        if 'telegram' in self.channels:
            self._init_telegram()
        
        logger.info("[LoggerNotifier] 记录通知模块初始化 | 渠道: {} | 最低级别: {}",
                   self.channels, self.min_level.value)
    
    def _init_database(self):
        """初始化数据库"""
        db_type = self.db_config.get('type', 'sqlite')
        
        if db_type == 'sqlite':
            db_path = Path(self.db_config.get('sqlite_path', 'data/wangcai.db'))
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path = str(db_path)
            self._init_sqlite()
        else:
            logger.error("[LoggerNotifier] ❌ 不支持的数据库类型: {}，无法持久化交易记录", db_type)
            self.db_path = None  # Fix: 不静默回退到 :memory:，调用者需检查
    
    def _init_sqlite(self):
        """初始化SQLite表结构"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 交易记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                order_id TEXT,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                side TEXT,
                amount REAL,
                price REAL,
                filled_amount REAL,
                average_price REAL,
                fee REAL,
                pnl REAL,
                exchange TEXT,
                status TEXT,
                raw_data TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 决策记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                decision_id TEXT,
                action TEXT NOT NULL,
                symbol TEXT,
                amount REAL,
                price REAL,
                confidence REAL,
                reason TEXT,
                strategy TEXT,
                ai_response TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 风控记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS risk_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                decision_id TEXT,
                overall_level TEXT,
                is_passed INTEGER,
                checks_detail TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 告警记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                title TEXT,
                content TEXT,
                channels TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 账户快照表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS account_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                exchange TEXT,
                total_equity REAL,
                available_usdt REAL,
                position_count INTEGER,
                daily_pnl REAL,
                total_pnl REAL,
                raw_data TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 市场数据表（精简）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT,
                exchange TEXT,
                price REAL,
                bid REAL,
                ask REAL,
                volume_24h REAL,
                change_24h_pct REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("[LoggerNotifier] 数据库初始化完成: {}", self.db_path)
    
    def _init_telegram(self):
        """初始化Telegram Bot"""
        try:
            from telegram import Bot
            token = self.config.get('telegram', {}).get('bot_token', '')
            if token:
                self._telegram_bot = Bot(token=token)
                logger.info("[LoggerNotifier] Telegram Bot 初始化成功")
        except ImportError:
            logger.warning("[LoggerNotifier] python-telegram-bot 未安装")
        except Exception as e:
            logger.error("[LoggerNotifier] Telegram Bot 初始化失败: {}", e)
    
    # ========== 数据库操作 ==========
    
    def save_trade(self, trade: Dict[str, Any]):
        """保存交易记录"""
        if not self.db_path:  # Fix: 数据库未初始化时跳过
            return
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO trades 
                (timestamp, order_id, symbol, action, side, amount, price, 
                 filled_amount, average_price, fee, pnl, exchange, status, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade.get('timestamp', datetime.now().isoformat()),
                trade.get('order_id', ''),
                trade.get('symbol', ''),
                trade.get('action', ''),
                trade.get('side', ''),
                trade.get('amount', 0),
                trade.get('price', 0),
                trade.get('filled_amount', 0),
                trade.get('average_price', 0),
                trade.get('fee', 0),
                trade.get('pnl', 0),
                trade.get('exchange', ''),
                trade.get('status', ''),
                json.dumps(trade.get('raw', {}))
            ))
            
            conn.commit()
            conn.close()
            logger.debug("[LoggerNotifier] 交易记录已保存")
        except Exception as e:
            logger.error("[LoggerNotifier] 保存交易记录失败: {}", e)
    
    def save_decision(self, decision: Dict[str, Any]):
        """保存决策记录"""
        if not self.db_path:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO decisions 
                (timestamp, decision_id, action, symbol, amount, price, 
                 confidence, reason, strategy, ai_response)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                decision.get('timestamp', datetime.now().isoformat()),
                decision.get('id', ''),
                decision.get('action', ''),
                decision.get('symbol', ''),
                decision.get('amount', 0),
                decision.get('price', 0),
                decision.get('confidence', 0),
                decision.get('reason', ''),
                decision.get('strategy', ''),
                decision.get('raw_response', '')[:2000]  # 限制长度
            ))
            
            conn.commit()
            conn.close()
            logger.debug("[LoggerNotifier] 决策记录已保存")
        except Exception as e:
            logger.error("[LoggerNotifier] 保存决策记录失败: {}", e)
    
    def save_risk_check(self, risk_report: Dict[str, Any]):
        """保存风控记录"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO risk_checks 
                (timestamp, decision_id, overall_level, is_passed, checks_detail)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                risk_report.get('timestamp', datetime.now().isoformat()),
                risk_report.get('decision_id', ''),
                risk_report.get('overall_level', ''),
                1 if risk_report.get('is_passed', False) else 0,
                json.dumps(risk_report.get('checks', []))
            ))
            
            conn.commit()
            conn.close()
            logger.debug("[LoggerNotifier] 风控记录已保存")
        except Exception as e:
            logger.error("[LoggerNotifier] 保存风控记录失败: {}", e)
    
    def save_account_snapshot(self, snapshot: Dict[str, Any]):
        """保存账户快照"""
        if not self.db_path:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            for ex, data in snapshot.get('accounts', {}).items():
                cursor.execute('''
                    INSERT INTO account_snapshots 
                    (timestamp, exchange, total_equity, available_usdt, 
                     position_count, daily_pnl, total_pnl, raw_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    snapshot.get('timestamp', datetime.now().isoformat()),
                    ex,
                    data.get('total_equity_usdt', 0),
                    data.get('available_usdt', 0),
                    data.get('position_count', 0),
                    data.get('daily_pnl', 0),
                    data.get('total_pnl', 0),
                    json.dumps(data)
                ))
            
            conn.commit()
            conn.close()
            logger.debug("[LoggerNotifier] 账户快照已保存")
        except Exception as e:
            logger.error("[LoggerNotifier] 保存账户快照失败: {}", e)
    
    # ========== 通知推送 ==========
    
    async def notify(self, alert: AlertMessage):
        """发送通知到所有启用渠道"""
        # 过滤级别
        level_order = {
            AlertLevel.INFO: 0,
            AlertLevel.WARNING: 1,
            AlertLevel.ERROR: 2,
            AlertLevel.CRITICAL: 3
        }
        if level_order.get(alert.level, 0) < level_order.get(self.min_level, 0):
            return
        
        # 保存到数据库
        self._save_alert(alert)
        
        # 推送到各渠道
        tasks = []
        for channel in self.channels:
            if channel == 'console':
                self._notify_console(alert)
            elif channel == 'telegram':
                tasks.append(self._notify_telegram(alert))
            elif channel == 'webhook':
                tasks.append(self._notify_webhook(alert))
            elif channel == 'file':
                self._notify_file(alert)
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    def _save_alert(self, alert: AlertMessage):
        """保存告警到数据库"""
        if not self.db_path:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO alerts (timestamp, level, title, content, channels)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                alert.timestamp.isoformat(),
                alert.level.value,
                alert.title,
                alert.content,
                json.dumps(self.channels)
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("[LoggerNotifier] 保存告警失败: {}", e)
    
    def _notify_console(self, alert: AlertMessage):
        """控制台通知"""
        text = alert.to_text()
        if alert.level == AlertLevel.CRITICAL:
            logger.critical(text)
        elif alert.level == AlertLevel.ERROR:
            logger.error(text)
        elif alert.level == AlertLevel.WARNING:
            logger.warning(text)
        else:
            logger.info(text)
    
    async def _notify_telegram(self, alert: AlertMessage):
        """Telegram通知"""
        if not self._telegram_bot:
            return
        
        try:
            chat_id = self.config.get('telegram', {}).get('chat_id', '')
            if not chat_id:
                return
            
            text = alert.to_markdown()
            # 截断长消息
            if len(text) > 4000:
                text = text[:4000] + "\n\n... (消息已截断)"
            
            await self._telegram_bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode='Markdown'
            )
            logger.debug("[LoggerNotifier] Telegram通知已发送")
        except Exception as e:
            logger.error("[LoggerNotifier] Telegram通知失败: {}", e)
    
    async def _notify_webhook(self, alert: AlertMessage):
        """Webhook通知"""
        if not self._webhook_url:
            return
        
        try:
            import aiohttp
            payload = {
                'level': alert.level.value,
                'title': alert.title,
                'content': alert.content,
                'timestamp': alert.timestamp.isoformat(),
                'tags': alert.tags,
                'data': alert.data
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        logger.debug("[LoggerNotifier] Webhook通知已发送")
                    else:
                        logger.warning("[LoggerNotifier] Webhook返回状态: {}", resp.status)
        except Exception as e:
            logger.error("[LoggerNotifier] Webhook通知失败: {}", e)
    
    def _notify_file(self, alert: AlertMessage):
        """文件通知（追加日志）"""
        try:
            log_dir = Path('logs')
            log_dir.mkdir(exist_ok=True)
            
            log_file = log_dir / f"alerts_{datetime.now().strftime('%Y%m%d')}.log"
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(f"[{alert.timestamp.isoformat()}] [{alert.level.value}] {alert.title}\n")
                f.write(f"{alert.content}\n")
                f.write("-" * 50 + "\n")
        except Exception as e:
            logger.error("[LoggerNotifier] 文件通知失败: {}", e)
    
    # ========== 快捷方法 ==========
    
    async def notify_trade(self, trade: Dict[str, Any]):
        """交易结果通知"""
        symbol = trade.get('symbol', '')
        action = trade.get('action', '')
        pnl = trade.get('pnl', 0)
        
        level = AlertLevel.INFO
        if pnl < 0:
            level = AlertLevel.WARNING if pnl > -100 else AlertLevel.ERROR
        elif pnl > 0:
            level = AlertLevel.INFO
        
        alert = AlertMessage(
            level=level,
            title=f"交易执行: {action} {symbol}",
            content=f"""交易所: {trade.get('exchange', 'N/A')}
订单ID: {trade.get('order_id', 'N/A')}
数量: {trade.get('amount', 0)}
成交价: {trade.get('average_price', 0)}
手续费: {trade.get('fee', 0)}
盈亏: {pnl:.2f}""",
            tags=['trade', symbol, action.lower()]
        )
        
        await self.notify(alert)
    
    async def notify_decision(self, decision: Dict[str, Any]):
        """AI决策通知"""
        alert = AlertMessage(
            level=AlertLevel.INFO,
            title=f"AI决策: {decision.get('action', 'HOLD')} {decision.get('symbol', '')}",
            content=f"""置信度: {decision.get('confidence', 0):.2f}
策略: {decision.get('strategy', 'default')}
理由: {decision.get('reason', 'N/A')}""",
            tags=['decision', decision.get('symbol', '')]
        )
        
        await self.notify(alert)
    
    async def notify_risk(self, risk_report: Dict[str, Any]):
        """风控通知"""
        level = AlertLevel.INFO if risk_report.get('is_passed') else AlertLevel.WARNING
        if risk_report.get('overall_level') == 'CIRCUIT_BREAKER':
            level = AlertLevel.CRITICAL
        
        checks = risk_report.get('checks', [])
        failed = [c for c in checks if not c.get('passed', True)]
        
        alert = AlertMessage(
            level=level,
            title=f"风控审核: {'通过' if risk_report.get('is_passed') else '拒绝'}",
            content=f"总体结果: {risk_report.get('overall_level', 'N/A')}\n" +
                   (f"未通过项: {len(failed)}\n" if failed else ""),
            tags=['risk']
        )
        
        await self.notify(alert)
    
    def query_trades(self, symbol: Optional[str] = None, 
                    limit: int = 100) -> List[Dict]:
        """查询交易记录"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            if symbol:
                cursor.execute('''
                    SELECT * FROM trades 
                    WHERE symbol = ? 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                ''', (symbol, limit))
            else:
                cursor.execute('''
                    SELECT * FROM trades 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                ''', (limit,))
            
            rows = cursor.fetchall()
            conn.close()
            
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error("[LoggerNotifier] 查询交易记录失败: {}", e)
            return []
