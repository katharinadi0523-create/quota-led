// 额度LED — 单面板三行：Codex / Cursor / Claude 剩余订阅额度
// 每行 = 左球体 + 右文字。可拖动（拖标题栏），位置记到 localStorage。
// 数据由 collect.py 提供。Übersicht 1.6+ (.jsx)

import { React, run } from "uebersicht";

// collect.py 随本仓库安装到 widgets/quota-led/（见 install.sh）。
// 用 $HOME 拼绝对路径：不含用户名、可移植、且不依赖运行时工作目录。
export const command =
  '/usr/bin/python3 "$HOME/Library/Application Support/Übersicht/widgets/quota-led/collect.py"';

export const refreshFrequency = 60000; // 60s

// 默认左下角（Übersicht 原生定位，最稳）。拖动时由 JS 改写容器的 left/top。
export const className = `
  left: 36px;
  bottom: 36px;
  font-family: "SF Mono", "JetBrains Mono", Menlo, monospace;
  color: #e6e6e6;
  -webkit-font-smoothing: antialiased;
  z-index: 0;
`;

const ACCENT = { Codex: "#10a37f", Cursor: "#7c5cff", Claude: "#d97757" };
const POS_KEY = "quotaLedPos_v2";

// 点击行启动对应 App
function launch(name) {
  if (name) run(`open -a ${JSON.stringify(name)}`);
}

// ---- 配色 / 状态灯 ----
function ledColor(r) {
  if (r == null) return "#5a5f6a";
  if (r >= 50) return "#36f6a0";
  if (r >= 20) return "#ffd23f";
  return "#ff5d5d";
}
function statusText(r) {
  if (r == null) return "无数据";
  if (r >= 50) return "绿灯";
  if (r >= 20) return "黄灯";
  return "红灯";
}
function fmtReset(ts) {
  if (!ts) return "";
  const diff = ts - Math.floor(Date.now() / 1000);
  if (diff <= 0) return "即将重置";
  const h = Math.floor(diff / 3600);
  const m = Math.floor((diff % 3600) / 60);
  const d = Math.floor(h / 24);
  if (d >= 1) return `${d}天${h % 24}h后重置`;
  if (h >= 1) return `${h}h${m}m后重置`;
  return `${m}m后重置`;
}

// ---- 小球体 + 进度环 ----
function Orb({ remaining, size = 62 }) {
  const color = ledColor(remaining);
  const deg = remaining == null ? 0 : Math.max(0, Math.min(100, remaining)) * 3.6;
  return (
    <div
      style={{
        width: size, height: size, borderRadius: "50%", padding: 4,
        boxSizing: "border-box", flex: "0 0 auto",
        background: `conic-gradient(${color} ${deg}deg, rgba(255,255,255,0.06) 0deg)`,
        boxShadow: `0 0 12px ${color}40`,
      }}
    >
      <div
        style={{
          position: "relative", width: "100%", height: "100%", borderRadius: "50%",
          background: "radial-gradient(circle at 36% 26%, #363c49 0%, #181b22 46%, #0a0c10 100%)",
          boxShadow: "inset 0 -7px 13px rgba(0,0,0,0.65), inset 0 4px 9px rgba(255,255,255,0.07)",
          display: "flex", alignItems: "center", justifyContent: "center",
        }}
      >
        <div
          style={{
            position: "absolute", top: "11%", left: "26%", width: "46%", height: "24%",
            borderRadius: "50%",
            background: "radial-gradient(ellipse at center, rgba(255,255,255,0.35), rgba(255,255,255,0) 70%)",
          }}
        />
        <div
          style={{
            fontSize: 17, fontWeight: 700, color, lineHeight: 1,
            textShadow: `0 0 9px ${color}99`, fontVariantNumeric: "tabular-nums",
          }}
        >
          {remaining == null ? "—" : Math.round(remaining)}
          {remaining != null && <span style={{ fontSize: 9 }}>%</span>}
        </div>
      </div>
    </div>
  );
}

// ---- 明细小行 ----
function MetricLine({ r }) {
  const color = ledColor(r.remaining);
  const note = r.sub || fmtReset(r.resets_at);
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginTop: 2 }}>
      <span style={{ fontSize: 10, color: "#9aa0ab", minWidth: 22 }}>{r.label}</span>
      <span style={{ fontSize: 11.5, fontWeight: 600, color, fontVariantNumeric: "tabular-nums" }}>
        {r.remaining == null ? "—" : Math.round(r.remaining)}%
      </span>
      {note && <span style={{ fontSize: 9, color: "#5f656f", marginLeft: "auto" }}>{note}</span>}
    </div>
  );
}

// ---- 一行服务（左球右字，点击启动）----
function ServiceRow({ name, svc, last }) {
  const ok = svc && svc.ok;
  const remaining = ok && svc.headline ? svc.headline.remaining : null;
  const color = ledColor(remaining);
  const accent = ACCENT[name];
  return (
    <div
      onClick={() => launch(name)}
      title={`打开 ${name}`}
      style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "11px 4px",
        borderBottom: last ? "none" : "1px solid rgba(255,255,255,0.05)",
        cursor: "pointer",
      }}
    >
      <Orb remaining={remaining} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: accent, boxShadow: `0 0 6px ${accent}` }} />
          <span style={{ fontSize: 12.5, fontWeight: 600 }}>{name}</span>
          {ok && (
            <span style={{ marginLeft: "auto", fontSize: 10, fontWeight: 600, color, textShadow: `0 0 7px ${color}77` }}>
              {svc.approx ? "≈" : ""}{statusText(remaining)}
            </span>
          )}
        </div>
        {ok ? (
          (svc.rows || []).map((r, i) => <MetricLine key={i} r={r} />)
        ) : (
          <div style={{ fontSize: 10, color: "#7b818c", marginTop: 3, wordBreak: "break-all" }}>
            {(svc && svc.error) || "暂无数据"}
          </div>
        )}
      </div>
    </div>
  );
}

// ---- 可拖动面板 ----
// 定位策略：默认靠 Übersicht 的 className（左下角）。拖动时直接改写
// 挂件「容器元素」(this.root.parentNode) 的 left/top，并存 localStorage；
// 每次渲染后从 localStorage 重新应用，刷新不丢位置。
class Panel extends React.Component {
  constructor(props) {
    super(props);
    this.onDown = this.onDown.bind(this);
    this.onMove = this.onMove.bind(this);
    this.onUp = this.onUp.bind(this);
  }
  get container() {
    return this.root ? this.root.parentNode : null;
  }
  applyPos(p) {
    const c = this.container;
    if (!c || !p) return;
    c.style.left = p.left + "px";
    c.style.top = p.top + "px";
    c.style.right = "auto";
    c.style.bottom = "auto";
  }
  savedPos() {
    try { return JSON.parse(window.localStorage.getItem(POS_KEY)); } catch (e) { return null; }
  }
  defaultPos() {
    // 左下角：按窗口高度算 top（容器只认 top/left，不认 bottom）
    const h = this.root ? this.root.offsetHeight : 210;
    return { left: 36, top: Math.max(20, (window.innerHeight || 980) - h - 44) };
  }
  effectivePos() { return this.savedPos() || this.defaultPos(); }
  componentDidMount() { this.applyPos(this.effectivePos()); }
  componentDidUpdate() { this.applyPos(this.effectivePos()); } // 刷新后重新贴回位置
  componentWillUnmount() {
    window.removeEventListener("mousemove", this.onMove);
    window.removeEventListener("mouseup", this.onUp);
  }
  onDown(e) {
    e.preventDefault();
    const c = this.container;
    if (!c) return;
    const r = c.getBoundingClientRect();
    this._start = { mx: e.clientX, my: e.clientY, left: r.left, top: r.top };
    this.applyPos({ left: r.left, top: r.top }); // 把 bottom 定位切成 top 定位
    window.addEventListener("mousemove", this.onMove);
    window.addEventListener("mouseup", this.onUp);
  }
  onMove(e) {
    const s = this._start;
    if (!s) return;
    const w = this.root ? this.root.offsetWidth : 300;
    const h = this.root ? this.root.offsetHeight : 220;
    let left = Math.max(0, Math.min(s.left + (e.clientX - s.mx), window.innerWidth - w));
    let top = Math.max(0, Math.min(s.top + (e.clientY - s.my), window.innerHeight - h));
    this._last = { left, top };
    this.applyPos(this._last);
  }
  onUp() {
    window.removeEventListener("mousemove", this.onMove);
    window.removeEventListener("mouseup", this.onUp);
    if (this._last) {
      try { window.localStorage.setItem(POS_KEY, JSON.stringify(this._last)); } catch (e) {}
    }
  }
  render() {
    const { data } = this.props;
    const s = (data && data.services) || {};
    const updated = new Date(((data && data.ts) || 0) * 1000).toLocaleTimeString("zh-CN", {
      hour: "2-digit", minute: "2-digit",
    });
    return (
      <div
        ref={(r) => (this.root = r)}
        style={{
          width: 288,
          background: "linear-gradient(165deg, rgba(26,29,38,0.96), rgba(12,13,18,0.96))",
          border: "1px solid rgba(255,255,255,0.08)",
          borderRadius: 18,
          boxShadow: "0 16px 44px rgba(0,0,0,0.5)",
          userSelect: "none",
          overflow: "hidden",
        }}
      >
        {/* 标题栏 = 拖动手柄 */}
        <div
          onMouseDown={this.onDown}
          style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "7px 12px",
            background: "rgba(255,255,255,0.03)",
            borderBottom: "1px solid rgba(255,255,255,0.06)",
            cursor: "grab",
            fontSize: 10, letterSpacing: 1, color: "#7a808b",
          }}
        >
          <span style={{ letterSpacing: 2, color: "#5f656f" }}>⠿</span>
          <span>额度LED</span>
          <span style={{ marginLeft: "auto", color: "#4b505a" }}>{updated}</span>
        </div>
        {/* 三行 */}
        <div style={{ padding: "2px 12px 6px" }}>
          <ServiceRow name="Codex" svc={s.codex} />
          <ServiceRow name="Cursor" svc={s.cursor} />
          <ServiceRow name="Claude" svc={s.claude} last />
        </div>
      </div>
    );
  }
}

export const render = ({ output }) => {
  let data = null;
  try {
    data = JSON.parse(output);
  } catch (e) {
    return (
      <div style={{ width: 280, padding: 16, background: "rgba(18,20,26,0.92)", borderRadius: 14, color: "#ff7b7b", fontSize: 11 }}>
        采集脚本输出无法解析：
        <pre style={{ whiteSpace: "pre-wrap", color: "#9aa0ab" }}>{String(output).slice(0, 240)}</pre>
      </div>
    );
  }
  return <Panel data={data} />;
};
