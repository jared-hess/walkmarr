# Walkmarr v1 Specification

Walkmarr exports selected media from Sonarr and Radarr into a separate portable
library using ffmpeg conversion and AtomicParsley tagging.

This repository follows the v1 scope described in the project request:

- CLI backend only (`walkmarr` via Click)
- YAML config, path mapping, profile-based conversion
- Sonarr TV and Radarr movie export support
- Safe write flow via temporary output + atomic rename
- Unit tests with pytest

See `README.md` for usage and setup details.
