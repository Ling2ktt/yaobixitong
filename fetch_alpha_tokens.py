#!/usr/bin/env python3
"""
拉取币安Alpha列表代币
数据来源: CoinMarketCap API (tagSlugs=binance-alpha)
"""
import requests
import json
import os
from datetime import datetime

# ============================================================
# 配置
# ============================================================
API_URL = "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "binance_alpha_tokens.json")
SUMMARY_FILE = os.path.join(OUTPUT_DIR, "binance_alpha_summary.txt")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

PARAMS = {
    "start": 1,
    "limit": 200,
    "sortBy": "market_cap",
    "sortType": "desc",
    "convert": "USD,BTC,ETH",
    "cryptoType": "all",
    "tagType": "all",
    "audited": "false",
    "aux": "ath,atl,high24h,low24h,num_market_pairs,cmc_rank,date_added,tags,platform,max_supply,circulating_supply,self_reported_circulating_supply,self_reported_market_cap,total_supply,volume_7d,volume_30d",
    "tagSlugs": "binance-alpha",
}


def fetch_alpha_tokens():
    """从CoinMarketCap拉取币安Alpha列表"""
    print("=" * 70)
    print("  币安Alpha代币列表抓取")
    print("  数据来源: CoinMarketCap API")
    print("=" * 70)
    print()

    print(f"[请求] {API_URL}")
    print(f"[参数] tagSlugs=binance-alpha, limit=200, sortBy=market_cap")
    print()

    try:
        resp = requests.get(API_URL, params=PARAMS, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"[错误] HTTP请求失败: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"[错误] JSON解析失败: {e}")
        return None

    if "data" not in data:
        print(f"[错误] API返回异常: {json.dumps(data, indent=2)[:500]}")
        return None

    # 提取数据
    alpha_list = data["data"]
    total = alpha_list.get("totalCount", 0)
    tokens = alpha_list.get("cryptoCurrencyList", [])

    print(f"[结果] 共 {total} 个Alpha代币，本次获取 {len(tokens)} 个")
    print()

    # 格式化输出
    print("-" * 100)
    print(f"{'#':>3}  {'排名':>5}  {'代币':<10}  {'名称':<25}  {'价格(USD)':>12}  {'24H涨跌':>9}  {'市值':>16}  {'平台':<12}")
    print("-" * 100)

    for i, token in enumerate(tokens, 1):
        rank = token.get("cmcRank", 0)
        symbol = token.get("symbol", "N/A")
        name = token.get("name", "N/A")[:24]
        quotes = token.get("quotes", [{}])[0]

        price = quotes.get("price", 0) or 0
        if price > 1:
            price_str = f"${price:,.2f}"
        elif price > 0.01:
            price_str = f"${price:,.4f}"
        else:
            price_str = f"${price:,.8f}"

        change = quotes.get("percentChange24h", 0) or 0
        market_cap = quotes.get("marketCap", 0) or 0
        if market_cap >= 1e9:
            mc_str = f"${market_cap/1e9:.2f}B"
        elif market_cap >= 1e6:
            mc_str = f"${market_cap/1e6:.2f}M"
        else:
            mc_str = f"${market_cap:,.0f}"

        # 平台
        platform = token.get("platform", {})
        platform_name = platform.get("name", "N/A") if platform else "N/A"

        change_str = f"{change:+.2f}%"

        print(f"{i:>3}  {rank:>5}  {symbol:<10}  {name:<25}  {price_str:>12}  {change_str:>9}  {mc_str:>16}  {platform_name:<12}")

    print("-" * 100)

    # 统计
    positive = sum(1 for t in tokens if (t.get("quotes", [{}])[0].get("percentChange24h") or 0) > 0)
    negative = sum(1 for t in tokens if (t.get("quotes", [{}])[0].get("percentChange24h") or 0) < 0)
    print(f"\n统计: 共{len(tokens)}个代币 | 上涨: {positive} | 下跌: {negative} | 中位排名: {tokens[len(tokens)//2].get('cmcRank', 'N/A') if tokens else 'N/A'}")
    print()

    # 保存JSON
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output = {
        "timestamp": int(datetime.now().timestamp()),
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_count": total,
        "fetched_count": len(tokens),
        "source": "CoinMarketCap",
        "tokens": [
            {
                "rank": t.get("cmcRank"),
                "symbol": t.get("symbol"),
                "name": t.get("name"),
                "slug": t.get("slug"),
                "price_usd": (t.get("quotes") or [{}])[0].get("price"),
                "change_24h_pct": (t.get("quotes") or [{}])[0].get("percentChange24h"),
                "market_cap": (t.get("quotes") or [{}])[0].get("marketCap"),
                "volume_24h": (t.get("quotes") or [{}])[0].get("volume24h"),
                "platform": (t.get("platform") or {}).get("name"),
                "cmc_id": t.get("id"),
            }
            for t in tokens
        ],
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"[保存] JSON数据 -> {OUTPUT_FILE}")

    # 保存纯符号列表（方便导入到系统）
    symbols_only = [t.get("symbol") for t in tokens]
    symbols_file = os.path.join(OUTPUT_DIR, "binance_alpha_symbols.json")
    with open(symbols_file, "w", encoding="utf-8") as f:
        json.dump(symbols_only, f, indent=2, ensure_ascii=False)
    print(f"[保存] 纯符号列表 -> {symbols_file} ({len(symbols_only)} 个)")

    return output


if __name__ == "__main__":
    fetch_alpha_tokens()
