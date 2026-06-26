import os
import sys
import json
import re
import sqlite3
import datetime
import subprocess
import requests
from bs4 import BeautifulSoup
import urllib3

# 略過 SSL 驗證警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 專案根目錄與設定檔路徑
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# 讀取設定檔
def load_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"Error: Config file not found at {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

# 初始化資料庫
def init_db(db_path):
    # 若資料庫所在資料夾不存在，則建立
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    # 建立持股資料表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,          -- 標的代碼 (例如: 00403A, ACPS10)
            stock_code TEXT,      -- 持股個股代碼 (例如: 2330, 基金可為空)
            stock_name TEXT,      -- 持股個股名稱 (例如: 台積電)
            weight REAL,          -- 持股比例 (單位: %)
            date TEXT             -- 抓取/資料日期 (YYYY-MM-DD)
        )
    """)
    # 建立索引加快比對
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbol_date ON holdings (symbol, date)")
    conn.commit()
    return conn

# 抓取 ETF 持股 (CMoney 專區)
def fetch_etf_holdings(symbol, wantgoo_id):
    print(f"Fetching ETF {symbol} (CMoney ID: {wantgoo_id})...")
    url = f"https://www.cmoney.tw/etf/tw/{wantgoo_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(url, headers=headers, verify=False, timeout=15)
        if r.status_code != 200:
            print(f"  Error: HTTP {r.status_code} for {url}")
            return []
        
        # 尋找 window.__NUXT__ 變數
        match = re.search(r'(window\.__NUXT__\s*=\s*.*?);</script>', r.text)
        if not match:
            print(f"  Warning: window.__NUXT__ not found in {url}")
            return []
        
        nuxt_statement = match.group(1)
        # 建構臨時 JS 程式碼交給 node 執行以解析複雜 JavaScript
        js_code = f"var window = {{}};\n{nuxt_statement}\nconsole.log(JSON.stringify(window.__NUXT__));"
        
        temp_js_path = os.path.join(BASE_DIR, f"temp_{wantgoo_id}.js")
        with open(temp_js_path, "w", encoding="utf-8") as f:
            f.write(js_code)
            
        try:
            res = subprocess.run(["node", temp_js_path], capture_output=True, text=True)
            if res.returncode != 0 or not res.stdout.strip():
                print(f"  Error running node for {symbol}: {res.stderr}")
                return []
            
            data = json.loads(res.stdout.strip())
        finally:
            # 刪除臨時 JS 檔案
            if os.path.exists(temp_js_path):
                os.remove(temp_js_path)
        
        # 遞迴搜尋 JSON 結構中的持股明細
        def find_holdings(obj):
            if isinstance(obj, dict):
                if "constituent" in obj and isinstance(obj["constituent"], dict) and "data" in obj["constituent"]:
                    lst = obj["constituent"]["data"]
                    if isinstance(lst, list) and len(lst) > 0 and "code" in lst[0] and "weight" in lst[0]:
                        return lst
                for k, v in obj.items():
                    res = find_holdings(v)
                    if res:
                        return res
            elif isinstance(obj, list):
                for item in obj:
                    res = find_holdings(item)
                    if res:
                        return res
            return None
        
        raw_holdings = find_holdings(data)
        if not raw_holdings:
            print(f"  Warning: No holdings found in JSON structure for {symbol}")
            return []
            
        holdings = []
        for item in raw_holdings:
            code = str(item.get("code", "")).strip()
            name = str(item.get("name", "")).strip()
            # 權重處理成 float
            try:
                weight = float(item.get("weight", 0.0))
            except ValueError:
                weight = 0.0
            if name:
                holdings.append({
                    "stock_code": code,
                    "stock_name": name,
                    "weight": weight
                })
        print(f"  Successfully fetched {len(holdings)} holdings for {symbol}")
        return holdings
        
    except Exception as e:
        print(f"  Error fetching ETF {symbol}: {e}")
        return []

# 抓取共同基金持股 (Cardif MoneyDJ 專區)
def fetch_fund_holdings(symbol):
    print(f"Fetching Fund {symbol} (MoneyDJ)...")
    url = f"https://cardif.moneydj.com/w/wr/wr902.djhtm?a={symbol}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(url, headers=headers, verify=False, timeout=15)
        if r.status_code != 200:
            print(f"  Error: HTTP {r.status_code} for {url}")
            return []
        
        soup = BeautifulSoup(r.text, 'lxml')
        tables = soup.find_all('table')
        
        holdings = []
        for table in tables:
            rows = table.find_all('tr')
            if len(rows) > 0:
                # 取得 Table 表頭欄位
                header = [th.get_text(strip=True) for th in rows[0].find_all(['th', 'td'])]
                if "個股名稱" in header and "比例" in header:
                    for row in rows[1:]:
                        cols = [td.get_text(strip=True) for td in row.find_all(['td', 'th'])]
                        if len(cols) >= 2:
                            stk_name = cols[0].replace("*", "").strip()  # 去除除權息標記 *
                            stk_ratio_str = cols[1].replace("%", "").strip()
                            try:
                                stk_ratio = float(stk_ratio_str)
                            except ValueError:
                                stk_ratio = 0.0
                            if stk_name:
                                holdings.append({
                                    "stock_code": "",  # 基金不提供個股代號，使用空字串
                                    "stock_name": stk_name,
                                    "weight": stk_ratio
                                })
                    break
        if not holdings:
            print(f"  Warning: No holdings found for fund {symbol}")
        else:
            print(f"  Successfully fetched {len(holdings)} holdings for {symbol}")
        return holdings
        
    except Exception as e:
        print(f"  Error fetching Fund {symbol}: {e}")
        return []

# 比對持股並生成報告
def compare_holdings(conn, symbol, new_holdings, target_date):
    cursor = conn.cursor()
    
    # 取得歷史最近一期的日期 (不包含今天即將寫入的，或者如果有今天資料，比對前一天)
    cursor.execute("""
        SELECT DISTINCT date FROM holdings 
        WHERE symbol = ? AND date < ? 
        ORDER BY date DESC LIMIT 1
    """, (symbol, target_date))
    row = cursor.fetchone()
    if not row:
        # 如果是今天第一次抓，直接把今天之前最近的一天抓出來
        cursor.execute("""
            SELECT DISTINCT date FROM holdings 
            WHERE symbol = ? AND date != ? 
            ORDER BY date DESC LIMIT 1
        """, (symbol, target_date))
        row = cursor.fetchone()
        
    if not row:
        print(f"  No historical data to compare for {symbol}. First time setup.")
        return None
        
    prev_date = row[0]
    print(f"  Comparing {symbol} ({target_date}) with previous date ({prev_date})...")
    
    # 載入前一期的持股
    cursor.execute("""
        SELECT stock_name, weight, stock_code FROM holdings 
        WHERE symbol = ? AND date = ?
    """, (symbol, prev_date))
    old_holdings = {r[0]: (r[1], r[2]) for r in cursor.fetchall()}
    
    # 載入最新一期的持股
    new_dict = {item["stock_name"]: (item["weight"], item["stock_code"]) for item in new_holdings}
    
    added = []     # 新增
    removed = []   # 剔除
    increased = [] # 加碼
    decreased = [] # 減碼
    
    # 比對邏輯
    for name, (weight, code) in new_dict.items():
        if name not in old_holdings:
            added.append({"name": name, "code": code, "weight": weight, "diff": weight})
        else:
            old_weight = old_holdings[name][0]
            diff = weight - old_weight
            if diff > 0.001:  # 加碼 (大於 0.0% / 0.1% 噪聲)
                increased.append({"name": name, "code": code, "weight": weight, "diff": diff})
            elif diff < -0.001:  # 減碼
                decreased.append({"name": name, "code": code, "weight": weight, "diff": diff})
                
    for name, (old_weight, code) in old_holdings.items():
        if name not in new_dict:
            removed.append({"name": name, "code": code, "weight": old_weight, "diff": -old_weight})
            
    # 格式化變動
    has_changes = bool(added or removed or increased or decreased)
    return {
        "prev_date": prev_date,
        "has_changes": has_changes,
        "added": added,
        "removed": removed,
        "increased": increased,
        "decreased": decreased
    }

# 儲存持股至資料庫
def save_holdings(conn, symbol, holdings, target_date):
    cursor = conn.cursor()
    # 先清除今天可能已寫入的重複資料，確保冪等性
    cursor.execute("DELETE FROM holdings WHERE symbol = ? AND date = ?", (symbol, target_date))
    
    # 插入新資料
    for item in holdings:
        cursor.execute("""
            INSERT INTO holdings (symbol, stock_code, stock_name, weight, date)
            VALUES (?, ?, ?, ?, ?)
        """, (symbol, item["stock_code"], item["stock_name"], item["weight"], target_date))
    conn.commit()

# 發送 LINE Notify
def send_line_notify(token, message):
    if not token:
        print("LINE Notify token not set. Skip sending.")
        return False
    url = "https://notify-api.line.me/api/notify"
    headers = {
        "Authorization": f"Bearer {token}"
    }
    data = {
        "message": message
    }
    try:
        r = requests.post(url, headers=headers, data=data, timeout=10)
        if r.status_code == 200:
            print("LINE Notify sent successfully.")
            return True
        else:
            print(f"Failed to send LINE Notify: HTTP {r.status_code} - {r.text}")
            return False
    except Exception as e:
        print(f"Error sending LINE Notify: {e}")
        return False

# 產生 Markdown 報告與通知
def generate_reports(config, results, today_str):
    reports_dir = config.get("reports_dir", "./reports")
    # 將相對路徑轉換為絕對路徑
    if not os.path.isabs(reports_dir):
        reports_dir = os.path.join(BASE_DIR, reports_dir)
        
    if not os.path.exists(reports_dir):
        os.makedirs(reports_dir)
        
    report_path = os.path.join(reports_dir, f"{today_str}.md")
    
    # 建構報告內容
    lines = []
    lines.append(f"# 投資標的持股變動每日追蹤報告 ({today_str})")
    lines.append(f"更新時間: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # LINE 通知用字串
    notify_lines = []
    notify_lines.append(f"\n【投資標的持股變動通知 {today_str}】")
    
    any_change = False
    
    # 處理每一個標的
    for res in results:
        symbol = res["symbol"]
        name = res["name"]
        compare = res["compare"]
        
        lines.append(f"## {name} ({symbol})")
        
        if not compare:
            lines.append("  *這是第一次建立基準資料，或無昨日歷史資料可用於比對。*\n")
            continue
            
        if not compare["has_changes"]:
            lines.append("  *今日持股無變動。*\n")
            continue
            
        any_change = True
        notify_lines.append(f"\n🔔 {name} ({symbol})")
        
        # 新增
        if compare["added"]:
            lines.append("### ➕ 新增持股")
            notify_lines.append("  ➕ 新增:")
            for item in compare["added"]:
                code_str = f"({item['code']})" if item['code'] else ""
                lines.append(f"  - {item['name']}{code_str}: 新增比例 {item['weight']}%")
                notify_lines.append(f"    - {item['name']}: {item['weight']}%")
                
        # 剔除
        if compare["removed"]:
            lines.append("### ❌ 剔除持股")
            notify_lines.append("  ❌ 剔除:")
            for item in compare["removed"]:
                code_str = f"({item['code']})" if item['code'] else ""
                lines.append(f"  - {item['name']}{code_str}: 原比例 {item['weight']}%")
                notify_lines.append(f"    - {item['name']} (原 {item['weight']}%)")
                
        # 加碼
        if compare["increased"]:
            lines.append("### 📈 加碼持股")
            notify_lines.append("  📈 加碼:")
            for item in compare["increased"]:
                code_str = f"({item['code']})" if item['code'] else ""
                lines.append(f"  - {item['name']}{code_str}: {item['weight']}% (增加 +{item['diff']:.2f}%)")
                notify_lines.append(f"    - {item['name']}: {item['weight']}% (+{item['diff']:.2f}%)")
                
        # 減碼
        if compare["decreased"]:
            lines.append("### 📉 減碼持股")
            notify_lines.append("  📉 減碼:")
            for item in compare["decreased"]:
                code_str = f"({item['code']})" if item['code'] else ""
                lines.append(f"  - {item['name']}{code_str}: {item['weight']}% (減少 {item['diff']:.2f}%)")
                notify_lines.append(f"    - {item['name']}: {item['weight']}% ({item['diff']:.2f}%)")
                
        lines.append("")
        
    report_content = "\n".join(lines)
    
    # 寫入本地報告
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)
    print(f"Report saved to {report_path}")
    
    # 寫入 Obsidian Vault
    obsidian_path = config.get("obsidian_vault_path", "").strip()
    if obsidian_path:
        if os.path.exists(obsidian_path):
            obsidian_file = os.path.join(obsidian_path, f"{today_str}-投資持股追蹤.md")
            try:
                with open(obsidian_file, "w", encoding="utf-8") as f:
                    f.write(report_content)
                print(f"Report synced to Obsidian Vault at {obsidian_file}")
            except Exception as e:
                print(f"Failed to sync to Obsidian: {e}")
        else:
            print(f"Warning: Obsidian Vault path '{obsidian_path}' does not exist.")
            
    # 發送 LINE 通知
    if any_change:
        token = os.environ.get("LINE_NOTIFY_TOKEN") or config.get("line_notify_token", "").strip()
        send_line_notify(token, "\n".join(notify_lines))
    else:
        print("No changes across all tracked symbols today. Skip LINE Notify.")

    # 寫入 data.js 供網頁使用
    data_js_path = os.path.join(BASE_DIR, "data.js")
    dashboard_data = {
        "updateTime": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "date": today_str,
        "results": results
    }
    try:
        with open(data_js_path, "w", encoding="utf-8") as f:
            f.write(f"window.dashboardData = {json.dumps(dashboard_data, ensure_ascii=False, indent=2)};")
        print(f"Web dashboard data JS saved to {data_js_path}")
    except Exception as e:
        print(f"Failed to save Web data.js: {e}")

# 主程式入口
def main():
    config = load_config()
    
    # 解析指令參數
    args = sys.argv[1:]
    
    # 測試爬蟲功能
    if "--test-crawl" in args:
        print("=== Test Crawling Mode ===")
        # 測試第一個 ETF
        if config.get("etfs"):
            etf = config["etfs"][0]
            holdings = fetch_etf_holdings(etf["symbol"], etf["wantgoo_id"])
            print(f"\nResult for ETF {etf['name']}:")
            for h in holdings[:5]:
                print(f"  {h['stock_name']} ({h['stock_code']}): {h['weight']}%")
        # 測試第一個基金
        if config.get("funds"):
            fund = config["funds"][0]
            holdings = fetch_fund_holdings(fund["symbol"])
            print(f"\nResult for Fund {fund['name']}:")
            for h in holdings[:5]:
                print(f"  {h['stock_name']}: {h['weight']}%")
        print("\nTest crawl finished.")
        sys.exit(0)
        
    # 測試 LINE 通知功能
    if "--test-notify" in args:
        print("=== Test Notification Mode ===")
        token = os.environ.get("LINE_NOTIFY_TOKEN") or config.get("line_notify_token", "").strip()
        if not token:
            print("Error: LINE_NOTIFY_TOKEN not set in environment or config.json")
            sys.exit(1)
        test_msg = f"\n🔔 【投資追蹤系統測試】\n本訊息為 LINE Notify 測試通知，發送時間為 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}。\n系統已準備就緒！"
        send_line_notify(token, test_msg)
        sys.exit(0)

    # 執行正常流程
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    
    db_path = config.get("db_path", "./data/holdings.db")
    if not os.path.isabs(db_path):
        db_path = os.path.join(BASE_DIR, db_path)
        
    conn = init_db(db_path)
    
    results = []
    
    # 1. 抓取與處理 ETF
    for etf in config.get("etfs", []):
        symbol = etf["symbol"]
        name = etf["name"]
        wantgoo_id = etf["wantgoo_id"]
        
        # 抓取最新持股
        new_holdings = fetch_etf_holdings(symbol, wantgoo_id)
        if new_holdings:
            # 持股比對
            compare = compare_holdings(conn, symbol, new_holdings, today_str)
            # 儲存到 SQLite
            save_holdings(conn, symbol, new_holdings, today_str)
            
            results.append({
                "symbol": symbol,
                "name": name,
                "holdings": new_holdings,
                "compare": compare
            })
        else:
            print(f"Failed to fetch data for ETF {name} ({symbol}) today.")
            
    # 2. 抓取與處理基金
    for fund in config.get("funds", []):
        symbol = fund["symbol"]
        name = fund["name"]
        
        # 抓取最新持股
        new_holdings = fetch_fund_holdings(symbol)
        if new_holdings:
            # 持股比對
            compare = compare_holdings(conn, symbol, new_holdings, today_str)
            # 儲存到 SQLite
            save_holdings(conn, symbol, new_holdings, today_str)
            
            results.append({
                "symbol": symbol,
                "name": name,
                "holdings": new_holdings,
                "compare": compare
            })
        else:
            print(f"Failed to fetch data for Fund {name} ({symbol}) today.")
            
    # 3. 關閉資料庫連接
    conn.close()
    
    # 4. 產生報告並通知
    if results:
        generate_reports(config, results, today_str)
    else:
        print("No results fetched today. Skip report generation.")

if __name__ == "__main__":
    main()
