#!/usr/bin/env python3
"""
额度LED — 采集 Codex / Cursor / Claude Code 三个订阅的剩余额度，输出 JSON。
被 Übersicht 挂件调用。纯本地 + 各服务自身接口，只读自己的账号数据。
"""
import os, re, json, glob, base64, sqlite3, time, urllib.request, urllib.error

HOME = os.path.expanduser("~")
NOW = int(time.time())


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
def _claude_statusline_cache():
    """读 statusline.py 写入的官方 rate_limits 快照（Claude Code 喂的真实数据）。"""
    path = os.path.expanduser("~/.claude/quota-led-claude.json")
    if not os.path.exists(path):
        return None
    try:
        d = json.load(open(path))
    except Exception:
        return None
    rl = d.get("rate_limits") or {}

    def mk(w, label):
        up = (w or {}).get("used_percentage")
        if up is None:
            return None
        return {"label": label, "remaining": round(100 - float(up), 1),
                "resets_at": (w or {}).get("resets_at")}

    rows = []
    r5 = mk(rl.get("five_hour"), "5h")
    r7 = mk(rl.get("seven_day"), "周")
    if r5:
        rows.append(r5)
    if r7:
        rows.append(r7)
    if not rows:
        return None
    headline = {"remaining": rows[0]["remaining"], "label": rows[0]["label"] + " 剩余"}
    return {"headline": headline, "rows": rows, "asof": d.get("ts")}


def _parse_claude_reset(text, event_ts):
    m = re.search(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(([^)]+)\)", text, re.I)
    if not m or not event_ts:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = m.group(3).lower()
    tz_name = m.group(4)
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0

    import datetime
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        # 系统 Python 找不到 IANA tzdata 时，至少稳定处理本机常见的 Asia/Shanghai。
        offsets = {"Asia/Shanghai": 8 * 3600}
        offset = offsets.get(tz_name)
        if offset is None:
            return None
        tz = datetime.timezone(datetime.timedelta(seconds=offset))
    event_local = datetime.datetime.fromtimestamp(event_ts, tz)
    reset_local = event_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if reset_local.timestamp() <= event_ts:
        reset_local += datetime.timedelta(days=1)
    return int(reset_local.timestamp())


def _claude_session_limit_from_logs():
    """读 Claude Code 本地日志中的 429 session limit，触顶时显示官方 0% 剩余。"""
    files = glob.glob(os.path.join(HOME, ".claude/projects/**/*.jsonl"), recursive=True)
    if not files:
        return None

    latest = None
    for f in sorted(files, key=os.path.getmtime, reverse=True):
        if os.path.getmtime(f) < NOW - 7 * 86400:
            break
        try:
            for line in open(f, "r", errors="ignore"):
                if "session limit" not in line and "rate_limit" not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("error") != "rate_limit" or o.get("apiErrorStatus") != 429:
                    continue
                text = ""
                for part in (o.get("message") or {}).get("content") or []:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text += part.get("text") or ""
                if "session limit" not in text:
                    continue
                ts = None
                raw_ts = o.get("timestamp")
                if raw_ts:
                    try:
                        import datetime
                        ts = int(datetime.datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).timestamp())
                    except Exception:
                        ts = None
                ts = ts or int(os.path.getmtime(f))
                if not latest or ts > latest["asof"]:
                    latest = {"asof": ts, "text": text, "reset": _parse_claude_reset(text, ts)}
        except Exception:
            continue

    if not latest:
        return None
    reset_at = latest.get("reset")
    if reset_at and reset_at <= NOW:
        return None
    if not reset_at and latest["asof"] < NOW - 5 * 3600:
        return None
    row = {"label": "5h", "remaining": 0.0}
    if reset_at:
        row["resets_at"] = reset_at
    else:
        row["sub"] = "已触顶"
    return {"headline": {"remaining": 0.0, "label": "5h 剩余"},
            "rows": [row], "asof": latest["asof"], "note": latest["text"]}


def _merge_claude_limit_with_statusline(limit, sl):
    """5h 触顶以 0% 为准；周额度仍沿用 statusLine 的官方快照。"""
    if not sl:
        return limit
    limit_row = limit["rows"][0]
    rows = []
    saw_5h = False
    for row in sl.get("rows") or []:
        if row.get("label") == "5h":
            rows.append(limit_row)
            saw_5h = True
        else:
            rows.append(row)
    if not saw_5h:
        rows.insert(0, limit_row)
    merged = dict(limit)
    merged["rows"] = rows
    return merged


def collect_claude():
    # 1) 官方实时数据：来自 statusLine 写入的缓存（Claude Code 自己取到的真实限额）
    sl = _claude_statusline_cache()
    limit = _claude_session_limit_from_logs()
    if limit and (not sl or limit.get("asof", 0) >= sl.get("asof", 0)):
        limit = _merge_claude_limit_with_statusline(limit, sl)
        return _ok(plan="Claude", source="session_limit",
                   headline=limit["headline"], rows=limit["rows"], asof=limit.get("asof"))
    if sl:
        return _ok(plan="Claude", source="statusline",
                   headline=sl["headline"], rows=sl["rows"], asof=sl.get("asof"))
    return _err("暂无 Claude statusLine 额度数据", plan="Claude")


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
