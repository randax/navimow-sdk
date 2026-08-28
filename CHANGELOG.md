# Changelog

## 0.2.0

- Support standard CPython 3.9.2 through 3.14.x, including Raspberry Pi OS on
  ARM32 and ARM64.
- Resolve Python 3.9 annotations through `typing.get_type_hints()` and adapt
  event-loop ownership to Python 3.14 behavior.
- Add caller-provided loop support, permanent loop affinity, cross-loop guards,
  and thread-safe MQTT callback dispatch.
- Move MQTT callbacks to Paho VERSION2 and constrain runtime dependencies to the
  Python 3.9-compatible aiohttp release line.
- Add an offline compatibility suite and Python/ARM GitHub Actions matrices.
- Translate maintained comments and docstrings to Nynorsk while preserving
  runtime messages and protocol values.
- Align the package version, GPL-3.0-only declaration, and project URLs with the
  maintained repository.
