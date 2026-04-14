import pandas as pd
import numpy as np
import ta
import tushare as ts
import requests
from datetime import datetime, timedelta
import sys
import time
import pytz # 需要安装 pytz 来精准处理时区

# ===================== 配置区域 =====================
# 1. 填入你的 Tushare Token
TUSHARE_TOKEN = "57f868042fd4ef8ffdfd9b29bba61e694514425ec4df2ab07aff96dc"
# 2. 填入你的 ServerChan Key (微信推送)
SCT_KEY = "${{ secrets.SCTKEY }}" 

# 股票代码池
STOCKS = [
    ("002446.SZ", "盛路通信"), ("000063.SZ", "中兴通讯"),
    ("603019.SH", "中科曙光"), ("002156.SZ", "通富微电"),
    ("002463.SZ", "沪电股份"), ("600183.SH", "生益科技"),
    ("600406.SH", "国电南瑞"), ("600089.SH", "特变电工"),
    ("002384.SZ", "东山精密"), ("601179.SH", "中国西电")
]

# ===================== 核心逻辑 =====================

def get_beijing_time():
    """获取精准的北京时间"""
    utc_now = datetime.now(pytz.utc)
    beijing_tz = pytz.timezone('Asia/Shanghai')
    return utc_now.astimezone(beijing_tz)

def is_trading_day():
    """判断今天是否为A股交易日"""
    now = get_beijing_time()
    today_str = now.strftime("%Y-%m-%d")
    weekday = now.weekday() # 0=Mon, 6=Sun

    # 周末判断
    if weekday >= 5:
        return False, "周末休市"

    # 2026年节假日硬编码 (可根据每年情况更新)
    holidays = {
        "2026-01-01", "2026-01-22", "2026-01-23", "2026-01-24", "2026-01-25",
        "2026-01-26", "2026-01-27", "2026-01-28", "2026-04-05", "2026-04-06",
        "2026-05-01", "2026-05-02", "2026-05-03", "2026-06-19", "2026-06-20",
        "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
        "2026-10-06", "2026-10-07"
    }
    
    if today_str in holidays:
        return False, "节假日休市"
    
    return True, today_str

def get_stock_news(code):
    """获取最新公告 (带异常处理)"""
    try:
        code_short = code.split(".")[0]
        url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
        data = {"stock": code_short, "pageSize": 2, "pageNum": 1}
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.post(url, data=data, headers=headers, timeout=5)
        if r.status_code != 200: return []
        
        json_data = r.json()
        # 简单过滤标题长度，防止空标题
        news = [ann["announcementTitle"] for ann in json_data.get("announcements", []) 
                if len(ann.get("announcementTitle", "")) > 5]
        return news[:2]
    except:
        return []

def judge_news(title):
    """简单判断利好利空"""
    good = ["预增", "扭亏", "中标", "合同", "订单", "增持", "回购", "重组", "收购", "分红", "喜报"]
    bad = ["亏损", "下降", "减持", "解禁", "立案", "处罚", "退市", "风险", "终止", "违约", "警示"]
    
    if any(k in title for k in good): return "✅利好"
    if any(k in title for k in bad): return "❌利空"
    return "ℹ️公告"

def fetch_data_with_retry(code, retries=3):
    """带重试机制的数据获取"""
    pro = ts.pro_api(TUSHARE_TOKEN)
    for i in range(retries):
        try:
            # 获取最近60天数据，确保覆盖均线周期
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
            
            df = pro.daily(ts_code=code, start_date=start_date, end_date=end_date)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            print(f"尝试第 {i+1} 次获取 {code} 失败: {e}")
            time.sleep(1.5) # 等待后重试
    return None

def analyze_stock(code, name):
    """单只股票分析逻辑"""
    df_raw = fetch_data_with_retry(code)
    if df_raw is None or len(df_raw) < 20:
        return f"⚠️ {name}: 数据获取失败或数据不足"

    # 数据处理
    df = df_raw.rename(columns={'trade_date':'date','vol':'volume'})
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    
    # 计算指标
    close = df['close']
    volume = df['volume']
    
    # 均线
    df['ma5'] = ta.trend.sma_indicator(close, 5)
    df['ma10'] = ta.trend.sma_indicator(close, 10)
    df['ma20'] = ta.trend.sma_indicator(close, 20)
    
    # MACD
    macd_indicator = ta.trend.MACD(close)
    df['macd'] = macd_indicator.macd()
    df['signal'] = macd_indicator.macd_signal()
    
    # RSI & Bollinger
    df['rsi'] = ta.momentum.rsi(close, 14)
    bb = ta.volatility.BollingerBands(close, 20, 2)
    df['bb_u'] = bb.bollinger_hband()
    df['bb_l'] = bb.bollinger_lband()

    # 获取最新数据
    d = df.iloc[-1]
    d_prev = df.iloc[-2] # 前一天数据用于对比

    # --- 策略判断 ---
    
    # 1. 趋势
    if d.close > d.ma5 > d.ma10 > d.ma20:
        trend = "📈多头"
    elif d.close < d.ma5 < d.ma10 < d.ma20:
        trend = "📉空头"
    else:
        trend = "〰️震荡"

    # 2. MACD 金叉/死叉
    macd_status = ""
    if d.macd > d.signal and d_prev.macd <= d_prev.signal:
        macd_status = "✨金叉"
    elif d.macd < d.signal and d_prev.macd >= d_prev.signal:
        macd_status = "💀死叉"
    
    # 3. 涨跌幅
    pct_change = d['pct_chg'] if 'pct_chg' in d else 0.0
    color = "🔺" if pct_change >= 0 else "🔻"
    
    # 4. 公告
    news_list = get_stock_news(code)
    news_str = ""
    if news_list:
        news_str = "%0A".join([f"{judge_news(t)} {t}" for t in news_list])
        news_str = f"%0A📢 {news_str}"

    # --- 格式化输出 ---
    report = (
        f"🔹 *{name}* ({code.split('.')[0]})%0A"
        f"{color} 收盘: {round(d.close, 2)} ({pct_change:+.2f}%)%0A"
        f"📊 趋势: {trend} | RSI: {round(d.rsi, 1)} {macd_status}%0A"
        f"🛡️ 支撑: {round(d.bb_l, 2)} | 压力: {round(d.bb_u, 2)}"
        f"{news_str}"
    )
    return report

# ===================== 主程序入口 =====================
if __name__ == "__main__":
    is_trading, date_str = is_trading_day()
    
    if not is_trading:
        print(f"MSG=📅 {date_str} 非交易日，今日不推送。")
        sys.exit()

    final_output = f"*📅 {date_str} 早盘量化简报*%0A%0A"
    
    for code, name in STOCKS:
        try:
            res = analyze_stock(code, name)
            final_output += res + "%0A%0A" + ("-"*30) + "%0A"
            time.sleep(0.3) # 防止请求过快
        except Exception as e:
            final_output += f"⚠️ {name} 分析出错%0A"

    # 输出给 GitHub Actions
    print(f"MSG={final_output}")
