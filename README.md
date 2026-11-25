# PersonSort

Desktop and browser-based photo organizer that automatically groups sports and event photos into per-player folders using AI face recognition (`InsightFace` + `DBSCAN`), optional reference folders, and jersey number detection (`YOLOv8` + `EasyOCR`), with built-in support for both local files and shared Dropbox folders.

---

## Features

- **AI Face Grouping & Reference Matching**: Detects player faces while filtering out small background crowd faces (`--min-face-size`, `--det-thresh`), matches against optional reference player folders, and clusters unknown players automatically with seamless GPU-to-CPU fallback.
- **Jersey Number Detection**: Optional full-body detection and jersey number OCR (`YOLOv8` + `EasyOCR`) to sort photos even when a player's face is turned away or partially obscured.
- **Interactive Browser Review UI**: Review auto-grouped folders, toggle face/jersey bounding boxes, accept face-match suggestions, rename or merge player and `#` jersey folders, and inspect photos before exporting.
- **Native Desktop Control Window**: Lightweight Tkinter companion window that displays status and URL, provides one-click **Open PersonSort in Browser** and **Copy Link** actions, and cleanly stops the background server when closed.
- **Local & Dropbox Workflows**: Import local photos or `.zip` archives, or browse shared Dropbox folders (unsupported non-image files highlighted in red) with fast batch thumbnail previews, automatic local Dropbox Desktop detection, persistent OAuth 2 PKCE login stored in the OS credential manager (`keyring`), and Local or cloud-to-cloud Dropbox export (**Copy** or **Move**).
- **Automatic GitHub Update Notifications**: Checks `MicahJaffa/PersonSort` GitHub Releases on startup and displays both a top-left header indicator and a startup **Update Available** modal when a newer release is published.

---

## Quick Start (Windows Installer)

1. Download the latest **`PersonSort_Setup_x.x.x.exe`** installer from the [GitHub Releases](https://github.com/MicahJaffa/PersonSort/releases/latest) page.
2. Double-click the installer to install per-user under `%LOCALAPPDATA%\Programs\PersonSort` and create a Desktop shortcut (no Python or administrator password required).
3. **First Launch Security Prompt**: Because releases are packaged without a paid commercial certificate, Windows SmartScreen may appear on the very first run—click **More info** -> **Run anyway**.
4. **First Scan**: The first photo scan downloads the required face/jersey AI models once (~1–2 minutes). Every scan after that starts immediately.

For a non-technical walkthrough see [packaging/USER_SETUP_GUIDE.md](packaging/USER_SETUP_GUIDE.md).

---

## Running from Source (Developers)

### Prerequisites
- Python 3.9 or newer

### Installation

```powershell
git clone https://github.com/MicahJaffa/PersonSort.git
cd PersonSort

python -m venv PersonSort\env
.\PersonSort\env\Scripts\Activate.ps1

# Optional: install CPU-only PyTorch first to avoid large CUDA wheels
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

pip install -r PersonSort\requirements.txt
```

### Launch

Start the Desktop Control Window and interactive Web UI (default):

```powershell
python PersonSort\PersonSort.py
```

Run in headless CLI mode without opening the Web UI:

```powershell
python PersonSort\PersonSort.py --cli -i path\to\photos -o path\to\sorted_output --enable-jersey
```

### CLI Options

| Flag | Default | Description |
| --- | --- | --- |
| `-i`, `--input` | `input_photos` | Input directory containing photos to sort (CLI mode). |
| `-o`, `--output` | `sorted_output` | Output directory where per-player folders are created. |
| `-r`, `--reference` | `None` | Optional directory of known player reference folders or a prior output directory. |
| `--enable-jersey` | `False` | Enable full-body detection and jersey number OCR. |
| `--cpu-only` | `False` | Force CPU execution even if a GPU is available. |
| `--ui` / `--cli` | `--ui` | Launch the interactive Web UI (default) or run headless in the terminal. |
| `--port` | `8080` | Local HTTP port for the interactive Web UI server (falls back automatically if busy). |
| `--min-face-size` | `45` | Minimum face width and height in pixels (filters small background crowd faces). |
| `--det-thresh` | `0.55` | Minimum face detection confidence score (`0.1` to `0.99`). |
| `--sim-threshold` | `0.45` | Cosine similarity threshold for matching faces to reference players. |
| `--cluster-eps` | `0.50` | DBSCAN cosine distance threshold for clustering unknown faces (smaller = stricter). |
| `--min-samples` | `1` | Minimum photos required to form a cluster (`1` creates a folder for every appearance). |
| `--no-crops` | `False` | Disable saving `_reference_crop.jpg` preview thumbnails inside player folders. |
| `--dropbox-app-key` | From `.env` | Dropbox OAuth 2 App Key (PKCE public client). |
| `--dropbox-shared-folder-id` | From `.env` | Optional Dropbox shared folder namespace ID to pin browsing/exports to. |
| `--self-test` | `False` | Run the built-in end-to-end clustering and folder sorting test. |

---

## Dropbox Configuration

PersonSort uses Dropbox OAuth 2 **PKCE** as a public client, so no App Secret is needed:

1. In the [Dropbox App Console](https://www.dropbox.com/developers/apps), create a **Scoped access** app and enable **Allow public clients (Implicit Grant & PKCE)**.
2. Under **OAuth 2 -> Redirect URIs**, register:
   - `http://localhost:8080/dropbox/callback`
   - Optional fallback ports: `http://localhost:8081/dropbox/callback`, `http://localhost:8082/dropbox/callback`
3. Under **Permissions**, enable:
   - `files.metadata.read`
   - `files.content.read`
   - `files.content.write`
   - `sharing.read`
4. Create a local, gitignored `PersonSort/.env` file with your App Key (never commit real keys):

```env
PERSONSORT_DROPBOX_APP_KEY=your_dropbox_app_key_here
PERSONSORT_DROPBOX_SHARED_FOLDER_ID=
# Optional override if forking releases to another GitHub repo:
# PERSONSORT_GITHUB_REPO=MicahJaffa/PersonSort
```

Once a user signs in via **Connect Dropbox**, refresh tokens are stored securely in the OS credential manager (`keyring` under service `PersonSort.Dropbox`) rather than in plain text files.

---

## Building the Windows Installer & Publishing Updates

### Prerequisites
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`ISCC.exe`) installed to compile the `.exe` installer. PyInstaller is installed automatically by the build script.

### Build Commands (PowerShell)

First build (creates a clean CPU-only `packaging\build_env` to keep the installer compact, builds PyInstaller `--onedir`, and compiles `PersonSort_Setup_<version>.exe`):

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\build_windows.ps1 -CreateCpuVenv -Version 1.0.3
```

Subsequent rebuilds reusing the existing `packaging\build_env`:

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\build_windows.ps1 -Version 1.0.3
```

Other build options:
- Recompile only the Inno Setup installer from an existing `packaging\dist\PersonSort` folder:
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\packaging\build_windows.ps1 -InstallerOnly -Version 1.0.3
  ```
- Pass `-PrebundleModels` to bundle already-downloaded AI model weights from `PersonSort\.models_cache` directly inside the distribution.

Compiled installers are written to `packaging\Output\PersonSort_Setup_<version>.exe`.

### Publishing a GitHub Release Update

1. Update `APP_VERSION` in [PersonSort/config.py](PersonSort/config.py) (for example, `"1.0.4"`).
2. Confirm `TEST_UPDATE = False` in [PersonSort/config.py](PersonSort/config.py). *(You can temporarily set `TEST_UPDATE = True` while running from source to preview both the top-left update pill and the startup **Update Available** modal.)*
3. Build the matching installer (`-Version 1.0.4`).
4. Create a new GitHub Release tagged `v1.0.4` and upload `packaging\Output\PersonSort_Setup_1.0.4.exe` as a release asset. Existing installations running an older version will automatically notify users on startup.

---

## Project Structure

```text
PersonSort/
├── README.md
├── .gitignore
├── PersonSort/
│   ├── PersonSort.py             # Application entry point & CLI/UI launcher
│   ├── config.py                 # Version metadata, paths, .env loading & CLI arguments
│   ├── web_ui.py                 # Local HTTP server & browser review UI
│   ├── desktop_window.py         # Native Tkinter control window & folder picker bridge
│   ├── session_state.py          # Interactive scan, cluster curation & export state
│   ├── face_detector.py          # InsightFace detection & embeddings (GPU/CPU fallback)
│   ├── body_jersey_detector.py   # YOLOv8 body detection & EasyOCR jersey numbers
│   ├── matcher.py                # Reference matching & DBSCAN face clustering
│   ├── image_loader.py           # Local image/ZIP loading & Dropbox Desktop detection
│   ├── organizer.py              # Local & Dropbox folder export routines
│   ├── dropbox_client.py         # Dropbox OAuth 2 PKCE, thumbnail batching & file ops
│   ├── updater.py                # GitHub Releases update checker & TEST_UPDATE mock support
│   ├── models.py                 # Shared dataclasses & data structures
│   └── requirements.txt          # Python dependencies
└── packaging/
    ├── build_windows.ps1         # Automated CPU build & Inno Setup compiler script
    ├── PersonSort.spec           # PyInstaller --onedir / --noconsole configuration
    ├── PersonSort.iss            # Inno Setup 6 per-user Windows installer script
    └── USER_SETUP_GUIDE.md       # Non-technical team member quick-start guide
```
