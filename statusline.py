#!/usr/bin/env python3
"""
额度LED 的 Claude 取数桥：作为 Claude Code 的 statusLine 命令。
Claude Code 会把含 rate_limits 的会话 JSON 通过 stdin 传进来；
本脚本把官方限额快照写入缓存文件，供 collect.py 读取（无需 token）。
同时打印一行状态栏文字（model · 上下文% · 5h/周 已用%）。
"""
import sys, os, json, time, tempfile

CACHE = os.path.expanduser("~/.claude/quota-led-claude.json")


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        print("")
        return

    rl = data.get("rate_limits") or {}
    five = rl.get("five_hour") or {}
    week = rl.get("seven_day") or {}

    # 仅当拿到限额数据时才写缓存（避免用空数据覆盖上次的好值）
    if five.get("used_percentage") is not None or week.get("used_percentage") is not None:
        try:
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(CACHE))
            with os.fdopen(fd, "w") as f:
                json.dump({"ts": int(time.time()), "rate_limits": rl}, f)
            os.replace(tmp, CACHE)
        except Exception:
            pass

    # 状态栏文字（"用"=已用，与 Claude 自带 /usage 口径一致）
    parts = [(data.get("model") or {}).get("display_name", "Claude")]
    cw = data.get("context_window") or {}
    if cw.get("used_percentage") is not None:
        parts.append(f"ctx {cw['used_percentage']}%")
    if five.get("used_percentage") is not None:
        parts.append(f"5h用{round(five['used_percentage'])}%")
    if week.get("used_percentage") is not None:
        parts.append(f"周用{round(week['used_percentage'])}%")
    print("  ".join(parts))


if __name__ == "__main__":
    main()
