from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse

from mindex.launcher import find_project_root
from mindex.logging_utils import append_action, create_log_run, utc_timestamp, write_status
from mindex.task_queue import QueueStore, QueueStoreError, ensure_ui_config


LOGIN_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} Login</title>
  <style>
    :root {{
      --bg: #0c2d30;
      --bg-accent: #18464a;
      --panel: rgba(250, 244, 231, 0.94);
      --panel-strong: #f5ecdb;
      --line: rgba(10, 43, 44, 0.16);
      --text: #163334;
      --muted: #5a716f;
      --accent: #cd6f32;
      --accent-strong: #b55a22;
      --danger: #8b2d2d;
      --shadow: 0 24px 60px rgba(2, 18, 20, 0.28);
      --font-display: "Palatino Linotype", "Book Antiqua", "Times New Roman", serif;
      --font-body: "Trebuchet MS", "Lucida Grande", "Segoe UI", sans-serif;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      background:
        radial-gradient(circle at top left, rgba(232, 189, 104, 0.35), transparent 28%),
        radial-gradient(circle at bottom right, rgba(255, 130, 69, 0.18), transparent 34%),
        linear-gradient(135deg, var(--bg) 0%, #133d40 55%, var(--bg-accent) 100%);
      color: var(--text);
      font-family: var(--font-body);
    }}

    .shell {{
      width: min(960px, 100%);
      display: grid;
      grid-template-columns: 1.15fr 0.85fr;
      gap: 0;
      overflow: hidden;
      border-radius: 28px;
      box-shadow: var(--shadow);
      background: rgba(255, 255, 255, 0.08);
      backdrop-filter: blur(8px);
    }}

    .intro {{
      padding: 48px;
      color: #f4eddc;
      background:
        linear-gradient(160deg, rgba(255, 255, 255, 0.06), transparent 70%),
        linear-gradient(180deg, rgba(255, 179, 102, 0.12), rgba(0, 0, 0, 0));
      position: relative;
    }}

    .intro::after {{
      content: "";
      position: absolute;
      inset: 24px;
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 24px;
      pointer-events: none;
    }}

    .eyebrow {{
      letter-spacing: 0.28em;
      text-transform: uppercase;
      font-size: 12px;
      color: rgba(244, 237, 220, 0.72);
      margin-bottom: 18px;
    }}

    h1 {{
      margin: 0 0 18px;
      font-family: var(--font-display);
      font-size: clamp(34px, 5vw, 56px);
      line-height: 0.98;
      font-weight: 600;
    }}

    .intro p {{
      max-width: 34ch;
      font-size: 18px;
      line-height: 1.6;
      color: rgba(244, 237, 220, 0.86);
    }}

    .note {{
      margin-top: 32px;
      padding: 16px 18px;
      border-radius: 18px;
      background: rgba(12, 33, 34, 0.26);
      border: 1px solid rgba(255, 255, 255, 0.12);
      font-size: 14px;
      line-height: 1.6;
    }}

    .login-card {{
      background: var(--panel);
      padding: 42px 34px;
      display: flex;
      flex-direction: column;
      justify-content: center;
    }}

    .login-card h2 {{
      margin: 0 0 10px;
      font-family: var(--font-display);
      font-size: 34px;
      font-weight: 600;
    }}

    .login-card p {{
      margin: 0 0 24px;
      color: var(--muted);
      line-height: 1.55;
    }}

    label {{
      display: block;
      margin-bottom: 16px;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }}

    input {{
      width: 100%;
      margin-top: 8px;
      padding: 14px 16px;
      border-radius: 16px;
      border: 1px solid rgba(22, 51, 52, 0.16);
      background: var(--panel-strong);
      color: var(--text);
      font-size: 16px;
      font-family: inherit;
    }}

    input:focus {{
      outline: 2px solid rgba(205, 111, 50, 0.24);
      border-color: var(--accent);
    }}

    button {{
      margin-top: 8px;
      border: none;
      border-radius: 999px;
      padding: 14px 20px;
      font: inherit;
      font-weight: 700;
      background: linear-gradient(135deg, var(--accent) 0%, var(--accent-strong) 100%);
      color: #fffaf2;
      cursor: pointer;
      transition: transform 0.18s ease, box-shadow 0.18s ease;
      box-shadow: 0 12px 24px rgba(181, 90, 34, 0.28);
    }}

    button:hover {{
      transform: translateY(-1px);
    }}

    .error {{
      margin-bottom: 18px;
      padding: 12px 14px;
      border-radius: 14px;
      background: rgba(139, 45, 45, 0.1);
      color: var(--danger);
      border: 1px solid rgba(139, 45, 45, 0.16);
      font-size: 14px;
    }}

    @media (max-width: 800px) {{
      .shell {{
        grid-template-columns: 1fr;
      }}

      .intro,
      .login-card {{
        padding: 30px 24px;
      }}

      .intro p {{
        max-width: none;
      }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="intro">
      <div class="eyebrow">MindX Console</div>
      <h1>Direct every coding session with a queue that remembers.</h1>
      <p>{title} keeps task sessions ordered, persistent, and reviewable so every prompt can move through a defined runbook.</p>
      <div class="note">
        Sign in to add queues, rewrite task instructions, reorder pending work, and review the permanent event log for each completed session.
      </div>
    </section>
    <section class="login-card">
      <h2>Sign in</h2>
      <p>Use the credentials from the config file to unlock the queue director.</p>
      {error_html}
      <form method="post" action="/login">
        <label>
          Username
          <input type="text" name="username" autocomplete="username" required>
        </label>
        <label>
          Password
          <input type="password" name="password" autocomplete="current-password" required>
        </label>
        <button type="submit">Open the interface</button>
      </form>
    </section>
  </div>
</body>
</html>
"""


APP_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  <style>
    :root {{
      --bg: #eef1ea;
      --surface: rgba(255, 249, 238, 0.86);
      --surface-strong: #fffaf1;
      --surface-accent: #183a37;
      --ink: #183231;
      --muted: #6a7a74;
      --line: rgba(24, 58, 55, 0.12);
      --accent: #d66b2d;
      --accent-strong: #b3561f;
      --accent-soft: rgba(214, 107, 45, 0.12);
      --success: #1f6d59;
      --success-soft: rgba(31, 109, 89, 0.12);
      --danger: #8b2d2d;
      --danger-soft: rgba(139, 45, 45, 0.1);
      --shadow: 0 30px 60px rgba(20, 38, 36, 0.12);
      --font-display: "Palatino Linotype", "Book Antiqua", "Times New Roman", serif;
      --font-body: "Trebuchet MS", "Lucida Grande", "Segoe UI", sans-serif;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top left, rgba(214, 107, 45, 0.18), transparent 24%),
        radial-gradient(circle at bottom right, rgba(36, 121, 99, 0.18), transparent 24%),
        linear-gradient(180deg, #f4f6ef 0%, var(--bg) 100%);
      color: var(--ink);
      font-family: var(--font-body);
    }}

    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      background-image: linear-gradient(rgba(24, 58, 55, 0.035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(24, 58, 55, 0.035) 1px, transparent 1px);
      background-size: 28px 28px;
      mask-image: radial-gradient(circle at center, black 38%, transparent 100%);
      pointer-events: none;
      z-index: 0;
    }}

    .app {{
      position: relative;
      z-index: 1;
      display: grid;
      grid-template-columns: 320px minmax(0, 1fr) 340px;
      gap: 22px;
      min-height: 100vh;
      padding: 22px;
    }}

    .panel {{
      background: var(--surface);
      border: 1px solid rgba(255, 255, 255, 0.42);
      border-radius: 28px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
    }}

    .rail {{
      padding: 28px 22px;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }}

    .brand {{
      padding: 20px;
      border-radius: 24px;
      background: linear-gradient(145deg, #163937 0%, #214947 100%);
      color: #f5edda;
    }}

    .brand .eyebrow {{
      margin-bottom: 10px;
      text-transform: uppercase;
      letter-spacing: 0.24em;
      font-size: 11px;
      color: rgba(245, 237, 218, 0.66);
    }}

    .brand h1 {{
      margin: 0 0 8px;
      font-family: var(--font-display);
      font-size: 34px;
      line-height: 0.98;
      font-weight: 600;
    }}

    .brand p {{
      margin: 0;
      color: rgba(245, 237, 218, 0.82);
      line-height: 1.55;
      font-size: 15px;
    }}

    .meta-strip {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}

    .meta-card {{
      padding: 14px 16px;
      border-radius: 18px;
      background: var(--surface-strong);
      border: 1px solid var(--line);
    }}

    .meta-card span {{
      display: block;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.14em;
      color: var(--muted);
      margin-bottom: 6px;
    }}

    .meta-card strong {{
      font-size: 16px;
    }}

    .section-title {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }}

    .section-title h2,
    .section-title h3 {{
      margin: 0;
      font-family: var(--font-display);
      font-size: 24px;
      font-weight: 600;
    }}

    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}

    .badge.active {{
      background: rgba(214, 107, 45, 0.12);
      color: var(--accent-strong);
    }}

    .badge.completed {{
      background: rgba(31, 109, 89, 0.14);
      color: var(--success);
    }}

    .queue-form,
    .task-form,
    .editor {{
      display: grid;
      gap: 12px;
    }}

    label {{
      display: grid;
      gap: 6px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }}

    input,
    textarea,
    button {{
      font: inherit;
    }}

    input,
    textarea {{
      width: 100%;
      padding: 13px 14px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: var(--surface-strong);
      color: var(--ink);
      resize: vertical;
      min-height: 48px;
    }}

    textarea {{
      min-height: 96px;
      line-height: 1.5;
    }}

    input:focus,
    textarea:focus {{
      outline: 2px solid rgba(214, 107, 45, 0.2);
      border-color: var(--accent);
    }}

    .button-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}

    button {{
      border: none;
      border-radius: 999px;
      padding: 12px 16px;
      cursor: pointer;
      font-weight: 700;
      transition: transform 0.18s ease, box-shadow 0.18s ease, opacity 0.18s ease;
    }}

    button:hover {{
      transform: translateY(-1px);
    }}

    button:disabled {{
      opacity: 0.55;
      cursor: not-allowed;
      transform: none;
      box-shadow: none;
    }}

    .primary {{
      background: linear-gradient(135deg, var(--accent) 0%, var(--accent-strong) 100%);
      color: #fffaf2;
      box-shadow: 0 10px 18px rgba(181, 86, 31, 0.24);
    }}

    .ghost {{
      background: rgba(255, 255, 255, 0.78);
      color: var(--ink);
      border: 1px solid var(--line);
    }}

    .danger {{
      background: var(--danger-soft);
      color: var(--danger);
      border: 1px solid rgba(139, 45, 45, 0.14);
    }}

    .success {{
      background: var(--success-soft);
      color: var(--success);
      border: 1px solid rgba(31, 109, 89, 0.14);
    }}

    .queue-list {{
      display: grid;
      gap: 10px;
      max-height: 50vh;
      overflow: auto;
      padding-right: 2px;
    }}

    .queue-card {{
      padding: 16px;
      border-radius: 20px;
      border: 1px solid transparent;
      background: rgba(255, 255, 255, 0.55);
      cursor: pointer;
      text-align: left;
      display: grid;
      gap: 8px;
    }}

    .queue-card.active {{
      border-color: rgba(214, 107, 45, 0.26);
      background: linear-gradient(135deg, rgba(214, 107, 45, 0.14), rgba(255, 255, 255, 0.86));
    }}

    .queue-card strong {{
      font-size: 17px;
    }}

    .queue-card p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.45;
      font-size: 14px;
    }}

    .queue-stats {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}

    .main {{
      padding: 28px 26px;
      display: grid;
      gap: 20px;
      align-content: start;
    }}

    .hero {{
      padding: 24px;
      border-radius: 24px;
      background: linear-gradient(135deg, rgba(24, 58, 55, 0.98), rgba(31, 78, 73, 0.92));
      color: #f8f1e2;
      display: grid;
      gap: 14px;
    }}

    .hero-top {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }}

    .hero h2 {{
      margin: 0;
      font-family: var(--font-display);
      font-size: clamp(28px, 4vw, 44px);
      line-height: 1.02;
      font-weight: 600;
    }}

    .hero p {{
      margin: 0;
      max-width: 62ch;
      color: rgba(248, 241, 226, 0.84);
      line-height: 1.55;
      font-size: 15px;
    }}

    .hero-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }}

    .hero-card {{
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(247, 240, 225, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.08);
    }}

    .hero-card span {{
      display: block;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.14em;
      color: rgba(248, 241, 226, 0.62);
      margin-bottom: 6px;
    }}

    .hero-card strong {{
      font-size: 22px;
    }}

    .task-list {{
      display: grid;
      gap: 14px;
    }}

    .task-card {{
      padding: 18px;
      border-radius: 22px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.72);
      display: grid;
      gap: 14px;
      transition: transform 0.18s ease, box-shadow 0.18s ease;
    }}

    .task-card.current {{
      border-color: rgba(214, 107, 45, 0.24);
      box-shadow: 0 18px 32px rgba(214, 107, 45, 0.12);
      transform: translateY(-1px);
    }}

    .task-card.completed {{
      background: rgba(31, 109, 89, 0.05);
      border-color: rgba(31, 109, 89, 0.14);
    }}

    .task-card.completed .task-title {{
      text-decoration: line-through;
      color: var(--muted);
    }}

    .task-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
    }}

    .task-labels {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
      margin-bottom: 10px;
    }}

    .task-number {{
      width: 36px;
      height: 36px;
      border-radius: 50%;
      display: grid;
      place-items: center;
      background: rgba(24, 58, 55, 0.08);
      font-weight: 700;
    }}

    .task-title {{
      margin: 0;
      font-size: 20px;
      font-weight: 700;
      line-height: 1.25;
    }}

    .task-body {{
      margin: 0;
      color: var(--muted);
      line-height: 1.6;
      white-space: pre-wrap;
    }}

    .empty {{
      padding: 18px;
      border-radius: 20px;
      border: 1px dashed rgba(24, 58, 55, 0.18);
      background: rgba(255, 255, 255, 0.46);
      color: var(--muted);
      line-height: 1.6;
    }}

    .history {{
      padding: 28px 22px;
      display: grid;
      gap: 18px;
      align-content: start;
    }}

    .history-list {{
      display: grid;
      gap: 12px;
      max-height: calc(100vh - 220px);
      overflow: auto;
      padding-right: 4px;
    }}

    .history-item {{
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.74);
      border: 1px solid var(--line);
    }}

    .history-item strong {{
      display: block;
      margin-bottom: 6px;
      font-size: 14px;
    }}

    .history-item p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.5;
      font-size: 14px;
      white-space: pre-wrap;
    }}

    .history-item time {{
      display: inline-block;
      margin-top: 10px;
      font-size: 12px;
      color: var(--muted);
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}

    .flash {{
      padding: 14px 16px;
      border-radius: 18px;
      font-size: 14px;
      line-height: 1.5;
      display: none;
    }}

    .flash.visible {{
      display: block;
    }}

    .flash.info {{
      background: rgba(214, 107, 45, 0.12);
      color: var(--accent-strong);
    }}

    .flash.error {{
      background: var(--danger-soft);
      color: var(--danger);
    }}

    .toolbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }}

    .muted {{
      color: var(--muted);
    }}

    .footer-note {{
      font-size: 13px;
      line-height: 1.5;
      color: var(--muted);
    }}

    @media (max-width: 1220px) {{
      .app {{
        grid-template-columns: 290px minmax(0, 1fr);
      }}

      .history {{
        grid-column: 1 / -1;
      }}

      .history-list {{
        max-height: 420px;
      }}
    }}

    @media (max-width: 880px) {{
      .app {{
        grid-template-columns: 1fr;
        padding: 14px;
      }}

      .rail,
      .main,
      .history {{
        padding: 22px 18px;
      }}

      .hero-grid {{
        grid-template-columns: 1fr;
      }}

      .meta-strip {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <div id="app" class="app"></div>
  <script>
    window.MINDEX_BOOT = __BOOT_PAYLOAD__;
  </script>
  <script>
    (() => {{
      const boot = window.MINDEX_BOOT;
      const app = document.getElementById('app');
      const state = {{
        queues: [],
        selectedQueueId: null,
        flash: null,
      }};

      const eventLabels = {{
        'queue.created': 'Queue created',
        'queue.updated': 'Queue updated',
        'queue.reordered': 'Task order changed',
        'queue.completed': 'Queue completed',
        'task.created': 'Task added',
        'task.updated': 'Task updated',
        'task.deleted': 'Task deleted',
        'task.completed': 'Task completed',
        'task.reopened': 'Task reopened',
      }};

      function escapeHtml(value) {{
        return String(value || '')
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;')
          .replace(/'/g, '&#39;');
      }}

      function formatTimestamp(value) {{
        if (!value) {{
          return 'n/a';
        }}
        const match = /^(\\d{{4}})(\\d{{2}})(\\d{{2}})T(\\d{{2}})(\\d{{2}})(\\d{{2}})Z$/.exec(value);
        if (!match) {{
          return value;
        }}
        const [, year, month, day, hour, minute, second] = match;
        const iso = `${{year}}-${{month}}-${{day}}T${{hour}}:${{minute}}:${{second}}Z`;
        return new Date(iso).toLocaleString();
      }}

      function queueStatusClass(status) {{
        return status === 'completed' ? 'completed' : 'active';
      }}

      function setFlash(kind, message) {{
        state.flash = message ? {{ kind, message }} : null;
        render();
      }}

      function getSelectedQueue() {{
        return state.queues.find((queue) => queue.id === state.selectedQueueId) || null;
      }}

      async function api(path, options = {{}}) {{
        const response = await fetch(path, {{
          headers: {{
            'Content-Type': 'application/json',
            ...(options.headers || {{}}),
          }},
          ...options,
        }});

        if (response.status === 401) {{
          window.location.href = '/login';
          return null;
        }}

        if (response.status === 204) {{
          return null;
        }}

        const payload = await response.json();
        if (!response.ok) {{
          throw new Error(payload.error || 'Request failed');
        }}
        return payload;
      }}

      async function refresh(selectedQueueId = state.selectedQueueId) {{
        const payload = await api('/api/state');
        if (!payload) {{
          return;
        }}
        state.queues = payload.queues || [];
        const availableIds = new Set(state.queues.map((queue) => queue.id));
        if (selectedQueueId && availableIds.has(selectedQueueId)) {{
          state.selectedQueueId = selectedQueueId;
        }} else if (state.queues.length) {{
          state.selectedQueueId = state.queues[0].id;
        }} else {{
          state.selectedQueueId = null;
        }}
        render();
      }}

      async function submitJson(path, method, payload, successMessage, selectedQueueId) {{
        try {{
          await api(path, {{
            method,
            body: JSON.stringify(payload || {{}}),
          }});
          setFlash('info', successMessage);
          await refresh(selectedQueueId);
        }} catch (error) {{
          setFlash('error', error.message);
        }}
      }}

      function eventSummary(event) {{
        const details = event.details || {{}};
        if (event.event_type === 'task.created' || event.event_type === 'task.deleted') {{
          return details.task && details.task.title ? details.task.title : 'Task updated';
        }}
        if (event.event_type === 'task.updated') {{
          const parts = [];
          if (details.changes && details.changes.title) {{
            parts.push(`Title: ${{details.changes.title}}`);
          }}
          if (details.changes && Object.prototype.hasOwnProperty.call(details.changes, 'instructions')) {{
            parts.push('Instructions revised');
          }}
          return parts.join(' | ') || 'Task fields changed';
        }}
        if (event.event_type === 'task.completed' || event.event_type === 'task.reopened') {{
          return details.title || details.task_id || 'Task state updated';
        }}
        if (event.event_type === 'queue.updated') {{
          return Object.entries(details).map(([key, value]) => `${{key}}: ${{value}}`).join(' | ') || 'Queue details changed';
        }}
        if (event.event_type === 'queue.reordered') {{
          return 'Pending sequence updated';
        }}
        if (event.event_type === 'queue.completed') {{
          return `Completed ${{details.completed_count || 0}} tasks`;
        }}
        if (event.event_type === 'queue.created') {{
          return details.description || 'New session queue opened';
        }}
        return '';
      }}

      function renderQueueList() {{
        if (!state.queues.length) {{
          return '<div class="empty">No session queues yet. Create one to start directing MindX.</div>';
        }}

        return state.queues.map((queue) => {{
          const isActive = queue.id === state.selectedQueueId;
          return `
            <button class="queue-card ${{isActive ? 'active' : ''}}" data-queue-id="${{queue.id}}">
              <div class="section-title">
                <strong>${{escapeHtml(queue.name)}}</strong>
                <span class="badge ${{queueStatusClass(queue.status)}}">${{escapeHtml(queue.status)}}</span>
              </div>
              <p>${{escapeHtml(queue.description || 'No queue description yet.')}}</p>
              <div class="queue-stats">
                <span>${{queue.completed_count}} / ${{queue.task_count}} done</span>
                <span>${{queue.current_task ? 'Next ready' : (queue.task_count ? 'Session closed' : 'Empty queue')}}</span>
              </div>
            </button>
          `;
        }}).join('');
      }}

      function renderTaskCard(queue, task, index) {{
        const canMoveUp = task.status === 'pending' && index > queue.completed_count;
        const canMoveDown = task.status === 'pending' && index < queue.tasks.length - 1;
        const isCurrent = queue.current_task_id === task.id;
        const isCompleted = task.status === 'completed';

        return `
          <article class="task-card ${{isCurrent ? 'current' : ''}} ${{isCompleted ? 'completed' : ''}}" data-task-id="${{task.id}}">
            <div class="task-head">
              <div>
                <div class="task-labels">
                  <span class="task-number">${{index + 1}}</span>
                  <span class="badge ${{isCompleted ? 'completed' : 'active'}}">${{isCompleted ? 'completed' : 'pending'}}</span>
                  ${{isCurrent ? '<span class="badge active">Next up</span>' : ''}}
                </div>
                <h3 class="task-title">${{escapeHtml(task.title)}}</h3>
              </div>
            </div>
            <p class="task-body">${{escapeHtml(task.instructions || 'No detailed instructions for this step yet.')}}</p>
            <div class="button-row">
              <button class="ghost" data-action="edit-task" data-task-id="${{task.id}}">Edit</button>
              <button class="ghost" data-action="move-up" data-task-id="${{task.id}}" ${{canMoveUp ? '' : 'disabled'}}>Move up</button>
              <button class="ghost" data-action="move-down" data-task-id="${{task.id}}" ${{canMoveDown ? '' : 'disabled'}}>Move down</button>
              <button class="${{isCompleted ? 'ghost' : 'success'}}" data-action="toggle-complete" data-task-id="${{task.id}}">
                ${{isCompleted ? 'Reopen' : 'Mark done'}}
              </button>
              <button class="danger" data-action="delete-task" data-task-id="${{task.id}}">Delete</button>
            </div>
            <div class="editor" id="editor-${{task.id}}" hidden>
              <label>
                Task title
                <input type="text" name="title" value="${{escapeHtml(task.title)}}">
              </label>
              <label>
                Instructions
                <textarea name="instructions">${{escapeHtml(task.instructions || '')}}</textarea>
              </label>
              <div class="button-row">
                <button class="primary" data-action="save-task" data-task-id="${{task.id}}">Save changes</button>
                <button class="ghost" data-action="cancel-edit" data-task-id="${{task.id}}">Cancel</button>
              </div>
            </div>
          </article>
        `;
      }}

      function renderMain(queue) {{
        if (!queue) {{
          return `
            <section class="panel main">
              <div class="hero">
                <div class="hero-top">
                  <h2>No queue selected</h2>
                  <span class="badge active">Ready</span>
                </div>
                <p>Create a queue in the left rail to start building a session for MindX.</p>
              </div>
            </section>
          `;
        }}

        return `
          <section class="panel main">
            <div class="flash ${{state.flash ? `visible ${{state.flash.kind}}` : ''}}">${{state.flash ? escapeHtml(state.flash.message) : ''}}</div>
            <div class="hero">
              <div class="hero-top">
                <div>
                  <span class="badge ${{queueStatusClass(queue.status)}}">${{escapeHtml(queue.status)}}</span>
                  <h2>${{escapeHtml(queue.name)}}</h2>
                </div>
                <a class="ghost" href="/logout" style="text-decoration:none;display:inline-flex;align-items:center;">Log out</a>
              </div>
              <p>${{escapeHtml(queue.description || 'Add a session description so the agent knows the objective and delivery shape.')}}</p>
              <div class="hero-grid">
                <div class="hero-card">
                  <span>Next Task</span>
                  <strong>${{escapeHtml(queue.current_task ? queue.current_task.title : (queue.task_count ? 'Session finished' : 'Awaiting tasks'))}}</strong>
                </div>
                <div class="hero-card">
                  <span>Completed</span>
                  <strong>${{queue.completed_count}} / ${{queue.task_count}}</strong>
                </div>
                <div class="hero-card">
                  <span>Created</span>
                  <strong>${{escapeHtml(formatTimestamp(queue.created_at))}}</strong>
                </div>
              </div>
            </div>

            <section class="panel" style="padding: 20px;">
              <div class="section-title">
                <h3>Queue details</h3>
                <span class="muted">Rename the session or clarify its objective.</span>
              </div>
              <form class="editor" id="queue-editor">
                <label>
                  Queue name
                  <input type="text" name="name" value="${{escapeHtml(queue.name)}}">
                </label>
                <label>
                  Queue description
                  <textarea name="description">${{escapeHtml(queue.description || '')}}</textarea>
                </label>
                <div class="button-row">
                  <button class="primary" type="submit">Save queue details</button>
                </div>
              </form>
            </section>

            <section class="panel" style="padding: 20px;">
              <div class="section-title">
                <h3>Add a task</h3>
                <span class="muted">Every new item lands at the end of the pending sequence.</span>
              </div>
              <form class="task-form" id="task-form">
                <label>
                  Task title
                  <input type="text" name="title" placeholder="Example: Review the failing tests" required>
                </label>
                <label>
                  Instructions
                  <textarea name="instructions" placeholder="Describe what MindX should do for this task."></textarea>
                </label>
                <div class="button-row">
                  <button class="primary" type="submit">Add task</button>
                </div>
              </form>
            </section>

            <section>
              <div class="toolbar">
                <div class="section-title" style="margin-bottom: 0;">
                  <h3>Task sequence</h3>
                </div>
                <div class="footer-note">Completed tasks stay crossed off, and only the next pending task can be marked done.</div>
              </div>
              <div class="task-list">
                ${{queue.tasks.length ? queue.tasks.map((task, index) => renderTaskCard(queue, task, index)).join('') : '<div class="empty">This session is empty. Add the first instruction to set the queue in motion.</div>'}}
              </div>
            </section>
          </section>
        `;
      }}

      function renderHistory(queue) {{
        return `
          <aside class="panel history">
            <div class="section-title">
              <h3>Permanent log</h3>
              <span class="muted">${{queue ? `${{queue.events.length}} events` : 'Select a queue'}}</span>
            </div>
            ${{queue ? `
              <div class="meta-strip">
                <div class="meta-card">
                  <span>Config file</span>
                  <strong>${{escapeHtml(boot.configPath)}}</strong>
                </div>
                <div class="meta-card">
                  <span>Project root</span>
                  <strong>${{escapeHtml(boot.projectRoot)}}</strong>
                </div>
              </div>
              <div class="history-list">
                ${{queue.events.length ? queue.events.slice().reverse().map((event) => `
                  <article class="history-item">
                    <strong>${{escapeHtml(eventLabels[event.event_type] || event.event_type)}}</strong>
                    <p>${{escapeHtml(eventSummary(event))}}</p>
                    <time>${{escapeHtml(formatTimestamp(event.timestamp))}}</time>
                  </article>
                `).join('') : '<div class="empty">The permanent log will appear here as this session evolves.</div>'}}
              </div>
            ` : '<div class="empty">Create or select a queue to inspect its permanent activity log.</div>'}
          </aside>
        `;
      }}

      function renderRail() {{
        return `
          <aside class="panel rail">
            <section class="brand">
              <div class="eyebrow">MindX Session Director</div>
              <h1>${{escapeHtml(boot.title)}}</h1>
              <p>Build queues, steer the next instruction, and preserve a permanent session log for every task run.</p>
            </section>
            <section>
              <div class="section-title">
                <h3>Open queues</h3>
                <span class="muted">${{state.queues.length}} total</span>
              </div>
              <div class="queue-list">${{renderQueueList()}}</div>
            </section>
            <section>
              <div class="section-title">
                <h3>New queue</h3>
                <span class="muted">One queue equals one session.</span>
              </div>
              <form class="queue-form" id="queue-form">
                <label>
                  Queue name
                  <input type="text" name="name" placeholder="Example: Publish release prep" required>
                </label>
                <label>
                  Description
                  <textarea name="description" placeholder="Explain the overall outcome this session should deliver."></textarea>
                </label>
                <div class="button-row">
                  <button class="primary" type="submit">Create queue</button>
                </div>
              </form>
            </section>
          </aside>
        `;
      }}

      function render() {{
        const queue = getSelectedQueue();
        app.innerHTML = `
          ${{renderRail()}}
          ${{renderMain(queue)}}
          ${{renderHistory(queue)}}
        `;
        bindEvents();
      }}

      function moveTask(queue, taskId, offset) {{
        const tasks = queue.tasks.slice();
        const index = tasks.findIndex((task) => task.id === taskId);
        if (index < 0) {{
          return;
        }}
        const targetIndex = index + offset;
        if (targetIndex < 0 || targetIndex >= tasks.length) {{
          return;
        }}
        const [task] = tasks.splice(index, 1);
        tasks.splice(targetIndex, 0, task);
        submitJson(`/api/queues/${{queue.id}}/reorder`, 'POST', {{
          task_ids: tasks.map((item) => item.id),
        }}, 'Task order updated.', queue.id);
      }}

      function bindEvents() {{
        const queueForm = document.getElementById('queue-form');
        if (queueForm) {{
          queueForm.addEventListener('submit', (event) => {{
            event.preventDefault();
            const form = new FormData(queueForm);
            submitJson('/api/queues', 'POST', {{
              name: form.get('name'),
              description: form.get('description'),
            }}, 'Queue created.', state.selectedQueueId);
            queueForm.reset();
          }});
        }}

        document.querySelectorAll('[data-queue-id]').forEach((button) => {{
          button.addEventListener('click', () => {{
            state.selectedQueueId = button.dataset.queueId;
            render();
          }});
        }});

        const queueEditor = document.getElementById('queue-editor');
        if (queueEditor && state.selectedQueueId) {{
          queueEditor.addEventListener('submit', (event) => {{
            event.preventDefault();
            const form = new FormData(queueEditor);
            submitJson(`/api/queues/${{state.selectedQueueId}}`, 'PUT', {{
              name: form.get('name'),
              description: form.get('description'),
            }}, 'Queue details saved.', state.selectedQueueId);
          }});
        }}

        const taskForm = document.getElementById('task-form');
        if (taskForm && state.selectedQueueId) {{
          taskForm.addEventListener('submit', (event) => {{
            event.preventDefault();
            const form = new FormData(taskForm);
            submitJson(`/api/queues/${{state.selectedQueueId}}/tasks`, 'POST', {{
              title: form.get('title'),
              instructions: form.get('instructions'),
            }}, 'Task added to the session.', state.selectedQueueId);
            taskForm.reset();
          }});
        }}

        const queue = getSelectedQueue();
        document.querySelectorAll('[data-action]').forEach((button) => {{
          button.addEventListener('click', (event) => {{
            event.preventDefault();
            if (!queue) {{
              return;
            }}
            const action = button.dataset.action;
            const taskId = button.dataset.taskId;
            if (action === 'edit-task') {{
              document.getElementById(`editor-${{taskId}}`).hidden = false;
              return;
            }}
            if (action === 'cancel-edit') {{
              document.getElementById(`editor-${{taskId}}`).hidden = true;
              return;
            }}
            if (action === 'save-task') {{
              const editor = document.getElementById(`editor-${{taskId}}`);
              submitJson(`/api/queues/${{queue.id}}/tasks/${{taskId}}`, 'PUT', {{
                title: editor.querySelector('input[name="title"]').value,
                instructions: editor.querySelector('textarea[name="instructions"]').value,
              }}, 'Task updated.', queue.id);
              return;
            }}
            if (action === 'move-up') {{
              moveTask(queue, taskId, -1);
              return;
            }}
            if (action === 'move-down') {{
              moveTask(queue, taskId, 1);
              return;
            }}
            if (action === 'toggle-complete') {{
              const task = queue.tasks.find((item) => item.id === taskId);
              submitJson(`/api/queues/${{queue.id}}/tasks/${{taskId}}/complete`, 'POST', {{
                completed: !(task && task.status === 'completed'),
              }}, task && task.status === 'completed' ? 'Task reopened.' : 'Task marked complete.', queue.id);
              return;
            }}
            if (action === 'delete-task') {{
              const task = queue.tasks.find((item) => item.id === taskId);
              const title = task ? task.title : 'this task';
              if (window.confirm(`Delete "${{title}}" from the queue? The permanent log entry will remain.`)) {{
                api(`/api/queues/${{queue.id}}/tasks/${{taskId}}`, {{ method: 'DELETE' }})
                  .then(() => {{
                    setFlash('info', 'Task deleted from the active queue.');
                    return refresh(queue.id);
                  }})
                  .catch((error) => setFlash('error', error.message));
              }}
            }}
          }});
        }});
      }}

      refresh().catch((error) => {{
        setFlash('error', error.message);
        render();
      }});
    }})();
  </script>
</body>
</html>
"""


class AuthSessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: set[str] = set()

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens.add(token)
        return token

    def contains(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            return token in self._tokens

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._tokens.discard(token)


class MindXHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        *,
        queue_store: QueueStore,
        config: dict[str, Any],
        config_path: Path,
        project_root: Path,
        log_run: Any | None = None,
    ) -> None:
        super().__init__(server_address, request_handler_class)
        self.queue_store = queue_store
        self.config = config
        self.config_path = config_path
        self.project_root = project_root
        self.sessions = AuthSessionStore()
        self.log_run = log_run


class UIRequestHandler(BaseHTTPRequestHandler):
    server: MindXHTTPServer
    server_version = "MindXUI/0.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            if not self._is_authenticated():
                self._redirect("/login")
                return
            self._send_html(self._app_page())
            return
        if path == "/login":
            self._send_html(self._login_page())
            return
        if path == "/logout":
            self.server.sessions.delete(self._session_token())
            self._redirect("/login", cookie="mindex_session=; Path=/; HttpOnly; Max-Age=0; SameSite=Strict")
            return
        if path == "/api/state":
            if not self._require_auth_json():
                return
            self._send_json(HTTPStatus.OK, self._state_payload())
            return
        if path == "/healthz":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/login":
            form = self._parse_form_body()
            username = form.get("username", "")
            password = form.get("password", "")
            auth = self.server.config.get("auth", {})
            if username == auth.get("username") and password == auth.get("password"):
                token = self.server.sessions.create()
                self._redirect("/", cookie=f"mindex_session={token}; Path=/; HttpOnly; SameSite=Strict")
                return
            self._send_html(self._login_page("Invalid username or password."), status=HTTPStatus.UNAUTHORIZED)
            return
        if not self._require_auth_json():
            return
        try:
            body = self._parse_json_body()
            if path == "/api/queues":
                queue = self.server.queue_store.create_queue(
                    name=body.get("name", ""),
                    description=body.get("description", ""),
                )
                self._send_json(HTTPStatus.CREATED, {"queue": queue})
                return

            segments = self._path_segments(path)
            if len(segments) == 4 and segments[:3] == ["api", "queues", segments[2]] and segments[3] == "tasks":
                queue = self.server.queue_store.add_task(
                    segments[2],
                    title=body.get("title", ""),
                    instructions=body.get("instructions", ""),
                )
                self._send_json(HTTPStatus.CREATED, {"queue": queue})
                return
            if len(segments) == 4 and segments[:2] == ["api", "queues"] and segments[3] == "reorder":
                queue = self.server.queue_store.reorder_tasks(segments[2], task_ids=list(body.get("task_ids", [])))
                self._send_json(HTTPStatus.OK, {"queue": queue})
                return
            if len(segments) == 6 and segments[:2] == ["api", "queues"] and segments[3] == "tasks" and segments[5] == "complete":
                queue = self.server.queue_store.set_task_completion(
                    segments[2],
                    segments[4],
                    completed=bool(body.get("completed")),
                )
                self._send_json(HTTPStatus.OK, {"queue": queue})
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
        except QueueStoreError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Request body must be valid JSON."})

    def do_PUT(self) -> None:
        if not self._require_auth_json():
            return
        path = urlparse(self.path).path
        try:
            body = self._parse_json_body()
            segments = self._path_segments(path)
            if len(segments) == 3 and segments[:2] == ["api", "queues"]:
                queue = self.server.queue_store.update_queue(
                    segments[2],
                    name=body.get("name"),
                    description=body.get("description"),
                )
                self._send_json(HTTPStatus.OK, {"queue": queue})
                return
            if len(segments) == 5 and segments[:2] == ["api", "queues"] and segments[3] == "tasks":
                queue = self.server.queue_store.update_task(
                    segments[2],
                    segments[4],
                    title=body.get("title"),
                    instructions=body.get("instructions"),
                )
                self._send_json(HTTPStatus.OK, {"queue": queue})
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
        except QueueStoreError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Request body must be valid JSON."})

    def do_DELETE(self) -> None:
        if not self._require_auth_json():
            return
        path = urlparse(self.path).path
        try:
            segments = self._path_segments(path)
            if len(segments) == 5 and segments[:2] == ["api", "queues"] and segments[3] == "tasks":
                queue = self.server.queue_store.delete_task(segments[2], segments[4])
                self._send_json(HTTPStatus.OK, {"queue": queue})
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
        except QueueStoreError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _session_token(self) -> str | None:
        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return None
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        morsel = cookie.get("mindex_session")
        return morsel.value if morsel is not None else None

    def _is_authenticated(self) -> bool:
        return self.server.sessions.contains(self._session_token())

    def _require_auth_json(self) -> bool:
        if self._is_authenticated():
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Authentication required."})
        return False

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def _parse_json_body(self) -> dict[str, Any]:
        body = self._read_body()
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))

    def _parse_form_body(self) -> dict[str, str]:
        body = self._read_body().decode("utf-8")
        return {key: values[-1] for key, values in parse_qs(body, keep_blank_values=True).items()}

    def _send_html(self, html: str, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _redirect(self, location: str, *, cookie: str | None = None) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _state_payload(self) -> dict[str, Any]:
        snapshot = self.server.queue_store.snapshot()
        return {
            "generated_at": snapshot["generated_at"],
            "queues": snapshot["queues"],
            "title": self.server.config.get("ui", {}).get("title", "MindX Session Director"),
            "config_path": str(self.server.config_path),
            "project_root": str(self.server.project_root),
        }

    def _login_page(self, error_message: str | None = None) -> str:
        error_html = ""
        if error_message:
            error_html = f'<div class="error">{error_message}</div>'
        return LOGIN_PAGE_TEMPLATE.format(
            title=self.server.config.get("ui", {}).get("title", "MindX Session Director"),
            error_html=error_html,
        )

    def _app_page(self) -> str:
        payload = {
            "title": self.server.config.get("ui", {}).get("title", "MindX Session Director"),
            "configPath": str(self.server.config_path),
            "projectRoot": str(self.server.project_root),
        }
        return (
            APP_PAGE_TEMPLATE.replace(
                "__TITLE__",
                self.server.config.get("ui", {}).get("title", "MindX Session Director"),
            )
            .replace("__BOOT_PAYLOAD__", json.dumps(payload, sort_keys=True))
            .replace("{{", "{")
            .replace("}}", "}")
        )

    def _path_segments(self, path: str) -> list[str]:
        return [segment for segment in path.split("/") if segment]


def build_server(
    *,
    project_root: Path | str,
    config_path: Path | str,
    config: dict[str, Any],
    queue_store: QueueStore,
    log_run: Any | None = None,
) -> MindXHTTPServer:
    resolved_root = Path(project_root).resolve()
    resolved_config_path = Path(config_path).resolve()
    server_config = config.get("server", {})
    host = str(server_config.get("host", "0.0.0.0"))
    port = int(server_config.get("port", 8000))
    return MindXHTTPServer(
        (host, port),
        UIRequestHandler,
        queue_store=queue_store,
        config=config,
        config_path=resolved_config_path,
        project_root=resolved_root,
        log_run=log_run,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the MindX task queue interface")
    parser.add_argument("--project-root", help="Override the project root used for config and storage resolution")
    parser.add_argument("--config", help="Override the UI config file path")
    parser.add_argument(
        "--init-only",
        action="store_true",
        help="Create the default config and storage layout without starting the server",
    )
    return parser


def main(argv: list[str] | None = None, *, project_root: Path | str | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    resolved_project_root = (
        Path(args.project_root).resolve() if args.project_root else find_project_root(project_root)
    )
    config_path, config = ensure_ui_config(resolved_project_root, config_path=args.config)
    queue_store = QueueStore.from_config(resolved_project_root, config)

    if args.init_only:
        print(
            json.dumps(
                {
                    "config_path": str(config_path),
                    "project_root": str(resolved_project_root),
                    "server": config["server"],
                    "storage": config["storage"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    log_run = create_log_run(
        resolved_project_root / "logs",
        "ui",
        prompt_text="mindex ui",
        metadata={
            "project_root": str(resolved_project_root),
            "config_path": str(config_path),
            "server": config.get("server", {}),
            "storage": config.get("storage", {}),
        },
    )

    server = build_server(
        project_root=resolved_project_root,
        config_path=config_path,
        config=config,
        queue_store=queue_store,
        log_run=log_run,
    )
    host, port = server.server_address
    append_action(log_run, f"UI config: {config_path}")
    append_action(log_run, f"Serving queue interface on {host}:{port}")
    write_status(log_run, "running", host=host, port=port, started_at=utc_timestamp())

    print(f"MindX UI ready at http://{host}:{port}/")
    if host == "0.0.0.0":
        print(f"Local access: http://127.0.0.1:{port}/")
    print(f"Config file: {config_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        append_action(log_run, "UI server interrupted by operator.")
    finally:
        server.server_close()
        write_status(log_run, "success", host=host, port=port, stopped_at=utc_timestamp())
    return 0
