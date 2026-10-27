# Install MTM PLAYER

## Scope

MTM PLAYER supports a standalone Windows application and a macOS command launcher. It does not require a Codex plugin. The release includes this guide so an AI coding session can install and verify the application. Team packs remain separate packages.

## macOS package

Run `npm run package:macos` from `mtm-player/`. A build on macOS bundles the current Node executable for the build machine's architecture (arm64 or x64). A build on Windows produces a portable macOS archive requiring Node.js 20+ installed separately; it never bundles Windows binaries. Packages are unsigned and are not notarized.

Extract the entire `.tar.gz` archive and open `MTM Player.command`. Install Git and authenticate the Codex CLI first. The launcher checks `/opt/homebrew/bin`, `/usr/local/bin`, and the inherited PATH. If the CLI is elsewhere, enter its absolute path in Init and refresh models. Player stores its state under `~/Library/Application Support/MTM Player/`. Open `http://127.0.0.1:54321/manage` if the browser does not open.

Team packs must be installed separately. Existing Windows PowerShell installers are not native macOS installers; this Player package does not claim to port those installers. Preserve the team workspace, `.codex/`, `.agents/`, and pack assets when preparing a Mac workspace. Before distributing for competition, verify Init, preparation, start, chat, stop, restart recovery, and snapshot delivery on both Apple Silicon and Intel. Windows tests cannot establish macOS runtime compatibility.

## Required artifacts

- `mtm-player-<version>.zip`: extract the complete archive.
- `player-app/` inside it: copy the entire directory containing mtm-player.exe, node.exe and app/. A lone executable is insufficient.
- The requested team's compatible runtime package and its installer, if not already installed.

Use artifacts supplied by the organizer. Do not invent download URLs. If an artifact is missing, request its location. When working in the source repository, building the Windows application requires `node dev/build-player-app.mjs` from mtm-player/.

## Procedure for Codex

1. Inspect installed versions and query `http://127.0.0.1:54321/health`. Check `pwsh --version` and `codex --version`. The Codex CLI must be authenticated. The app bundles Node.js; a team installer may separately require Node.js and npm. Do not print authentication material.
2. Before stopping a running Player/runtime for upgrade, ask the operator. Existing explicit approval for this installation is sufficient. Stop through the app; verify LOCAL_APP_STOPPED and the listener closing. Do not kill all Node/Codex processes. Preserve rounds, local user-profile data, credentials, logs, challenge source and team runtime.
3. Extract the release and copy the complete `player-app` folder to an agreed location, for example `D:\mtm\MTM-PLAYER\<version>`. Prefer a new version directory. Never mix app/ or node.exe from different builds. The application is portable and has no registry or plugin installation step.
4. If needed, install the requested team pack separately using its own install.ps1 and documented TeamWorkspace parameter. Verify team ID/name/version. Do not substitute HNK for HNKSUPER or another team.
5. Launch the installed mtm-player.exe WITHOUT arguments. It starts the server and opens Manage. Workspace selection happens in the web UI. If using Start-Process, use -WindowStyle Hidden.
6. Verify `/health` reports LOCAL_APP_READY and the intended version. Open `/manage` visibly. Select the team workspace with Browse/Load workspace to see saved rounds. Init uses that workspace and loads its team pack. Do not initialize a new match merely to verify installation; Resume continues a saved eligible round.
7. Report the application version, installed executable path, management URL and any unverified prerequisite.

## Stop and recovery

Stop Team Orchestrator stops the owned runtime while keeping the web server available. Shut down MTM PLAYER also closes the server. Both preserve saved round data. Resume is available for an operator-stopped current round whose official match has not finished or been cancelled. Saved bootstrap secrets stay in local server storage; do not use DPAPI or browser storage. A rotated or invalid credential requires replacement.
