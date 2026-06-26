#!/bin/bash

# 取得目前腳本所在的絕對路徑
BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PORT=8000

echo "=== 投資標的換股追蹤 Web 儀表板啟動器 ==="

# 檢查 port 8000 是否被佔用
if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null ; then
    echo "警告: 通訊埠 (Port) $PORT 已被佔用。"
    # 尋找另一個可用的 port
    for p in {8001..8010}; do
        if ! lsof -Pi :$p -sTCP:LISTEN -t >/dev/null ; then
            PORT=$p
            break
        fi
    done
fi

echo "1. 即將在 http://localhost:$PORT 啟動本地 Web 伺服器..."
echo "2. 啟動後將自動為您開啟瀏覽器。"
echo "3. 若要結束服務，請在終端機按下 Ctrl + C。"
echo ""

# 延遲 1.5 秒後在預設瀏覽器開啟網頁 (背景執行，確保 server 先起來)
(sleep 1.5 && open "http://localhost:$PORT") &

# 啟動 Python 內建 HTTP Server
cd "$BASE_DIR"
python3 -m http.server $PORT
