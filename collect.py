#!/usr/bin/env python3
"""
额度LED — 采集 Codex / Cursor / Claude Code 三个订阅的剩余额度，输出 JSON。
被 Übersicht 挂件调用。纯本地 + 各服务自身接口，只读自己的账号数据。
"""
import os, re, json, glob, base64, sqlite3, subprocess, time, urllib.request, urllib.error

HOME = os.path.expanduser("~")
NOW = int(time.time())
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config():
    try:
        return json.load(open(CONFIG_PATH))
    except Exception:
        return {}


CONFIG = load_config()


class _Redirect308(urllib.request.HTTPRedirectHandler):
    # 系统自带 Python 3.9 的 urllib 不会跟随 308，这里补上。
    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_307


_OPENER = urllib.request.build_opener(_Redirect308)


def http_get_json(url, headers, timeout=12):
    req = urllib.request.Request(url, headers=headers)
    return json.loads(_OPENER.open(req, timeout=timeout).read().decode())


def _ok(**kw):
    d = {"ok": True}
    d.update(kw)
    return d


def _err(msg, **kw):
    d = {"ok": False, "error": msg}
    d.update(kw)
    return d


# ---------------------------------------------------------------- Codex
def collect_codex():
    """读最新 session 文件里的 rate_limits 快照（5h + 周窗口）。纯本地。"""
    files = glob.glob(os.path.join(HOME, ".codex/sessions/**/*.jsonl"), recursive=True)
    if not files:
        return _err("无 codex session 文件")
    latest = max(files, key=os.path.getmtime)
    try:
        text = open(latest, "r", errors="ignore").read()
    except Exception as e:
        return _err(f"读取失败: {e}")

    # 找最后一个 "rate_limits":{...}，做花括号配对提取
    idx = text.rfind('"rate_limits":')
    if idx == -1:
        return _err("session 中无 rate_limits（该会话还没产生用量）")
    start = text.find("{", idx)
    depth, end = 0, -1
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        return _err("rate_limits 解析失败")
    try:
        rl = json.loads(text[start:end])
    except Exception as e:
        return _err(f"rate_limits JSON 解析失败: {e}")

    def win(d):
        if not d or d.get("used_percent") is None:
            return None
        used = float(d["used_percent"])
        return {
            "used": round(used, 1),
            "remaining": round(100 - used, 1),
            "resets_at": d.get("resets_at"),
            "window_minutes": d.get("window_minutes"),
        }

    primary = win(rl.get("primary"))
    secondary = win(rl.get("secondary"))
    if not primary:
        return _err("无 primary 窗口数据")
    rows = [{"label": "5h", "remaining": primary["remaining"], "resets_at": primary.get("resets_at")}]
    if secondary:
        rows.append({"label": "周", "remaining": secondary["remaining"], "resets_at": secondary.get("resets_at")})
    return _ok(plan="ChatGPT", source="local",
               headline={"remaining": primary["remaining"], "label": "5h 剩余"},
               rows=rows)


# ---------------------------------------------------------------- Cursor
def _cursor_token():
    db = os.path.join(HOME, "Library/Application Support/Cursor/User/globalStorage/state.vscdb")
    if not os.path.exists(db):
        return None, None
    # 只读 + immutable，避免 Cursor 运行时锁库
    uri = f"file:{urllib.request.pathname2url(db)}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True, timeout=3)
    try:
        cur = con.cursor()

        def g(k):
            cur.execute("SELECT value FROM ItemTable WHERE key=?", (k,))
            r = cur.fetchone()
            return r[0] if r else None

        tok = g("cursorAuth/accessToken")
        memb = g("cursorAuth/stripeMembershipType")
        return tok, memb
    finally:
        con.close()


def collect_cursor():
    tok, memb = _cursor_token()
    if not tok:
        return _err("未找到 Cursor 登录态")
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        sub = json.loads(base64.urlsafe_b64decode(p))["sub"]
    except Exception as e:
        return _err(f"token 解析失败: {e}")
    cookie = f"WorkosCursorSessionToken={sub}::{tok}"
    try:
        body = http_get_json(
            "https://cursor.com/api/usage-summary",
            {"Cookie": cookie, "User-Agent": "Mozilla/5.0"},
        )
    except Exception as e:
        return _err(f"接口请求失败: {e}", plan=memb)

    plan = (body.get("individualUsage") or {}).get("plan") or {}
    total = plan.get("totalPercentUsed")
    if total is None:
        return _err("接口未返回用量百分比", plan=memb)
    auto = plan.get("autoPercentUsed")
    api = plan.get("apiPercentUsed")
    reset = body.get("billingCycleEnd")
    reset_ts = None
    if reset:
        try:
            import datetime
            reset_ts = int(datetime.datetime.fromisoformat(reset.replace("Z", "+00:00")).timestamp())
        except Exception:
            pass

    def rem(x):
        return round(100 - float(x), 1) if x is not None else None

    rows = [{"label": "总", "remaining": rem(total), "resets_at": reset_ts}]
    if auto is not None:
        rows.append({"label": "Auto+Composer", "remaining": rem(auto)})
    if api is not None:
        rows.append({"label": "API", "remaining": rem(api)})
    return _ok(
        plan=(memb or body.get("membershipType") or "").capitalize() or "Cursor",
        source="api",
        headline={"remaining": rem(total), "label": "总剩余"},
        rows=rows,
    )


# ---------------------------------------------------------------- Claude Code
def _claude_keychain_token():
    """读 `claude login` 写入的主账号 token（钥匙串 Claude Code-credentials）。"""
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    try:
        oa = json.loads(r.stdout).get("claudeAiOauth")
    except Exception:
        return None
    if oa and oa.get("accessToken"):
        return oa
    return None


def _claude_oauth_usage(oa):
    tok = oa["accessToken"]
    hdrs = {
        "Authorization": f"Bearer {tok}",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-cli",
        "Content-Type": "application/json",
    }
    return http_get_json("https://api.anthropic.com/api/oauth/usage", hdrs)


def _claude_local_estimate():
    """无 token 时的兜底：按本地日志统计近 5h / 7d 消耗的 token（近似）。"""
    files = glob.glob(os.path.join(HOME, ".claude/projects/**/*.jsonl"), recursive=True)
    if not files:
        return None
    import datetime
    count_cr = bool(CONFIG.get("claude", {}).get("count_cache_read", False))
    sum5h = sum7d = 0
    t5 = NOW - 5 * 3600
    t7 = NOW - 7 * 86400
    for f in files:
        if os.path.getmtime(f) < t7:
            continue
        try:
            for line in open(f, "r", errors="ignore"):
                if '"usage"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                u = (o.get("message") or {}).get("usage") or {}
                if not u:
                    continue
                ts = o.get("timestamp")
                if not ts:
                    continue
                try:
                    et = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except Exception:
                    continue
                tot = (u.get("input_tokens", 0) + u.get("output_tokens", 0)
                       + u.get("cache_creation_input_tokens", 0))
                if count_cr:
                    tot += u.get("cache_read_input_tokens", 0)
                if et >= t7:
                    sum7d += tot
                if et >= t5:
                    sum5h += tot
        except Exception:
            continue
    return {"tokens_5h": sum5h, "tokens_7d": sum7d}


def collect_claude():
    # 默认不读钥匙串（本机无主账号 token，且读取会弹授权框）。
    # 若日后做了常规 `claude login`，设 QUOTA_CLAUDE_KEYCHAIN=1 即可启用实时接口。
    oa = _claude_keychain_token() if os.environ.get("QUOTA_CLAUDE_KEYCHAIN") == "1" else None
    if oa:
        if oa.get("expiresAt") and oa["expiresAt"] / 1000 < NOW:
            return _err("token 已过期，请重新登录", plan="Claude")
        try:
            data = _claude_oauth_usage(oa)
        except Exception as e:
            return _err(f"usage 接口失败: {e}", plan="Claude")
        # usage 接口字段名可能随版本变化，尽量自适应提取百分比
        def find_pct(d, *keys):
            for k in keys:
                if isinstance(d, dict) and d.get(k) is not None:
                    return float(d[k])
            return None
        five = data.get("five_hour") or data.get("fiveHour") or {}
        week = data.get("seven_day") or data.get("week") or data.get("weekly") or {}
        p5 = find_pct(five, "utilization", "used_percent", "percent")
        pw = find_pct(week, "utilization", "used_percent", "percent")
        if p5 is None and pw is None:
            return _ok(plan="Claude", raw=data, note="接口返回，但未识别百分比字段")
        rows, headline = [], None
        if p5 is not None:
            r5 = {"label": "5h", "remaining": round(100 - p5, 1), "resets_at": five.get("resets_at")}
            rows.append(r5)
            headline = {"remaining": r5["remaining"], "label": "5h 剩余"}
        if pw is not None:
            rows.append({"label": "周", "remaining": round(100 - pw, 1), "resets_at": week.get("resets_at")})
        if not headline and rows:
            headline = {"remaining": rows[0]["remaining"], "label": rows[0]["label"]}
        return _ok(plan="Claude", source="api", headline=headline, rows=rows)

    # 兜底：本地估算 → 按 config 的额度上限换算成百分比
    est = _claude_local_estimate()
    if not est:
        return _err("本机无订阅 token，且无本地日志", plan="Claude")
    cfg = CONFIG.get("claude", {})
    cap5 = float(cfg.get("cap_5h_tokens") or 0)
    cap7 = float(cfg.get("cap_7d_tokens") or 0)

    def pct_window(tokens, cap, label):
        if cap <= 0:
            return None
        used = min(100.0, tokens / cap * 100.0)
        return {"used": round(used, 1), "remaining": round(100 - used, 1),
                "label": label, "tokens": tokens, "cap": int(cap)}

    primary = pct_window(est["tokens_5h"], cap5, "5h")
    secondary = pct_window(est["tokens_7d"], cap7, "周")
    if not primary:
        # 没配上限就退回显示原始消耗量
        return _err("仅本地估算（未配额度上限）", plan="Claude", estimate=est)

    def sub(w):
        return f"{w['tokens'] / 1e6:.2f}M/{w['cap'] / 1e6:.0f}M"

    rows = [{"label": "5h", "remaining": primary["remaining"], "sub": sub(primary)}]
    if secondary:
        rows.append({"label": "周", "remaining": secondary["remaining"], "sub": sub(secondary)})
    return _ok(plan="Claude", source="estimate", approx=True,
               headline={"remaining": primary["remaining"], "label": "5h 剩余"},
               rows=rows)


# ---------------------------------------------------------------- main
def main():
    result = {"ts": NOW, "services": {}}
    for fn, key in ((collect_codex, "codex"), (collect_cursor, "cursor"), (collect_claude, "claude")):
        try:
            result["services"][key] = fn()
        except Exception as e:
            result["services"][key] = _err(f"采集异常: {e}")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
