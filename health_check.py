"""
妖币系统 本地全面体检脚本
"""
import sys, os, py_compile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.platform == 'win32' and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import yaml
import pandas as pd
import numpy as np

errors = []
warnings = []
ok_count = 0

def check(label, condition, detail=''):
    global ok_count
    if condition:
        print(f'  ✅ {label}')
        ok_count += 1
    else:
        print(f'  ❌ {label} {detail}')
        errors.append(f'{label}: {detail}')

def warn(label, msg):
    print(f'  ⚠️  {label}: {msg}')
    warnings.append(f'{label}: {msg}')

# ==========================================
print('=' * 55)
print('妖币系统 本地全面体检')
print('=' * 55)

# ---- 1. 语法检查 ----
print()
print('--- 1. 语法检查 ---')
files = [
    'core/engine.py',
    'modules/token_screener.py',
    'modules/trinity_engine.py',
    'modules/trinity_wyckoff.py',
    'modules/trinity_smc.py',
    'modules/trinity_pa.py',
    'modules/trinity_llm_decide.py',
    'modules/__init__.py',
]
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        check(f'Syntax: {f}', True)
    except py_compile.PyCompileError as e:
        check(f'Syntax: {f}', False, str(e)[:60])

# ---- 2. 导入链测试 ----
print()
print('--- 2. 导入链测试 ---')
try:
    from modules.token_screener import TokenScreener
    check('Import: TokenScreener', True)
except Exception as e:
    check('Import: TokenScreener', False, str(e)[:60])

try:
    from modules.trinity_engine import TrinityEngine
    check('Import: TrinityEngine', True)
except Exception as e:
    check('Import: TrinityEngine', False, str(e)[:60])

try:
    from modules.trinity_llm_decide import TrinityLLMDecider
    check('Import: TrinityLLMDecider', True)
except Exception as e:
    check('Import: TrinityLLMDecider', False, str(e)[:60])

try:
    from modules.trinity_wyckoff import WyckoffAnalyzer
    from modules.trinity_smc import SMCAnalyzer
    from modules.trinity_pa import PAAnalyzer
    check('Import: 子分析器(Wyckoff/SMC/PA)', True)
except Exception as e:
    check('Import: 子分析器', False, str(e)[:60])

try:
    from core.engine import WangCaiEngine
    check('Import: WangCaiEngine', True)
except Exception as e:
    check('Import: WangCaiEngine', False, str(e)[:60])

# ---- 3. 类方法完整性 ----
print()
print('--- 3. 类方法完整性 ---')

try:
    s = TokenScreener()
    for m in ['screen', 'screen_from_klines', 'generate_report', 'to_ai_context',
              '_score_token', '_calc_atr', '_calc_rsi', '_quick_trend_sense']:
        check(f'TokenScreener.{m}', hasattr(s, m))
except Exception as e:
    check('TokenScreener', False, str(e)[:60])

try:
    eng = TrinityEngine()
    check('TrinityEngine.analyze', hasattr(eng, 'analyze'))
    check('TrinityEngine.get_status_report', hasattr(eng, 'get_status_report'))
    check('TrinityEngine.wyckoff', eng.wyckoff is not None)
    check('TrinityEngine.smc', eng.smc is not None)
    check('TrinityEngine.pa', eng.pa is not None)
except Exception as e:
    check('TrinityEngine', False, str(e)[:60])

for m in ['_decision_ai', '_decision_rule', '_decision_trinity',
          '_run_token_screening', '_save_trinity_status', '_trinity_signal_to_decision',
          '_execute_decision', 'run', 'get_status']:
    check(f'WangCaiEngine.{m}', hasattr(WangCaiEngine, m))

# ---- 4. 配置验证 ----
print()
print('--- 4. 配置验证 ---')
try:
    with open('config/system.yaml', 'r') as f:
        cfg = yaml.safe_load(f)
    
    dm = cfg.get('system', {}).get('decision_mode', 'N/A')
    mode = cfg.get('system', {}).get('mode', 'N/A')
    interval = cfg.get('system', {}).get('main_loop', {}).get('interval_seconds', 'N/A')
    preferred = cfg.get('exchanges', {}).get('binance', {}).get('preferred_markets', [])
    screener_enabled = cfg.get('screener', {}).get('enabled', False)
    
    check(f'decision_mode={dm}', dm in ('ai','rule','trinity','yanchi'))
    check(f'mode={mode}', mode in ('live','paper'))
    check(f'interval={interval}s', isinstance(interval, (int, float)))
    check(f'preferred_markets={len(preferred)}个', len(preferred) > 0)
    check(f'screener.enabled={screener_enabled}', screener_enabled is True)
    
    if len(preferred) > 10:
        warn(f'代币={len(preferred)}个', '确保筛选器已开启')
    if dm in ('trinity', 'yanchi') and mode == 'live':
        warn(f'{dm}+live', '确认充分验证后再部署')
    
    sc = cfg.get('screener', {})
    print(f'  📋 筛选器: minVol=${sc.get("min_volume_usdt"):,} maxTokens={sc.get("max_tokens")}')
except Exception as e:
    check('system.yaml', False, str(e)[:60])

try:
    with open('config/trinity.yaml', 'r', encoding='utf-8') as f:
        tcfg = yaml.safe_load(f)
    risk = tcfg.get('risk_per_trade', 'N/A')
    lev = tcfg.get('leverage', 'N/A')
    llm = tcfg.get('llm', {}).get('enabled', 'N/A')
    print(f'  📋 trinity: risk={risk} lev={lev}x llm={llm}')
    check('trinity.yaml存在', True)
except Exception as e:
    check('trinity.yaml', False, str(e)[:60])

# ---- 5. 文件清单 ----
print()
print('--- 5. 文件清单 ---')
required = [
    'core/engine.py', 'core/config_loader.py',
    'modules/token_screener.py', 'modules/trinity_engine.py',
    'modules/trinity_wyckoff.py', 'modules/trinity_smc.py', 'modules/trinity_pa.py',
    'modules/trinity_llm_decide.py', 'modules/market_data.py',
    'modules/ai_decision.py', 'modules/risk_control.py',
    'config/system.yaml', 'config/trinity.yaml',
    'docs/price-action-knowledge.md', 'docs/trinity-strategy.md',
    'test_trinity.py', 'test_screener_trinity.py', 'verify_trinity_local.py',
    'deploy/deploy_trinity.py',
]
for f in required:
    check(f, os.path.exists(f))

# ---- 6. 功能测试 ----
print()
print('--- 6. 功能测试(模拟数据) ---')
try:
    np.random.seed(0)
    n = 100
    price = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        'open': price - np.abs(np.random.randn(n)*0.3),
        'high': price + np.abs(np.random.randn(n)*0.5),
        'low': price - np.abs(np.random.randn(n)*0.5),
        'close': price,
        'volume': np.random.uniform(1000, 5000, n)
    })
    
    screener = TokenScreener({'max_tokens': 3})
    score = screener._score_token('TEST/USDT', {
        'price': 100, 'volume_24h': 5_000_000,
        'klines_1h': df, 'klines_4h': df
    })
    check(f'评分计算: {score.total_score:.0f}/100', score.total_score > 0)
    check(f'趋势检测: {score.trend}', score.trend in ('BULLISH','BEARISH','NEUTRAL'))
    check(f'ATR: {score.atr_pct:.1f}%', score.atr_pct > 0)
    check(f'RSI: {score.rsi:.0f}' if score.rsi is not None else 'RSI: N/A', score.rsi is None or 0 < score.rsi < 100)
    
    # 死币拒绝
    dead = screener._score_token('DEAD/USDT', {'price': 0, 'volume_24h': 0})
    check('死币拒绝', not dead.passed)
    
    # 低量拒绝
    low = screener._score_token('LOW/USDT', {
        'price': 10, 'volume_24h': 100, 'klines_1h': df
    })
    check('低量拒绝', not low.passed)
    
    # 空数据
    result = screener.screen({})
    check('空数据不崩', result.total_scanned == 0)
    
except Exception as e:
    check('功能测试', False, str(e)[:80])

# ---- 7. 规模统计 ----
print()
print('--- 7. 代码规模 ---')
stats = {}
for f in required:
    if f.endswith('.py') and os.path.exists(f):
        with open(f, 'r', encoding='utf-8', errors='ignore') as fh:
            lines = len(fh.readlines())
            stats[f] = lines

trinity_lines = sum(l for k, l in stats.items() if 'trinity' in k)
total_lines = sum(stats.values())
print(f'  总Python代码: {total_lines} 行')
print(f'  Trinity模块: {trinity_lines} 行')
for f, l in sorted(stats.items(), key=lambda x: -x[1])[:5]:
    print(f'    {f}: {l}行')

# ---- 总结 ----
print()
print('=' * 55)
print('体检总结')
print('=' * 55)
print(f'  ✅ 通过: {ok_count}')
print(f'  ❌ 错误: {len(errors)}')
print(f'  ⚠️  警告: {len(warnings)}')

if errors:
    print()
    print('错误:')
    for e in errors:
        print(f'  ❌ {e}')

if warnings:
    print()
    print('警告:')
    for w in warnings:
        print(f'  ⚠️  {w}')

if not errors:
    print()
    print('全部核心检查通过！系统健康。')
else:
    print()
    print(f'{len(errors)}项需要修复!')
    sys.exit(1)
