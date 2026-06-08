#!/usr/bin/env python3
"""旺财 BTC QuantTrend 策略 - Web 仪表盘"""
import json, os, sys, time, subprocess, threading
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template_string, jsonify, request, abort
from loguru import logger
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

app = Flask(__name__)
DB = ROOT / "data" / "wangcai.db"
LOG = ROOT / "logs" / "wangcai.log"
LOGDIR = ROOT / "logs"

# ── 认证配置：设置环境变量 WEB_AUTH_TOKEN 启用密码保护 ──
AUTH_TOKEN = os.environ.get("WEB_AUTH_TOKEN", "")
_market_cache_lock = threading.Lock()

def require_auth():
    """简易Token认证装饰器（localhost/内网免认证）"""
    if not AUTH_TOKEN:
        return  # 未配置时不启用认证
    # localhost / 内网请求免认证（watchdog/前端直连）
    if request.remote_addr in ('127.0.0.1', '::1', 'localhost'):
        return
    token = request.headers.get("X-Auth-Token") or request.args.get("token", "")
    if token != AUTH_TOKEN:
        abort(401, description="未授权，请提供有效的 X-Auth-Token 或 ?token= 参数")

@app.before_request
def _check_auth():
    """所有 /api/ 路由需要认证（主页和静态文件除外）"""
    if request.path.startswith('/api/') and AUTH_TOKEN:
        require_auth()

# ── 11 模块 ──
MODULES = [
    {"id":"engine","name":"核心引擎","icon":"🧠","desc":"AI Agent 4H 主循环"},
    {"id":"config_loader","name":"配置加载","icon":"⚙️","desc":"YAML + 环境变量"},
    {"id":"market_data","name":"行情采集","icon":"📡","desc":"Binance+OKX BTC/USDT"},
    {"id":"strategy","name":"QuantTrend 策略","icon":"📈","desc":"三EMA趋势评分+ATR止损"},
    {"id":"risk_control","name":"风控审核","icon":"🛡️","desc":"硬编码限额+熔断"},
    {"id":"order_executor","name":"订单执行","icon":"📤","desc":"Binance/OKX 下单"},
    {"id":"account_mgr","name":"账户管理","icon":"💰","desc":"持仓/盈亏/净值"},
    {"id":"info_agg","name":"信息聚合","icon":"📰","desc":"恐惧贪婪+资金费率"},
    {"id":"logger","name":"日志通知","icon":"📝","desc":"SQLite+多渠道推送"},
    {"id":"ai_decision","name":"AI 决策","icon":"🧠","desc":"DeepSeek 大模型综合判断"},
    {"id":"daily_review","name":"每日复盘","icon":"📊","desc":"AI 生成日报"},
]

def get_db():
    import sqlite3
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    return conn

def is_running():
    try:
        project_main = (str(ROOT / "main.py")).lower().replace('\\', '/')
        project_dir = (str(ROOT)).lower().replace('\\', '/')
        import psutil
        for p in psutil.process_iter(["pid","cmdline","cwd"]):
            try:
                info = p.info
                if info["cmdline"]:
                    cmd = " ".join(info["cmdline"]).lower().replace('\\', '/')
                    cwd = (info.get("cwd") or "").lower().replace('\\', '/')
                    if "main.py" in cmd and (project_main in cmd or project_dir in cwd):
                        return True
            except: pass
    except: pass
    return False

def _kill_main_process():
    """跨平台（Windows/Linux）杀死 main.py 进程"""
    try:
        import psutil
        # Fix #8: 只杀本项目 main.py 进程
        project_main = str(ROOT / "main.py").lower().replace('/', '\\')
        for p in psutil.process_iter(["pid","cmdline","name"]):
            try:
                cmdline = " ".join(p.info["cmdline"] or []).lower()
                name = p.info["name"] or ""
                if "main.py" in cmdline and "python" in name.lower():
                    # 检查是否为本项目的 main.py
                    if project_main.replace('\\', '/') in cmdline or project_main in cmdline:
                        p.kill()
                        logger.info(f"[Web] 已终止进程 {p.info['pid']}: {name}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except ImportError:
        # Fix #9: psutil 不可用时，提示用户手动关闭而非盲目杀进程
        logger.error("[Web] psutil 未安装，无法自动终止进程。请手动关闭 main.py")
        logger.error("[Web] pip install psutil 可解决此问题")

def module_status():
    running = is_running()
    results = []
    for m in MODULES:
        f = ROOT / m["id"].replace("_","/") + ".py" if "/" in m["id"] else None
        r = {"id":m["id"],"name":m["name"],"icon":m["icon"],"desc":m["desc"]}
        if m["id"] == "ai_decision":
            cfg = load_config()
            dm = cfg.get("system", {}).get("decision_mode", "ai")
            if dm == "ai":
                r["status"] = "ok" if running else "stopped"
                r["message"] = "AI大模型 (DeepSeek)"
            else:
                r["status"] = "ok" if running else "stopped"
                r["message"] = f"模式: {dm}"
        elif not running:
            r["status"] = "stopped"
            r["message"] = "旺财未运行"
        else:
            r["status"] = "ok"
            r["message"] = "运行中"
        results.append(r)
    return results

def load_config():
    p = ROOT / "config" / "system.yaml"
    if p.exists():
        import yaml
        with open(p,"r",encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
            return cfg if cfg else {}
    return {}

# ═══════════════════════ HTML ═══════════════════════
HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>旺财 · BTC QuantTrend 策略</title>
<style>
:root{--bg:#07090d;--card:#0d1117;--border:#21262d;--text:#c9d1d9;--dim:#484f58;--gold:#f0b90b;--green:#3fb950;--red:#f85149;--blue:#58a6ff;--purple:#8b5cf6;}
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;}

/* Header */
.header{background:linear-gradient(180deg,var(--card),var(--bg));border-bottom:1px solid var(--border);padding:14px 36px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100;}
.header-l{display:flex;align-items:center;gap:14px;}
.header-l .logo{font-size:28px;}
.header-l h1{font-size:20px;font-weight:800;color:#fff;}
.header-l .pair{font-size:13px;font-weight:600;color:var(--gold);background:rgba(240,185,11,.12);padding:3px 10px;border-radius:6px;}
.header-l .ver{font-size:11px;color:var(--dim);}
.header-r{display:flex;align-items:center;gap:16px;}
.pill{padding:5px 14px;border-radius:14px;font-size:12px;font-weight:600;}
.pill-run{background:rgba(63,185,80,.12);color:var(--green);border:1px solid rgba(63,185,80,.3);}
.pill-stop{background:rgba(248,81,73,.12);color:var(--red);border:1px solid rgba(248,81,73,.3);}
.clock{color:var(--dim);font-size:12px;font-family:'SF Mono',Consolas,monospace;}

/* Container */
.container{max-width:1360px;margin:0 auto;padding:24px 36px;}

/* Toolbar */
.toolbar{display:flex;gap:10px;margin-bottom:24px;align-items:center;}
.btn{padding:8px 18px;border-radius:8px;border:none;cursor:pointer;font-size:13px;font-weight:600;transition:all .2s;}
.btn-primary{background:var(--gold);color:#000;}
.btn-primary:hover{opacity:.85;}
.btn-danger{background:var(--red);color:#fff;}
.btn-outline{background:transparent;color:var(--text);border:1px solid var(--border);}
.btn-outline:hover{background:var(--card);}

/* Section title */
.sec-title{font-size:14px;font-weight:700;color:#fff;margin-bottom:14px;display:flex;align-items:center;gap:8px;}
.sec-title::before{content:'';width:3px;height:14px;background:var(--gold);border-radius:2px;}

/* Strategy panel - KEY FOCUS AREA */
.strategy-panel{display:grid;grid-template-columns:1fr 1.6fr;gap:20px;margin-bottom:28px;}

/* ── 信号卡片 ── */
.signal-card{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;}
.signal-card .sig-top{display:flex;align-items:center;gap:12px;padding:18px 22px 0;}
.signal-card .sig-symbol{font-size:13px;font-weight:700;color:var(--gold);background:rgba(240,185,11,.1);padding:3px 10px;border-radius:5px;font-family:'SF Mono',Consolas,monospace;}
.signal-card .sig-dir{font-size:16px;font-weight:800;padding:4px 14px;border-radius:6px;margin-left:auto;}
.signal-card .sig-dir.long{color:var(--green);background:rgba(63,185,80,.12);}
.signal-card .sig-dir.short{color:var(--red);background:rgba(248,81,73,.12);}
.signal-card .sig-dir.hold{color:var(--dim);background:rgba(72,79,88,.1);}
.signal-card .sig-body{padding:16px 22px 20px;}
.signal-card .sig-price-row{display:flex;align-items:baseline;gap:10px;margin-bottom:6px;}
.signal-card .sig-price{font-size:30px;font-weight:800;color:#fff;letter-spacing:-1px;font-family:'SF Mono',Consolas,monospace;}
.signal-card .sig-score-badge{font-size:22px;font-weight:800;color:var(--gold);}
.signal-card .sig-score-label{font-size:11px;color:var(--dim);}
.signal-card .sig-reason{color:var(--text);font-size:12px;line-height:1.6;margin-top:10px;padding-top:10px;border-top:1px solid var(--border);}
.signal-card .sig-reason .sig-rr{color:var(--green);font-weight:600;}
.signal-card .sig-detail-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-top:12px;}
.signal-card .sig-detail-item{background:var(--bg);border-radius:6px;padding:10px 12px;text-align:center;}
.signal-card .sig-detail-item .sdl{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px;}
.signal-card .sig-detail-item .sdv{font-size:15px;font-weight:700;color:#fff;font-family:'SF Mono',Consolas,monospace;}
.signal-card .sig-bar{border-radius:4px;height:6px;background:var(--bg);margin-top:14px;overflow:hidden;}
.signal-card .sig-bar-fill{height:100%;border-radius:4px;transition:width .6s ease-out-quart;}
.signal-card .sig-bar-fill.strong{background:linear-gradient(90deg,var(--green),var(--gold));}
.signal-card .sig-bar-fill.good{background:linear-gradient(90deg,var(--gold),var(--gold));}
.signal-card .sig-bar-fill.weak{background:linear-gradient(90deg,var(--dim),var(--gold));}
.sig-empty{text-align:center;padding:40px 20px;color:var(--dim);font-size:13px;}

/* ── 持仓卡片 ── */
.pos-card{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;}
.pos-card .pos-header-bar{display:flex;align-items:center;padding:14px 20px;border-bottom:1px solid var(--border);font-size:13px;color:var(--dim);font-weight:600;text-transform:uppercase;letter-spacing:1px;}
.pos-card .pos-header-bar .pos-count{color:var(--gold);margin-left:6px;}
.pos-row{display:grid;grid-template-columns:90px 35px 65px 80px 75px 45px 75px 1fr;align-items:center;padding:12px 20px;font-size:12px;border-bottom:1px solid rgba(255,255,255,.03);transition:background .15s;}
.pos-row:hover{background:rgba(255,255,255,.02);}
.pos-row .pr-sym{font-weight:700;color:var(--gold);font-family:'SF Mono',Consolas,monospace;}
.pos-row .pr-side{font-size:11px;padding:2px 7px;border-radius:4px;font-weight:700;text-align:center;}
.pos-row .pr-side.long{color:var(--green);background:rgba(63,185,80,.15);}
.pos-row .pr-side.short{color:var(--red);background:rgba(248,81,73,.15);}
.pos-row .pr-size{font-family:'SF Mono',Consolas,monospace;color:var(--text);}
.pos-row .pr-pnl{font-family:'SF Mono',Consolas,monospace;font-weight:700;}
.pos-row .pr-pnl.pos{color:var(--green);}.pos-row .pr-pnl.neg{color:var(--red);}
.pos-row .pr-lev{font-family:'SF Mono',Consolas,monospace;color:var(--dim);font-size:11px;text-align:center;}
.pos-col-header{display:grid;grid-template-columns:90px 35px 65px 80px 75px 45px 75px 1fr;padding:10px 20px 8px;font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.8px;border-bottom:1px solid var(--border);}
.pos-empty{text-align:center;padding:32px 20px;color:var(--dim);font-size:13px;}

/* Stats row */
.stats-row{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:28px;}
.stat-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:18px 20px;}
.stat-card .s-label{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px;}
.stat-card .s-val{font-size:22px;font-weight:800;color:#fff;}
.stat-card .s-val.green{color:var(--green);}.stat-card .s-val.red{color:var(--red);}

/* Module grid */
.mod-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin-bottom:28px;}
.mod-card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px;}
.mod-card::before{content:'';display:block;width:6px;height:6px;border-radius:50%;margin-bottom:8px;}
.mod-card.ok::before{background:var(--green);box-shadow:0 0 6px var(--green);}
.mod-card.stopped::before{background:var(--dim);}
.mod-card.disabled::before{background:var(--purple);}
.mod-icon{font-size:18px;display:inline;}
.mod-name{font-size:12px;font-weight:700;color:#fff;margin:4px 0 2px;}
.mod-msg{font-size:10px;color:var(--dim);}

/* Logs */
.log-section{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden;}
.log-box{background:#02040a;padding:14px 18px;max-height:320px;overflow-y:auto;font-family:'SF Mono',Consolas,monospace;font-size:11px;line-height:1.7;color:var(--dim);}
.log-ok{color:var(--green)}.log-err{color:var(--red)}.log-warn{color:var(--gold)}

.sig-header .sig-cycle{font-size:11px;color:var(--gold);}
.table-wrap{overflow-x:auto;margin-bottom:8px;}
.table-wrap table tbody tr{border-bottom:1px solid var(--border);transition:background .15s;}
.table-wrap table tbody tr:hover{background:rgba(255,255,255,.03);}
.analysis-row{display:flex;justify-content:space-between;align-items:center;padding:6px 8px;border-bottom:1px solid var(--border);font-size:12px;}
.analysis-row .ar-sym{color:var(--text);font-weight:600;width:100px;}
.analysis-row .ar-status{font-size:11px;padding:2px 8px;border-radius:4px;}
.analysis-row .ar-status.ok{background:rgba(0,200,83,.15);color:var(--green);}
.analysis-row .ar-status.warn{background:rgba(255,193,7,.15);color:var(--gold);}
.analysis-row .ar-status.err{background:rgba(255,82,82,.15);color:var(--red);}
.analysis-row .ar-reason{color:var(--dim);text-align:right;flex:1;margin-left:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.analysis-row .ar-score{color:var(--gold);font-weight:700;width:40px;text-align:right;}
@media(max-width:900px){.strategy-panel{grid-template-columns:1fr}.stats-row{grid-template-columns:1fr 1fr}.ema-grid{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>

<div class="header">
  <div class="header-l">
    <div class="logo">🐕</div>
    <div>
      <h1>旺财自动交易系统</h1>
      <div style="display:flex;align-items:center;gap:8px;margin-top:2px;">
        <span class="pair">BTC/USDT</span>
        <span class="ver">QuantTrend v3 · 4H · 仅做多</span>
      </div>
    </div>
  </div>
  <div class="header-r">
    <span class="clock" id="clk">--</span>
    <span id="badge" class="pill pill-stop">● 已停止</span>
  </div>
</div>

<div class="container">

  <!-- Controls -->
  <div class="toolbar">
    <button class="btn btn-primary" onclick="doAction('start')">▶ 启动系统</button>
    <button class="btn btn-danger" onclick="doAction('stop')">■ 停止</button>
    <button class="btn btn-outline" onclick="doAction('restart')">↻ 重启</button>
    <button class="btn btn-outline" onclick="refreshAll()" style="margin-left:auto;">↻ 刷新</button>
  </div>

  <!-- Stats -->
  <div class="sec-title">📊 账户概览</div>
  <div class="stats-row">
    <div class="stat-card"><div class="s-label">账户权益</div><div class="s-val" id="eq">$--</div></div>
    <div class="stat-card"><div class="s-label">可用余额</div><div class="s-val" id="avail">$--</div></div>
    <div class="stat-card"><div class="s-label">当前持仓</div><div class="s-val" id="posCnt">0</div></div>
    <div class="stat-card"><div class="s-label">运行模式</div><div class="s-val" id="runMode" style="font-size:18px">--</div></div>
  </div>

  <!-- Strategy Panel - main focus -->
  <div class="sec-title">🎯 策略状态</div>
  <div class="strategy-panel">
    <!-- 最新信号 -->
    <div class="signal-card" id="signalCard">
      <div class="sig-top">
        <span class="sig-symbol" id="sigSymbol">--</span>
        <span class="sig-dir hold" id="sigDir">HOLD</span>
      </div>
      <div class="sig-body">
        <div class="sig-price-row">
          <span class="sig-price" id="sigPrice">--</span>
          <span class="sig-score-badge" id="sigScore">--</span>
          <span class="sig-score-label">/31</span>
        </div>
        <div class="sig-bar"><div class="sig-bar-fill weak" id="sigBar" style="width:0%"></div></div>
        <div class="sig-detail-grid">
          <div class="sig-detail-item"><div class="sdl">止损</div><div class="sdv" id="sigStop">--</div></div>
          <div class="sig-detail-item"><div class="sdl">止盈1</div><div class="sdv" id="sigTP1">--</div></div>
          <div class="sig-detail-item"><div class="sdl">盈亏比</div><div class="sdv" id="sigRR">--</div></div>
        </div>
        <div class="sig-reason" id="sigReason">等待策略信号...</div>
      </div>
    </div>

    <!-- 持仓详情 -->
    <div class="pos-card">
      <div class="pos-header-bar">📋 持仓 <span class="pos-count" id="posCount">0</span></div>
      <div class="pos-col-header">
        <span>代币</span><span></span><span>仓位</span><span>入场价</span><span>标记价</span><span></span><span>盈亏</span><span></span>
      </div>
      <div id="posList"></div>
    </div>
  </div>

  <!-- Screen results table -->
  <div class="sec-title" style="margin-top:24px;">🔍 Step0 筛选结果 <span style="font-size:12px;color:var(--dim);font-weight:400;" id="screenSummary">--</span></div>
  <div class="table-wrap" id="screenTable" style="display:none;">
    <table style="width:100%;border-collapse:collapse;font-size:12px;">
      <thead><tr style="color:var(--dim);border-bottom:1px solid var(--border);">
        <th style="text-align:left;padding:6px 8px;">#</th><th style="text-align:left;padding:6px 8px;">代币</th>
        <th style="text-align:right;padding:6px 8px;">评分</th><th style="text-align:right;padding:6px 8px;">价格</th>
        <th style="text-align:right;padding:6px 8px;">24h量</th><th style="text-align:right;padding:6px 8px;">波动%</th>
        <th style="text-align:center;padding:6px 8px;">趋势参考</th>
      </tr></thead>
      <tbody id="screenBody"></tbody>
    </table>
  </div>

  <!-- Analysis pipeline -->
  <div class="sec-title">📊 Step1 三位一体分析 <span style="font-size:12px;color:var(--dim);font-weight:400;" id="analysisSummary">--</span></div>
  <div id="analysisBox" style="font-size:12px;color:var(--dim);padding:12px 0;">等待数据...</div>

  <!-- Modules -->
  <div class="sec-title" style="margin-top:28px;">🔧 模块状态</div>
  <div class="mod-grid" id="modGrid"><div style="color:var(--dim);text-align:center;grid-column:1/-1;padding:30px;">加载中...</div></div>

  <!-- Logs -->
  <div class="sec-title">📜 运行日志</div>
  <div class="log-section"><div class="log-box" id="logBox"><span style="color:var(--dim)">加载中...</span></div></div>

</div>

<script>
function pad2(n){return n<10?'0'+n:''+n;}
function now(){var d=new Date();return d.getFullYear()+'-'+pad2(d.getMonth()+1)+'-'+pad2(d.getDate())+' '+pad2(d.getHours())+':'+pad2(d.getMinutes())+':'+pad2(d.getSeconds());}
setInterval(function(){document.getElementById('clk').textContent=now();},1000);
document.getElementById('clk').textContent=now();

var STATUS_LABEL={ok:'运行中',stopped:'未启动',disabled:'未启用'};

function getAuthToken(){return localStorage.getItem('yaobi_web_auth_token')||'';}
function setAuthToken(){
  var old=getAuthToken();
  var token=prompt('请输入 Web 认证 Token（会保存在本浏览器 localStorage）', old);
  if(token!==null){localStorage.setItem('yaobi_web_auth_token', token.trim());refreshAll();}
}
async function apiFetch(url, opts){
  opts=opts||{};opts.headers=opts.headers||{};
  var token=getAuthToken();
  if(token)opts.headers['X-Auth-Token']=token;
  var r=await window.fetch(url, opts);
  if(r.status===401){setAuthToken();throw new Error('unauthorized');}
  return r;
}

async function loadModules(){
  try{var r=await apiFetch('/api/module_status');var d=await r.json();
    var g=document.getElementById('modGrid');g.innerHTML='';
    d.modules.forEach(function(m){
      g.innerHTML+='<div class="mod-card '+m.status+'"><div class="mod-icon">'+m.icon+'</div><div class="mod-name">'+m.name+'</div><div class="mod-msg">'+STATUS_LABEL[m.status]+'</div></div>';});
  }catch(e){}
}

async function loadStrategy(){
  try{var r=await apiFetch('/api/strategy_status');var d=await r.json();
    // 信号卡片
    var sigs=d.signals||[];
    var sig=sigs.length>0?sigs[0]:null;
    var act=sig?sig.action:d.action||'HOLD';
    var dirEl=document.getElementById('sigDir');
    dirEl.textContent=act==='BUY'? '▲ 做多' : act==='SELL'? '▼ 做空' : '— 观望';
    dirEl.className='sig-dir '+(act==='BUY'?'long':act==='SELL'?'short':'hold');

    if(sig){
      document.getElementById('sigSymbol').textContent=sig.symbol||'--';
      document.getElementById('sigPrice').textContent=sig.price?'$'+parseFloat(sig.price).toFixed(4):'--';
      var score=sig.score||0;
      document.getElementById('sigScore').textContent=score;
      var pct=Math.min(100, score/31*100);
      var bar=document.getElementById('sigBar');
      bar.style.width=pct+'%';
      bar.className='sig-bar-fill '+(score>=20?'strong':score>=10?'good':'weak');
      document.getElementById('sigStop').textContent=sig.stop_price?'$'+parseFloat(sig.stop_price).toFixed(4):'--';
      var tp=sig.take_profit_levels||[];
      document.getElementById('sigTP1').textContent=tp.length>0?'$'+parseFloat(tp[0]).toFixed(4):'--';
      if(sig.stop_price&&tp.length>0){
        var risk=Math.abs(parseFloat(sig.price)-parseFloat(sig.stop_price));
        var reward=Math.abs(parseFloat(tp[0])-parseFloat(sig.price));
        document.getElementById('sigRR').textContent=risk>0?(reward/risk).toFixed(1)+':1':'--';
      }else{document.getElementById('sigRR').textContent='--';}
      var reason=sig.reason||'';
      var res=sig.resonance_breakdown||{};
      var detail=[];
      if(res.pa_trend)detail.push('PA趋势:'+res.pa_trend);
      if(res.smc_score!==undefined)detail.push('SMC:'+res.smc_score);
      if(res.wyckoff_score!==undefined)detail.push('威科夫:'+res.wyckoff_score);
      document.getElementById('sigReason').innerHTML=
        (reason?'<span>'+reason+'</span>':'')+
        (detail.length?' · '+detail.join(' · '):'');
    }else{
      document.getElementById('sigSymbol').textContent='--';
      document.getElementById('sigPrice').textContent='--';
      document.getElementById('sigScore').textContent='--';
      document.getElementById('sigBar').style.width='0%';
      document.getElementById('sigBar').className='sig-bar-fill weak';
      document.getElementById('sigStop').textContent='--';
      document.getElementById('sigTP1').textContent='--';
      document.getElementById('sigRR').textContent='--';
      document.getElementById('sigReason').textContent='等待策略信号...';
    }

    // Step0 筛选
    var s=d.screening;
    if(s&&s.top_tokens&&s.top_tokens.length>0){
      document.getElementById('screenSummary').textContent='扫描'+s.total+' | 通过'+s.passed+' | 拒绝'+(s.total-s.passed);
      document.getElementById('screenTable').style.display='block';
      var tb=document.getElementById('screenBody');
      tb.innerHTML=s.top_tokens.map(function(t,i){
        var trendColor=t.trend==='BULLISH'?'var(--green)':t.trend==='BEARISH'?'var(--red)':'var(--dim)';
        return '<tr>'+
          '<td style="padding:5px 8px;color:var(--dim);">'+(i+1)+'</td>'+
          '<td style="padding:5px 8px;">'+t.symbol+'</td>'+
          '<td style="padding:5px 8px;text-align:right;color:var(--gold);font-weight:700;">'+t.score+'</td>'+
          '<td style="padding:5px 8px;text-align:right;">$'+t.price.toFixed(4)+'</td>'+
          '<td style="padding:5px 8px;text-align:right;color:var(--dim);">$'+(t.volume_24h/1e6).toFixed(1)+'M</td>'+
          '<td style="padding:5px 8px;text-align:right;">'+t.atr_pct+'%</td>'+
          '<td style="padding:5px 8px;text-align:center;color:'+trendColor+';">'+t.trend+'</td>'+
          '</tr>';
      }).join('');
    }else{
      document.getElementById('screenSummary').textContent='暂无筛选数据';
      document.getElementById('screenTable').style.display='none';
    }

    // Step1 分析
    var ad=d.analysis;
    if(ad&&ad.details&&ad.details.length>0){
      document.getElementById('analysisSummary').textContent='分析'+ad.analyzed+'个 | 错误'+ad.errors+'个';
      var ab=document.getElementById('analysisBox');
      ab.innerHTML=ad.details.map(function(t){
        var cls=t.status==='signal'?'ok':t.status==='error'?'err':'warn';
        var label={'signal':'✅ 信号','analyzed':'⏳ 等待','no_data':'-- 无数据','low_confidence':'🔻 低置信','error':'❌ 错误','pending':'⏳'}[t.status]||t.status;
        return '<div class="analysis-row">'+
          '<span class="ar-sym">'+t.symbol+'</span>'+
          '<span class="ar-status '+cls+'">'+label+'</span>'+
          '<span class="ar-score">'+(t.score||'')+'</span>'+
          '<span class="ar-reason" title="'+(t.reason||'')+'">'+(t.reason||'')+'</span>'+
          '</div>';
      }).join('');
    }else{
      document.getElementById('analysisSummary').textContent='暂无分析数据';
      document.getElementById('analysisBox').innerHTML='<span style="color:var(--dim);">等待下一轮分析...</span>';
    }
  }catch(e){}
}

async function loadStatus(){
  try{var r=await apiFetch('/api/status');var d=await r.json();
    var b=document.getElementById('badge');
    b.className='pill '+(d.running?'pill-run':'pill-stop');
    b.textContent=d.running?'● 运行中':'● 已停止';
    document.getElementById('runMode').textContent=d.mode==='live'?'🔴 实盘':'🟡 模拟';
    document.getElementById('runMode').className='s-val'+(d.mode==='live'?' red':'');
  }catch(e){}
}

async function loadAccount(){
  try{var r=await apiFetch('/api/account_realtime');var d=await r.json();
    if(d.error)return;
    document.getElementById('eq').textContent='$'+parseFloat(d.total_equity||0).toFixed(2);
    document.getElementById('avail').textContent='$'+parseFloat(d.available_usdt||0).toFixed(2);
    var posC=document.getElementById('posCnt');
    var pc=d.position_count||(d.positions?d.positions.length:0);
    posC.textContent=pc;
    posC.className='s-val'+(pc>=5?' red':'');
    // 持仓列表
    var posDiv=document.getElementById('posList');
    document.getElementById('posCount').textContent=pc;
    if(d.positions&&d.positions.length>0){
      posDiv.innerHTML=d.positions.map(function(p){
        var side=p.side===('SHORT'||'short')?'short':'long';
        var pnl=parseFloat(p.unrealized_pnl||0);
        var ep=parseFloat(p.entry_price||0);
        var mp=parseFloat(p.mark_price||0)||parseFloat(p.entry_price||0);
        var pnlPct=ep>0?((mp-ep)/ep*100*(side==='short'?-1:1)).toFixed(2):0;
        return '<div class="pos-row">'+
          '<span class="pr-sym">'+p.symbol.replace('USDT','')+'</span>'+
          '<span class="pr-side '+side+'">'+(side==='long'?'多':'空')+'</span>'+
          '<span class="pr-size">'+parseFloat(p.amount||0)+'</span>'+
          '<span style="color:var(--text);">$'+ep.toFixed(2)+'</span>'+
          '<span style="color:var(--dim);">$'+mp.toFixed(2)+'</span>'+
          '<span class="pr-lev">'+(p.leverage||1)+'x</span>'+
          '<span class="pr-pnl '+(pnl>=0?'pos':'neg')+'">$'+pnl.toFixed(2)+'<br><span style="font-size:10px;">'+(pnlPct>=0?'+':'')+pnlPct+'%</span></span>'+
          '<span></span>'+
          '</div>';
      }).join('');
    }else{
      posDiv.innerHTML='<div class="pos-empty">暂无持仓</div>';
    }
  }catch(e){}
}

async function loadLogs(){
  try{var r=await apiFetch('/api/logs?lines=60');var d=await r.json();
    var box=document.getElementById('logBox');
    if(!d.logs||!d.logs.length){box.innerHTML='<span style="color:var(--dim)">暂无日志</span>';return;}
    box.innerHTML=d.logs.map(function(ln){
      var cls='';
      if(ln.indexOf('ERROR')>=0||ln.indexOf('\u274C')>=0)cls='log-err';
      else if(ln.indexOf('WARNING')>=0||ln.indexOf('\u26A0')>=0)cls='log-warn';
      else if(ln.indexOf('SUCCESS')>=0||ln.indexOf('\u2705')>=0)cls='log-ok';
      return '<div class="'+cls+'">'+ln+'</div>';
    }).join('');
    box.scrollTop=box.scrollHeight;
  }catch(e){}
}

async function doAction(act){
  if(!confirm(act==='stop'?'确认停止？':(act==='start'?'确认启动？':'确认重启？')))return;
  await apiFetch('/api/action/'+act,{method:'POST'});
  setTimeout(refreshAll,4000);
}

function refreshAll(){loadStatus();loadModules();loadStrategy();loadAccount();loadLogs();}
refreshAll();
setInterval(loadStatus,8000);setInterval(loadModules,12000);setInterval(loadStrategy,10000);setInterval(loadAccount,15000);setInterval(loadLogs,6000);
</script>
</body>
</html>"""

# ════════════════ API ════════════════

@app.route("/")
def index():
    dashboard_file = ROOT / "dashboard.html"
    if dashboard_file.exists():
        with open(dashboard_file, 'r', encoding='utf-8') as f:
            return f.read()
    return render_template_string(HTML)

@app.route("/api/status")
def api_status():
    r = is_running()
    cfg = load_config()
    # Fix #7: 从真实配置读取decision_mode，不硬编码
    dm = cfg.get("system",{}).get("decision_mode","ai")
    return jsonify({"running":r,"mode":cfg.get("system",{}).get("mode","paper"),"decision_mode":dm,"time":datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

@app.route("/api/strategy_status")
def api_strategy_status():
    """最新策略信号详情"""
    import json
    status_file = ROOT / "data" / "strategy_status.json"
    if status_file.exists():
        try:
            with open(status_file, "r", encoding="utf-8", errors="ignore") as f:
                return jsonify(json.load(f))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.warning(f"[Web] strategy_status.json 读取失败，返回默认状态: {e}")
            # 修复损坏文件：重建一个干净的默认文件
            try:
                default_status = {"action":"HOLD","score":0,"reason":"状态文件损坏，已重置","cycle":0,"status":"degraded"}
                with open(status_file, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(default_status, f, ensure_ascii=True, indent=2)
            except:
                pass
            return jsonify(default_status)
    return jsonify({"action":"HOLD","score":0,"reason":"等待首次信号...","cycle":0,"status":"ok"})

@app.route("/api/module_status")
def api_module_status(): return jsonify({"modules":module_status()})

@app.route("/api/account")
def api_account():
    try:
        conn=get_db();row=conn.execute("SELECT * FROM account_snapshots ORDER BY timestamp DESC LIMIT 1").fetchone();conn.close()
        return jsonify(dict(row) if row else {"total_equity":0,"position_count":0})
    except: return jsonify({"total_equity":0,"position_count":0})



@app.route("/api/account_realtime")
def api_account_realtime():
    """实时账户信息 - 直接从交易所API获取（合约账户）"""
    try:
        import requests
        import time
        import hmac
        import hashlib
        from dotenv import load_dotenv
        import os
        
        load_dotenv()
        api_key = os.getenv('BINANCE_API_KEY')
        api_secret = os.getenv('BINANCE_API_SECRET')
        
        # 方式1：优先使用 CCXT 方法（指定 type='future'）
        try:
            import ccxt
            from core.config_loader import load_config
            
            config = load_config(str(ROOT / "config" / "system.yaml"))
            proxy_url = config.get('proxy') or None
            if proxy_url:
                os.environ['http_proxy'] = proxy_url
                os.environ['https_proxy'] = proxy_url
            
            ccxt_params = {
                'apiKey': api_key,
                'secret': api_secret,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'future',
                    'adjustForTimeDifference': True,
                }
            }
            if proxy_url:
                ccxt_params['proxies'] = {'http': proxy_url, 'https': proxy_url}
            
            exchange = ccxt.binance(ccxt_params)
            
            # 使用CCXT futures专用方法
            try:
                balance = exchange.fetch_balance({'type': 'future'})
                # Fix P1-4: 全量 fetch_positions()，而不是只查 ['BTC/USDT:USDT']
                # 从 config preferred_markets / trinity symbols 生成符号列表，或直接全量获取
                try:
                    # 先尝试获取所有持仓（ccxt 不传参数 = 全量）
                    positions_raw = exchange.fetch_positions()
                except Exception:
                    # 降级：从配置读取监控列表
                    try:
                        from core.config_loader import load_config
                        cfg = load_config(str(ROOT / "config" / "system.yaml"))
                        symbols = cfg.get('exchanges', {}).get('binance', {}).get('preferred_markets', [])
                        if not symbols:
                            trinity_cfg = cfg.get('trinity', {})
                            symbols = trinity_cfg.get('symbols', [])
                        if symbols:
                            positions_raw = exchange.fetch_positions(symbols)
                        else:
                            positions_raw = []
                    except Exception:
                        positions_raw = []
            except:
                # 如果CCXT方法失败，尝试直接REST API
                raise Exception("CCXT方法失败，尝试直接API")
            
            total_equity_f = float(balance['total'].get('USDT', 0) or 0)
            available_usdt_f = float(balance['free'].get('USDT', 0) or 0)
            
            position_list = []
            open_positions = 0
            for pos in positions_raw:
                contracts = float(pos.get("contracts", 0) or 0)
                if abs(contracts) > 0:
                    open_positions += 1
                    position_list.append({
                        'symbol': pos.get('symbol', ''),
                        'side': 'long' if contracts > 0 else 'short',
                        'amount': abs(contracts),
                        'entry_price': float(pos.get('entryPrice', 0) or 0),
                        'mark_price': float(pos.get('markPrice', 0) or 0),
                        'unrealized_pnl': float(pos.get('unrealizedPnl', 0) or 0),
                        'leverage': float(pos.get('leverage', 1))
                    })
            
            return jsonify({
                'total_equity': round(total_equity_f, 2),
                'available_usdt': round(available_usdt_f, 2),
                'position_count': open_positions,
                'positions': position_list,
                'timestamp': datetime.now().isoformat()
            })
            
        except:
            # 方式2：直接调用币安合约 REST API（备选方案，无需ccxt）
            # 先获取服务器时间，校准本地时钟偏移
            try:
                import requests as req_ts
                from core.config_loader import load_config as cfg_loader_ts
                cfg_ts = cfg_loader_ts(str(ROOT / "config" / "system.yaml"))
                proxy_url_ts = cfg_ts.get('proxy') or None
                proxies_ts = {'http': proxy_url_ts, 'https': proxy_url_ts} if proxy_url_ts else None
                server_time_resp = req_ts.get('https://fapi.binance.com/fapi/v1/time', proxies=proxies_ts, timeout=10)
                server_time_data = server_time_resp.json()
                server_time_ms = int(server_time_data['serverTime'])
                local_time_ms = int(time.time() * 1000)
                time_offset = server_time_ms - local_time_ms
                logger.info(f"[Web/Account] 币安服务器时间校准: 偏移={time_offset}ms")
            except Exception as ts_err:
                logger.warning(f"[Web/Account] 获取服务器时间失败: {ts_err}，使用本地时间")
                time_offset = 0

            adjusted_ms = int(time.time() * 1000) + time_offset
            # 使用较大 recvWindow 缓解余下偏差
            timestamp = adjusted_ms
            query_string = f"timestamp={timestamp}&recvWindow=15000"
            signature = hmac.new(
                api_secret.encode('utf-8'),
                query_string.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()

            url = f"https://fapi.binance.com/fapi/v2/account?{query_string}&signature={signature}"
            headers = {"X-MBX-APIKEY": api_key}

            # 设置代理
            from core.config_loader import load_config as cfg_loader2
            config = cfg_loader2(str(ROOT / "config" / "system.yaml"))
            proxy_url = config.get('proxy', 'socks5://127.0.0.1:7897')
            
            proxies = None
            if proxy_url:
                proxies = {'http': proxy_url, 'https': proxy_url}
            
            response = requests.get(url, headers=headers, proxies=proxies, timeout=30)
            if response.status_code != 200:
                raise Exception(f"API返回错误: {response.status_code} - {response.text}")
            
            account_data = response.json()
            
            # 解析合约账户数据
            total_equity = float(account_data.get('totalWalletBalance', 0))
            available_usdt = float(account_data.get('availableBalance', 0))
            
            position_list = []
            open_positions = 0
            for p in account_data.get('positions', []):
                amt = float(p.get('positionAmt', 0))
                if amt != 0:
                    open_positions += 1
                    position_list.append({
                        'symbol': p.get('symbol', ''),
                        'side': 'long' if amt > 0 else 'short',
                        'amount': abs(amt),
                        'entry_price': float(p.get('entryPrice', 0)),
                        'mark_price': float(p.get('markPrice', 0)),
                        'unrealized_pnl': float(p.get('unrealizedProfit', 0)),
                        'leverage': float(p.get('leverage', 1))
                    })
            
            return jsonify({
                'total_equity': round(total_equity, 2),
                'available_usdt': round(available_usdt, 2),
                'position_count': open_positions,
                'positions': position_list,
                'timestamp': datetime.now().isoformat(),
                'source': 'fapi_v2_direct'
            })
        
    except Exception as e:
        logger.error(f"实时账户信息获取失败: {e}")
        return jsonify({"error": str(e), "total_equity": 0, "position_count": 0, "positions": []})

@app.route("/api/positions")
def api_positions():
    try:
        conn=get_db();rows=conn.execute("SELECT * FROM positions WHERE status='open' ORDER BY timestamp DESC").fetchall();conn.close()
        return jsonify([dict(r) for r in rows])
    except: return jsonify([])

@app.route("/api/logs")
def api_logs():
    n = request.args.get("lines", 60, type=int)
    try:
        all_lines = []
        # 只匹配日期命名的日志文件，排除wangcai.log / wangcai_err.log
        import re
        log_files = sorted(
            [f for f in LOGDIR.glob("wangcai_*.log") 
             if re.search(r'wangcai_\d{4}-\d{2}-\d{2}\.log$', f.name)],
            reverse=True
        )
        
        if log_files:
            try:
                with open(log_files[0], "r", encoding="utf-8", errors="ignore") as f:
                    all_lines = f.readlines()[-n:]
            except:
                pass
        
        result = [l.strip() for l in all_lines[-n:]]
        return jsonify({"logs": result})
    except:
        pass
    return jsonify({"logs": []})

@app.route("/api/config")
def api_config():
    cfg=load_config()
    # Fix #10: 过滤敏感字段
    safe_cfg = {}
    # 只保留允许公开的部分
    if "system" in cfg:
        safe_cfg["system"] = {k: v for k, v in cfg["system"].items() if k not in ("api_key", "api_secret", "passphrase")}
    if "exchanges" in cfg:
        safe_cfg["exchanges"] = {}
        for name, ex_cfg in cfg["exchanges"].items():
            safe_cfg["exchanges"][name] = {k: v for k, v in ex_cfg.items() if k not in ("api_key", "api_secret", "passphrase")}
    # 去掉 proxy
    safe_cfg.pop("proxy", None)
    # 去掉所有含 api_key, api_secret, passphrase 的顶层字段
    for k in list(safe_cfg.keys()):
        if k in ("info", "notifications"):
            safe_cfg[k] = {kk: vv for kk, vv in safe_cfg[k].items() if kk not in ("api_key", "api_secret", "passphrase", "bot_token", "chat_id")}
    return jsonify(safe_cfg)

@app.route("/api/action/<action>",methods=["POST"])
def api_action(action):
    cfg = load_config()
    mode = cfg.get("system",{}).get("mode","paper") if isinstance(cfg, dict) else "paper"
    if action=="start":
        python = sys.executable  # 兼容Windows/Linux
        # Fix #3 (Round3): 指定 encoding="utf-8"，Windows 默认 GBK 会导致中文乱码
        subprocess.Popen([python,"main.py","--mode",mode,"--confirm-yes"],cwd=str(ROOT),stdout=open(str(LOG),"a",encoding="utf-8"),stderr=subprocess.STDOUT,start_new_session=True)
        return jsonify({"s":True})
    elif action=="stop":
        _kill_main_process()
        return jsonify({"s":True})
    elif action=="restart":
        _kill_main_process();time.sleep(3)
        python = sys.executable
        # Fix #3 (Round3): 同上，指定 encoding="utf-8"
        subprocess.Popen([python,"main.py","--mode",mode,"--confirm-yes"],cwd=str(ROOT),stdout=open(str(LOG),"a",encoding="utf-8"),stderr=subprocess.STDOUT,start_new_session=True)
        return jsonify({"s":True})
    return jsonify({"e":"Unknown"}),400


@app.route("/api/trades")
def api_trades():
    """获取交易记录"""
    try:
        conn = get_db()
        rows = conn.execute("SELECT * FROM trades ORDER BY timestamp DESC LIMIT 50").fetchall()
        conn.close()
        trades = []
        for row in rows:
            d = dict(row)
            trades.append({
                'time': d.get('timestamp', '--'),
                'symbol': d.get('symbol', '--'),
                'action': d.get('action', '--'),
                'amount': d.get('amount', '--'),
                'price': d.get('price', '--'),
                'pnl': d.get('pnl', '--')
            })
        return jsonify({'trades': trades})
    except Exception as e:
        return jsonify({'trades': [], 'error': str(e)})

@app.route("/api/alerts")
def api_alerts():
    """获取告警记录"""
    try:
        conn = get_db()
        rows = conn.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT 20").fetchall()
        conn.close()
        alerts = []
        for row in rows:
            d = dict(row)
            alerts.append({
                'time': d.get('created_at', '--'),
                'level': d.get('level', 'INFO'),
                'title': d.get('title', '--'),
                'content': d.get('content', '--')
            })
        return jsonify({'alerts': alerts})
    except Exception as e:
        return jsonify({'alerts': [], 'error': str(e)})

@app.route("/api/datasource_status")
def api_datasource_status():
    """获取数据源状态 - 基于引擎运行状态判断"""
    running = is_running()
    # 引擎运行中说明已成功连接Binance
    status = 'ok' if running else 'stopped'
    return jsonify({
        'data': {
            '币安合约API': {
                'status': status,
                'connected': running,
                'latency': '--',
                'lastUpdate': datetime.now().strftime('%H:%M:%S')
            }
        }
    })

# 市场数据缓存（避免每次请求都实时拉取Binance全量ticker）
_market_cache = {'data': [], 'time': 0}

def _bg_refresh_cache():
    """后台线程每5分钟刷新市场数据""" 
    global _market_cache
    import requests as req
    from dotenv import load_dotenv
    while True:
        try:
            time.sleep(10)
            load_dotenv()
            from core.config_loader import load_config
            cfg = load_config(str(ROOT / "config" / "system.yaml"))
            proxy_url = cfg.get('proxy') or None
            p = {'http': proxy_url, 'https': proxy_url} if proxy_url else None
            resp = req.get('https://fapi.binance.com/fapi/v1/ticker/24hr', proxies=p, timeout=20)
            all_data = resp.json()
            pref = cfg.get('exchanges', {}).get('binance', {}).get('preferred_markets', [])
            targets = {m.split('/')[0] + 'USDT' for m in pref[:50]}
            md = []
            for item in all_data:
                sym, base = item['symbol'], item['symbol'][:-4]
                if sym in targets:
                    md.append({'symbol': f"{base}/USDT:USDT", 'price': float(item['lastPrice']),
                              'change': float(item['priceChangePercent']), 'high': float(item['highPrice']),
                              'low': float(item['lowPrice']), 'volume': float(item['quoteVolume'])})
            with _market_cache_lock:
                _market_cache = {'data': md, 'time': time.time()}
            logger.info(f"[MarketCache] 已刷新 {len(md)} 个代币")
            time.sleep(290)
        except Exception as e:
            logger.warning(f"[MarketCache] 刷新失败: {e}")
            time.sleep(300)

_bg_refresh_started = False
if not _bg_refresh_started:
    _bg_refresh_started = True
    threading.Thread(target=_bg_refresh_cache, daemon=True).start()

@app.route("/api/market_data")
def api_market_data():
    """获取市场交易对数据（带缓存，60秒内复用）"""
    global _market_cache
    
    # 缓存有效期内直接返回（5分钟，匹配引擎15分钟循环周期）
    with _market_cache_lock:
        cache_fresh = time.time() - _market_cache['time'] < 300
        cache_data = _market_cache['data'] if cache_fresh else []
    if cache_fresh and cache_data:
        return jsonify({'success': True, 'data': cache_data})
    
    try:
        import ccxt
        from core.config_loader import load_config
        import os
        from dotenv import load_dotenv
        
        load_dotenv()
        config = load_config(str(ROOT / "config" / "system.yaml"))
        proxy_url = config.get('proxy') or None
        
        # 设置代理
        ccxt_config = {
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        }
        if proxy_url:
            os.environ['http_proxy'] = proxy_url
            os.environ['https_proxy'] = proxy_url
            ccxt_config['proxies'] = {'http': proxy_url, 'https': proxy_url}
        
        exchange = ccxt.binance(ccxt_config)
        
        # 获取首选交易对
        preferred_markets = config.get('exchanges', {}).get('binance', {}).get('preferred_markets', [])
        
        # 如果没有配置，使用默认值
        if not preferred_markets:
            preferred_markets = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT']
        
        # 获取ticker数据 — 用直接REST API避免ccxt全量fetch_tickers超时
        import requests as req
        proxy_dict = {'http': proxy_url, 'https': proxy_url} if proxy_url else None
        try:
            # 只拉取前50个交易对（前端不需要一次显示全部97个）
            symbols_to_fetch = preferred_markets[:50]
            # Binance futures ticker API: 一次性拉全部，本地过滤
            resp = req.get('https://fapi.binance.com/fapi/v1/ticker/24hr', 
                          proxies=proxy_dict, timeout=20)
            all_data = resp.json()
            
            # 构建查询集合
            target_pairs = set()
            for m in symbols_to_fetch:
                base = m.split('/')[0]
                target_pairs.add(f'{base}USDT')  # Binance futures format: BTCUSDT
                target_pairs.add(m)
            
            market_data = []
            for item in all_data:
                sym = item['symbol']  # e.g. BTCUSDT
                pair = f"{sym[:-4]}/USDT"  # e.g. BTC/USDT
                if sym in target_pairs or pair in target_pairs:
                    market_data.append({
                        'symbol': f"{pair}:USDT",
                        'price': float(item['lastPrice']),
                        'change': float(item['priceChangePercent']),
                        'high': float(item['highPrice']),
                        'low': float(item['lowPrice']),
                        'volume': float(item['quoteVolume'])
                    })
        except Exception as e:
            # 回退方案：ccxt只拉BTC/ETH/SOL
            try:
                tickers = exchange.fetch_tickers(['BTC/USDT', 'ETH/USDT', 'SOL/USDT'])
                market_data = [{'symbol': s, 'price': t['last'], 'change': t.get('percentage', 0),
                               'high': t['high'], 'low': t['low'], 'volume': t.get('quoteVolume', 0)}
                              for s, t in tickers.items()]
            except:
                market_data = []
        
        # 更新缓存
        _market_cache['data'] = market_data
        _market_cache['time'] = time.time()
        
        return jsonify({
            'success': True,
            'data': market_data
        })
    except Exception as e:
        return jsonify({'success': False, 'data': [], 'error': str(e)})

@app.route("/api/alpha_tokens")
def api_alpha_tokens():
    """获取币安Alpha代币开单列表"""
    try:
        import json
        
        alpha_file = ROOT / "data" / "alpha_tokens.json"
        symbols = []
        if alpha_file.exists():
            with open(alpha_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                raw = data.get('tokens', [])
                # 修正符号：去掉重新的USDT后缀
                for t in raw:
                    if t.endswith('USDT') and len(t) > 4:
                        symbols.append(t[:-4])
                    else:
                        symbols.append(t)
        
        alpha_list = [{'symbol': s, 'pair': f'{s}/USDT', 'price': 0, 'change': 0, 
                       'high_24h': 0, 'low_24h': 0, 'volume': 0} for s in symbols]
        
        return jsonify({
            'success': True,
            'data': alpha_list,
            'count': len(alpha_list),
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'success': False, 'data': [], 'error': str(e)})


if __name__=="__main__":
    for m in["flask","psutil"]:
        try:__import__(m)
        except ImportError:print("pip install",m);sys.exit(1)
    print("="*60)
    print("  WangCai BTC QuantTrend Dashboard - :8081")
    print("="*60)
    app.run(host="0.0.0.0",port=8081,debug=False)
