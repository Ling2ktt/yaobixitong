# 🐕 旺财自动交易系统

> **WangCai Auto Trading System**
> 
> Binance + OKX 双源互补 | AI 驱动决策 | 硬编码风控 | 模块独立可调教

---

## 📋 系统架构

按照流程图实现8个独立模块：

```
┌─────────────────────────────────────────┐
│         AI Agent 主循环                  │
│        （运行在云服务器上）               │
└─────────────────────────────────────────┘
                    │
    ┌───────────────┼───────────────┐
    ▼               ▼               ▼
┌─────────┐   ┌─────────┐   ┌─────────┐
│ 1.行情  │   │ 2.信息  │   │ 3.账户  │
│ K线/深度│   │ 新闻/链上│   │ 持仓/盈亏│
└─────────┘   └─────────┘   └─────────┘
    │               │               │
    └───────────────┼───────────────┘
                    ▼
    ┌─────────────────────────────────┐
    │ 4. AI 综合判断                   │
    │ 调用大模型API (Claude/GPT 等)    │
    │ 输入: 行情 + 信息 + 持仓 + 策略   │
    │ 输出: 交易决策                   │
    └─────────────────────────────────┘
                    │
                    ▼
    ┌─────────────────────────────────┐
    │ 5. 风控审核 (硬编码规则)          │
    │ 单笔限额 / 日亏损 / 持仓数 / 熔断 │
    └─────────────────────────────────┘
                    │ 通过
                    ▼
    ┌─────────────────────────────────┐
    │ 6. 调用币安/OKX API 下单          │
    │ 现货 / 合约 / 杠杆                │
    └─────────────────────────────────┘
                    │
                    ▼
    ┌─────────────────────────────────┐
    │ 7. 记录与通知                     │
    │ 写入数据库 + 推送告警              │
    └─────────────────────────────────┘
                    │
                    └────── 回到第 1 步 ──────▶
                    │
                    ▼
    ┌─────────────────────────────────┐
    │ 8. 每日复盘                       │
    │ AI 自动生成日报                   │
    └─────────────────────────────────┘
```

---

## 🗂️ 项目结构

```
wangcai-trading-bot/
├── main.py                    # 入口文件
├── requirements.txt           # Python依赖
├── Dockerfile                 # Docker镜像
├── docker-compose.yml         # Docker编排
├── .env.example               # 环境变量模板
│
├── config/
│   └── system.yaml            # 主配置文件
│
├── core/
│   ├── config_loader.py       # 配置加载器
│   └── engine.py              # 核心引擎（主循环）
│
├── modules/                   # 8个独立可调教模块
│   ├── __init__.py
│   ├── market_data.py         # 1. 行情数据采集（双源互补）
│   ├── info_aggregator.py     # 2. 信息聚合
│   ├── account_manager.py     # 3. 账户管理
│   ├── ai_decision.py         # 4. AI 决策
│   ├── risk_control.py        # 5. 风控审核
│   ├── order_executor.py      # 6. 订单执行
│   ├── logger_notifier.py     # 7. 记录与通知
│   └── daily_review.py        # 8. 每日复盘
│
├── deploy/                    # 部署脚本
│   ├── aliyun-deploy.sh       # 阿里云部署
│   ├── update.sh              # 更新脚本
│   └── backup.sh              # 备份脚本
│
└── data/                      # 数据目录（运行后生成）
    ├── wangcai.db             # SQLite数据库
    └── ...
```

---

## 🚀 快速开始

### 1. 克隆项目

```bash
git clone <项目地址>
cd wangcai-trading-bot
```

### 2. 配置环境变量

```bash
cp .env.example .env
vim .env  # 填写你的API密钥
```

需要配置的密钥：
- **Binance API**: `BINANCE_API_KEY`, `BINANCE_API_SECRET`
- **OKX API**: `OKX_API_KEY`, `OKX_API_SECRET`, `OKX_PASSPHRASE`
- **AI API**: `AI_API_KEY` (OpenAI 或 Anthropic)
- **通知** (可选): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 运行

```bash
# 模拟模式（默认）
python main.py

# 实盘模式（⚠️ 谨慎！）
python main.py --mode live

# 测试各模块
python main.py --test-modules

# 查看状态
python main.py --status
```

---

## 🐳 Docker 部署

```bash
# 构建并启动
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止
docker-compose down
```

---

## ☁️ 阿里云轻量服务器部署

### 一键部署

```bash
# 1. 在阿里云轻量服务器上运行
wget https://your-domain.com/deploy/aliyun-deploy.sh
sudo bash aliyun-deploy.sh

# 2. 上传项目代码
scp -r ./wangcai-trading-bot root@你的服务器IP:/opt/wangcai/

# 3. 配置环境变量
ssh root@你的服务器IP
cd /opt/wangcai
cp .env.example .env
vim .env  # 填写API密钥

# 4. 启动
docker-compose up -d
```

### 管理命令

```bash
# 查看状态
cd /opt/wangcai
docker-compose ps
docker-compose logs -f

# 更新
cd /opt/wangcai
bash deploy/update.sh

# 备份
cd /opt/wangcai
bash deploy/backup.sh
```

---

## ⚙️ 模块调教指南

每个模块都有独立的配置项，可在 `config/system.yaml` 中调整：

### 模块 1: 行情数据
```yaml
exchanges:
  binance_weight: 0.5    # Binance数据权重
  okx_weight: 0.5        # OKX数据权重
  timeframes: ['5m', '15m', '1h', '4h', '1d']  # K线周期
```

### 模块 4: AI 决策
```yaml
ai:
  provider: "openai"     # 或 anthropic
  model: "gpt-4"
  temperature: 0.3       # 决策随机性
  strategy_style: "balanced"  # conservative / balanced / aggressive
```

### 模块 5: 风控
```yaml
risk:
  max_single_order_usdt: 1000    # 单笔限额
  max_daily_loss_usdt: 3000      # 日亏损限额
  max_positions: 5               # 最大持仓数
  circuit_breaker:
    enabled: true
    consecutive_losses: 3        # 连续亏损熔断
    cooldown_minutes: 30         # 冷却时间
```

---

## 📊 监控

- **健康检查**: http://服务器IP:8080
- **Prometheus指标**: http://服务器IP:9090

---

## ⚠️ 风险提示

1. **⚡ 实盘交易前务必充分测试**
2. **💰 建议先用模拟模式运行至少1周**
3. **🔑 妥善保管API密钥，使用IP白名单**
4. **📉 加密货币交易风险极高，可能损失全部本金**
5. **🛡️ 本系统仅作为工具，不构成投资建议**

---

## 📜 License

MIT License

---

> 🐕 **旺财祝你交易顺利！** 
