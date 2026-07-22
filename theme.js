export const CSS = `
/* mobius-ui:Root v1 — keep in sync; library candidate. Memory still owns
   most layout through S.* inline constants, so this block is the shared
   platform floor rather than a full rewrite. */
.mg-root {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 100%;
  max-width: 100%;
  overflow: hidden;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  -webkit-font-smoothing: antialiased;
  -webkit-tap-highlight-color: transparent;
}
/* /mobius-ui:Root */

/* mobius-ui:Focus v1 -- shared keyboard focus ring (WCAG 2.4.7); never bare outline:none */
:where(button,a,input,textarea,select,summary,[role="button"],[tabindex]:not([tabindex="-1"])):focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
/* /mobius-ui:Focus */

.mg-sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
}

@keyframes mg-orbit-spin { to { transform: rotate(360deg); } }
.mg-orbit {
  position: relative; width: 46px; height: 46px;
  animation: mg-orbit-spin 2.4s linear infinite;
}
.mg-orbit span {
  position: absolute; width: 8px; height: 8px; border-radius: 50%;
  background: var(--accent); top: 50%; left: 50%; margin: -4px;
}
.mg-orbit span:nth-child(1) { transform: rotate(0deg) translateX(18px); opacity: 1; }
.mg-orbit span:nth-child(2) { transform: rotate(120deg) translateX(18px); opacity: 0.6; }
.mg-orbit span:nth-child(3) { transform: rotate(240deg) translateX(18px); opacity: 0.3; }

@keyframes mg-twinkle { 0%,100% { opacity: 0.35; } 50% { opacity: 1; } }
.mg-star { animation: mg-twinkle 2.8s ease-in-out infinite; }
.mg-star-hub { filter: drop-shadow(0 0 6px var(--accent)); }
@keyframes mg-pulse-ring {
  0% { transform: scale(0.8); opacity: 0.5; }
  70% { transform: scale(1.5); opacity: 0; }
  100% { opacity: 0; }
}
.mg-pulse { transform-origin: 66px 48px; animation: mg-pulse-ring 2.6s ease-out infinite; }

.mg-graph { cursor: grab; }
.mg-graph:active { cursor: grabbing; }

@media (hover: hover) {
  .mg-row:hover { background: var(--surface2); }
  .mg-th:hover { color: var(--text); }
  .mg-legend-row:hover { background: var(--surface2); }
  .mg-tgl:hover { color: var(--text); }
  .mg-settings-btn:hover { color: var(--text); }
  .mg-tab:hover { color: var(--text); }
  .mg-close:hover { background: var(--border); color: var(--text); }
  .mg-discuss:hover { filter: brightness(0.94); }
}
/* Keyboard-focus ring for the now-focusable list rows + sort-header buttons,
   so the keyboard affordance these gained is actually visible. */
.mg-row:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.mg-th:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
.mg-discuss:active { transform: translateY(1px); }

/* mobius-ui:Scrollskin v2 — keep in sync; hidden by default, content stays scrollable. */
.mg-scroll,
.mg-md pre {
  scrollbar-width: none;
  -ms-overflow-style: none;
}
.mg-scroll::-webkit-scrollbar,
.mg-md pre::-webkit-scrollbar {
  display: none;
  width: 0;
  height: 0;
}
/* /mobius-ui:Scrollskin */

@keyframes mg-skel-pulse { 0%,100% { opacity: 0.5; } 50% { opacity: 1; } }
@keyframes mg-pulse { 0%,100% { opacity: 0.4; } 50% { opacity: 1; } }
.mg-skel {
  height: 13px; border-radius: 5px;
  background: linear-gradient(90deg, var(--surface2), var(--border), var(--surface2));
  animation: mg-skel-pulse 1.4s ease-in-out infinite;
}

@keyframes mg-panel-in {
  from { transform: translateX(20px); opacity: 0; }
  to { transform: translateX(0); opacity: 1; }
}
@keyframes mg-scrim-in { from { opacity: 0; } to { opacity: 1; } }
.mg-panel { inset: 0 0 0 auto; width: min(980px, 96vw); animation: mg-panel-in 0.22s cubic-bezier(0.22,1,0.36,1); }
.mg-scrim { animation: mg-scrim-in 0.2s ease; }
.mg-local-graph { cursor: grab; background: var(--bg); }
.mg-local-graph:active { cursor: grabbing; }
.mg-md a[href^="#memory-node-"] {
  border: 1px solid var(--accent-dim, rgba(167,139,250,0.35));
  background: var(--accent-dim, rgba(167,139,250,0.12));
  border-radius: 6px;
  padding: 0 5px;
  font-weight: 600;
}
.mg-agent-settings {
  flex: 1 1 100%;
  min-width: 0;
  display: grid;
  gap: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
}
.mg-agent-settings-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
.mg-agent-settings-title {
  color: var(--text);
  font-size: 12.5px;
  font-weight: 700;
  line-height: 1.35;
}
.mg-agent-settings-sub {
  margin-top: 2px;
  color: var(--muted);
  font-size: 11.5px;
  line-height: 1.35;
}
.mg-agent-stack {
  display: grid;
  grid-template-columns: repeat(2, minmax(220px, 1fr));
  gap: 10px;
}
.mg-agent-slot {
  display: grid;
  gap: 8px;
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface) 70%, var(--bg));
}
.mg-agent-slot-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}
.mg-agent-slot-title {
  color: var(--text);
  font-size: 12.5px;
  font-weight: 700;
  line-height: 1.35;
}
.mg-agent-mode {
  display: inline-flex;
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
}
.mg-agent-mode-btn {
  min-height: 44px;
  padding: 0 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  touch-action: manipulation;
  user-select: none;
}
.mg-agent-mode-btn.is-active {
  background: var(--accent-hover, var(--accent));
  color: var(--accent-fg);
}
.mg-agent-inherit {
  min-height: 40px;
  display: flex;
  align-items: center;
  padding: 8px 10px;
  border: 1px dashed var(--border);
  border-radius: 8px;
  background: var(--bg);
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.35;
}
.mg-agent-select {
  width: 100%;
  min-height: 40px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  font-size: 13px;
  font-weight: 650;
  padding: 0 9px;
}
.mg-agent-meta {
  color: var(--muted);
  font-size: 11.5px;
  line-height: 1.4;
}
.mg-agent-meta span {
  font-family: var(--mono);
}
.mobius-model-trigger {
  display:flex; align-items:center; gap:10px; width:100%; padding:8px 10px;
  border:1px solid var(--border); border-radius:9px; text-align:left;
  background:color-mix(in srgb,var(--bg) 60%,var(--surface)); color:var(--text);
  font:inherit; cursor:pointer; touch-action:manipulation;
}
.mobius-model-trigger__icon,.mobius-model-sheet__row-icon {
  display:grid; place-items:center; flex:none; color:var(--text);
  background:color-mix(in srgb,var(--surface) 82%,var(--bg)); border:1px solid var(--border);
}
.mobius-model-trigger__icon { width:26px; height:26px; border-radius:7px; }
.mobius-model-trigger__icon svg { width:15px; height:15px; }
.mobius-model-trigger__main { flex:1; min-width:0; display:flex; flex-direction:column; }
.mobius-model-trigger__name,.mobius-model-trigger__id,.mobius-model-sheet__row-title,.mobius-model-sheet__row-id { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.mobius-model-trigger__name { font-size:13.5px; font-weight:500; line-height:1.3; }
.mobius-model-trigger__id { font:11px/1.3 var(--mono); color:var(--muted); }
.mobius-model-trigger__effort {
  flex:none; padding:3px 7px; border:1px solid color-mix(in srgb,var(--accent) 24%,var(--border));
  border-radius:999px; background:color-mix(in srgb,var(--accent) 12%,var(--surface));
  color:var(--muted); font-size:11px; font-weight:500; line-height:1; white-space:nowrap;
}
.mobius-model-trigger__effort-visual {
  position:relative; flex:none; display:inline-flex; align-items:center;
  justify-content:space-between; gap:5px; min-width:68px; padding:7px 3px;
}
.mobius-model-trigger__effort-visual::before {
  content:''; position:absolute; left:6px; right:6px; top:50%; height:1px;
  background:var(--border); transform:translateY(-50%);
}
.mobius-model-trigger__effort-dot {
  position:relative; z-index:1; width:6px; height:6px; border-radius:50%;
  border:1px solid var(--border); background:var(--surface);
}
.mobius-model-trigger__effort-dot.is-filled { border-color:var(--accent); background:var(--accent); }
.mobius-model-trigger__effort-dot.is-active { transform:scale(1.35); box-shadow:0 0 0 2px var(--accent-dim); }
.mobius-model-sheet__backdrop {
  position:fixed; inset:0; z-index:1000; display:flex; align-items:flex-end; justify-content:center;
  box-sizing:border-box; background:rgba(0,0,0,.5); overscroll-behavior:contain;
  padding:max(8px,env(safe-area-inset-top)) max(8px,env(safe-area-inset-right)) max(8px,env(safe-area-inset-bottom)) max(8px,env(safe-area-inset-left));
}
.mobius-model-sheet {
  width:100%; max-width:440px; max-height:calc(100dvh - 16px); min-height:0;
  display:flex; flex-direction:column; overflow:hidden; background:var(--surface);
  border:1px solid var(--border); border-radius:16px 16px 0 0;
  box-shadow:0 -4px 8px rgba(0,0,0,.24); animation:mobius-model-sheet-in .18s ease;
}
@keyframes mobius-model-sheet-in { from { transform:translateY(14px); opacity:.5; } }
.mobius-model-sheet__head { display:flex; align-items:center; justify-content:space-between; padding:14px 16px 8px; }
.mobius-model-sheet__title { color:var(--muted); font-size:13px; font-weight:500; }
.mobius-model-sheet__close { min-width:44px; min-height:44px; margin:-8px -8px -8px 0; padding:4px 6px; border:0; background:none; color:var(--accent); font:500 14px var(--font); cursor:pointer; }
.mobius-model-sheet__body { min-height:0; overflow-y:auto; overscroll-behavior-y:contain; padding:0 8px 16px; }
.mobius-model-sheet__group-head { display:flex; align-items:center; gap:8px; padding:12px 10px 6px; color:var(--muted); font-size:11px; font-weight:600; }
.mobius-model-sheet__group-icon { width:18px; height:18px; display:grid; place-items:center; color:var(--text); }
.mobius-model-sheet__group-icon svg { width:15px; height:15px; }
.mobius-model-sheet__group-hint { font-weight:400; }
.mobius-model-sheet__row { display:flex; align-items:center; gap:12px; width:100%; padding:9px 10px; border:0; border-radius:9px; background:none; color:var(--text); font:inherit; text-align:left; cursor:pointer; }
.mobius-model-sheet__row.is-selected { background:color-mix(in srgb,var(--accent) 10%,var(--surface)); }
.mobius-model-sheet__row:disabled { opacity:.45; cursor:not-allowed; }
.mobius-model-sheet__row-icon { width:30px; height:30px; border-radius:8px; }
.mobius-model-sheet__row-icon svg { width:16px; height:16px; }
.mobius-model-sheet__row-main { flex:1; min-width:0; display:flex; flex-direction:column; gap:1px; }
.mobius-model-sheet__row-title { font-size:14px; font-weight:500; }
.mobius-model-sheet__row-id { color:var(--muted); font:12px var(--mono); }
.mobius-model-sheet__check { width:18px; height:18px; flex:none; position:relative; border-radius:50%; background:var(--accent); border:1.5px solid var(--accent); }
.mobius-model-sheet__check::after { content:''; position:absolute; left:5px; top:2px; width:5px; height:9px; border:1.5px solid var(--accent-fg); border-top:0; border-left:0; transform:rotate(45deg); }
.mobius-model-sheet__effort { margin:2px 10px 8px 52px; }
.mobius-model-sheet__empty { padding:16px 10px; color:var(--muted); font-size:13px; }
.mobius-effort { margin-top:8px; display:flex; align-items:center; gap:10px; min-height:24px; }
.mobius-effort-track { position:relative; display:flex; align-items:center; gap:10px; min-height:24px; padding:0 2px; }
.mobius-effort-track::before { content:''; position:absolute; left:7px; right:7px; top:50%; height:2px; transform:translateY(-50%); background:var(--border); }
.mobius-effort-stop {
  position:relative; z-index:1; width:14px; height:14px; padding:0;
  border:1px solid var(--border); border-radius:999px; background:var(--surface);
  cursor:pointer; touch-action:manipulation; user-select:none;
}
.mobius-effort-stop.is-filled { background:var(--accent); border-color:var(--accent); }
.mobius-effort-stop.is-active { transform:scale(1.3); box-shadow:0 0 0 3px var(--accent-dim); }
.mobius-effort-stop:disabled { cursor:default; opacity:.55; pointer-events:none; }
.mobius-effort-label { color:var(--muted); font-size:12px; line-height:1; white-space:nowrap; }
.mobius-effort.is-disabled .mobius-effort-label { opacity:.55; }
@media (hover:hover) and (pointer:fine) {
  .mobius-model-trigger:hover { border-color:var(--accent); }
  .mobius-model-sheet__row:hover:not(:disabled) { background:color-mix(in srgb,var(--accent) 8%,var(--surface)); }
  .mobius-effort-stop:not(:disabled):not(.is-active):hover { border-color:var(--accent); }
}
@media (prefers-reduced-motion:no-preference) {
  .mobius-effort-stop { transition:background .15s,border-color .15s,box-shadow .15s,transform .15s; }
  .mobius-effort-stop:not(:disabled):active { opacity:.82; }
}
@media (min-width:620px) {
  .mobius-model-sheet__backdrop { align-items:center; padding:24px; }
  .mobius-model-sheet { border-radius:16px; }
}
@media (hover: hover) {
  .mg-agent-mode-btn:not(.is-active):hover { color: var(--text); }
}
@media (max-width: 640px) {
  .mg-agent-stack { grid-template-columns: 1fr; }
  .mg-agent-settings-head { flex-direction: column; }
  .mg-scrim { display: none; }
  .mg-panel {
    inset: 0; width: 100%; height: 100%; border-left: none;
    border-top: none; border-radius: 0; box-shadow: none;
    animation: mg-panel-in 0.18s cubic-bezier(0.22,1,0.36,1);
  }
  .mg-panel-head { padding: 11px 12px 8px !important; }
  .mg-panel .mg-close {
    width: 44px !important; height: 44px !important; border-radius: 10px !important;
  }
  .mg-panel .mg-tag-row {
    flex-wrap: nowrap !important; overflow-x: auto; padding: 0 12px 7px !important;
    scrollbar-width: none;
  }
  .mg-panel .mg-tag-row::-webkit-scrollbar { display: none; }
  .mg-md {
    padding: 10px 14px 18px !important;
    font-size: 13px !important;
    line-height: 1.54 !important;
  }
  .mg-md h1 { font-size: 17px !important; }
  .mg-md h2 { font-size: 15px !important; }
  .mg-md h3 { font-size: 13px !important; }
  .mg-md p { margin: 8px 0 !important; }
  .mg-md ul, .mg-md ol { margin: 8px 0 !important; }
  .mg-md code { font-size: 0.82em !important; }
  .mg-panel .mg-discuss { padding: 9px 12px !important; }
  .mg-scroll table th:nth-child(n+3),
  .mg-scroll table td:nth-child(n+3) {
    display: none;
  }
  .mg-scroll table th,
  .mg-scroll table td {
    padding-left: 10px !important;
    padding-right: 10px !important;
  }
}
/* mobius-ui:ReducedMotion v1 — keep in sync; library candidate. Diverge below the marker only. */
@media (prefers-reduced-motion: reduce) {
  .mg-orbit, .mg-star, .mg-pulse, .mg-skel, .mg-panel, .mg-scrim, .mg-star-hub { animation: none !important; }
}
/* /mobius-ui:ReducedMotion */

.mg-md h1, .mg-md h2, .mg-md h3 { margin: 16px 0 7px; line-height: 1.25; font-weight: 700; letter-spacing: 0; }
.mg-md h1 { font-size: 19px; } .mg-md h2 { font-size: 16px; } .mg-md h3 { font-size: 14px; }
.mg-md h1:first-child, .mg-md h2:first-child, .mg-md h3:first-child { margin-top: 0; }
.mg-md p { margin: 9px 0; }
.mg-md ul, .mg-md ol { margin: 9px 0; padding-left: 22px; }
.mg-md li { margin: 4px 0; }
.mg-md li::marker { color: var(--muted); }
.mg-md a { color: var(--accent); text-decoration: none; border-bottom: 1px solid var(--accent-dim, rgba(167,139,250,0.4)); }
.mg-md a:hover { border-bottom-color: var(--accent); }
.mg-md strong { color: var(--text); font-weight: 700; }
.mg-md code { background: var(--surface2); border-radius: 5px; padding: 1px 5px; font-family: var(--mono); font-size: 0.85em; border: 1px solid var(--border-light, var(--border)); }
.mg-md pre { background: var(--surface2); border: 1px solid var(--border); border-radius: 9px; padding: 13px; overflow-x: auto; margin: 11px 0; }
.mg-md pre code { background: none; padding: 0; border: none; }
.mg-md blockquote {
  margin: 11px 0; padding: 10px 13px;
  border: 1px solid color-mix(in srgb, var(--accent) 28%, var(--border));
  border-radius: 8px;
  background: color-mix(in srgb, var(--accent) 9%, transparent);
  color: var(--muted);
}
.mg-md table { border-collapse: collapse; margin: 11px 0; font-size: 13px; width: 100%; }
.mg-md th, .mg-md td { border: 1px solid var(--border); padding: 6px 10px; text-align: left; }
.mg-md th { background: var(--surface2); font-weight: 600; }
.mg-md img { max-width: 100%; border-radius: 8px; }
.mg-md hr { border: none; border-top: 1px solid var(--border); margin: 16px 0; }
`;
