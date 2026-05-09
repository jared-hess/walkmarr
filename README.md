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

## What Walkmarr does not do

- No web app or daemon.
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
  - `fdkaac`
  - `AtomicParsley` (or `atomicparsley`)

Install required system binaries:

```bash
sudo apt update
sudo apt install ffmpeg fdkaac atomicparsley
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
```

Walkmarr also auto-loads a `.env` file located next to the config file (for
example `./.env` when using `./walkmarr.yml`). Existing shell env vars take
precedence.

If you use `api_key_env` in config, create the sibling `.env` file yourself or
export the variables in your shell.

Staging defaults to `auto`: Walkmarr detects network-like source mounts and
copies source media to local temp storage before probing/conversion. You can
set `staging.mode` to `always` or `never` in config.

For one-off runs, you can override this with `--staging-mode auto|always|never`
on `sonarr convert` and `radarr convert`.

Queue defaults:

- `queue.workers: 1` (v2 supports one active worker)
- `queue.continue_on_error: true`
- `queue.start_paused: false`
- `queue.default_mode: missing_only`
- `queue.remember_completed_until_exit: true`

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

Launch the TUI queue workflow:

```bash
uv run walkmarr tui
```

TUI key highlights:

- `tab` cycle focus: media -> details -> queue -> log -> search
- `j` / `k` move selection down/up in focused media or queue pane
- `a` add selected media as missing-only queue job
- `A` add selected media as overwrite queue job (confirmation modal)
- `d` add selected media as dry-run queue job
- `space` pause/resume queue
- `x` cancel current queue item
- `delete` remove selected pending queue item
- `u` move selected queue item up
- `J` / `K` move selected queue item down/up
- `C` clear completed queue items
- `X` clear pending queue items (confirmation modal)
- `p` toggle provider Sonarr/Radarr

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

## Debugging failed conversions

When a conversion fails, Walkmarr deletes any partial temp files (WAV, M4A,
partial MP4) by default. To keep them for inspection, set this in your config:

```yaml
debug:
  keep_failed_temps: true
```

Or pass `--keep-temp` on the CLI:

```bash
uv run walkmarr sonarr convert "Futurama" --keep-temp
```

Only files from failed conversions are kept. Successful outputs follow the
normal write-then-rename flow and are not affected by this flag.

## Safety notes

- Source files are never modified.
- Walkmarr writes to `*.tmp.mp4`, tags that temp file, and only then renames
  it to the final output.
- Existing outputs are skipped unless `--overwrite` is set.
- iTunes import/sync is intentionally separate from Walkmarr.
