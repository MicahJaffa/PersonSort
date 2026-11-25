"""Interactive Web UI and REST API for uploading, reviewing, and curating PersonSort folders."""

from __future__ import annotations

import html
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

from config import APP_VERSION, DROPBOX_CALLBACK_PATH
from desktop_window import PersonSortControlWindow
from dropbox_client import DropboxManager, DropboxPermissionError
from session_state import CurationSession
from updater import check_for_update


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
      max-width: 920px;
      width: 100%;
      padding: 34px;
      display: flex;
      flex-direction: column;
      gap: 22px;
      box-shadow: 0 20px 45px rgba(0, 0, 0, 0.35);
    }
    .source-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
      gap: 18px;
      align-items: stretch;
    }
    .source-card {
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 18px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      gap: 14px;
    }
    .source-card-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      font-size: 1rem;
      font-weight: 700;
    }
    .dbx-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 5px 12px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: #1e293b;
      color: var(--text);
      font-size: 0.8rem;
      font-weight: 600;
      cursor: pointer;
    }
    .dbx-pill:hover {
      border-color: var(--primary);
    }
    .dbx-pill.connected {
      border-color: rgba(16, 185, 129, 0.55);
      background: rgba(16, 185, 129, 0.12);
      color: #a7f3d0;
    }
    .update-pill {
      display: none;
      align-items: center;
      padding: 5px 12px;
      border-radius: 999px;
      border: 1px solid rgba(16, 185, 129, 0.65);
      background: rgba(16, 185, 129, 0.18);
      color: #6ee7b7;
      font-size: 0.8rem;
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
    }
    .update-pill.visible { display: inline-flex; }
    .version-tag {
      font-size: 0.78rem;
      color: var(--muted);
      background: #111827;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 3px 9px;
    }
    details.dbx-advanced-details {
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 10px 14px;
    }
    details.dbx-advanced-details summary {
      cursor: pointer;
      font-size: 0.86rem;
      font-weight: 700;
      color: #93c5fd;
      user-select: none;
    }
    details.dbx-advanced-details[open] summary {
      margin-bottom: 12px;
    }
    .dbx-warning-box {
      background: rgba(245, 158, 11, 0.14);
      border: 1px solid var(--warning);
      color: #fde68a;
      border-radius: 8px;
      padding: 10px 12px;
      font-size: 0.82rem;
      line-height: 1.45;
      display: none;
    }
    .dbx-warning-box.visible { display: block; }
    .dbx-error-box {
      background: rgba(239, 68, 68, 0.14);
      border: 1px solid var(--danger);
      color: #fecaca;
      border-radius: 8px;
      padding: 10px 12px;
      font-size: 0.84rem;
      line-height: 1.45;
      display: none;
    }
    .dbx-error-box.visible { display: block; }
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

    /* Dropbox Settings & Folder Picker Modals */
    .overlay-modal {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.84);
      align-items: center;
      justify-content: center;
      z-index: 120;
      padding: 18px;
    }
    .overlay-modal.open { display: flex; }
    .dbx-input {
      width: 100%;
      background: #0b1120;
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 9px 12px;
      font-size: 0.9rem;
      font-family: inherit;
    }
    .dbx-input:focus {
      outline: none;
      border-color: var(--primary);
    }
    .dbx-folder-list {
      border: 1px solid var(--border);
      border-radius: 10px;
      background: #0b1120;
      max-height: 320px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
    }
    .dbx-folder-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 14px;
      border-bottom: 1px solid rgba(51, 65, 85, 0.6);
      cursor: pointer;
      gap: 12px;
      font-size: 0.9rem;
    }
    .dbx-folder-row:last-child { border-bottom: none; }
    .dbx-folder-row:hover { background: rgba(59, 130, 246, 0.14); }
    .dbx-file-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 9px 14px;
      border-bottom: 1px solid rgba(51, 65, 85, 0.45);
      gap: 12px;
      font-size: 0.88rem;
      color: var(--text);
    }
    .dbx-file-row:last-child { border-bottom: none; }
    .dbx-file-row.unsupported {
      color: #fca5a5;
      background: rgba(239, 68, 68, 0.12);
      border-left: 3px solid var(--danger);
    }
    .dbx-badge-folder {
      background: rgba(59, 130, 246, 0.22);
      color: #93c5fd;
      border: 1px solid rgba(59, 130, 246, 0.45);
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 700;
      flex-shrink: 0;
    }
    .dbx-badge-photo {
      background: rgba(16, 185, 129, 0.18);
      color: #6ee7b7;
      border: 1px solid rgba(16, 185, 129, 0.4);
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 700;
      flex-shrink: 0;
    }
    .dbx-badge-unsupported {
      background: rgba(239, 68, 68, 0.24);
      color: #fecaca;
      border: 1px solid var(--danger);
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 700;
      flex-shrink: 0;
    }
    .export-option-card {
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      transition: border-color 0.15s, background 0.15s;
    }
    .export-option-card.selected {
      border-color: var(--primary);
      background: rgba(59, 130, 246, 0.08);
    }
    .dbx-shared-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 8px 12px;
      background: #0b1120;
      border: 1px solid var(--border);
      border-radius: 8px;
      font-size: 0.84rem;
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <span style="cursor:pointer;" onclick="backToHome()">PersonSort</span>
      <span id="header-version-badge" class="version-tag">v1.0.0</span>
      <a id="header-update-btn" class="update-pill" href="#" target="_blank" rel="noopener noreferrer">Update Available</a>
      <a id="crm-return-link" href="#" style="display:none; color:#93c5fd; font-size:0.85rem; text-decoration:none;">Return</a>
    </div>
    <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
      <button id="header-dropbox-btn" type="button" class="dbx-pill" onclick="openDropboxSettingsModal()">
        Dropbox: Not Connected
      </button>
      <div class="header-stats" id="header-stats">Ready</div>
      <button id="header-quit-btn" type="button" class="btn-small btn-remove" style="padding:6px 12px; font-size:0.8rem;" onclick="quitApp()">
        Quit App
      </button>
    </div>
  </header>

  <!-- ================= 1. HOMESCREEN (Upload .zip/Photos OR Pick Dropbox) ================= -->
  <div id="home-screen" class="active">
    <div class="home-card">
      <div id="commit-success-banner" class="commit-success-banner"></div>

      <div>
        <h1 style="font-size:1.55rem; margin-bottom:6px;">Choose Photo Source to Group by Face</h1>
        <p style="color:var(--muted); font-size:0.94rem;">
          Upload local photos or a <strong>.zip file</strong>, or connect your shared <strong>Dropbox</strong> folder. PersonSort detects faces and jersey numbers, lets you review and adjust folders, and lets you export to either a local directory or Dropbox.
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

      <div class="source-grid">
        <!-- Card 1: Local Photos -->
        <div class="source-card">
          <div class="source-card-title">
            <span>Local Photos</span>
            <span class="badge">Offline Ready</span>
          </div>
          <div
            id="dropzone"
            class="upload-dropzone"
            style="padding: 28px 18px;"
            onclick="document.getElementById('file-input').click()"
            ondragover="event.preventDefault(); this.classList.add('dragover');"
            ondragleave="this.classList.remove('dragover');"
            ondrop="event.preventDefault(); this.classList.remove('dragover'); handleFileSelect(event.dataTransfer.files);"
          >
            <div style="font-size:1.02rem; font-weight:700;">
              Click to Choose a .ZIP File or Photos (or Drag & Drop Here)
            </div>
            <div style="font-size:0.82rem; color:var(--muted);">
              Supports <code>.zip</code> archives or multiple <code>.jpg</code>, <code>.png</code>, <code>.webp</code> images
            </div>
            <button type="button" class="btn-primary-large" style="margin-top:6px;">
              Select .ZIP or Photos to Upload
            </button>
          </div>
        </div>

        <!-- Card 2: Dropbox Cloud -->
        <div id="dropbox-source-card" class="source-card">
          <div style="display:flex; flex-direction:column; gap:10px;">
            <div class="source-card-title">
              <span>Dropbox Cloud</span>
              <span id="dbx-card-status-badge" class="badge">Not Connected</span>
            </div>
            <div id="dbx-account-line" style="font-size:0.84rem; color:var(--muted);">
              Connect once with OAuth PKCE to browse shared team folders without re-logging in.
            </div>
            <div id="dbx-pinned-badge" style="display:none; font-size:0.78rem; color:#93c5fd; background:rgba(59,130,246,0.12); border:1px solid rgba(59,130,246,0.35); border-radius:6px; padding:6px 10px;"></div>
            <div id="dropbox-permission-warning" class="dbx-warning-box"></div>
            <div id="dbx-selected-input-box" style="display:none; background:#0b1120; border:1px solid var(--border); border-radius:8px; padding:10px 12px; font-size:0.84rem;">
              <div style="color:var(--muted); font-size:0.76rem; margin-bottom:3px;">Selected Dropbox Source Folder:</div>
              <code id="dbx-selected-input-label" style="color:#6ee7b7; word-break:break-all;">No folder selected</code>
            </div>
            <div id="dbx-thumb-opt-row" style="display:none; background:#0b1120; border:1px solid var(--border); border-radius:8px; padding:8px 12px;">
              <label style="display:flex; align-items:center; gap:8px; cursor:pointer; font-size:0.82rem; color:var(--text);">
                <input type="checkbox" id="dbx-use-thumbnails-checkbox" checked style="width:16px; height:16px; cursor:pointer;" />
                <span>Fast Thumbnail Scan (Recommended - full-res kept on Dropbox)</span>
              </label>
            </div>
          </div>

          <div id="dbx-card-actions" style="display:flex; gap:10px; flex-wrap:wrap; margin-top:6px;">
            <button type="button" class="btn-primary-large" onclick="openDropboxSettingsModal()">
              Configure Dropbox App
            </button>
          </div>
        </div>
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

      <div style="display:flex; justify-content:space-between; align-items:center; border-top:1px solid var(--border); padding-top:16px; flex-wrap:wrap; gap:12px;">
        <div style="display:flex; flex-direction:column; gap:4px; font-size:0.84rem; color:var(--muted);">
          <span>
            Local output directory: <code id="home-output-dir" style="color:#cbd5e1;"></code>
          </span>
          <span>
            Dropbox output folder: <code id="home-dropbox-output-dir" style="color:#cbd5e1;">Not selected</code>
          </span>
        </div>
        <button id="home-pick-dbx-output-btn" type="button" class="btn-secondary" style="display:none;" onclick="openDropboxBrowser('output')">
          Pick Dropbox Output
        </button>
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
        <div style="display:flex; align-items:center; gap:10px;">
          <button id="toggle-bbox-btn" class="btn-secondary" onclick="toggleBoundingBoxes()" style="border-color:rgba(16,185,129,0.5); color:#6ee7b7;">
            Bounding Boxes: ON
          </button>
          <button class="btn-secondary" onclick="closeCompareModal()">Close (Esc)</button>
        </div>
      </div>
      <div class="modal-body" id="compare-body"></div>
    </div>
  </div>

  <!-- ================= 4. DROPBOX CONNECTION & SETTINGS MODAL ================= -->
  <div id="dropbox-settings-modal" class="overlay-modal" onclick="if(event.target===this) closeDropboxSettingsModal()">
    <div class="modal-box" style="max-width:680px;">
      <div class="modal-header">
        <strong>Dropbox Connection & Settings</strong>
        <button class="btn-secondary" onclick="closeDropboxSettingsModal()">Close (Esc)</button>
      </div>
      <div class="modal-body" style="gap:16px;">
        <div id="dbx-settings-error" class="dbx-error-box"></div>
        <div id="dbx-settings-warning" class="dbx-warning-box"></div>

        <details id="dbx-advanced-details" class="dbx-advanced-details">
          <summary>Advanced Settings (App Key, Redirect URI, Shared Folder ID)</summary>
          <div style="display:flex; flex-direction:column; gap:14px;">
            <div style="background:#0b1120; border:1px solid var(--border); border-radius:8px; padding:10px 12px; display:flex; flex-direction:column; gap:6px;">
              <span style="font-size:0.8rem; color:var(--muted); font-weight:600;">OAuth 2 Redirect URI (Add in Dropbox App Console -> Settings -> Redirect URIs):</span>
              <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap;">
                <code id="dbx-redirect-uri" style="color:#93c5fd; font-size:0.86rem; word-break:break-all;">http://localhost:8080/dropbox/callback</code>
                <button type="button" class="btn-secondary" onclick="copyDropboxRedirectUri()">Copy</button>
              </div>
            </div>

            <div style="display:flex; flex-direction:column; gap:6px;">
              <label for="dbx-app-key-input" style="font-size:0.86rem; font-weight:600;">Dropbox App Key (PKCE Public Client - no App Secret needed)</label>
              <input id="dbx-app-key-input" type="text" class="dbx-input" placeholder="e.g. abc123xyz456..." />
            </div>

            <div style="display:flex; flex-direction:column; gap:6px;">
              <label for="dbx-shared-folder-input" style="font-size:0.86rem; font-weight:600;">Shared Folder Namespace ID (Optional - pins browsing & output to team shared folder)</label>
              <input id="dbx-shared-folder-input" type="text" class="dbx-input" placeholder="Leave blank for personal root, or paste namespace ID" />
            </div>
          </div>
        </details>

        <div id="dbx-shared-folders-section" style="display:none; flex-direction:column; gap:8px; border-top:1px solid var(--border); padding-top:12px;">
          <div style="display:flex; align-items:center; justify-content:space-between; gap:10px;">
            <span style="font-size:0.84rem; color:var(--muted); font-weight:600;">Your Dropbox Shared Folders</span>
            <button type="button" class="btn-secondary" onclick="loadSharedFoldersList()">Load My Shared Folders</button>
          </div>
          <div id="dbx-shared-folders-list" style="display:flex; flex-direction:column; gap:6px;"></div>
        </div>

        <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px; border-top:1px solid var(--border); padding-top:14px;">
          <div style="display:flex; gap:10px; flex-wrap:wrap;">
            <button type="button" class="btn-secondary" onclick="saveDropboxSettings()">Save Settings</button>
            <button id="dbx-modal-connect-btn" type="button" class="btn-primary-large" style="padding:8px 16px; font-size:0.88rem;" onclick="connectDropbox()">Connect Dropbox</button>
            <button id="dbx-modal-disconnect-btn" type="button" class="btn-small btn-remove" style="display:none; padding:8px 14px; font-size:0.86rem;" onclick="disconnectDropbox()">Disconnect Dropbox</button>
          </div>
          <button type="button" class="btn-secondary" onclick="closeDropboxSettingsModal()">Close</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ================= 5. DROPBOX FOLDER PICKER MODAL ================= -->
  <div id="dropbox-browser-modal" class="overlay-modal" style="z-index:130;" onclick="if(event.target===this) closeDropboxBrowser()">
    <div class="modal-box" style="max-width:740px;">
      <div class="modal-header">
        <strong id="dbx-browser-title">Browse Dropbox Folders</strong>
        <button class="btn-secondary" onclick="closeDropboxBrowser()">Close (Esc)</button>
      </div>
      <div class="modal-body" style="gap:14px;">
        <div id="dbx-browser-error" class="dbx-error-box"></div>

        <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px; background:#0f172a; padding:10px 14px; border-radius:8px; border:1px solid var(--border);">
          <div style="display:flex; flex-direction:column; gap:3px; min-width:0;">
            <span id="dbx-browser-scope-label" style="font-size:0.74rem; color:#93c5fd; font-weight:700; text-transform:uppercase; letter-spacing:0.05em;">Scope: My Dropbox</span>
            <code id="dbx-browser-path-label" style="font-size:0.9rem; color:#f8fafc; word-break:break-all;">/</code>
          </div>
          <div style="display:flex; gap:8px; flex-wrap:wrap;">
            <button id="dbx-browser-up-btn" type="button" class="btn-secondary" onclick="navigateDropboxUp()">Back</button>
            <button type="button" class="btn-secondary" onclick="refreshDropboxBrowser()">Refresh</button>
            <button type="button" class="btn-secondary" onclick="promptCreateDropboxFolder()">+ New Folder</button>
          </div>
        </div>

        <div id="dbx-browser-folder-list" class="dbx-folder-list"></div>

        <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; border-top:1px solid var(--border); padding-top:14px;">
          <div style="font-size:0.86rem; color:var(--muted);">
            <span id="dbx-browser-stats">0 photo(s) found here</span>
          </div>
          <div style="display:flex; gap:10px;">
            <button type="button" class="btn-secondary" onclick="closeDropboxBrowser()">Cancel</button>
            <button type="button" class="btn-commit" style="padding:9px 18px; font-size:0.9rem;" onclick="confirmDropboxFolderSelection()">Select This Folder</button>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ================= 6. SAVE SORTED PHOTOS (LOCAL OR DROPBOX) MODAL ================= -->
  <div id="export-modal" class="overlay-modal" onclick="if(event.target===this && !isExporting) closeExportModal()">
    <div class="modal-box" style="max-width:720px;">
      <div class="modal-header">
        <strong>Save Sorted Photos</strong>
        <button class="btn-secondary" onclick="if(!isExporting) closeExportModal()">Close (Esc)</button>
      </div>
      <div class="modal-body" style="gap:16px;">
        <div id="export-error" class="dbx-error-box"></div>

        <p style="font-size:0.9rem; color:var(--muted);">
          Choose where to create the sorted player folders: on your local computer or inside Dropbox.
        </p>

        <div id="export-card-local" class="export-option-card selected">
          <label style="display:flex; align-items:center; gap:10px; cursor:pointer; font-weight:700; font-size:0.95rem;">
            <input type="radio" name="export-dest-type" id="export-dest-local" value="local" checked onchange="updateExportDestUI()" style="width:17px; height:17px; accent-color:var(--primary);" />
            <span>Save to Local Computer Folder</span>
          </label>
          <div style="display:flex; gap:8px; align-items:center;">
            <input id="export-local-dir" type="text" class="dbx-input" placeholder="C:\path\to\sorted_output" />
            <button type="button" class="btn-secondary" style="white-space:nowrap;" onclick="browseLocalFolder()">Browse...</button>
          </div>
        </div>

        <div id="export-card-dropbox" class="export-option-card">
          <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap;">
            <label style="display:flex; align-items:center; gap:10px; cursor:pointer; font-weight:700; font-size:0.95rem;">
              <input type="radio" name="export-dest-type" id="export-dest-dropbox" value="dropbox" onchange="updateExportDestUI()" style="width:17px; height:17px; accent-color:var(--primary);" />
              <span>Save to Dropbox Folder</span>
            </label>
            <span id="export-dbx-status-line" style="font-size:0.8rem; color:var(--muted);">Checking Dropbox status...</span>
          </div>
          <div style="display:flex; gap:8px; align-items:center;">
            <input id="export-dropbox-dir" type="text" class="dbx-input" placeholder="/Sorted_Photos/Game_1" />
            <button id="export-pick-dbx-btn" type="button" class="btn-secondary" style="white-space:nowrap;" onclick="openDropboxBrowser('export_output')">Pick Folder</button>
            <button id="export-connect-dbx-btn" type="button" class="btn-primary-large" style="display:none; padding:8px 14px; font-size:0.84rem; white-space:nowrap;" onclick="connectDropbox()">Connect Dropbox</button>
          </div>
          <span id="export-cloud-hint" style="font-size:0.78rem; color:var(--muted);">
            Photos sourced from Dropbox are organized cloud-to-cloud instantly; local photos are uploaded directly into each player folder.
          </span>
        </div>

        <div style="background:#0f172a; border:1px solid var(--border); border-radius:10px; padding:12px 14px; display:flex; flex-direction:column; gap:8px;">
          <strong style="font-size:0.86rem; color:#cbd5e1;">File Handling</strong>
          <label style="display:flex; align-items:center; gap:8px; cursor:pointer; font-size:0.88rem;">
            <input type="radio" name="export-file-mode" id="export-mode-copy" value="copy" checked style="accent-color:var(--primary);" />
            <span>Copy photos (Keep original photos intact)</span>
          </label>
          <label style="display:flex; align-items:center; gap:8px; cursor:pointer; font-size:0.88rem;">
            <input type="radio" name="export-file-mode" id="export-mode-move" value="move" style="accent-color:var(--primary);" />
            <span>Move photos (Move directly into player folders)</span>
          </label>
        </div>

        <div id="export-progress" class="upload-progress">
          <div class="progress-header-row">
            <span id="export-status-title">Exporting folders...</span>
            <span id="export-status-pct">0%</span>
          </div>
          <div class="progress-track">
            <div id="export-progress-fill" class="progress-fill"></div>
          </div>
          <span id="export-status-detail" style="color:var(--muted); font-size:0.82rem;"></span>
        </div>

        <div style="display:flex; justify-content:flex-end; gap:10px; border-top:1px solid var(--border); padding-top:14px;">
          <button id="export-cancel-btn" type="button" class="btn-secondary" onclick="closeExportModal()">Cancel</button>
          <button id="export-submit-btn" type="button" class="btn-commit" onclick="runExportFolders()">
            Export & Create Folders
          </button>
        </div>
      </div>
    </div>
  </div>

  <!-- ================= 7. FIRST-TIME CONNECT DROPBOX WELCOME POPUP ================= -->
  <div id="first-run-dropbox-modal" class="overlay-modal" style="z-index:125;" onclick="if(event.target===this) dismissFirstRunDropboxModal()">
    <div class="modal-box" style="max-width:520px;">
      <div class="modal-header">
        <strong>Welcome to PersonSort</strong>
        <button class="btn-secondary" onclick="dismissFirstRunDropboxModal()">Close (Esc)</button>
      </div>
      <div class="modal-body" style="gap:16px;">
        <p style="font-size:0.96rem; line-height:1.55; color:var(--text);">
          Connect your Dropbox account once to access shared team folders and save sorted player folders directly in Dropbox.
        </p>
        <p style="font-size:0.86rem; line-height:1.5; color:var(--muted);">
          Clicking <strong>Connect Dropbox</strong> will open Dropbox so you can sign in and click <strong>Allow</strong>. If you only want to sort photos stored on this computer, you can skip this step.
        </p>
        <div style="display:flex; justify-content:flex-end; gap:10px; flex-wrap:wrap; border-top:1px solid var(--border); padding-top:14px;">
          <button type="button" class="btn-secondary" onclick="dismissFirstRunDropboxModal()">Use Local Files Only</button>
          <button type="button" class="btn-primary-large" style="padding:10px 20px; font-size:0.94rem;" onclick="connectDropbox()">
            Connect Dropbox
          </button>
        </div>
      </div>
    </div>
  </div>

  <!-- ================= 8. SHUTDOWN CONFIRMATION OVERLAY ================= -->
  <div id="shutdown-overlay" class="overlay-modal" style="z-index:999;">
    <div class="modal-box" style="max-width:460px;">
      <div class="modal-header">
        <strong>PersonSort Has Closed</strong>
      </div>
      <div class="modal-body" style="gap:12px;">
        <p style="font-size:0.95rem; line-height:1.5; color:var(--text);">
          The PersonSort background app has stopped cleanly.
        </p>
        <p style="font-size:0.86rem; color:var(--muted);">
          You may now close this browser tab.
        </p>
      </div>
    </div>
  </div>

  <script>
    let appState = null;
    let dropboxStatus = null;
    let currentView = '__ALL__';
    let currentModalData = null;
    let activeFaceId = null;
    let showBoundingBoxes = true;
    let dropboxBrowserMode = 'input';
    let dropboxBrowserPath = '';
    let dropboxBrowserParent = null;
    let isExporting = false;

    const urlParams = new URLSearchParams(window.location.search);
    const returnUrl = urlParams.get('return_url');
    if (returnUrl) {
      const link = document.getElementById('crm-return-link');
      link.href = returnUrl;
      link.style.display = 'inline';
    }

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        if (document.getElementById('dropbox-browser-modal').classList.contains('open')) {
          closeDropboxBrowser();
          return;
        }
        dismissFirstRunDropboxModal();
        closeCompareModal();
        closeDropboxSettingsModal();
        if (!isExporting) closeExportModal();
      }
    });

    window.addEventListener('message', async (event) => {
      if (!event || !event.data || event.data.type !== 'personsort-dropbox-connected') {
        return;
      }
      document.getElementById('first-run-dropbox-modal').classList.remove('open');
      closeDropboxSettingsModal();
      await loadDropboxStatus();
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
      await loadDropboxStatus();
      maybeShowFirstRunDropboxPopup();
      checkForUpdates();
    }

    async function quitApp() {
      if (appState && appState.mode === 'review') {
        const proceed = confirm(
          'Quit PersonSort? Any uncommitted folder edits in this review session will be lost.'
        );
        if (!proceed) return;
      }
      try {
        await fetch('/api/shutdown', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: '{}',
        });
      } catch (err) {
        console.debug('Shutdown request finished:', err);
      }
      document.getElementById('shutdown-overlay').classList.add('open');
      try {
        window.close();
      } catch (err) {
        // Browsers may block closing a tab not opened by script; overlay informs user.
      }
    }

    async function checkForUpdates() {
      try {
        const res = await fetch('/api/update-check');
        if (!res.ok) return;
        const info = await res.json();
        const verEl = document.getElementById('header-version-badge');
        if (verEl && info.current_version) {
          verEl.textContent = `v${info.current_version}`;
        }
        const btn = document.getElementById('header-update-btn');
        if (btn && info.update_available && info.release_url) {
          btn.href = info.release_url;
          btn.textContent = `Update Available (${info.latest_version})`;
          btn.classList.add('visible');
        }
      } catch (err) {
        console.debug('Update check skipped:', err);
      }
    }

    function maybeShowFirstRunDropboxPopup() {
      if (!appState || appState.mode !== 'home') return;
      if (!dropboxStatus || !dropboxStatus.configured || dropboxStatus.connected) return;
      if (sessionStorage.getItem('ps_skip_dbx_welcome') === '1') return;
      document.getElementById('first-run-dropbox-modal').classList.add('open');
    }

    function dismissFirstRunDropboxModal() {
      const el = document.getElementById('first-run-dropbox-modal');
      if (el && el.classList.contains('open')) {
        sessionStorage.setItem('ps_skip_dbx_welcome', '1');
        el.classList.remove('open');
      }
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
        if (err && err.message) {
          alert('Error: ' + err.message);
        }
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
          const destKindLabel = appState.last_commit_destination_type === 'dropbox'
            ? 'Dropbox'
            : 'Local Computer';
          banner.classList.add('visible');
          banner.innerHTML = `
            <strong>Real Player Folders Created Successfully</strong><br/>
            Saved <strong>${appState.last_commit.total_players_grouped}</strong> player folder(s) (${appState.last_commit.photos_with_grouped_players} matched photos, ${appState.last_commit.no_face_photos_count} unassigned/no-face) to <strong>${destKindLabel}</strong>:<br/>
            <code>${escapeHtml(appState.last_commit_output_dir || '')}</code>
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

      const updateToggleBtnUI = () => {
        const btn = document.getElementById('toggle-bbox-btn');
        if (!btn) return;
        btn.textContent = showBoundingBoxes ? 'Bounding Boxes: ON' : 'Bounding Boxes: OFF';
        btn.style.borderColor = showBoundingBoxes ? 'rgba(16,185,129,0.5)' : 'var(--border)';
        btn.style.color = showBoundingBoxes ? '#6ee7b7' : 'var(--muted)';
      };
      updateToggleBtnUI();

      body.innerHTML = `
        <div class="lightbox-stage-wrap">
          <div class="lightbox-stage">
            <img src="${data.photo_url}" alt="${data.photo_id}" />
            <svg id="photo-bbox-svg" class="face-overlay-svg" viewBox="0 0 ${imgW} ${imgH}" preserveAspectRatio="xMidYMid meet" style="display:${showBoundingBoxes ? 'block' : 'none'};">
              ${svgBodyBoxesHtml}
              ${svgNumberBoxesHtml}
              ${svgBoxesHtml}
            </svg>
          </div>
          <div style="display:flex; align-items:center; justify-content:space-between; width:100%; max-width:900px; gap:12px; flex-wrap:wrap;">
            <div style="font-size:0.84rem; color:var(--muted);">
              ${boxes.length > 0
                ? `Click on a face bounding box in the photo above to view its cropped reference face and top 5-10 closest matches.`
                : `No face bounding boxes detected in this photo.`}
            </div>
            <label style="display:inline-flex; align-items:center; gap:8px; cursor:pointer; font-size:0.84rem; color:var(--text); user-select:none; background:#0f172a; padding:5px 10px; border-radius:6px; border:1px solid var(--border);">
              <input type="checkbox" id="toggle-bbox-checkbox" ${showBoundingBoxes ? 'checked' : ''} onchange="toggleBoundingBoxes()" style="accent-color:var(--primary); cursor:pointer;" />
              <span>Show Bounding Boxes</span>
            </label>
          </div>
        </div>
        ${faceMatchesHtml}
      `;
    }

    function toggleBoundingBoxes() {
      showBoundingBoxes = !showBoundingBoxes;
      const svgEl = document.getElementById('photo-bbox-svg');
      if (svgEl) {
        svgEl.style.display = showBoundingBoxes ? 'block' : 'none';
      }
      const chkEl = document.getElementById('toggle-bbox-checkbox');
      if (chkEl) {
        chkEl.checked = showBoundingBoxes;
      }
      const btn = document.getElementById('toggle-bbox-btn');
      if (btn) {
        btn.textContent = showBoundingBoxes ? 'Bounding Boxes: ON' : 'Bounding Boxes: OFF';
        btn.style.borderColor = showBoundingBoxes ? 'rgba(16,185,129,0.5)' : 'var(--border)';
        btn.style.color = showBoundingBoxes ? '#6ee7b7' : 'var(--muted)';
      }
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
      openExportModal();
    }

    function openExportModal() {
      const errBox = document.getElementById('export-error');
      errBox.classList.remove('visible');
      errBox.textContent = '';
      document.getElementById('export-progress').classList.remove('visible');

      const localInput = document.getElementById('export-local-dir');
      if (localInput && (!localInput.value || !localInput.value.trim())) {
        localInput.value = (appState && appState.output_dir) ? appState.output_dir : '';
      }

      const dbxInput = document.getElementById('export-dropbox-dir');
      if (dbxInput && (!dbxInput.value || !dbxInput.value.trim())) {
        let defaultDbx = (dropboxStatus && dropboxStatus.selected_output_folder) ? dropboxStatus.selected_output_folder : '';
        if (!defaultDbx && appState && appState.source_type === 'dropbox' && appState.source_dropbox_folder) {
          const srcBase = appState.source_dropbox_folder === '/' ? '' : appState.source_dropbox_folder.replace(/\/+$/, '');
          defaultDbx = `${srcBase}/Sorted`;
        }
        dbxInput.value = defaultDbx;
      }

      if (appState && appState.source_type === 'dropbox' && dropboxStatus && dropboxStatus.connected) {
        document.getElementById('export-dest-dropbox').checked = true;
      } else {
        document.getElementById('export-dest-local').checked = true;
      }

      updateExportDestUI();
      document.getElementById('export-modal').classList.add('open');
    }

    function closeExportModal() {
      if (isExporting) return;
      document.getElementById('export-modal').classList.remove('open');
    }

    function updateExportDestUI() {
      const isDbx = document.getElementById('export-dest-dropbox').checked;
      document.getElementById('export-card-local').classList.toggle('selected', !isDbx);
      document.getElementById('export-card-dropbox').classList.toggle('selected', isDbx);

      const dbxStatusLine = document.getElementById('export-dbx-status-line');
      const pickBtn = document.getElementById('export-pick-dbx-btn');
      const connectBtn = document.getElementById('export-connect-dbx-btn');
      if (dropboxStatus && dropboxStatus.connected) {
        dbxStatusLine.textContent = `Connected as ${dropboxStatus.account_name || 'Dropbox User'}`;
        dbxStatusLine.style.color = '#6ee7b7';
        pickBtn.style.display = 'inline-block';
        connectBtn.style.display = 'none';
      } else {
        dbxStatusLine.textContent = 'Not connected to Dropbox';
        dbxStatusLine.style.color = '#fcd34d';
        pickBtn.style.display = 'none';
        connectBtn.style.display = 'inline-block';
      }
    }

    async function browseLocalFolder() {
      const res = await fetch('/api/browse-local-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ initial_dir: document.getElementById('export-local-dir').value || '' }),
      });
      const data = await res.json().catch(() => ({}));
      if (data && data.path) {
        document.getElementById('export-local-dir').value = data.path;
        document.getElementById('export-dest-local').checked = true;
        updateExportDestUI();
      } else if (data && data.error) {
        const errBox = document.getElementById('export-error');
        errBox.textContent = data.error;
        errBox.classList.add('visible');
      }
    }

    async function runExportFolders() {
      const errBox = document.getElementById('export-error');
      errBox.classList.remove('visible');
      const isDbx = document.getElementById('export-dest-dropbox').checked;
      const localDir = (document.getElementById('export-local-dir').value || '').trim();
      const dbxDir = (document.getElementById('export-dropbox-dir').value || '').trim();
      const fileMode = document.getElementById('export-mode-move').checked ? 'move' : 'copy';

      if (isDbx) {
        if (!dropboxStatus || !dropboxStatus.connected) {
          errBox.textContent = 'Please connect Dropbox first before saving to Dropbox.';
          errBox.classList.add('visible');
          return;
        }
        if (!dbxDir) {
          errBox.textContent = 'Please choose or enter a Dropbox destination folder path.';
          errBox.classList.add('visible');
          return;
        }
      }

      isExporting = true;
      const progEl = document.getElementById('export-progress');
      const fillEl = document.getElementById('export-progress-fill');
      const titleEl = document.getElementById('export-status-title');
      const pctEl = document.getElementById('export-status-pct');
      const detailEl = document.getElementById('export-status-detail');
      const submitBtn = document.getElementById('export-submit-btn');
      const cancelBtn = document.getElementById('export-cancel-btn');

      submitBtn.disabled = true;
      cancelBtn.disabled = true;
      progEl.classList.add('visible');
      fillEl.style.width = '5%';
      pctEl.textContent = '0%';
      titleEl.textContent = isDbx ? 'Exporting to Dropbox...' : 'Exporting to Local Folder...';
      detailEl.textContent = '';

      try {
        await apiCall('/api/export-start', 'POST', {
          destination_type: isDbx ? 'dropbox' : 'local',
          local_output_dir: localDir,
          dropbox_output_folder: dbxDir,
          file_mode: fileMode,
        });

        while (true) {
          await new Promise(r => setTimeout(r, 200));
          const p = await apiCall('/api/progress');
          if (p.error) {
            throw new Error(p.error);
          }
          const pct = Math.max(0, Math.min(100, p.percent || 0));
          fillEl.style.width = `${pct}%`;
          pctEl.textContent = `${Math.round(pct)}%`;
          titleEl.textContent = p.stage || 'Exporting...';
          detailEl.textContent = p.current_file || '';
          if (!p.active) {
            break;
          }
        }

        fillEl.style.width = '100%';
        pctEl.textContent = '100%';
        titleEl.textContent = 'Export complete!';
        isExporting = false;
        document.getElementById('export-modal').classList.remove('open');
        appState = await apiCall('/api/state');
        render();
      } catch (err) {
        errBox.textContent = err.message || 'Export failed.';
        errBox.classList.add('visible');
      } finally {
        isExporting = false;
        submitBtn.disabled = false;
        cancelBtn.disabled = false;
        progEl.classList.remove('visible');
      }
    }

    async function startDropboxScan() {
      if (!dropboxStatus || !dropboxStatus.connected) {
        openDropboxSettingsModal();
        return;
      }
      const folderPath = dropboxStatus.selected_input_folder || '';
      if (!folderPath) {
        openDropboxBrowser('input');
        return;
      }

      const scanJerseys = document.getElementById('scan-jerseys-checkbox')
        ? document.getElementById('scan-jerseys-checkbox').checked
        : true;
      const useThumbnails = document.getElementById('dbx-use-thumbnails-checkbox')
        ? document.getElementById('dbx-use-thumbnails-checkbox').checked
        : true;

      const prog = document.getElementById('upload-progress');
      prog.classList.add('visible');
      setProgressBar(2, 'Connecting to Dropbox...', folderPath);

      try {
        await apiCall('/api/dropbox/process', 'POST', {
          folder_path: folderPath,
          scan_jerseys: scanJerseys,
          use_thumbnails: useThumbnails,
        });

        while (true) {
          await new Promise(r => setTimeout(r, 200));
          const p = await apiCall('/api/progress');
          if (p.error) {
            throw new Error(p.error);
          }
          const stageLower = (p.stage || '').toLowerCase();
          const isDlStage = stageLower.includes('download') || stageLower.includes('listing');
          const combinedPct = isDlStage
            ? Math.round((p.percent || 0) * 0.25)
            : 25 + Math.round((p.percent || 0) * 0.75);
          setProgressBar(
            combinedPct,
            p.stage || 'Scanning Dropbox photos...',
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
        if (err && err.message) {
          alert('Error: ' + err.message);
        }
      } finally {
        prog.classList.remove('visible');
      }
    }

    function formatFileSize(bytes) {
      const n = Number(bytes) || 0;
      if (n < 1024) return `${n} B`;
      if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
      return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    }

    function escapeHtml(value) {
      return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    async function loadDropboxStatus() {
      try {
        const res = await fetch('/api/dropbox/status');
        if (!res.ok) return;
        dropboxStatus = await res.json();
        renderDropboxStatus();
      } catch (err) {
        console.error('Failed to load Dropbox status:', err);
      }
    }

    function renderDropboxStatus() {
      if (!dropboxStatus) return;

      const headerBtn = document.getElementById('header-dropbox-btn');
      const cardBadge = document.getElementById('dbx-card-status-badge');
      const accountLine = document.getElementById('dbx-account-line');
      const pinnedBadge = document.getElementById('dbx-pinned-badge');
      const warnBox = document.getElementById('dropbox-permission-warning');
      const selectedInputBox = document.getElementById('dbx-selected-input-box');
      const selectedInputLabel = document.getElementById('dbx-selected-input-label');
      const actionsEl = document.getElementById('dbx-card-actions');
      const homeDbxOutEl = document.getElementById('home-dropbox-output-dir');
      const homePickOutBtn = document.getElementById('home-pick-dbx-output-btn');

      const redirectEl = document.getElementById('dbx-redirect-uri');
      if (redirectEl && dropboxStatus.redirect_uri) {
        redirectEl.textContent = dropboxStatus.redirect_uri;
      }

      if (dropboxStatus.connected) {
        const who = dropboxStatus.account_name || 'Connected';
        headerBtn.textContent = `Dropbox: ${who}`;
        headerBtn.classList.add('connected');
        cardBadge.textContent = 'Connected';
        cardBadge.style.background = 'rgba(16, 185, 129, 0.2)';
        cardBadge.style.color = '#6ee7b7';

        const thumbRow = document.getElementById('dbx-thumb-opt-row');
        if (thumbRow) thumbRow.style.display = 'block';

        const emailSuffix = dropboxStatus.account_email ? ` (${dropboxStatus.account_email})` : '';
        accountLine.textContent = `Signed in as ${who}${emailSuffix}.`;
        selectedInputBox.style.display = 'block';
        selectedInputLabel.textContent = dropboxStatus.selected_input_folder || 'No folder selected';

        homeDbxOutEl.textContent = dropboxStatus.selected_output_folder || 'Not selected';
        homeDbxOutEl.style.color = dropboxStatus.selected_output_folder ? '#6ee7b7' : '#cbd5e1';
        homePickOutBtn.style.display = 'inline-block';

        const scanBtnHtml = dropboxStatus.selected_input_folder
          ? `<button type="button" class="btn-commit" style="padding:10px 16px; font-size:0.9rem;" onclick="startDropboxScan()">Load & Scan Dropbox Folder</button>`
          : '';

        actionsEl.innerHTML = `
          ${scanBtnHtml}
          <button type="button" class="btn-primary-large" style="padding:10px 18px; font-size:0.9rem;" onclick="openDropboxBrowser('input')">
            Browse Dropbox Folders
          </button>
          <button type="button" class="btn-secondary" onclick="openDropboxSettingsModal()">
            Settings
          </button>
        `;
      } else {
        headerBtn.classList.remove('connected');
        selectedInputBox.style.display = 'none';
        const thumbRow = document.getElementById('dbx-thumb-opt-row');
        if (thumbRow) thumbRow.style.display = 'none';
        homeDbxOutEl.textContent = dropboxStatus.selected_output_folder || 'Not selected';
        homeDbxOutEl.style.color = '#cbd5e1';
        homePickOutBtn.style.display = 'none';

        if (!dropboxStatus.sdk_available) {
          headerBtn.textContent = 'Dropbox: SDK Missing';
          cardBadge.textContent = 'Setup Needed';
          cardBadge.style.background = 'rgba(239, 68, 68, 0.2)';
          cardBadge.style.color = '#fecaca';
          accountLine.textContent = dropboxStatus.error || 'Install dropbox and keyring packages.';
          actionsEl.innerHTML = `
            <button type="button" class="btn-secondary" onclick="openDropboxSettingsModal()">
              Settings
            </button>
          `;
        } else if (dropboxStatus.configured) {
          headerBtn.textContent = 'Dropbox: Ready to Connect';
          cardBadge.textContent = 'Not Connected';
          cardBadge.style.background = 'var(--panel-hover)';
          cardBadge.style.color = 'var(--text)';
          accountLine.textContent = 'App Key saved. Sign in once with Dropbox to browse team shared folders.';
          actionsEl.innerHTML = `
            <button type="button" class="btn-primary-large" style="padding:10px 18px; font-size:0.9rem;" onclick="connectDropbox()">
              Connect Dropbox
            </button>
            <button type="button" class="btn-secondary" onclick="openDropboxSettingsModal()">
              Settings
            </button>
          `;
        } else {
          headerBtn.textContent = 'Dropbox: Not Configured';
          cardBadge.textContent = 'Not Configured';
          cardBadge.style.background = 'var(--panel-hover)';
          cardBadge.style.color = 'var(--text)';
          accountLine.textContent = 'Enter your Dropbox App Key once to connect your account or team shared folder.';
          actionsEl.innerHTML = `
            <button type="button" class="btn-primary-large" style="padding:10px 18px; font-size:0.9rem;" onclick="openDropboxSettingsModal()">
              Configure Dropbox App
            </button>
          `;
        }
      }

      if (dropboxStatus.shared_folder_id) {
        pinnedBadge.style.display = 'block';
        pinnedBadge.textContent = `Pinned Shared Folder Namespace: ${dropboxStatus.shared_folder_id}`;
      } else {
        pinnedBadge.style.display = 'none';
      }

      const warnText = dropboxStatus.permission_warning || '';
      if (warnText) {
        warnBox.textContent = warnText;
        warnBox.classList.add('visible');
      } else {
        warnBox.classList.remove('visible');
      }

      // Sync Settings Modal state if open
      const modalWarn = document.getElementById('dbx-settings-warning');
      if (modalWarn) {
        if (warnText) {
          modalWarn.textContent = warnText;
          modalWarn.classList.add('visible');
        } else {
          modalWarn.classList.remove('visible');
        }
      }
      const sharedSection = document.getElementById('dbx-shared-folders-section');
      const modalConnectBtn = document.getElementById('dbx-modal-connect-btn');
      const modalDisconnectBtn = document.getElementById('dbx-modal-disconnect-btn');
      if (sharedSection && modalConnectBtn && modalDisconnectBtn) {
        sharedSection.style.display = dropboxStatus.connected ? 'flex' : 'none';
        modalConnectBtn.style.display = dropboxStatus.connected ? 'none' : 'inline-block';
        modalDisconnectBtn.style.display = dropboxStatus.connected ? 'inline-block' : 'none';
      }
      if (document.getElementById('export-modal') && document.getElementById('export-modal').classList.contains('open')) {
        updateExportDestUI();
      }
    }

    function openDropboxSettingsModal() {
      const errBox = document.getElementById('dbx-settings-error');
      errBox.classList.remove('visible');
      errBox.textContent = '';

      const advDetails = document.getElementById('dbx-advanced-details');
      if (dropboxStatus) {
        document.getElementById('dbx-app-key-input').value = dropboxStatus.app_key || '';
        document.getElementById('dbx-shared-folder-input').value = dropboxStatus.shared_folder_id || '';
        if (dropboxStatus.redirect_uri) {
          document.getElementById('dbx-redirect-uri').textContent = dropboxStatus.redirect_uri;
        }
        if (advDetails) {
          advDetails.open = !dropboxStatus.configured;
        }
        renderDropboxStatus();
      } else if (advDetails) {
        advDetails.open = false;
      }
      document.getElementById('dbx-shared-folders-list').innerHTML = '';
      document.getElementById('dropbox-settings-modal').classList.add('open');
    }

    function closeDropboxSettingsModal() {
      document.getElementById('dropbox-settings-modal').classList.remove('open');
    }

    async function copyDropboxRedirectUri() {
      const text = document.getElementById('dbx-redirect-uri').textContent || '';
      if (navigator.clipboard && text) {
        try {
          await navigator.clipboard.writeText(text);
        } catch (err) {
          console.error(err);
        }
      }
    }

    async function saveDropboxSettings(silent = false) {
      const errBox = document.getElementById('dbx-settings-error');
      errBox.classList.remove('visible');
      const appKey = (document.getElementById('dbx-app-key-input').value || '').trim();
      const sharedFolderId = (document.getElementById('dbx-shared-folder-input').value || '').trim();

      const res = await fetch('/api/dropbox/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          app_key: appKey,
          shared_folder_id: sharedFolderId,
        }),
      });
      const data = await res.json().catch(() => ({ error: res.statusText }));
      if (!res.ok) {
        errBox.textContent = data.error || 'Failed to save Dropbox settings.';
        errBox.classList.add('visible');
        throw new Error(data.error || 'Failed to save Dropbox settings.');
      }
      dropboxStatus = data;
      renderDropboxStatus();
      if (!silent) {
        closeDropboxSettingsModal();
      }
      return dropboxStatus;
    }

    async function connectDropbox() {
      const errBox = document.getElementById('dbx-settings-error');
      if (errBox) {
        errBox.classList.remove('visible');
      }

      try {
        const modalOpen = document.getElementById('dropbox-settings-modal').classList.contains('open');
        if (modalOpen) {
          await saveDropboxSettings(true);
        }

        const res = await fetch('/api/dropbox/auth-url', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        const data = await res.json().catch(() => ({ error: res.statusText }));
        if (!res.ok) {
          const msg = data.error || 'Unable to start Dropbox OAuth flow.';
          if (modalOpen && errBox) {
            errBox.textContent = msg;
            errBox.classList.add('visible');
          } else {
            alert(msg);
          }
          return;
        }
        const width = 620;
        const height = 740;
        const left = Math.max(0, Math.round(window.screenX + (window.outerWidth - width) / 2));
        const top = Math.max(0, Math.round(window.screenY + (window.outerHeight - height) / 2));
        const popup = window.open(
          data.auth_url,
          'personsort_dropbox_oauth',
          `width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes`
        );
        if (!popup || popup.closed || typeof popup.closed === 'undefined') {
          window.location.href = data.auth_url;
        } else {
          popup.focus();
        }
      } catch (err) {
        console.error(err);
      }
    }

    async function disconnectDropbox() {
      if (!confirm('Disconnect Dropbox and remove your saved sign-in token from this computer?')) {
        return;
      }
      dropboxStatus = await apiCall('/api/dropbox/disconnect', 'POST', {});
      document.getElementById('dbx-shared-folders-list').innerHTML = '';
      renderDropboxStatus();
    }

    async function loadSharedFoldersList() {
      const listEl = document.getElementById('dbx-shared-folders-list');
      const errBox = document.getElementById('dbx-settings-error');
      errBox.classList.remove('visible');
      listEl.innerHTML = '<div style="color:var(--muted); font-size:0.84rem;">Loading shared folders...</div>';

      const res = await fetch('/api/dropbox/shared-folders');
      const data = await res.json().catch(() => ({ error: res.statusText }));
      if (!res.ok) {
        listEl.innerHTML = '';
        errBox.textContent = data.error || 'Failed to load shared folders.';
        errBox.classList.add('visible');
        return;
      }

      const items = data.shared_folders || [];
      if (items.length === 0) {
        listEl.innerHTML = '<div style="color:var(--muted); font-size:0.84rem;">No shared folders found on this Dropbox account.</div>';
        return;
      }

      listEl.innerHTML = '';
      for (const sf of items) {
        const row = document.createElement('div');
        row.className = 'dbx-shared-item';

        const infoDiv = document.createElement('div');
        infoDiv.style.display = 'flex';
        infoDiv.style.flexDirection = 'column';
        infoDiv.innerHTML = `<strong>${escapeHtml(sf.name)}</strong><span style="color:var(--muted); font-size:0.76rem;">ID: ${escapeHtml(sf.shared_folder_id)}</span>`;

        const pinBtn = document.createElement('button');
        pinBtn.type = 'button';
        pinBtn.className = 'btn-secondary';
        pinBtn.textContent = sf.is_pinned ? 'Pinned' : 'Pin';
        pinBtn.onclick = async () => {
          document.getElementById('dbx-shared-folder-input').value = sf.shared_folder_id;
          await saveDropboxSettings(true);
          await loadSharedFoldersList();
        };

        row.appendChild(infoDiv);
        row.appendChild(pinBtn);
        listEl.appendChild(row);
      }
    }

    async function openDropboxBrowser(mode = 'input') {
      dropboxBrowserMode = mode;
      const titleEl = document.getElementById('dbx-browser-title');
      titleEl.textContent = (mode === 'output' || mode === 'export_output')
        ? 'Choose Dropbox Output Folder'
        : 'Choose Dropbox Source Folder';

      let startPath = '';
      if (mode === 'export_output') {
        const currentExportPath = (document.getElementById('export-dropbox-dir').value || '').trim();
        startPath = currentExportPath === '/' ? '' : currentExportPath;
      } else if (dropboxStatus) {
        const existing = mode === 'output'
          ? (dropboxStatus.selected_output_folder || '')
          : (dropboxStatus.selected_input_folder || '');
        startPath = existing === '/' ? '' : existing;
      }

      document.getElementById('dropbox-browser-modal').classList.add('open');
      await browseDropboxPath(startPath);
    }

    function closeDropboxBrowser() {
      document.getElementById('dropbox-browser-modal').classList.remove('open');
    }

    async function refreshDropboxBrowser() {
      await browseDropboxPath(dropboxBrowserPath);
    }

    async function navigateDropboxUp() {
      if (dropboxBrowserParent === null || dropboxBrowserParent === undefined) return;
      await browseDropboxPath(dropboxBrowserParent);
    }

    async function browseDropboxPath(path) {
      const errBox = document.getElementById('dbx-browser-error');
      const listEl = document.getElementById('dbx-browser-folder-list');
      const statsEl = document.getElementById('dbx-browser-stats');
      const pathLabel = document.getElementById('dbx-browser-path-label');
      const scopeLabel = document.getElementById('dbx-browser-scope-label');
      const upBtn = document.getElementById('dbx-browser-up-btn');

      errBox.classList.remove('visible');
      listEl.innerHTML = '<div style="padding:16px; color:var(--muted); font-size:0.86rem;">Loading Dropbox folders...</div>';

      const res = await fetch(`/api/dropbox/folders?path=${encodeURIComponent(path || '')}`);
      const data = await res.json().catch(() => ({ error: res.statusText }));
      if (!res.ok) {
        listEl.innerHTML = '';
        errBox.textContent = data.error || 'Unable to list Dropbox folder.';
        errBox.classList.add('visible');
        return;
      }

      dropboxBrowserPath = data.current_path || '';
      dropboxBrowserParent = data.parent_path;

      scopeLabel.textContent = data.shared_folder_id
        ? `Scope: Pinned Shared Folder (${data.shared_folder_id})`
        : 'Scope: My Dropbox';
      pathLabel.textContent = dropboxBrowserPath || '/';
      upBtn.disabled = dropboxBrowserParent === null || dropboxBrowserParent === undefined;
      upBtn.style.opacity = upBtn.disabled ? '0.45' : '1';

      const folders = data.folders || [];
      const files = data.files || [];
      const unsupportedCount = files.filter(f => !f.is_supported_image).length;
      statsEl.textContent = unsupportedCount > 0
        ? `${data.image_count} supported photo(s), ${unsupportedCount} non-photo file(s) (highlighted in red)`
        : `${data.image_count} supported photo(s) found here`;

      if (folders.length === 0 && files.length === 0) {
        listEl.innerHTML = '<div style="padding:16px; color:var(--muted); font-size:0.86rem;">This folder is empty.</div>';
        return;
      }

      listEl.innerHTML = '';
      for (const f of folders) {
        const row = document.createElement('div');
        row.className = 'dbx-folder-row';
        row.onclick = () => browseDropboxPath(f.path_display || f.path_lower);

        const leftWrap = document.createElement('div');
        leftWrap.style.display = 'flex';
        leftWrap.style.alignItems = 'center';
        leftWrap.style.gap = '10px';
        leftWrap.style.minWidth = '0';

        const folderBadge = document.createElement('span');
        folderBadge.className = 'dbx-badge-folder';
        folderBadge.textContent = 'Folder';

        const nameSpan = document.createElement('span');
        nameSpan.textContent = f.name;
        nameSpan.style.fontWeight = '600';

        leftWrap.appendChild(folderBadge);
        leftWrap.appendChild(nameSpan);

        const pathSpan = document.createElement('span');
        pathSpan.textContent = f.path_display || f.path_lower || '';
        pathSpan.style.color = 'var(--muted)';
        pathSpan.style.fontSize = '0.78rem';

        row.appendChild(leftWrap);
        row.appendChild(pathSpan);
        listEl.appendChild(row);
      }

      for (const file of files) {
        const row = document.createElement('div');
        row.className = file.is_supported_image ? 'dbx-file-row' : 'dbx-file-row unsupported';

        const leftWrap = document.createElement('div');
        leftWrap.style.display = 'flex';
        leftWrap.style.alignItems = 'center';
        leftWrap.style.gap = '10px';
        leftWrap.style.minWidth = '0';

        const fileBadge = document.createElement('span');
        fileBadge.className = file.is_supported_image ? 'dbx-badge-photo' : 'dbx-badge-unsupported';
        fileBadge.textContent = file.is_supported_image ? 'Photo' : 'Not a Photo';

        const nameSpan = document.createElement('span');
        nameSpan.textContent = file.name;
        nameSpan.style.wordBreak = 'break-all';

        leftWrap.appendChild(fileBadge);
        leftWrap.appendChild(nameSpan);

        const sizeSpan = document.createElement('span');
        sizeSpan.textContent = formatFileSize(file.size);
        sizeSpan.style.fontSize = '0.76rem';
        sizeSpan.style.color = file.is_supported_image ? 'var(--muted)' : '#fca5a5';
        sizeSpan.style.flexShrink = '0';

        row.appendChild(leftWrap);
        row.appendChild(sizeSpan);
        listEl.appendChild(row);
      }
    }

    async function promptCreateDropboxFolder() {
      const folderName = prompt('Enter name for the new Dropbox folder:');
      if (!folderName || !folderName.trim()) return;
      const cleaned = folderName.trim().replace(/^\/+|\/+$/g, '');
      if (!cleaned) return;
      const targetPath = dropboxBrowserPath ? `${dropboxBrowserPath}/${cleaned}` : `/${cleaned}`;

      const errBox = document.getElementById('dbx-browser-error');
      errBox.classList.remove('visible');

      const res = await fetch('/api/dropbox/create-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: targetPath }),
      });
      const data = await res.json().catch(() => ({ error: res.statusText }));
      if (!res.ok) {
        errBox.textContent = data.error || 'Could not create Dropbox folder.';
        errBox.classList.add('visible');
        return;
      }
      await refreshDropboxBrowser();
    }

    async function confirmDropboxFolderSelection() {
      const chosen = dropboxBrowserPath || '/';
      if (dropboxBrowserMode === 'export_output') {
        const exportInput = document.getElementById('export-dropbox-dir');
        if (exportInput) exportInput.value = chosen;
        document.getElementById('export-dest-dropbox').checked = true;
        updateExportDestUI();
      }
      const payload = (dropboxBrowserMode === 'output' || dropboxBrowserMode === 'export_output')
        ? { selected_output_folder: chosen }
        : { selected_input_folder: chosen };

      const res = await fetch('/api/dropbox/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({ error: res.statusText }));
      if (!res.ok) {
        const errBox = document.getElementById('dbx-browser-error');
        errBox.textContent = data.error || 'Failed to save selected folder.';
        errBox.classList.add('visible');
        return;
      }
      dropboxStatus = data;
      renderDropboxStatus();
      closeDropboxBrowser();
    }

    loadState();
  </script>
</body>
</html>
"""


def _render_oauth_callback_html(
    title: str,
    message: str,
    *,
    is_error: bool = False,
    auto_redirect: bool = False,
    auto_close_popup: bool = False,
) -> str:
    """Render a standalone dark-mode confirmation page for the OAuth 2 PKCE callback."""
    safe_title = html.escape(title)
    safe_msg = html.escape(message)
    accent = "#ef4444" if is_error else "#10b981"
    meta_refresh = (
        '<meta http-equiv="refresh" content="2;url=/" />'
        if (auto_redirect and not auto_close_popup)
        else ""
    )
    if auto_close_popup:
        auto_script = """
  <script>
    try {
      if (window.opener && !window.opener.closed) {
        window.opener.postMessage({ type: 'personsort-dropbox-connected' }, '*');
        setTimeout(() => window.close(), 350);
      } else {
        setTimeout(() => { window.location.href = '/'; }, 600);
      }
    } catch (e) {
      setTimeout(() => { window.location.href = '/'; }, 600);
    }
  </script>
"""
    elif auto_redirect:
        auto_script = '<script>setTimeout(() => { window.location.href = "/"; }, 600);</script>'
    else:
        auto_script = ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  {meta_refresh}
  <title>{safe_title} - PersonSort</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      margin: 0;
      padding: 20px;
    }}
    .card {{
      background: #1e293b;
      border: 1px solid {accent};
      border-radius: 14px;
      padding: 28px 32px;
      max-width: 520px;
      width: 100%;
      display: flex;
      flex-direction: column;
      gap: 14px;
      box-shadow: 0 20px 45px rgba(0, 0, 0, 0.45);
    }}
    .btn {{
      display: inline-block;
      background: #3b82f6;
      color: #ffffff;
      text-decoration: none;
      padding: 10px 18px;
      border-radius: 8px;
      font-weight: 700;
      text-align: center;
      align-self: flex-start;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h2 style="margin:0; color:{accent};">{safe_title}</h2>
    <p style="margin:0; color:#cbd5e1; line-height:1.5;">{safe_msg}</p>
    <a class="btn" href="/">Return to PersonSort</a>
  </div>
  {auto_script}
</body>
</html>
"""


class CurationRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler serving the PersonSort Web UI and JSON REST endpoints with CORS enabled."""

    session: CurationSession
    dropbox_manager: DropboxManager
    server_port: int = 8080
    staging_files: list[Path] = []
    staging_dir: Path | None = None

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _get_dropbox_redirect_uri(self) -> str:
        return f"http://localhost:{self.server_port}{DROPBOX_CALLBACK_PATH}"

    def _send_html(self, html_str: str, status: int = HTTPStatus.OK) -> None:
        body = html_str.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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

        if route == DROPBOX_CALLBACK_PATH:
            flat_query = {k: v[0] for k, v in query.items() if v}
            if "error" in flat_query:
                desc = flat_query.get("error_description") or flat_query.get("error") or "Authorization cancelled."
                self._send_html(
                    _render_oauth_callback_html(
                        "Dropbox Sign-In Cancelled",
                        desc,
                        is_error=True,
                        auto_redirect=False,
                    )
                )
                return

            try:
                status_data = self.dropbox_manager.finish_oauth_flow(
                    flat_query,
                    redirect_uri=self._get_dropbox_redirect_uri(),
                )
                who = status_data.get("account_name") or "Dropbox User"
                warn = status_data.get("permission_warning")
                extra = f" Note: {warn}" if warn else " Redirecting back to PersonSort..."
                self._send_html(
                    _render_oauth_callback_html(
                        "Dropbox Connected",
                        f"Signed in as {who}.{extra}",
                        is_error=False,
                        auto_redirect=True,
                        auto_close_popup=True,
                    )
                )
            except Exception as exc:
                self._send_html(
                    _render_oauth_callback_html(
                        "Dropbox Connection Failed",
                        str(exc),
                        is_error=True,
                        auto_redirect=False,
                    ),
                    status=HTTPStatus.BAD_REQUEST,
                )
            return

        if route == "/api/update-check":
            self._send_json(check_for_update())
            return

        if route == "/api/dropbox/status":
            self._send_json(
                self.dropbox_manager.get_status(
                    redirect_uri=self._get_dropbox_redirect_uri()
                )
            )
            return

        if route == "/api/dropbox/shared-folders":
            try:
                folders = self.dropbox_manager.list_shared_folders()
                self._send_json({"shared_folders": folders})
            except DropboxPermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if route == "/api/dropbox/folders":
            path_arg = query.get("path", [""])[0]
            ns_override = query["shared_folder_id"][0] if "shared_folder_id" in query else None
            try:
                listing = self.dropbox_manager.list_folder(
                    path=path_arg,
                    shared_folder_id=ns_override,
                )
                self._send_json(listing)
            except DropboxPermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
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

            if route == "/api/dropbox/settings":
                state = self.dropbox_manager.update_settings(
                    app_key=str(data["app_key"]) if "app_key" in data else None,
                    shared_folder_id=str(data["shared_folder_id"]) if "shared_folder_id" in data else None,
                    selected_input_folder=(
                        str(data["selected_input_folder"])
                        if "selected_input_folder" in data
                        else None
                    ),
                    selected_output_folder=(
                        str(data["selected_output_folder"])
                        if "selected_output_folder" in data
                        else None
                    ),
                    redirect_uri=self._get_dropbox_redirect_uri(),
                )
                self._send_json(state)
                return

            if route == "/api/dropbox/auth-url":
                redirect_uri = self._get_dropbox_redirect_uri()
                auth_url = self.dropbox_manager.start_oauth_flow(redirect_uri)
                self._send_json({"auth_url": auth_url, "redirect_uri": redirect_uri})
                return

            if route == "/api/dropbox/disconnect":
                state = self.dropbox_manager.disconnect(
                    redirect_uri=self._get_dropbox_redirect_uri()
                )
                self._send_json(state)
                return

            if route == "/api/dropbox/create-folder":
                ns_override = str(data["shared_folder_id"]) if "shared_folder_id" in data else None
                try:
                    created = self.dropbox_manager.create_folder(
                        path=str(data.get("path", "")),
                        shared_folder_id=ns_override,
                    )
                    self._send_json({"folder": created})
                except DropboxPermissionError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                return

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

            if route == "/api/dropbox/process":
                folder_path = (
                    str(data["folder_path"])
                    if "folder_path" in data
                    else self.dropbox_manager.get_status(verify_shared_access=False).get("selected_input_folder", "")
                )
                if not str(folder_path).strip():
                    raise ValueError("Please select a Dropbox source folder first.")
                scan_jerseys = bool(data.get("scan_jerseys", True))
                use_thumbnails = bool(data.get("use_thumbnails", True))
                ns_override = str(data["shared_folder_id"]) if "shared_folder_id" in data else None
                self.session.start_dropbox_processing(
                    self.dropbox_manager,
                    folder_path=str(folder_path),
                    enable_jersey_ocr=scan_jerseys,
                    use_thumbnails=use_thumbnails,
                    shared_folder_id=ns_override,
                )
                self._send_json({"status": "processing_started"})
                return

            if route == "/api/browse-local-folder":
                try:
                    initial = str(data.get("initial_dir") or self.session.config.output_dir)
                    picker = getattr(self.server, "local_folder_picker", None)
                    if callable(picker):
                        selected = picker(initial)
                    else:
                        import tkinter as tk
                        from tkinter import filedialog

                        root = tk.Tk()
                        root.withdraw()
                        root.attributes("-topmost", True)
                        selected = filedialog.askdirectory(initialdir=initial, title="Choose Output Folder")
                        root.destroy()
                    self._send_json({"path": selected or ""})
                except Exception as exc:
                    self._send_json({"path": "", "error": f"Folder dialog unavailable: {exc}"})
                return

            if route == "/api/export-start":
                dest_type = str(data.get("destination_type", "local"))
                local_out = str(data["local_output_dir"]) if "local_output_dir" in data else None
                dbx_out = str(data["dropbox_output_folder"]) if "dropbox_output_folder" in data else None
                file_mode = str(data.get("file_mode", "copy"))
                ns_override = str(data["shared_folder_id"]) if "shared_folder_id" in data else None
                self.session.start_background_export(
                    destination_type=dest_type,
                    local_output_dir=local_out,
                    dropbox_output_folder=dbx_out,
                    file_mode=file_mode,
                    dropbox_manager=self.dropbox_manager,
                    shared_folder_id=ns_override,
                )
                self._send_json({"status": "export_started"})
                return

            if route == "/api/shutdown":
                self.session._cleanup_temp_dirs()
                self._send_json({"status": "shutting_down"})
                on_shutdown = getattr(self.server, "on_shutdown_callback", None)
                if callable(on_shutdown):
                    threading.Timer(0.1, on_shutdown).start()
                threading.Timer(0.15, self.server.shutdown).start()
                return

            if route == "/api/commit":
                if any(
                    k in data
                    for k in ("destination_type", "local_output_dir", "dropbox_output_folder", "file_mode")
                ):
                    result = self.session.commit_export(
                        destination_type=str(data.get("destination_type", "local")),
                        local_output_dir=str(data["local_output_dir"]) if "local_output_dir" in data else None,
                        dropbox_output_folder=str(data["dropbox_output_folder"]) if "dropbox_output_folder" in data else None,
                        file_mode=str(data.get("file_mode", "copy")),
                        dropbox_manager=self.dropbox_manager,
                        shared_folder_id=str(data["shared_folder_id"]) if "shared_folder_id" in data else None,
                    )
                else:
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
    dropbox_manager: DropboxManager | None = None,
) -> ThreadingHTTPServer:
    """Create a ThreadingHTTPServer bound to `(host, port)` for `session`."""

    class BoundHandler(CurationRequestHandler):
        pass

    BoundHandler.session = session
    BoundHandler.dropbox_manager = dropbox_manager or DropboxManager(session.config)
    server = ThreadingHTTPServer((host, port), BoundHandler)
    BoundHandler.server_port = server.server_address[1]
    server.dropbox_manager = BoundHandler.dropbox_manager  # type: ignore[attr-defined]
    server.on_shutdown_callback = None  # type: ignore[attr-defined]
    server.local_folder_picker = None  # type: ignore[attr-defined]
    return server


def bind_http_server_with_fallback(
    session: CurationSession,
    host: str = "127.0.0.1",
    preferred_port: int = 8080,
    dropbox_manager: DropboxManager | None = None,
) -> ThreadingHTTPServer:
    """
    Bind the local HTTP server to `preferred_port` or cleanly fall back to 8081, 8082,
    or an ephemeral port if the default port is already in use.
    """
    candidates: list[int] = []
    for port in (preferred_port, 8081, 8082, 0):
        if port not in candidates:
            candidates.append(port)

    last_exc: OSError | None = None
    for port in candidates:
        try:
            return create_http_server(
                session,
                host=host,
                port=port,
                dropbox_manager=dropbox_manager,
            )
        except OSError as exc:
            last_exc = exc
            continue
    if last_exc is not None:
        raise last_exc
    return create_http_server(session, host=host, port=0, dropbox_manager=dropbox_manager)


def launch_curation_ui(
    session: CurationSession,
    host: str = "127.0.0.1",
    port: int = 8080,
    open_browser: bool = True,
    show_control_window: bool = True,
) -> None:
    """Start the local PersonSort Review UI server and optionally show the desktop control window."""
    server = bind_http_server_with_fallback(session, host=host, preferred_port=port)
    actual_port = server.server_address[1]
    browser_host = "localhost" if host in ("127.0.0.1", "localhost") else host
    url = f"http://{browser_host}:{actual_port}"

    print("\n" + "=" * 64)
    print(f"  PersonSort Web UI v{APP_VERSION} is running!")
    print(f"  Open in browser : {url}")
    print("  Close the PersonSort control window or click 'Quit App' to exit cleanly.")
    print("=" * 64 + "\n")

    window: PersonSortControlWindow | None = None
    if show_control_window:
        try:
            window = PersonSortControlWindow(
                url=url,
                version=APP_VERSION,
                on_quit=lambda: threading.Thread(target=server.shutdown, daemon=True).start(),
                open_browser_on_start=open_browser,
            )
            server.on_shutdown_callback = window.request_close  # type: ignore[attr-defined]
            server.local_folder_picker = window.ask_directory  # type: ignore[attr-defined]
        except Exception as exc:
            print(f"Control window unavailable ({exc}); running in console mode.")
            window = None

    if window is not None:
        server_exited = threading.Event()

        def _serve() -> None:
            try:
                server.serve_forever()
            finally:
                server_exited.set()

        server_thread = threading.Thread(target=_serve, daemon=True)
        server_thread.start()
        try:
            window.run()
        except KeyboardInterrupt:
            print("\nShutting down PersonSort Web UI server...")
        finally:
            if not server_exited.is_set():
                try:
                    server.shutdown()
                except Exception:
                    pass
            server_thread.join(timeout=2.0)
            session._cleanup_temp_dirs()
            server.server_close()
    else:
        if open_browser:
            threading.Timer(0.4, lambda: webbrowser.open(url)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down PersonSort Web UI server...")
        finally:
            session._cleanup_temp_dirs()
            server.server_close()
