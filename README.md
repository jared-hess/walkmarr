# Walkmarr

Walkmarr exports media from *arr-managed libraries into portable-device-ready
mirror libraries, starting with iPod Classic-compatible video exports.

## What Walkmarr does

- Reads TV metadata and file paths from Sonarr.
- Reads movie metadata and file paths from Radarr.
- Maps provider paths to local paths (WSL/Docker-friendly mappings).
- Converts to MP4 (H.264 + AAC) with profile-driven settings.
- Tags MP4/M4V outputs with AtomicParsley for iTunes/iPod metadata.
- Never modifies source media files.

## What Walkmarr does not do (v1)

- No TUI, web app, or daemon.
- No automatic *arr hook scripts.
- No Lidarr/music pipeline.
- No artwork download/embed pipeline (beyond future optional extension).
- No source/output cleanup automation.
- No Plex/Jellyfin/Tdarr/FileFlows integration.

## Requirements

- Python 3.12+
- `uv`
- System binaries:
  - `ffmpeg`
  - `ffprobe`
  - `AtomicParsley` (or `atomicparsley`)

Install required system binaries:

```bash
sudo apt update
sudo apt install ffmpeg atomicparsley
```

## Install with uv

```bash
git clone https://github.com/USER/walkmarr.git
cd walkmarr

uv sync
```

## Example config

```bash
uv run walkmarr config init
cp ~/.config/walkmarr/.env.example ~/.config/walkmarr/.env
```

Walkmarr also auto-loads a `.env` file located next to the config file (for
example `./.env` when using `./walkmarr.yml`). Existing shell env vars take
precedence.

You can bootstrap with prompts:

```bash
uv run walkmarr config init --prompt
```

Prompt terminology:

- `Provider ... root path`: the path Sonarr/Radarr return in API responses.
- `Local ... root path`: where Walkmarr can access that same media on this machine.
- You only need one mapping per media type (shows and movies).

You can still export variables directly in your shell if preferred:

```bash
export SONARR_API_KEY="..."
export RADARR_API_KEY="..."
```

`walkmarr` looks for config in this order unless `--config` is passed:

1. `./walkmarr.yml`
2. `./config.yml`
3. `~/.config/walkmarr/config.yml`

## Quickstart

```bash
uv run walkmarr config check
uv run walkmarr sonarr list
uv run walkmarr sonarr convert "Futurama" --dry-run
uv run walkmarr sonarr convert "Futurama"
```

Radarr example:

```bash
uv run walkmarr radarr list
uv run walkmarr radarr convert "American Psycho" --dry-run
uv run walkmarr radarr convert "American Psycho"
```

## Dry-run behavior

Dry-run never writes files or creates directories. It prints:

- provider
- source path
- mapped local path
- output path
- selected profile
- metadata to be written
- ffmpeg command
- AtomicParsley command

## Safety notes

- Source files are never modified.
- Walkmarr writes to `*.tmp.mp4`, tags that temp file, and only then renames
  it to the final output.
- Existing outputs are skipped unless `--overwrite` is set.
- iTunes import/sync is intentionally separate from Walkmarr.
