"""Interactive Web UI and REST API for uploading, reviewing, and curating PersonSort folders."""

from __future__ import annotations

import json
import mimetypes
import tempfile
import threading
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from session_state import CurationSession


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>PersonSort</title>
  <style>
    :root {
      --bg: #0f172a;
      --panel: #1e293b;
      --panel-hover: #334155;
      --border: #334155;
      --text: #f8fafc;
      --muted: #94a3b8;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      display: flex;
      flex-direction: column;
      height: 100vh;
      overflow: hidden;
    }
    /* Top Header */
    header {
      background: #0b1120;
      border-bottom: 1px solid var(--border);
      padding: 12px 22px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-shrink: 0;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 700;
      font-size: 1.15rem;
    }
    .header-stats {
      display: flex;
      align-items: center;
      gap: 14px;
      font-size: 0.9rem;
      color: var(--muted);
    }

    /* ================= HOMESCREEN VIEW ================= */
    #home-screen {
      flex: 1;
      overflow-y: auto;
      display: none;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 32px 20px;
      gap: 24px;
    }
    #home-screen.active { display: flex; }
    .home-card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 16px;
      max-width: 660px;
      width: 100%;
      padding: 34px;
      display: flex;
      flex-direction: column;
      gap: 22px;
      box-shadow: 0 20px 45px rgba(0, 0, 0, 0.35);
    }
    .commit-success-banner {
      display: none;
      background: rgba(16, 185, 129, 0.15);
      border: 1px solid var(--success);
      border-radius: 12px;
      padding: 16px 20px;
      color: #a7f3d0;
      font-size: 0.92rem;
      line-height: 1.5;
    }
    .commit-success-banner.visible { display: block; }
    .upload-dropzone {
      border: 2px dashed #60a5fa;
      background: rgba(59, 130, 246, 0.07);
      border-radius: 14px;
      padding: 42px 24px;
      text-align: center;
      cursor: pointer;
      transition: background 0.15s, border-color 0.15s;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 12px;
    }
    .upload-dropzone:hover, .upload-dropzone.dragover {
      background: rgba(59, 130, 246, 0.15);
      border-color: #93c5fd;
    }
    .btn-primary-large {
      background: var(--primary);
      color: white;
      border: none;
      padding: 12px 24px;
      border-radius: 8px;
      font-size: 0.98rem;
      font-weight: 700;
      cursor: pointer;
    }
    .btn-primary-large:hover { background: var(--primary-hover); }

    /* Progress Bar Container */
    .upload-progress {
      display: none;
      flex-direction: column;
      gap: 10px;
      background: #0f172a;
      padding: 16px 18px;
      border-radius: 10px;
      border: 1px solid var(--border);
    }
    .upload-progress.visible { display: flex; }
    .progress-header-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 0.9rem;
      font-weight: 600;
      color: #93c5fd;
    }
    .progress-track {
      width: 100%;
      height: 12px;
      background: #1e293b;
      border-radius: 999px;
      overflow: hidden;
      border: 1px solid #334155;
    }
    .progress-fill {
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, #3b82f6, #10b981);
      transition: width 0.2s ease;
    }

    /* ================= REVIEW VIEW ================= */
    #review-screen {
      flex: 1;
      display: none;
      flex-direction: column;
      overflow: hidden;
    }
    #review-screen.active { display: flex; }

    /* Unassigned Photo Warning Banner */
    #warning-banner {
      display: none;
      background: rgba(245, 158, 11, 0.16);
      border-bottom: 2px solid var(--warning);
      color: #fde68a;
      padding: 10px 20px;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      font-size: 0.92rem;
      font-weight: 600;
      flex-shrink: 0;
    }
    #warning-banner.visible { display: flex; }
    .warn-btn {
      background: var(--warning);
      color: #0f172a;
      border: none;
      padding: 6px 12px;
      border-radius: 6px;
      font-weight: 700;
      cursor: pointer;
      font-size: 0.82rem;
    }
    .workspace {
      display: flex;
      flex: 1;
      overflow: hidden;
    }
    aside {
      width: 295px;
      background: #111827;
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      overflow-y: auto;
      padding: 14px;
      gap: 6px;
      flex-shrink: 0;
    }
    .sidebar-section-title {
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin: 10px 6px 4px;
      font-weight: 700;
    }
    .folder-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 10px;
      border-radius: 8px;
      cursor: pointer;
      color: var(--text);
      font-size: 0.88rem;
      transition: background 0.15s;
      border: 1px solid transparent;
      gap: 10px;
    }
    .folder-item:hover { background: var(--panel); }
    .folder-item.active {
      background: rgba(59, 130, 246, 0.18);
      border-color: var(--primary);
      font-weight: 600;
    }
    .folder-item.warning-folder {
      color: #fcd34d;
      border-color: rgba(245, 158, 11, 0.4);
    }
    .sidebar-face-avatar {
      width: 38px;
      height: 38px;
      border-radius: 8px;
      object-fit: cover;
      border: 1px solid #475569;
      background: #070b12;
      flex-shrink: 0;
    }
    .sidebar-face-placeholder {
      width: 38px;
      height: 38px;
      border-radius: 8px;
      background: #1e293b;
      border: 1px solid #334155;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 0.7rem;
      color: var(--muted);
      flex-shrink: 0;
    }
    .sidebar-jersey-badge {
      width: 38px;
      height: 38px;
      border-radius: 8px;
      background: rgba(245, 158, 11, 0.18);
      border: 1px solid var(--warning);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 0.86rem;
      font-weight: 800;
      color: #fde68a;
      flex-shrink: 0;
    }
    .folder-item.jersey-folder-item.active {
      background: rgba(245, 158, 11, 0.22);
      border-color: var(--warning);
    }
    .badge {
      background: var(--panel-hover);
      color: var(--text);
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 0.75rem;
      font-weight: 600;
    }
    .badge.warn {
      background: var(--danger);
      color: white;
    }
    .new-folder-btn {
      margin-top: 10px;
      padding: 9px 12px;
      background: var(--panel);
      border: 1px dashed var(--muted);
      color: var(--text);
      border-radius: 8px;
      cursor: pointer;
      font-size: 0.85rem;
      font-weight: 600;
      text-align: center;
    }
    .new-folder-btn:hover { border-color: var(--primary); color: #93c5fd; }

    main {
      flex: 1;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    /* Folder Header with Prominent Player Reference Face Picture */
    .folder-header {
      padding: 14px 22px;
      background: var(--panel);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
      flex-shrink: 0;
    }
    .folder-title-group {
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .header-ref-face {
      width: 68px;
      height: 68px;
      border-radius: 10px;
      object-fit: cover;
      border: 2px solid var(--primary);
      background: #070b12;
      display: none;
      flex-shrink: 0;
    }
    .header-ref-face.visible {
      display: block;
    }
    .folder-title-group h2 { font-size: 1.25rem; }
    .header-controls {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .header-select {
      background: #0f172a;
      color: var(--text);
      border: 1px solid #475569;
      border-radius: 6px;
      padding: 6px 10px;
      font-size: 0.82rem;
    }
    .btn-secondary {
      background: var(--panel-hover);
      color: var(--text);
      border: 1px solid #475569;
      padding: 6px 12px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 0.82rem;
      font-weight: 600;
    }
    .btn-secondary:hover { background: #475569; }

    /* Photo Grid */
    .photo-grid {
      flex: 1;
      padding: 20px;
      overflow-y: auto;
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
      grid-auto-rows: max-content;
      gap: 20px;
      align-content: start;
    }
    .photo-card {
      background: var(--panel);
      border: 2px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      min-height: 365px;
      transition: transform 0.12s, border-color 0.12s;
    }
    .photo-card:hover {
      border-color: #64748b;
    }
    .photo-card.no-folder-warning {
      border-color: var(--danger);
      box-shadow: 0 0 0 2px rgba(239, 68, 68, 0.25);
    }
    .photo-thumb-wrap {
      position: relative;
      width: 100%;
      height: 235px;
      min-height: 235px;
      flex-shrink: 0;
      background: #070b12;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      cursor: pointer;
    }
    .photo-thumb-wrap img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
    }
    .zoom-hint {
      position: absolute;
      bottom: 8px;
      right: 8px;
      background: rgba(15, 23, 42, 0.85);
      color: #cbd5e1;
      border: 1px solid rgba(255,255,255,0.18);
      padding: 3px 8px;
      border-radius: 5px;
      font-size: 0.72rem;
      pointer-events: none;
    }
    .sim-pill {
      position: absolute;
      top: 8px;
      right: 8px;
      padding: 4px 9px;
      border-radius: 6px;
      font-size: 0.78rem;
      font-weight: 700;
      backdrop-filter: blur(6px);
      pointer-events: none;
    }
    .sim-high { background: rgba(16, 185, 129, 0.92); color: #052e16; }
    .sim-med  { background: rgba(245, 158, 11, 0.92); color: #0f172a; }
    .sim-low  { background: rgba(100, 116, 139, 0.92); color: #f8fafc; }
    .jersey-pill {
      position: absolute;
      bottom: 8px;
      left: 8px;
      background: rgba(30, 58, 138, 0.92);
      border: 1px solid #60a5fa;
      color: #eff6ff;
      padding: 2px 8px;
      border-radius: 5px;
      font-size: 0.75rem;
      font-weight: 700;
      pointer-events: none;
    }
    .photo-body {
      padding: 12px 14px;
      display: flex;
      flex-direction: column;
      gap: 9px;
      flex: 1;
    }
    .photo-name-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-weight: 600;
      font-size: 0.92rem;
    }
    .folder-tags {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      min-height: 24px;
    }
    .folder-tag {
      background: #1e3a8a;
      color: #bfdbfe;
      font-size: 0.74rem;
      padding: 2px 7px;
      border-radius: 4px;
      font-weight: 600;
      cursor: pointer;
    }
    .folder-tag.unassigned-tag {
      background: rgba(239, 68, 68, 0.2);
      border: 1px solid var(--danger);
      color: #fca5a5;
      font-weight: 700;
    }
    .folder-tag.jersey-tag {
      background: rgba(245, 158, 11, 0.22);
      border: 1px solid var(--warning);
      color: #fde68a;
      font-weight: 700;
    }
    .jersey-merge-bar {
      background: rgba(245, 158, 11, 0.14);
      border: 1px solid var(--warning);
      padding: 6px 10px;
      border-radius: 8px;
      display: none;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .btn-jersey-merge {
      background: var(--warning);
      color: #0f172a;
      border: none;
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 0.82rem;
      font-weight: 800;
      cursor: pointer;
    }
    .btn-jersey-merge:hover {
      filter: brightness(1.08);
    }
    .card-actions {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-top: auto;
      padding-top: 8px;
      border-top: 1px solid rgba(255,255,255,0.07);
    }
    .move-row {
      display: flex;
      gap: 5px;
    }
    .move-row select {
      flex: 1;
      background: #0f172a;
      color: var(--text);
      border: 1px solid #475569;
      border-radius: 5px;
      padding: 5px 6px;
      font-size: 0.78rem;
    }
    .btn-small {
      padding: 5px 9px;
      border-radius: 5px;
      border: none;
      font-size: 0.76rem;
      font-weight: 600;
      cursor: pointer;
    }
    .btn-move { background: var(--primary); color: white; }
    .btn-move:hover { background: var(--primary-hover); }
    .btn-add { background: #0284c7; color: white; }
    .btn-remove {
      background: rgba(239, 68, 68, 0.16);
      color: #fca5a5;
      border: 1px solid rgba(239, 68, 68, 0.4);
    }
    .btn-remove:hover { background: var(--danger); color: white; }
    .btn-compare {
      background: #334155;
      color: #e2e8f0;
    }
    .btn-compare:hover { background: #475569; }

    /* ================= FULL-SCREEN PREVIEW + CLICKABLE FACE BOXES MODAL ================= */
    #compare-modal {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.84);
      align-items: center;
      justify-content: center;
      z-index: 100;
      padding: 18px;
    }
    #compare-modal.open { display: flex; }
    .modal-box {
      background: var(--panel);
      border: 1px solid #475569;
      border-radius: 14px;
      width: 96%;
      max-width: 1120px;
      max-height: 94vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      box-shadow: 0 24px 60px rgba(0,0,0,0.65);
    }
    .modal-header {
      padding: 14px 20px;
      background: #0f172a;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-shrink: 0;
    }
    .modal-body {
      padding: 20px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 20px;
    }
    .lightbox-stage-wrap {
      background: #05080f;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 10px;
    }
    .lightbox-stage {
      position: relative;
      display: inline-block;
      max-width: 100%;
    }
    .lightbox-stage img {
      display: block;
      max-width: 100%;
      max-height: 52vh;
      width: auto;
      height: auto;
      border-radius: 6px;
    }
    .face-overlay-svg {
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
    }
    .clickable-face-group {
      cursor: pointer;
      pointer-events: all;
    }
    .clickable-face-group:hover rect.face-box-stroke {
      stroke: #facc15;
    }

    /* Clicked Face Reference & Top 5-10 Matches Strip */
    .face-matches-panel {
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .selected-face-header {
      display: flex;
      align-items: center;
      gap: 16px;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--border);
    }
    .face-crop-thumb-lg {
      width: 84px;
      height: 84px;
      border-radius: 10px;
      object-fit: cover;
      border: 2px solid var(--success);
      background: #05080f;
      flex-shrink: 0;
    }
    .matches-strip {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
      gap: 12px;
    }
    .match-crop-card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 6px;
      transition: border-color 0.15s;
    }
    .match-crop-card:hover {
      border-color: var(--primary);
    }
    .match-crop-Card-img {
      width: 82px;
      height: 82px;
      border-radius: 8px;
      object-fit: cover;
      background: #05080f;
      border: 1px solid #475569;
      cursor: pointer;
    }

    /* Bottom Sticky Bar */
    footer {
      background: #0b1120;
      border-top: 1px solid var(--border);
      padding: 12px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-shrink: 0;
    }
    .footer-status {
      font-size: 0.9rem;
      color: var(--muted);
    }
    .btn-commit {
      background: var(--success);
      color: #052e16;
      border: none;
      padding: 11px 22px;
      border-radius: 8px;
      font-size: 0.96rem;
      font-weight: 800;
      cursor: pointer;
      box-shadow: 0 4px 14px rgba(16, 185, 129, 0.3);
    }
    .btn-commit:hover { filter: brightness(1.08); }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <span style="cursor:pointer;" onclick="backToHome()">PersonSort</span>
      <a id="crm-return-link" href="#" style="display:none; color:#93c5fd; font-size:0.85rem; text-decoration:none;">Return</a>
    </div>
    <div class="header-stats" id="header-stats">Ready</div>
  </header>

  <!-- ================= 1. HOMESCREEN (Upload .zip or Photos) ================= -->
  <div id="home-screen" class="active">
    <div class="home-card">
      <div id="commit-success-banner" class="commit-success-banner"></div>

      <div>
        <h1 style="font-size:1.55rem; margin-bottom:6px;">Upload Photos to Group by Face</h1>
        <p style="color:var(--muted); font-size:0.94rem;">
          Upload a <strong>.zip file</strong> of photos (or select multiple image files). PersonSort will detect faces, calculate similarity scores, and let you review and adjust folders before creating the real folders.
        </p>
      </div>

      <input
        type="file"
        id="file-input"
        multiple
        accept=".zip,.jpg,.jpeg,.png,.webp,.bmp"
        style="display:none;"
        onchange="handleFileSelect(this.files)"
      />

      <div
        id="dropzone"
        class="upload-dropzone"
        onclick="document.getElementById('file-input').click()"
        ondragover="event.preventDefault(); this.classList.add('dragover');"
        ondragleave="this.classList.remove('dragover');"
        ondrop="event.preventDefault(); this.classList.remove('dragover'); handleFileSelect(event.dataTransfer.files);"
      >
        <div style="font-size:1.08rem; font-weight:700;">
          Click to Choose a .ZIP File or Photos (or Drag & Drop Here)
        </div>
        <div style="font-size:0.84rem; color:var(--muted);">
          Supports <code>.zip</code> archives or multiple <code>.jpg</code>, <code>.png</code>, <code>.webp</code> images
        </div>
        <button type="button" class="btn-primary-large" style="margin-top:6px;">
          Select .ZIP or Photos to Upload
        </button>
      </div>

      <div style="display:flex; align-items:center; justify-content:space-between; background:#0f172a; padding:12px 16px; border-radius:10px; border:1px solid var(--border);">
        <label style="display:flex; align-items:center; gap:10px; cursor:pointer; font-size:0.92rem; font-weight:600;">
          <input type="checkbox" id="scan-jerseys-checkbox" checked style="width:18px; height:18px; cursor:pointer;" />
          <span>Scan Jersey Numbers (#)</span>
        </label>
        <span style="font-size:0.8rem; color:var(--muted);">Uncheck for instant face-only grouping</span>
      </div>

      <!-- Real-Time Progress Bar -->
      <div id="upload-progress" class="upload-progress">
        <div class="progress-header-row">
          <span id="upload-status-title">Processing photos...</span>
          <span id="upload-status-pct">0%</span>
        </div>
        <div class="progress-track">
          <div id="progress-fill" class="progress-fill"></div>
        </div>
        <span id="upload-status-detail" style="color:var(--muted); font-size:0.82rem;"></span>
      </div>

      <div style="display:flex; justify-content:space-between; align-items:center; border-top:1px solid var(--border); padding-top:16px; flex-wrap:wrap; gap:10px;">
        <span style="font-size:0.84rem; color:var(--muted);">
          Output directory: <code id="home-output-dir" style="color:#cbd5e1;"></code>
        </span>
      </div>
    </div>
  </div>

  <!-- ================= 2. REVIEW & CURATION SCREEN ================= -->
  <div id="review-screen">
    <!-- Live Warning Banner if any photo from All Photos is in NO folder -->
    <div id="warning-banner">
      <span id="warning-text"></span>
      <button class="warn-btn" onclick="selectFolder('__UNASSIGNED__')">View Unassigned Photos</button>
    </div>

    <div class="workspace">
      <aside>
        <button class="btn-secondary" style="margin-bottom:8px; width:100%;" onclick="backToHome()">
          Upload Another Batch
        </button>

        <div class="sidebar-section-title">Overview</div>
        <div id="nav-all" class="folder-item active" onclick="selectFolder('__ALL__')">
          <span>All Photos</span>
          <span class="badge" id="count-all">0</span>
        </div>
        <div id="nav-unassigned" class="folder-item warning-folder" onclick="selectFolder('__UNASSIGNED__')">
          <span>In No Folder</span>
          <span class="badge warn" id="count-unassigned">0</span>
        </div>

        <div class="sidebar-section-title">Player Folders (A-Z / 1-9)</div>
        <div id="folder-list" style="display:flex; flex-direction:column; gap:5px;"></div>

        <button class="new-folder-btn" onclick="promptCreateFolder()">+ New Player Folder</button>

        <div id="jersey-section-title" class="sidebar-section-title" style="margin-top:16px; color:#fcd34d;">
          Jersey Number Folders (#)
        </div>
        <div id="jersey-folder-list" style="display:flex; flex-direction:column; gap:5px;"></div>
      </aside>

      <main>
        <div class="folder-header">
          <div class="folder-title-group">
            <!-- Player Reference Face Thumbnail at the Top of Selected Folder -->
            <img id="header-ref-face" class="header-ref-face" src="" alt="Folder Reference Face" />
            <div id="header-jersey-badge" class="sidebar-jersey-badge" style="display:none; width:56px; height:56px; font-size:1.15rem;"></div>
            <div>
              <div style="display:flex; align-items:center; gap:10px;">
                <h2 id="current-folder-title">All Photos</h2>
                <span class="badge" id="current-folder-subtitle"></span>
              </div>
              <div id="header-ref-caption" style="font-size:0.78rem; color:var(--muted); margin-top:3px; display:none;">
                Folder Reference Face
              </div>
            </div>
          </div>

          <div class="header-controls">
            <label style="font-size:0.8rem; color:var(--muted);">
              Sort Photos:
              <select id="photo-sort-select" class="header-select" onchange="render()">
                <option value="name">Name (Numerical / A-Z)</option>
                <option value="prox_desc">Closest to Player BBox First</option>
                <option value="sim_asc">Lowest Similarity First (Spot Outliers)</option>
                <option value="sim_desc">Highest Similarity First</option>
              </select>
            </label>

            <div id="merge-jersey-group" class="jersey-merge-bar">
              <span style="font-size:0.8rem; font-weight:700; color:#fde68a;">Add Into Player (Sorted by BBox Proximity):</span>
              <select id="merge-jersey-target-select" class="header-select"></select>
              <button id="merge-jersey-btn" class="btn-jersey-merge" onclick="mergeCurrentJerseyIntoPlayer()">
                Add Into Player Folder
              </button>
            </div>

            <div id="player-add-jersey-group" class="jersey-merge-bar">
              <span style="font-size:0.8rem; font-weight:700; color:#fde68a;">Add Jersey #:</span>
              <select id="player-add-jersey-select" class="header-select"></select>
              <button id="player-add-jersey-btn" class="btn-jersey-merge" onclick="addJerseyFromPlayerFolder()">
                + Add # Jersey
              </button>
            </div>

            <button id="rename-folder-btn" class="btn-secondary" style="display:none;" onclick="promptRenameCurrentFolder()">
              Rename Folder
            </button>

            <button id="delete-folder-btn" class="btn-small btn-remove" style="display:none; padding:6px 12px; font-size:0.82rem;" onclick="deleteCurrentFolder()">
              Delete Folder
            </button>

            <div id="merge-folder-group" style="display:none; align-items:center; gap:5px;">
              <select id="merge-target-select" class="header-select"></select>
              <button class="btn-secondary" onclick="mergeCurrentFolder()" title="Combine all photos from this folder into another folder">
                Merge Into Folder
              </button>
            </div>
          </div>
        </div>

        <div class="photo-grid" id="photo-grid"></div>
      </main>
    </div>

    <footer>
      <div class="footer-status" id="footer-status">
        Review similarity scores and folder assignments, then click Create Real Folders.
      </div>
      <button class="btn-commit" onclick="commitRealFolders()">
        Create Real Folders
      </button>
    </footer>
  </div>

  <!-- ================= 3. LARGE PHOTO PREVIEW + CLICKABLE FACE BOXES MODAL ================= -->
  <div id="compare-modal" onclick="if(event.target===this) closeCompareModal()">
    <div class="modal-box">
      <div class="modal-header">
        <strong id="compare-title">Photo Preview</strong>
        <button class="btn-secondary" onclick="closeCompareModal()">Close (Esc)</button>
      </div>
      <div class="modal-body" id="compare-body"></div>
    </div>
  </div>

  <script>
    let appState = null;
    let currentView = '__ALL__';
    let currentModalData = null;
    let activeFaceId = null;

    const urlParams = new URLSearchParams(window.location.search);
    const returnUrl = urlParams.get('return_url');
    if (returnUrl) {
      const link = document.getElementById('crm-return-link');
      link.href = returnUrl;
      link.style.display = 'inline';
    }

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeCompareModal();
    });

    // Natural alphanumeric comparator in JS ('Player_2' < 'Player_10')
    const naturalCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

    async function apiCall(path, method = 'GET', body = null) {
      const opts = { method, headers: { 'Content-Type': 'application/json' } };
      if (body) opts.body = JSON.stringify(body);
      const res = await fetch(path, opts);
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: res.statusText }));
        alert('Error: ' + (err.error || res.statusText));
        throw new Error(err.error || res.statusText);
      }
      return res.json();
    }

    async function loadState() {
      appState = await apiCall('/api/state');
      render();
    }

    function setProgressBar(pct, titleText, detailText) {
      document.getElementById('progress-fill').style.width = `${Math.max(0, Math.min(100, pct))}%`;
      document.getElementById('upload-status-pct').textContent = `${Math.round(pct)}%`;
      document.getElementById('upload-status-title').textContent = titleText;
      document.getElementById('upload-status-detail').textContent = detailText || '';
    }

    async function handleFileSelect(fileList) {
      if (!fileList || fileList.length === 0) return;

      const prog = document.getElementById('upload-progress');
      prog.classList.add('visible');
      setProgressBar(0, 'Uploading files...', '');

      try {
        await apiCall('/api/upload-start', 'POST', {});

        for (let i = 0; i < fileList.length; i++) {
          const file = fileList[i];
          const uploadPct = ((i) / fileList.length) * 25;
          setProgressBar(
            uploadPct,
            `Uploading file ${i + 1} of ${fileList.length}: ${file.name}`,
            `${(file.size / 1024).toFixed(1)} KB`
          );
          const res = await fetch(`/api/upload-chunk?filename=${encodeURIComponent(file.name)}`, {
            method: 'POST',
            body: file,
          });
          if (!res.ok) {
            const err = await res.json().catch(() => ({ error: res.statusText }));
            throw new Error(err.error || 'Upload failed');
          }
        }

        const scanJerseys = document.getElementById('scan-jerseys-checkbox') ? document.getElementById('scan-jerseys-checkbox').checked : true;
        setProgressBar(25, scanJerseys ? 'Starting face & jersey detection...' : 'Starting face detection...', 'Preparing photos...');
        await apiCall('/api/upload-finish', 'POST', { scan_jerseys: scanJerseys });

        while (true) {
          await new Promise(r => setTimeout(r, 200));
          const p = await apiCall('/api/progress');
          if (p.error) {
            throw new Error(p.error);
          }
          const combinedPct = 25 + Math.round((p.percent || 0) * 0.75);
          setProgressBar(
            combinedPct,
            p.stage || 'Scanning photos...',
            p.current_file ? `Current photo: ${p.current_file} (${p.current}/${p.total})` : ''
          );
          if (!p.active) {
            break;
          }
        }

        setProgressBar(100, 'Complete!', '');
        appState = await apiCall('/api/state');
        currentView = '__ALL__';
        render();
      } catch (err) {
        console.error(err);
      } finally {
        prog.classList.remove('visible');
        document.getElementById('file-input').value = '';
      }
    }

    async function backToHome() {
      appState = await apiCall('/api/reset-home', 'POST', {});
      render();
    }

    function selectFolder(viewId) {
      currentView = viewId;
      const sortSelect = document.getElementById('photo-sort-select');
      if (sortSelect) {
        if (viewId && viewId.startsWith('jersey_')) {
          sortSelect.value = 'prox_desc';
        } else if (sortSelect.value === 'prox_desc') {
          sortSelect.value = 'name';
        }
      }
      render();
    }

    function simBadgeClass(sim) {
      if (sim >= 0.75) return 'sim-high';
      if (sim >= 0.45) return 'sim-med';
      return 'sim-low';
    }

    function render() {
      if (!appState) return;

      const homeEl = document.getElementById('home-screen');
      const reviewEl = document.getElementById('review-screen');
      document.getElementById('home-output-dir').textContent = appState.output_dir || '';

      if (appState.mode === 'home') {
        homeEl.classList.add('active');
        reviewEl.classList.remove('active');
        document.getElementById('header-stats').textContent = 'Homescreen';

        const banner = document.getElementById('commit-success-banner');
        if (appState.last_commit) {
          banner.classList.add('visible');
          banner.innerHTML = `
            <strong>Real Player Folders Created Successfully</strong><br/>
            Saved <strong>${appState.last_commit.total_players_grouped}</strong> player folder(s) (${appState.last_commit.photos_with_grouped_players} matched photos, ${appState.last_commit.no_face_photos_count} unassigned/no-face) to:<br/>
            <code>${appState.last_commit_output_dir}</code>
          `;
        } else {
          banner.classList.remove('visible');
        }
        return;
      }

      homeEl.classList.remove('active');
      reviewEl.classList.add('active');

      const jerseyFolders = [...(appState.jersey_folders || [])].sort((a, b) =>
        naturalCollator.compare(a.jersey_number, b.jersey_number)
      );

      document.getElementById('header-stats').textContent =
        `${appState.total_photos} Total Photos | ${appState.total_folders} Player Folders | ${jerseyFolders.length} Jersey Number Folders | Output: ${appState.output_dir}`;

      const warnBanner = document.getElementById('warning-banner');
      const warnText = document.getElementById('warning-text');
      if (appState.has_unassigned_warning) {
        warnBanner.classList.add('visible');
        warnText.textContent =
          `WARNING: ${appState.unassigned_count} photo(s) from All Photos are not in any folder (${appState.unassigned_photos.join(', ')})!`;
      } else {
        warnBanner.classList.remove('visible');
      }

      document.getElementById('count-all').textContent = appState.total_photos;
      document.getElementById('count-unassigned').textContent = appState.unassigned_count;
      document.getElementById('nav-all').classList.toggle('active', currentView === '__ALL__');
      document.getElementById('nav-unassigned').classList.toggle('active', currentView === '__UNASSIGNED__');

      // Sort folders naturally by folder_name
      const sortedFolders = [...appState.folders].sort((a, b) =>
        naturalCollator.compare(a.folder_name, b.folder_name)
      );

      // Render Sidebar Player Folders with Reference Face Avatar
      const folderListEl = document.getElementById('folder-list');
      folderListEl.innerHTML = '';
      for (const f of sortedFolders) {
        const item = document.createElement('div');
        item.className = 'folder-item' + (currentView === f.player_id ? ' active' : '');
        item.onclick = () => selectFolder(f.player_id);

        const avatarHtml = f.reference_crop_url
          ? `<img class="sidebar-face-avatar" src="${f.reference_crop_url}" alt="${f.folder_name}" />`
          : `<div class="sidebar-face-placeholder">No Face</div>`;

        item.innerHTML = `
          <div style="display:flex; align-items:center; gap:10px; min-width:0;">
            ${avatarHtml}
            <div style="display:flex; flex-direction:column; min-width:0;">
              <span style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${f.folder_name}</span>
              <span style="font-size:0.72rem; color:var(--muted);">Avg Sim: ${f.average_similarity_pct}%</span>
            </div>
          </div>
          <span class="badge">${f.photo_count}</span>
        `;
        folderListEl.appendChild(item);
      }

      // Render Separate Jersey Number Folders at the Bottom of Sidebar
      const jerseyListEl = document.getElementById('jersey-folder-list');
      jerseyListEl.innerHTML = '';
      if (jerseyFolders.length === 0) {
        jerseyListEl.innerHTML = `<div style="font-size:0.78rem; color:var(--muted); padding:4px 6px;">No separate jersey folders</div>`;
      } else {
        for (const jf of jerseyFolders) {
          const jItem = document.createElement('div');
          jItem.className = 'folder-item jersey-folder-item' + (currentView === jf.folder_id ? ' active' : '');
          jItem.onclick = () => selectFolder(jf.folder_id);
          jItem.innerHTML = `
            <div style="display:flex; align-items:center; gap:10px; min-width:0;">
              <div class="sidebar-jersey-badge">${jf.folder_name}</div>
              <div style="display:flex; flex-direction:column; min-width:0;">
                <span style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-weight:700; color:#fde68a;">Jersey ${jf.folder_name}</span>
                <span style="font-size:0.72rem; color:var(--muted);">Avg BBox Prox: ${jf.average_proximity_pct}%</span>
              </div>
            </div>
            <span class="badge">${jf.photo_count}</span>
          `;
          jerseyListEl.appendChild(jItem);
        }
      }

      // Header Elements
      let photosToShow = [];
      const titleEl = document.getElementById('current-folder-title');
      const subEl = document.getElementById('current-folder-subtitle');
      const renameBtn = document.getElementById('rename-folder-btn');
      const deleteBtn = document.getElementById('delete-folder-btn');
      const headerRefImg = document.getElementById('header-ref-face');
      const headerRefCap = document.getElementById('header-ref-caption');
      const headerJerseyBadge = document.getElementById('header-jersey-badge');
      const mergeGroup = document.getElementById('merge-folder-group');
      const mergeSelect = document.getElementById('merge-target-select');
      const mergeJerseyGroup = document.getElementById('merge-jersey-group');
      const mergeJerseySelect = document.getElementById('merge-jersey-target-select');
      const mergeJerseyBtn = document.getElementById('merge-jersey-btn');
      const playerAddJerseyGroup = document.getElementById('player-add-jersey-group');
      const playerAddJerseySelect = document.getElementById('player-add-jersey-select');

      if (currentView === '__ALL__') {
        titleEl.textContent = 'All Photos';
        subEl.textContent = `${appState.total_photos} photos`;
        renameBtn.style.display = 'none';
        deleteBtn.style.display = 'none';
        mergeGroup.style.display = 'none';
        mergeJerseyGroup.style.display = 'none';
        playerAddJerseyGroup.style.display = 'none';
        headerRefImg.classList.remove('visible');
        headerRefCap.style.display = 'none';
        headerJerseyBadge.style.display = 'none';
        photosToShow = [...appState.all_photos];
      } else if (currentView === '__UNASSIGNED__') {
        titleEl.textContent = 'Photos in No Folder';
        subEl.textContent = `${appState.unassigned_count} unassigned`;
        renameBtn.style.display = 'none';
        deleteBtn.style.display = 'none';
        mergeGroup.style.display = 'none';
        mergeJerseyGroup.style.display = 'none';
        playerAddJerseyGroup.style.display = 'none';
        headerRefImg.classList.remove('visible');
        headerRefCap.style.display = 'none';
        headerJerseyBadge.style.display = 'none';
        photosToShow = appState.all_photos.filter(p => p.in_no_folder);
      } else if (currentView.startsWith('jersey_')) {
        const jFolder = jerseyFolders.find(jf => jf.folder_id === currentView);
        if (!jFolder) {
          currentView = '__ALL__';
          return render();
        }
        titleEl.textContent = `Jersey ${jFolder.folder_name} (Separate Jersey Folder)`;
        subEl.textContent = `${jFolder.photo_count} photos | Avg BBox Proximity: ${jFolder.average_proximity_pct}%`;
        renameBtn.style.display = 'none';
        deleteBtn.style.display = 'inline-block';
        mergeGroup.style.display = 'none';
        playerAddJerseyGroup.style.display = 'none';
        headerRefImg.classList.remove('visible');
        headerRefCap.style.display = 'none';

        headerJerseyBadge.textContent = jFolder.folder_name;
        headerJerseyBadge.style.display = 'flex';

        const suggestions = jFolder.suggested_players || [];
        if (suggestions.length > 0) {
          mergeJerseyGroup.style.display = 'flex';
          mergeJerseyBtn.textContent = `Add ${jFolder.folder_name} Into Player Folder`;
          mergeJerseySelect.innerHTML = suggestions
            .map(s => {
              const proxTag = s.bbox_proximity > 0
                ? ` — BBox Proximity: ${s.bbox_proximity_pct}% (${s.shared_photos} shared)`
                : '';
              return `<option value="${s.player_id}">${s.folder_name}${proxTag} -> "${s.folder_name} ${jFolder.folder_name}"</option>`;
            })
            .join('');
        } else {
          mergeJerseyGroup.style.display = 'none';
        }

        photosToShow = [...jFolder.photos];
      } else {
        const folder = sortedFolders.find(f => f.player_id === currentView);
        if (!folder) {
          currentView = '__ALL__';
          return render();
        }
        titleEl.textContent = folder.folder_name;
        subEl.textContent = `${folder.photo_count} photos | Avg Similarity: ${folder.average_similarity} (${folder.average_similarity_pct}%)`;
        renameBtn.style.display = 'inline-block';
        deleteBtn.style.display = 'inline-block';
        mergeJerseyGroup.style.display = 'none';
        headerJerseyBadge.style.display = 'none';

        // Populate "Add Jersey #" bar directly inside the Player (Face Grouping) Folder!
        playerAddJerseyGroup.style.display = 'flex';
        const suggestedJerseys = folder.suggested_jerseys || [];
        const jerseyOpts = suggestedJerseys.map(sj => {
          const proxTag = sj.bbox_proximity > 0
            ? ` — BBox Prox: ${sj.bbox_proximity_pct}% (${sj.shared_photos} shared)`
            : ` (${sj.photo_count} photos)`;
          return `<option value="${sj.jersey_number}">#${sj.jersey_number}${proxTag}</option>`;
        });
        jerseyOpts.push(`<option value="__CUSTOM__">+ Type Custom #...</option>`);
        playerAddJerseySelect.innerHTML = jerseyOpts.join('');

        // Show Reference Face Thumbnail at the Top of the Folder Header!
        if (folder.reference_crop_url) {
          headerRefImg.src = folder.reference_crop_url;
          headerRefImg.classList.add('visible');
          headerRefCap.style.display = 'block';
        } else {
          headerRefImg.classList.remove('visible');
          headerRefCap.style.display = 'none';
        }

        // Populate Merge Into Folder dropdown
        const otherFolders = sortedFolders.filter(f => f.player_id !== currentView);
        if (otherFolders.length > 0) {
          mergeGroup.style.display = 'flex';
          mergeSelect.innerHTML = otherFolders
            .map(f => `<option value="${f.player_id}">${f.folder_name} (${f.photo_count})</option>`)
            .join('');
        } else {
          mergeGroup.style.display = 'none';
        }

        photosToShow = [...folder.photos];
      }

      // Apply selected Photo Sort Order
      const sortMode = document.getElementById('photo-sort-select').value;
      if (sortMode === 'prox_desc') {
        photosToShow.sort((a, b) => (b.bbox_proximity || 0) - (a.bbox_proximity || 0) || naturalCollator.compare(a.name, b.name));
      } else if (sortMode === 'sim_asc') {
        photosToShow.sort((a, b) => a.similarity - b.similarity || naturalCollator.compare(a.name, b.name));
      } else if (sortMode === 'sim_desc') {
        photosToShow.sort((a, b) => b.similarity - a.similarity || naturalCollator.compare(a.name, b.name));
      } else {
        photosToShow.sort((a, b) => naturalCollator.compare(a.name, b.name));
      }

      const gridEl = document.getElementById('photo-grid');
      gridEl.innerHTML = '';

      if (photosToShow.length === 0) {
        gridEl.innerHTML = `<div style="color:var(--muted); padding:20px;">No photos in this view.</div>`;
      }

      const folderOptions =
        sortedFolders.map(f => `<option value="${f.player_id}">${f.folder_name}</option>`).join('') +
        `<option value="__NEW__">+ Create New Folder...</option>`;

      for (const photo of photosToShow) {
        const isUnassigned = !photo.assigned_folders || photo.assigned_folders.length === 0;
        const card = document.createElement('div');
        card.className = 'photo-card' + (isUnassigned ? ' no-folder-warning' : '');

        const isViewingJerseyFolder = currentView.startsWith('jersey_');
        const simText = isViewingJerseyFolder
          ? `BBox Prox: ${photo.bbox_proximity_pct || photo.similarity_pct}%`
          : (photo.has_face
              ? `Sim: ${photo.similarity.toFixed(2)} (${photo.similarity_pct}%)`
              : (photo.jerseys && photo.jerseys.length ? `Jersey #${photo.jerseys.join(', #')} (BBox: ${photo.bbox_proximity_pct}%)` : 'No Face'));

        const jerseyHtml = (photo.jerseys && photo.jerseys.length)
          ? `<span class="jersey-pill">Jersey #${photo.jerseys.join(', #')}${photo.bbox_proximity_pct > 0 ? ` (BBox: ${photo.bbox_proximity_pct}%)` : ''}</span>`
          : '';

        let tagsHtml = '';
        if (isUnassigned) {
          tagsHtml = `<span class="folder-tag unassigned-tag">IN NO FOLDER</span>`;
        } else {
          tagsHtml = photo.assigned_folders.map(af => {
            const extraCls = af.folder_type === 'jersey' ? ' jersey-tag' : '';
            const metricLabel = af.folder_type === 'jersey' ? `BBox: ${af.similarity_pct}%` : `${af.similarity_pct}%`;
            return `<span class="folder-tag${extraCls}" onclick="selectFolder('${af.player_id}')" title="Click to open folder">${af.folder_name} (${metricLabel})</span>`;
          }).join('');
        }

        const isInsideFolder = currentView !== '__ALL__' && currentView !== '__UNASSIGNED__';
        const removeBtnHtml = isInsideFolder
          ? `<button class="btn-small btn-remove" onclick="removePhoto('${photo.id}', '${currentView}')">Remove from Folder</button>`
          : '';

        const faceCount = photo.face_boxes ? photo.face_boxes.length : 0;

        card.innerHTML = `
          <div class="photo-thumb-wrap" onclick="openCompareModal('${photo.id}')" title="Click to open full-screen preview">
            <img src="${photo.url}" alt="${photo.name}" loading="lazy" />
            <span class="sim-pill ${simBadgeClass(isViewingJerseyFolder ? (photo.bbox_proximity || photo.similarity) : photo.similarity)}">${simText}</span>
            ${jerseyHtml}
            <span class="zoom-hint">Click to Enlarge (${faceCount} face${faceCount === 1 ? '' : 's'})</span>
          </div>
          <div class="photo-body">
            <div class="photo-name-row">
              <span>${photo.name}</span>
              <button class="btn-small btn-compare" onclick="openCompareModal('${photo.id}')">Full Preview</button>
            </div>
            <div class="folder-tags">${tagsHtml}</div>
            <div class="card-actions">
              <div class="move-row">
                <select id="sel-${photo.id}">
                  ${folderOptions}
                </select>
                <button class="btn-small btn-move" title="Move to selected folder" onclick="movePhoto('${photo.id}', false)">Move</button>
                <button class="btn-small btn-add" title="Also copy into selected folder" onclick="movePhoto('${photo.id}', true)">+Add</button>
              </div>
              ${removeBtnHtml}
            </div>
          </div>
        `;
        gridEl.appendChild(card);
      }

      const footerStatus = document.getElementById('footer-status');
      if (appState.has_unassigned_warning) {
        footerStatus.innerHTML =
          `<strong style="color:#fcd34d;">WARNING: ${appState.unassigned_count} photo(s) have no folder assigned.</strong> (${appState.edit_count} unsaved edits)`;
      } else {
        footerStatus.innerHTML =
          `<span style="color:#6ee7b7;">All ${appState.total_photos} photos are assigned to folders.</span> (${appState.edit_count} unsaved edits)`;
      }
    }

    async function removePhoto(photoId, playerId) {
      appState = await apiCall('/api/remove', 'POST', {
        photo_id: photoId,
        player_id: playerId,
      });
      render();
    }

    async function movePhoto(photoId, keepInSource) {
      const sel = document.getElementById(`sel-${photoId}`);
      if (!sel || !sel.value) return;
      const sourceId = (currentView !== '__ALL__' && currentView !== '__UNASSIGNED__') ? currentView : null;

      if (sel.value === '__NEW__') {
        const name = prompt('Enter name for the new player folder (e.g. Player_23_Marcus):');
        if (!name || !name.trim()) return;
        appState = await apiCall('/api/create-folder', 'POST', {
          folder_name: name.trim(),
          initial_photo_id: photoId,
          source_player_id: sourceId,
          keep_in_source: keepInSource,
        });
        render();
        return;
      }

      appState = await apiCall('/api/move', 'POST', {
        photo_id: photoId,
        target_player_id: sel.value,
        source_player_id: sourceId,
        keep_in_source: keepInSource,
      });
      render();
    }

    async function deleteCurrentFolder() {
      if (currentView === '__ALL__' || currentView === '__UNASSIGNED__') return;
      let folderName = currentView;
      if (currentView.startsWith('jersey_')) {
        const jf = (appState.jersey_folders || []).find(j => j.folder_id === currentView);
        if (!jf) return;
        folderName = `Jersey ${jf.folder_name}`;
      } else {
        const folder = appState.folders.find(f => f.player_id === currentView);
        if (!folder) return;
        folderName = folder.folder_name;
      }
      if (!confirm(`Delete folder "${folderName}"? Photos only in this folder will move to "In No Folder".`)) {
        return;
      }
      appState = await apiCall('/api/delete-folder', 'POST', { player_id: currentView });
      currentView = '__ALL__';
      render();
    }

    async function mergeCurrentJerseyIntoPlayer() {
      if (!currentView.startsWith('jersey_')) return;
      const jFolder = (appState.jersey_folders || []).find(j => j.folder_id === currentView);
      if (!jFolder) return;
      const targetPlayerId = document.getElementById('merge-jersey-target-select').value;
      if (!targetPlayerId) return;
      const dstFolder = appState.folders.find(f => f.player_id === targetPlayerId);
      const dstName = dstFolder ? dstFolder.folder_name : targetPlayerId;

      if (!confirm(`Add Jersey ${jFolder.folder_name} into "${dstName}" and rename the folder to "${dstName} ${jFolder.folder_name}"?`)) {
        return;
      }

      appState = await apiCall('/api/merge-jersey', 'POST', {
        jersey_number: jFolder.jersey_number,
        target_player_id: targetPlayerId,
      });
      currentView = targetPlayerId;
      const sortSelect = document.getElementById('photo-sort-select');
      if (sortSelect && sortSelect.value === 'prox_desc') {
        sortSelect.value = 'name';
      }
      render();
    }

    async function addJerseyFromPlayerFolder() {
      if (currentView === '__ALL__' || currentView === '__UNASSIGNED__' || currentView.startsWith('jersey_')) return;
      const sel = document.getElementById('player-add-jersey-select');
      if (!sel) return;

      let chosenNum = sel.value;
      if (!chosenNum || chosenNum === '__CUSTOM__') {
        const typed = prompt('Enter the jersey number to add to this player folder (e.g. 23):');
        if (!typed || !typed.trim()) return;
        chosenNum = typed.trim().replace(/^#/, '');
      }

      appState = await apiCall('/api/merge-jersey', 'POST', {
        jersey_number: chosenNum,
        target_player_id: currentView,
      });
      render();
    }

    async function quickAssignFromModal(photoId, targetPlayerId) {
      if (!targetPlayerId) return;
      const sourceId = (currentView !== '__ALL__' && currentView !== '__UNASSIGNED__') ? currentView : null;
      appState = await apiCall('/api/move', 'POST', {
        photo_id: photoId,
        target_player_id: targetPlayerId,
        source_player_id: sourceId,
        keep_in_source: false,
      });
      render();
      await openCompareModal(photoId, activeFaceId);
    }

    async function mergeCurrentFolder() {
      if (currentView === '__ALL__' || currentView === '__UNASSIGNED__') return;
      const targetId = document.getElementById('merge-target-select').value;
      if (!targetId) return;
      const srcFolder = appState.folders.find(f => f.player_id === currentView);
      const dstFolder = appState.folders.find(f => f.player_id === targetId);
      if (!confirm(`Merge all photos from "${srcFolder.folder_name}" into "${dstFolder.folder_name}"?`)) {
        return;
      }
      appState = await apiCall('/api/merge-folders', 'POST', {
        source_player_id: currentView,
        target_player_id: targetId,
      });
      currentView = targetId;
      render();
    }

    async function promptCreateFolder() {
      const name = prompt('Enter name for the new player folder (e.g. Player_23_Marcus):');
      if (!name || !name.trim()) return;
      appState = await apiCall('/api/create-folder', 'POST', { folder_name: name.trim() });
      render();
    }

    async function promptRenameCurrentFolder() {
      if (currentView === '__ALL__' || currentView === '__UNASSIGNED__') return;
      const folder = appState.folders.find(f => f.player_id === currentView);
      const name = prompt('Enter new folder name:', folder ? folder.folder_name : '');
      if (!name || !name.trim()) return;
      appState = await apiCall('/api/rename-folder', 'POST', {
        player_id: currentView,
        new_name: name.trim(),
      });
      render();
    }

    async function openCompareModal(photoId, initialFaceId = null) {
      const data = await apiCall(`/api/compare?photo_id=${encodeURIComponent(photoId)}`);
      currentModalData = data;
      document.getElementById('compare-title').textContent =
        `Full Preview — ${data.photo_id} (Click any face box to inspect cropped face & top matches)`;

      const boxes = data.face_boxes || [];
      activeFaceId = initialFaceId || (boxes.length > 0 ? boxes[0].face_id : null);

      await renderModalContents();
      document.getElementById('compare-modal').classList.add('open');
    }

    async function selectFaceInModal(faceId) {
      activeFaceId = faceId;
      await renderModalContents();
    }

    async function renderModalContents() {
      if (!currentModalData) return;
      const data = currentModalData;
      const body = document.getElementById('compare-body');

      const imgW = data.image_width || 800;
      const imgH = data.image_height || 520;
      const boxes = data.face_boxes || [];
      const numberBoxes = data.number_boxes || [];
      const bodyBoxes = data.body_boxes || [];

      const strokeW = Math.max(3, Math.round(Math.min(imgW, imgH) * 0.006));
      const fontSize = Math.max(14, Math.round(Math.min(imgW, imgH) * 0.028));

      const svgBodyBoxesHtml = bodyBoxes.map((bb) => {
        const bw = Math.max(10, bb.x2 - bb.x1);
        const bh = Math.max(10, bb.y2 - bb.y1);
        return `
          <rect x="${bb.x1}" y="${bb.y1}" width="${bw}" height="${bh}"
                fill="none" stroke="#06b6d4" stroke-width="${Math.max(2, Math.round(strokeW * 0.7))}"
                stroke-dasharray="8,5" rx="4" pointer-events="none" />
        `;
      }).join('');

      const svgNumberBoxesHtml = numberBoxes.map((nb) => {
        const bw = Math.max(10, nb.x2 - nb.x1);
        const bh = Math.max(10, nb.y2 - nb.y1);
        const labelText = `#${nb.jersey_number} (BBox Prox: ${nb.proximity_pct}%)`;
        const labelY = Math.min(imgH - 6, nb.y2 + fontSize + 6);
        return `
          <g pointer-events="none">
            <rect x="${nb.x1}" y="${nb.y1}" width="${bw}" height="${bh}"
                  fill="rgba(245, 158, 11, 0.12)" stroke="#f59e0b" stroke-width="${strokeW}" rx="4" />
            <rect x="${nb.x1}" y="${labelY - fontSize - 4}" width="${Math.min(imgW - nb.x1, labelText.length * (fontSize * 0.62) + 12)}" height="${fontSize + 8}"
                  fill="#f59e0b" rx="4" />
            <text x="${nb.x1 + 6}" y="${labelY - 4}" fill="#0f172a" font-family="sans-serif" font-weight="bold" font-size="${fontSize}">
              ${labelText}
            </text>
          </g>
        `;
      }).join('');

      const svgBoxesHtml = boxes.map((b) => {
        const bw = Math.max(10, b.x2 - b.x1);
        const bh = Math.max(10, b.y2 - b.y1);
        const isSelected = b.face_id === activeFaceId;
        const color = isSelected ? '#10b981' : '#3b82f6';
        const labelText = `${b.label} (${b.similarity_pct}%)`;
        const labelY = Math.max(fontSize + 6, b.y1 - 6);
        return `
          <g class="clickable-face-group" onclick="selectFaceInModal('${b.face_id}')">
            <rect class="face-box-stroke" x="${b.x1}" y="${b.y1}" width="${bw}" height="${bh}"
                  fill="rgba(255,255,255,0.01)" stroke="${color}" stroke-width="${isSelected ? strokeW * 1.4 : strokeW}" rx="4" />
            <rect x="${b.x1}" y="${labelY - fontSize - 4}" width="${Math.min(imgW - b.x1, labelText.length * (fontSize * 0.62) + 12)}" height="${fontSize + 8}"
                  fill="${color}" rx="4" />
            <text x="${b.x1 + 6}" y="${labelY - 4}" fill="#052e16" font-family="sans-serif" font-weight="bold" font-size="${fontSize}">
              ${labelText}
            </text>
          </g>
        `;
      }).join('');

      let faceMatchesHtml = '';
      if (activeFaceId) {
        try {
          const matchData = await apiCall(`/api/face-matches?face_id=${encodeURIComponent(activeFaceId)}&limit=10`);
          const sel = matchData.selected_face;
          const matches = matchData.matches || [];

          const matchCardsHtml = matches.length > 0
            ? matches.map((m, idx) => {
                const assignBtn = m.player_id
                  ? `<button class="btn-small btn-move" style="width:100%; margin-top:4px;" onclick="quickAssignFromModal('${data.photo_id}', '${m.player_id}')">Move to ${m.folder_name}</button>`
                  : '';
                return `
                  <div class="match-crop-card">
                    <img class="match-crop-Card-img" src="${m.crop_url}" alt="${m.photo_name}"
                         onclick="openCompareModal('${m.photo_id}', '${m.face_id}')" title="Click to inspect ${m.photo_name}" />
                    <strong style="font-size:0.84rem; color:#6ee7b7;">#${idx + 1} &bull; ${m.similarity_pct}%</strong>
                    <span style="font-size:0.74rem; color:var(--muted);">Sim: ${m.similarity.toFixed(4)}</span>
                    <span style="font-size:0.75rem; font-weight:600; color:#bfdbfe; text-align:center;">${m.folder_name}</span>
                    <span style="font-size:0.72rem; color:var(--muted); text-align:center; word-break:break-all;">${m.photo_name}</span>
                    ${assignBtn}
                  </div>
                `;
              }).join('')
            : `<div style="color:var(--muted); font-size:0.88rem;">No other faces in the batch to compare against.</div>`;

          faceMatchesHtml = `
            <div class="face-matches-panel">
              <div class="selected-face-header">
                <img class="face-crop-thumb-lg" src="${sel.crop_url}" alt="Selected Face" />
                <div style="display:flex; flex-direction:column; gap:4px;">
                  <span style="font-size:0.76rem; text-transform:uppercase; letter-spacing:0.06em; color:#6ee7b7; font-weight:700;">
                    Selected Face Reference Crop
                  </span>
                  <strong style="font-size:1.05rem;">${sel.photo_name} &mdash; ${sel.folder_name}</strong>
                  <span style="font-size:0.84rem; color:var(--muted);">
                    Detection Confidence: ${(sel.confidence * 100).toFixed(1)}% &bull; Click any face box on the photo above to switch faces
                  </span>
                </div>
              </div>
              <div>
                <h4 style="margin-bottom:10px; color:#93c5fd; font-size:0.92rem;">
                  Top ${matches.length} Closest Matching Faces Across All Photos
                </h4>
                <div class="matches-strip">
                  ${matchCardsHtml}
                </div>
              </div>
            </div>
          `;
        } catch (e) {
          console.error(e);
        }
      }

      body.innerHTML = `
        <div class="lightbox-stage-wrap">
          <div class="lightbox-stage">
            <img src="${data.photo_url}" alt="${data.photo_id}" />
            <svg class="face-overlay-svg" viewBox="0 0 ${imgW} ${imgH}" preserveAspectRatio="xMidYMid meet">
              ${svgBodyBoxesHtml}
              ${svgNumberBoxesHtml}
              ${svgBoxesHtml}
            </svg>
          </div>
          <div style="font-size:0.84rem; color:var(--muted);">
            ${boxes.length > 0
              ? `Click on a face bounding box in the photo above to view its cropped reference face and top 5-10 closest matches.`
              : `No face bounding boxes detected in this photo.`}
          </div>
        </div>
        ${faceMatchesHtml}
      `;
    }

    function closeCompareModal() {
      document.getElementById('compare-modal').classList.remove('open');
    }

    async function commitRealFolders() {
      if (appState.has_unassigned_warning) {
        const proceed = confirm(
          `WARNING: ${appState.unassigned_count} photo(s) from All Photos are currently in NO player folder:\n\n` +
          `${appState.unassigned_photos.join(', ')}\n\n` +
          `If you continue, they will be placed in "No_Face_Detected". Do you want to create the real folders anyway?`
        );
        if (!proceed) {
          selectFolder('__UNASSIGNED__');
          return;
        }
      }

      appState = await apiCall('/api/commit', 'POST', {});
      render();
    }

    loadState();
  </script>
</body>
</html>
"""


class CurationRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler serving the PersonSort Web UI and JSON REST endpoints with CORS enabled."""

    session: CurationSession
    staging_files: list[Path] = []
    staging_dir: Path | None = None

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if route in {"/", "/index.html"}:
            body = INDEX_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if route == "/api/state":
            self._send_json(self.session.to_state_dict())
            return

        if route == "/api/progress":
            self._send_json(self.session.get_progress())
            return

        if route == "/api/compare":
            photo_id = query.get("photo_id", [""])[0]
            try:
                self._send_json(self.session.compare_photo(photo_id))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if route == "/api/face-matches":
            face_id = query.get("face_id", [""])[0]
            limit = int(query.get("limit", ["10"])[0])
            try:
                self._send_json(self.session.get_top_face_matches(face_id, limit=limit))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if route == "/api/face-crop":
            face_id = query.get("face_id", [""])[0]
            crop_bytes = self.session.get_face_crop_bytes(face_id)
            if crop_bytes is None:
                self.send_error(HTTPStatus.NOT_FOUND, "Face crop not found")
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(crop_bytes)))
            self.end_headers()
            self.wfile.write(crop_bytes)
            return

        if route == "/api/photo":
            photo_id = query.get("id", [""])[0]
            photo_path: Path | None = self.session.photo_by_id.get(photo_id)
            if photo_path is None or not photo_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "Photo not found")
                return

            raw = photo_path.read_bytes()
            mime, _ = mimetypes.guess_type(str(photo_path))
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime or "image/jpeg")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        content_len = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_len) if content_len > 0 else b""

        try:
            if route == "/api/upload-start":
                CurationRequestHandler.staging_dir = Path(
                    tempfile.mkdtemp(prefix="personsort_stage_")
                )
                CurationRequestHandler.staging_files = []
                self._send_json({"status": "ready"})
                return

            if route == "/api/upload-chunk":
                if CurationRequestHandler.staging_dir is None:
                    CurationRequestHandler.staging_dir = Path(
                        tempfile.mkdtemp(prefix="personsort_stage_")
                    )
                raw_name = query.get("filename", ["upload.bin"])[0]
                safe_name = Path(raw_name).name
                dest = CurationRequestHandler.staging_dir / safe_name
                dest.write_bytes(raw_body)
                CurationRequestHandler.staging_files.append(dest)
                self._send_json({"status": "uploaded", "filename": safe_name})
                return

            if route == "/api/upload-finish":
                data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                scan_jerseys = bool(data.get("scan_jerseys", True))
                self.session.start_background_processing(
                    CurationRequestHandler.staging_files,
                    enable_jersey_ocr=scan_jerseys,
                )
                self._send_json({"status": "processing_started"})
                return

            if route == "/api/reset-home":
                state = self.session.reset_to_home()
                self._send_json(state)
                return

            data = json.loads(raw_body.decode("utf-8")) if raw_body else {}

            if route == "/api/remove":
                state = self.session.remove_photo_from_folder(
                    photo_id=str(data.get("photo_id", "")),
                    player_id=str(data.get("player_id", "")),
                )
                self._send_json(state)
                return

            if route == "/api/move":
                state = self.session.move_or_add_photo(
                    photo_id=str(data.get("photo_id", "")),
                    target_player_id=str(data.get("target_player_id", "")),
                    source_player_id=data.get("source_player_id"),
                    keep_in_source=bool(data.get("keep_in_source", False)),
                )
                self._send_json(state)
                return

            if route == "/api/merge-folders":
                state = self.session.merge_folders(
                    source_player_id=str(data.get("source_player_id", "")),
                    target_player_id=str(data.get("target_player_id", "")),
                )
                self._send_json(state)
                return

            if route == "/api/merge-jersey":
                state = self.session.merge_jersey_into_player(
                    jersey_number=str(data.get("jersey_number", "")),
                    target_player_id=str(data.get("target_player_id", "")),
                )
                self._send_json(state)
                return

            if route == "/api/create-folder":
                state = self.session.create_folder(
                    folder_name=str(data.get("folder_name", "New_Player")),
                    initial_photo_id=data.get("initial_photo_id"),
                    source_player_id=data.get("source_player_id"),
                    keep_in_source=bool(data.get("keep_in_source", False)),
                )
                self._send_json(state)
                return

            if route == "/api/delete-folder":
                state = self.session.delete_folder(
                    player_id=str(data.get("player_id", "")),
                )
                self._send_json(state)
                return

            if route == "/api/rename-folder":
                state = self.session.rename_folder(
                    player_id=str(data.get("player_id", "")),
                    new_name=str(data.get("new_name", "")),
                )
                self._send_json(state)
                return

            if route == "/api/commit":
                result = self.session.commit_to_disk()
                self._send_json(result)
                return

            self._send_json({"error": f"Unknown endpoint {route}"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)


def create_http_server(
    session: CurationSession,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> ThreadingHTTPServer:
    """Create a ThreadingHTTPServer bound to `(host, port)` for `session`."""

    class BoundHandler(CurationRequestHandler):
        pass

    BoundHandler.session = session
    return ThreadingHTTPServer((host, port), BoundHandler)


def launch_curation_ui(
    session: CurationSession,
    host: str = "127.0.0.1",
    port: int = 8080,
    open_browser: bool = True,
) -> None:
    """Start the local PersonSort Review UI server and optionally open the browser."""
    server = create_http_server(session, host=host, port=port)
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}"

    print("\n" + "=" * 64)
    print("  PersonSort Web UI is running!")
    print(f"  Open in browser : {url}")
    print("  Press Ctrl+C in the terminal to stop the server.")
    print("=" * 64 + "\n")

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down PersonSort Web UI server...")
    finally:
        server.server_close()
