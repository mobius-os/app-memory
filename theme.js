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
.mg-hint-touch { display: none; }
@media (pointer: coarse), (max-width: 640px) {
  .mg-hint-pointer { display: none; }
  .mg-hint-touch { display: inline; }
}
.mg-settings-icon { display: none; }
.mg-svg-graph { display: block; user-select: none; -webkit-user-select: none; }
.mg-svg-node {
  cursor: pointer;
  transition: opacity 180ms cubic-bezier(0.22,1,0.36,1);
}
.mg-svg-node-focus { opacity: 0; pointer-events: none; }
.mg-svg-node:focus { outline: none; }
.mg-svg-node:focus-visible .mg-svg-node-focus { opacity: 1; }
.mg-svg-label { transition: opacity 180ms cubic-bezier(0.22,1,0.36,1); }
.mg-graph-controls {
  position: absolute;
  right: 12px;
  bottom: 12px;
  z-index: 2;
  display: flex;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 11px;
  background: color-mix(in srgb, var(--surface) 94%, transparent);
  box-shadow: 0 8px 28px rgb(0 0 0 / 24%);
  backdrop-filter: blur(7px);
}
.mg-graph-control {
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 0;
  border-right: 1px solid var(--border);
  background: transparent;
  color: var(--text);
  font: 700 11px var(--font);
  cursor: pointer;
  touch-action: manipulation;
}
.mg-graph-control:last-child { border-right: 0; }
.mg-graph-control svg { width: 18px; height: 18px; }
.mg-graph-control:disabled { color: var(--muted); opacity: .45; cursor: default; }
.mg-graph-reset { width: 54px; color: var(--muted); font-variant-numeric: tabular-nums; }
.mg-legend-toggle {
  width: 100%;
  height: 44px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 11px 0 13px;
  border: 0;
  background: color-mix(in srgb, var(--surface2) 56%, transparent);
  color: var(--text);
  font: 700 11.5px var(--font);
  text-align: left;
  cursor: pointer;
  touch-action: manipulation;
}
.mg-legend-toggle > span:first-child { flex: 1; }
.mg-legend-count {
  min-width: 23px;
  height: 23px;
  display: grid;
  place-items: center;
  padding-inline: 6px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--muted);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}
.mg-legend-chevron { width: 16px; height: 16px; color: var(--muted); transition: transform 160ms ease; }
.mg-legend.is-open .mg-legend-chevron { transform: rotate(180deg); }
.mg-legend-body {
  max-height: calc(62vh - 44px);
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 3px 8px 8px;
  border-top: 1px solid var(--border);
}
.mg-legend:not(.is-open) .mg-legend-body { display: none; }
.mg-memory-list th.mg-col-type { width: 82px; }
.mg-memory-list th.mg-col-reads { width: 76px; }
.mg-memory-list th.mg-col-size { width: 82px; }

@media (hover: hover) {
  .mg-row:hover { background: var(--surface2); }
  .mg-th:hover { color: var(--text); }
  .mg-legend-row:hover { background: var(--surface2); }
  .mg-tgl:hover { color: var(--text); }
  .mg-settings-btn:hover { color: var(--text); }
  .mg-tab:hover { color: var(--text); }
  .mg-close:hover { background: var(--border); color: var(--text); }
  .mg-discuss:hover { filter: brightness(0.94); }
  .mg-graph-control:not(:disabled):hover { background: var(--surface2); }
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
/* A note opened from a recall card is the app destination, not a drawer over
   the graph. The ordinary graph/list drill-down keeps .mg-panel unchanged. */
.mg-panel.mg-panel--direct {
  inset: 0;
  width: 100%;
  height: 100%;
  border-left: 0;
  box-shadow: none;
  animation: none;
}
.mg-panel--direct .mg-md {
  width: min(920px, 100%);
  margin-inline: auto;
}
.mg-panel--direct .mg-discuss {
  width: min(920px, 100%) !important;
  margin-inline: auto;
}
.mg-direct-pending {
  position: absolute;
  inset: 0;
  z-index: 22;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 28px;
  text-align: center;
  background: var(--surface);
}
.mg-direct-back {
  min-height: 44px;
  padding: 9px 14px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--surface2);
  color: var(--text);
  font: 650 13px var(--font);
  cursor: pointer;
}
.mg-local-graph { cursor: grab; background: var(--bg); }
.mg-local-graph:active { cursor: grabbing; }
.mg-md a[href^="#memory-node-"] {
  border: 1px solid var(--accent-dim, rgba(167,139,250,0.35));
  background: var(--accent-dim, rgba(167,139,250,0.12));
  border-radius: 6px;
  padding: 0 5px;
  font-weight: 600;
}
.mg-settings-backdrop {
  position: absolute;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  padding: clamp(10px, 3vw, 32px);
  background: rgb(5 7 12 / 72%);
  backdrop-filter: blur(7px);
  animation: mg-scrim-in .18s ease;
}
.mg-settings-deck {
  width: min(920px, 100%);
  max-height: min(760px, 100%);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--accent) 18%, var(--border));
  border-radius: 20px;
  background:
    radial-gradient(circle at 88% -16%, color-mix(in srgb, var(--accent) 13%, transparent), transparent 33%),
    var(--surface);
  box-shadow: 0 28px 80px rgb(0 0 0 / 46%);
  animation: mg-settings-in .22s cubic-bezier(.22,1,.36,1);
}
@keyframes mg-settings-in {
  from { transform: translateY(12px) scale(.985); opacity: .55; }
}
.mg-settings-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  padding: 24px 26px 20px;
  border-bottom: 1px solid var(--border);
}
.mg-settings-kicker {
  color: var(--accent);
  font-size: 10.5px;
  font-weight: 800;
  letter-spacing: .13em;
  text-transform: uppercase;
}
.mg-settings-head h2,
.mg-section-intro h3 { margin: 0; color: var(--text); letter-spacing: -.025em; }
.mg-settings-head h2 { margin-top: 4px; font-size: clamp(22px, 3vw, 29px); line-height: 1.12; }
.mg-settings-head p {
  max-width: 580px;
  margin: 7px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.45;
}
.mg-settings-close {
  width: 44px;
  height: 44px;
  flex: none;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--surface2) 78%, transparent);
  color: var(--muted);
  font: 300 25px/1 var(--font);
  cursor: pointer;
}
.mg-settings-close svg,
.mg-close svg { display: block; margin: auto; }
.mg-settings-layout {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  min-height: 0;
  flex: 1;
}
.mg-settings-nav {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 18px 14px;
  border-right: 1px solid var(--border);
  background: color-mix(in srgb, var(--surface2) 45%, transparent);
}
.mg-settings-nav-item {
  width: 100%;
  min-height: 58px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 3px;
  padding: 9px 11px;
  border: 1px solid transparent;
  border-radius: 11px;
  background: transparent;
  color: var(--muted);
  text-align: left;
  font: inherit;
  cursor: pointer;
}
.mg-settings-nav-item span { color: inherit; font-size: 13px; font-weight: 720; }
.mg-settings-nav-item small { color: var(--muted); font-size: 10.5px; line-height: 1.25; }
.mg-settings-nav-item.is-active {
  border-color: color-mix(in srgb, var(--accent) 28%, var(--border));
  background: color-mix(in srgb, var(--accent) 10%, var(--surface));
  color: var(--text);
  box-shadow: inset 3px 0 0 var(--accent);
}
.mg-settings-content {
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 24px 26px 30px;
}
.mg-settings-section { display: grid; gap: 22px; }
.mg-section-intro {
  display: grid;
  grid-template-columns: minmax(180px, .8fr) minmax(240px, 1.2fr);
  align-items: end;
  gap: 24px;
}
.mg-section-intro h3 { margin-top: 5px; font-size: 19px; line-height: 1.2; }
.mg-section-intro p {
  margin: 0;
  color: var(--muted);
  font-size: 12.5px;
  line-height: 1.5;
}
.mg-advanced-policy {
  border-top: 1px solid var(--border);
  padding-top: 2px;
}
.mg-advanced-policy > summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 14px 2px;
  color: var(--text);
  cursor: pointer;
  list-style: none;
  font-size: 12.5px;
  font-weight: 720;
}
.mg-advanced-policy > summary::-webkit-details-marker { display: none; }
.mg-advanced-policy > summary::after { content: '›'; color: var(--accent); font-size: 18px; transition: transform .16s ease; }
.mg-advanced-policy[open] > summary::after { transform: rotate(90deg); }
.mg-advanced-policy > summary small { margin-left: auto; color: var(--muted); font-size: 10.5px; font-weight: 500; }
.mg-advanced-policy-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding-top: 12px;
}
.mg-advanced-policy-foot p { max-width: 58ch; margin: 0; color: var(--muted); font-size: 11px; line-height: 1.5; }
.mg-advanced-policy-foot button {
  flex: none;
  min-height: 34px;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--surface);
  color: var(--text);
  font: 650 11px/1 var(--font);
  cursor: pointer;
}
.mg-advanced-policy-foot button:disabled { opacity: .45; cursor: default; }
.mg-policy-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(220px, 1fr));
  gap: 12px;
}
.mg-policy-card {
  min-width: 0;
  margin: 0;
  padding: 8px 16px 12px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--bg) 72%, var(--surface));
}
.mg-policy-card.is-nightly {
  border-color: color-mix(in srgb, var(--accent) 32%, var(--border));
  background: color-mix(in srgb, var(--accent) 6%, var(--surface));
}
.mg-policy-card legend {
  padding: 0 7px;
  color: var(--text);
  font-size: 12.5px;
  font-weight: 750;
}
.mg-policy-card legend span {
  margin-left: 5px;
  color: var(--muted);
  font-size: 10px;
  font-weight: 500;
}
.mg-policy-card label {
  display: grid;
  grid-template-columns: 1fr 72px;
  align-items: center;
  gap: 12px;
  min-height: 54px;
  border-bottom: 1px solid var(--border);
  color: var(--text);
  font-size: 12px;
  font-weight: 680;
}
.mg-policy-card label:last-child { border-bottom: 0; }
.mg-policy-card label span { display: flex; flex-direction: column; gap: 2px; }
.mg-policy-card label small { color: var(--muted); font-size: 10.5px; font-weight: 500; }
.mg-policy-card input,
.mg-schedule-card input {
  box-sizing: border-box;
  border: 1px solid var(--border);
  background: var(--bg);
  color: var(--text);
  font-variant-numeric: tabular-nums;
}
.mg-policy-card input {
  width: 100%;
  height: 40px;
  border-radius: 9px;
  font: 700 13px/1 var(--font);
  text-align: center;
}
.mg-policy-card input:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
.mg-schedule-card {
  display: grid;
  grid-template-columns: auto 1fr auto auto;
  align-items: center;
  gap: 15px;
  padding: 18px;
  border: 1px solid color-mix(in srgb, var(--accent) 26%, var(--border));
  border-radius: 15px;
  background: color-mix(in srgb, var(--accent) 6%, var(--surface));
}
.mg-schedule-orb {
  width: 48px;
  height: 48px;
  display: grid;
  place-items: center;
  border-radius: 14px;
  background: color-mix(in srgb, var(--accent) 15%, var(--surface));
}
.mg-schedule-orb span {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  box-shadow: -5px 4px 0 0 var(--accent);
  transform: translate(3px, -2px);
}
.mg-schedule-copy { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.mg-schedule-copy label { color: var(--text); font-size: 13px; font-weight: 720; }
.mg-schedule-copy span,
.mg-schedule-copy small { color: var(--muted); font-size: 11px; line-height: 1.35; }
.mg-schedule-card input {
  width: 128px;
  height: 44px;
  padding: 0 10px;
  border-radius: 10px;
  font: 700 13px var(--font);
}
.mg-schedule-card select {
  box-sizing: border-box;
  width: 230px;
  max-width: 100%;
  height: 44px;
  padding: 0 10px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--bg);
  color: var(--text);
  font: 700 13px var(--font);
}
.mg-settings-loading,
.mg-settings-callout {
  min-height: 76px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  border: 1px dashed var(--border);
  border-radius: 13px;
  color: var(--muted);
  font-size: 12px;
}
.mg-settings-callout { justify-content: space-between; gap: 12px; }
.mg-settings-callout.is-error { color: var(--danger); border-color: color-mix(in srgb, var(--danger) 35%, var(--border)); }
.mg-settings-callout button {
  min-height: 40px;
  padding: 0 13px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--surface2);
  color: var(--text);
  font: 650 12px var(--font);
  cursor: pointer;
}
.mg-settings-foot {
  min-height: 70px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 15px;
  padding: 12px 26px max(12px, env(safe-area-inset-bottom));
  border-top: 1px solid var(--border);
  background: color-mix(in srgb, var(--surface) 90%, transparent);
}
.mg-settings-message {
  margin-right: auto;
  color: var(--danger);
  font-size: 11.5px;
  font-weight: 620;
}
.mg-settings-message.is-ok { color: var(--green); }
.mg-settings-save {
  min-width: 126px;
  height: 44px;
  padding: 0 17px;
  border: 0;
  border-radius: 11px;
  background: var(--accent);
  color: var(--accent-fg);
  font: 750 12.5px var(--font);
  cursor: pointer;
  box-shadow: 0 8px 24px color-mix(in srgb, var(--accent) 20%, transparent);
}
.mg-settings-save:disabled { opacity: .5; cursor: default; box-shadow: none; }
@media (hover:hover) {
  .mg-settings-close:hover,
  .mg-settings-nav-item:not(.is-active):hover { color: var(--text); background: var(--surface2); }
  .mg-settings-save:not(:disabled):hover { filter: brightness(1.06); }
}
.mobius-agent-priority-list { display:flex; flex-direction:column; gap:6px; position:relative; }
.mobius-agent-priority-row {
  position:relative; display:grid; grid-template-columns:44px minmax(0,1fr);
  align-items:center; min-height:54px; padding:0; border:0; border-radius:9px;
  background:transparent; user-select:none; -webkit-user-select:none;
  -webkit-touch-callout:none; will-change:transform;
  transition:transform .18s cubic-bezier(.22,1,.36,1), background .15s ease,
    border-color .15s ease, box-shadow .15s ease, opacity .15s ease;
}
.mobius-agent-priority-list.is-committing .mobius-agent-priority-row { transition:none; }
.mobius-agent-priority-row.is-dragging { opacity:.96; }
.mobius-agent-priority-row.is-dragging .mobius-model-trigger,
.mobius-agent-priority-row.is-drop-target .mobius-model-trigger {
  border-color:color-mix(in srgb,var(--accent) 62%,var(--border));
  background:color-mix(in srgb,var(--accent) 7%,var(--surface));
  box-shadow:0 4px 8px rgb(0 0 0 / 18%);
}
.mobius-agent-priority-handle {
  align-self:stretch; min-width:44px; min-height:44px; display:grid; place-items:center;
  border:0; border-radius:7px; padding:0; color:var(--muted); background:transparent;
  font:inherit; cursor:grab; touch-action:none; -webkit-tap-highlight-color:transparent;
}
.mobius-agent-priority-handle:active,
.mobius-agent-priority-row.is-dragging .mobius-agent-priority-handle {
  cursor:grabbing; color:var(--accent);
  background:color-mix(in srgb,var(--accent) 10%,transparent);
}
.mobius-agent-priority-handle:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
.mobius-agent-priority-handle svg { display:block; }
.mobius-agent-priority-handle:disabled { cursor:not-allowed; opacity:.45; }
.mobius-agent-priority-body { min-width:0; }
.mobius-agent-priority-help { margin:0 0 2px; color:var(--muted); font-size:12px; line-height:1.4; }
.mobius-agent-priority-status {
  position:absolute; width:1px; height:1px; padding:0; margin:-1px;
  overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0;
}
@media (hover:hover) and (pointer:fine) {
  .mobius-agent-priority-handle:not(:disabled):hover { color:var(--text); background:var(--surface2); }
}
@media (prefers-reduced-motion:reduce) { .mobius-agent-priority-row { transition:none; } }
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
.mobius-model-sheet__empty { padding:16px 10px; color:var(--muted); font-size:13px; }
@media (hover:hover) and (pointer:fine) {
  .mobius-model-trigger:hover { border-color:var(--accent); }
  .mobius-model-sheet__row:hover:not(:disabled) { background:color-mix(in srgb,var(--accent) 8%,var(--surface)); }
}
@media (min-width:620px) {
  .mobius-model-sheet__backdrop { align-items:center; padding:24px; }
  .mobius-model-sheet { border-radius:16px; }
}

/* Supporting chats are provenance, not a second transcript reader. */
.mg-supporting { margin-top:26px; padding-top:18px; border-top:1px solid var(--border); }
.mg-supporting-heading {
  display:flex; align-items:center; justify-content:space-between;
  gap:12px; margin-bottom:7px;
}
.mg-supporting-heading strong { color:var(--text); font-size:13px; line-height:1.3; }
.mg-supporting-heading > span {
  color:var(--muted); font-size:11px; font-variant-numeric:tabular-nums;
}
.mg-supporting-list { margin:0 !important; padding:0 !important; list-style:none; }
.mg-supporting-item {
  display:flex; align-items:flex-start; gap:16px; padding:13px 0;
  border-top:1px solid var(--border);
}
.mg-supporting-main { min-width:0; flex:1; display:flex; flex-direction:column; gap:3px; }
.mg-supporting-main > strong {
  overflow:hidden; color:var(--text); font-size:12.5px; line-height:1.35;
  text-overflow:ellipsis; white-space:nowrap;
}
.mg-supporting-main > span { color:var(--muted); font-size:10.75px; line-height:1.4; }
.mg-supporting-main > p {
  margin:5px 0 0 !important; color:var(--muted); font-size:11.5px; line-height:1.5;
}
.mg-supporting-main > p b { margin-right:5px; color:var(--text); font-weight:650; }
.mg-supporting-item > button {
  flex:0 0 auto; min-height:44px; padding:0 11px; border:1px solid var(--border);
  border-radius:9px; background:transparent; color:var(--accent); cursor:pointer;
  font:600 11.5px/1 var(--font); transition:border-color .15s ease, background .15s ease;
}
.mg-supporting-item > button:focus-visible {
  outline:2px solid var(--accent); outline-offset:2px;
}
@media (hover:hover) and (pointer:fine) {
  .mg-supporting-item > button:hover {
    border-color:color-mix(in srgb,var(--accent) 55%,var(--border));
    background:color-mix(in srgb,var(--accent) 7%,transparent);
  }
}

@media (hover: hover) {
  .mg-agent-mode-btn:not(.is-active):hover { color: var(--text); }
}
@media (max-width: 640px) {
  .mg-app-header {
    gap: 7px !important;
    padding-left: max(12px, env(safe-area-inset-left)) !important;
    padding-right: max(12px, env(safe-area-inset-right)) !important;
  }
  .mg-header-right { gap: 6px !important; }
  .mg-settings-btn {
    width: 44px;
    display: grid;
    place-items: center;
    padding: 0 !important;
  }
  .mg-settings-icon { display: block; width: 18px; height: 18px; }
  .mg-settings-label { display: none; }
  .mg-tgl {
    width: 44px;
    justify-content: center;
    padding-inline: 0 !important;
  }
  .mg-tgl-label { display: none; }
  .mg-legend:not(.is-open) { width: min(174px, calc(100% - 178px)) !important; }
  .mg-legend.is-open { width: min(238px, calc(100% - 24px)) !important; }
  .mg-graph:has(.mg-legend.is-open) .mg-graph-controls { bottom: 68px; }
  .mg-graph-hint { top: 10px !important; }
  .mg-graph-controls { right: 12px; bottom: 12px; }
  .mg-agent-stack { grid-template-columns: 1fr; }
  .mg-settings-backdrop { padding: 0; place-items: stretch; }
  .mg-settings-deck {
    width: 100%; max-height: 100%; height: 100%; border: 0; border-radius: 0;
  }
  .mg-settings-head { padding: max(15px, env(safe-area-inset-top)) 16px 14px; }
  .mg-settings-head h2 { font-size: 22px; }
  .mg-settings-head p { font-size: 11.5px; }
  .mg-settings-layout { grid-template-columns: 1fr; grid-template-rows: auto minmax(0, 1fr); }
  .mg-settings-nav {
    flex-direction: row; gap: 5px; overflow-x: auto; padding: 9px 10px;
    border-right: 0; border-bottom: 1px solid var(--border); scrollbar-width: none;
  }
  .mg-settings-nav::-webkit-scrollbar { display: none; }
  .mg-settings-nav-item {
    min-width: max-content; min-height: 44px; padding: 7px 11px; border-radius: 9px;
  }
  .mg-settings-nav-item small { display: none; }
  .mg-settings-nav-item.is-active { box-shadow: inset 0 -3px 0 var(--accent); }
  .mg-settings-content { padding: 19px 16px 24px; }
  .mg-settings-section { gap: 17px; }
  .mg-section-intro { grid-template-columns: 1fr; align-items: start; gap: 8px; }
  .mg-stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .mg-advanced-policy-foot { align-items: flex-start; flex-direction: column; }
  .mg-advanced-policy > summary { align-items: flex-start; }
  .mg-advanced-policy > summary small { display: none; }
  .mg-policy-grid { grid-template-columns: 1fr; }
  .mg-schedule-card { grid-template-columns: auto 1fr; }
  .mg-schedule-card input,
  .mg-schedule-card select { grid-column: 1 / -1; width: 100%; }
  .mg-settings-foot { min-height: 66px; padding: 10px 16px max(10px, env(safe-area-inset-bottom)); }
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
  .mg-memory-list .mg-col-type,
  .mg-memory-list .mg-col-size {
    display: none;
  }
  .mg-memory-list th,
  .mg-memory-list td {
    padding-left: 10px !important;
    padding-right: 10px !important;
  }
}
@media (max-width: 350px) {
  .mg-brand > div:last-child { display: none; }
  .mg-legend:not(.is-open) .mg-legend-count { display: none; }
}
/* mobius-ui:ReducedMotion v1 — keep in sync; library candidate. Diverge below the marker only. */
@media (prefers-reduced-motion: reduce) {
  .mg-orbit, .mg-star, .mg-pulse, .mg-skel, .mg-panel, .mg-scrim, .mg-star-hub, .mg-settings-deck, .mg-settings-backdrop { animation: none !important; }
  .mg-svg-node, .mg-svg-label, .mg-legend-chevron { transition: none !important; }
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
