#!/bin/bash

# 取得目前腳本所在的絕對路徑
BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PLIST_NAME="com.user.investment_tracker.plist"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== 開始設定 macOS launchd 定時排程 ==="

# 建立 plist 檔案內容
cat <<EOF > "$PLIST_NAME"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.investment_tracker</string>
    <key>ProgramArguments</key>
    <array>
        <string>$BASE_DIR/venv/bin/python</string>
        <string>$BASE_DIR/tracker.py</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>20</integer>
        <key>Minute</key>
        <integer>30</integer>
    </dict>
    <key>WorkingDirectory</key>
    <string>$BASE_DIR</string>
    <key>StandardOutPath</key>
    <string>$BASE_DIR/data/tracker_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$BASE_DIR/data/tracker_stderr.log</string>
</dict>
</plist>
EOF

echo "1. 已生成 plist 設定檔: $PLIST_NAME"

# 複製到 macOS LaunchAgents 目錄
cp "$PLIST_NAME" "$PLIST_PATH"
echo "2. 已將設定檔複製到 LaunchAgents: $PLIST_PATH"

# 卸載舊排程 (若存在)
launchctl unload "$PLIST_PATH" 2>/dev/null
# 載入新排程
launchctl load "$PLIST_PATH"
echo "3. 已成功載入 launchd 定時任務！"

# 測試定時任務是否註冊成功
echo "4. 檢查 launchd 中的服務狀態:"
launchctl list | grep investment_tracker

echo "====================================="
echo "排程設定完成！"
echo "系統將於每日晚上 20:30 自動執行 tracker.py 進行換股追蹤。"
echo "日誌檔路徑:"
echo "  - 標準輸出: $BASE_DIR/data/tracker_stdout.log"
echo "  - 標準錯誤: $BASE_DIR/data/tracker_stderr.log"
echo "====================================="

echo "Done"
