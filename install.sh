#!/bin/bash
# 安装到 Übersicht：把本仓库作为一个「文件夹挂件」软链到 widgets 目录。
# 这样 quota.jsx 与 collect.py 同目录，command 用相对路径即可，无需硬编码个人路径。
set -e
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
WIDGETS="$HOME/Library/Application Support/Übersicht/widgets"
LINK="$WIDGETS/quota-led"

mkdir -p "$WIDGETS"

# 清掉早期可能残留的单文件安装
rm -f "$WIDGETS/quota.jsx"

# 软链整个文件夹（pull 更新后自动生效，无需重装）
rm -rf "$LINK"
ln -s "$SRC_DIR" "$LINK"
echo "✅ 已链接挂件目录 -> $LINK"

if ! pgrep -f "bersicht" >/dev/null; then
  open -a "Übersicht" && echo "✅ 已启动 Übersicht"
else
  osascript -e 'tell application id "tracesOf.Uebersicht" to refresh' 2>/dev/null || true
  echo "✅ 已通知 Übersicht 刷新"
fi
echo "完成。挂件默认出现在桌面左下角，拖标题栏可移动。"
