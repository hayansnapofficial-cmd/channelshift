# Dependency license snapshot

ChannelShift 0.1.0 depends directly on the official [MCP Python SDK 2.2.0](https://pypi.org/project/mcp/2.2.0/) (MIT). This directory preserves the original license and notice files found in the verified installation of that SDK and its active transitive runtime dependencies. The dependency libraries are installed separately by pip; their source code and binaries are not vendored into ChannelShift.

Snapshot environment: CPython 3.12.10, win32, AMD64. The table contains 29 runtime distributions and 49 copied notice files. SHA-256 checksums, upstream URLs, active dependency edges, metadata hashes and file-level installed RECORD checks are in [dependency-inventory.json](../dependency-inventory.json).

The snapshot follows installed `Requires-Dist` requirements, platform/Python markers, and required extras (including `PyJWT[crypto]`). Optional SDK CLI/rich extras were not requested. A later installation or another platform can resolve different transitive versions; this inventory is not a lockfile.

## Component-specific terms

`pywin32` reports `PSF` in its legacy package metadata, but its installed distribution carries additional component licenses. In particular, `adodbapi/license.txt` contains LGPL 2.1, Win32/Pythonwin have BSD-style notices, IDLE has Python license terms, Scintilla has its own permissive notice, and MAPI has MIT notices. All identified notice files are retained, including duplicate copies present in distribution metadata and installed component directories. ChannelShift does not import adodbapi or bundle pywin32 code. Do not summarize this entire dependency set as MIT-only or as uniformly permissive.

`cryptography` declares `Apache-2.0 OR BSD-3-Clause`; both complete alternatives are retained. The table reports package declarations and these documented observations. It is not a complete recursive audit of native/binary components.

## Runtime distributions

| Distribution | Version | Declared license / component note | Role | Notice files |
| --- | --- | --- | --- | --- |
| [annotated-types](https://pypi.org/project/annotated-types/0.8.0/) | 0.8.0 | MIT | Transitive | [1](annotated-types-0.8.0/) |
| [anyio](https://pypi.org/project/anyio/4.15.1/) | 4.15.1 | MIT | Transitive | [1](anyio-4.15.1/) |
| [attrs](https://pypi.org/project/attrs/26.1.0/) | 26.1.0 | MIT | Transitive | [1](attrs-26.1.0/) |
| [cffi](https://pypi.org/project/cffi/2.1.1/) | 2.1.1 | MIT-0 | Transitive | [1](cffi-2.1.1/) |
| [click](https://pypi.org/project/click/8.5.0/) | 8.5.0 | BSD-3-Clause | Transitive | [1](click-8.5.0/) |
| [cryptography](https://pypi.org/project/cryptography/50.0.1/) | 50.0.1 | Apache-2.0 OR BSD-3-Clause | Transitive | [3](cryptography-50.0.1/) |
| [h11](https://pypi.org/project/h11/0.16.0/) | 0.16.0 | MIT | Transitive | [1](h11-0.16.0/) |
| [httpcore2](https://pypi.org/project/httpcore2/2.13.1/) | 2.13.1 | BSD-3-Clause | Transitive | [1](httpcore2-2.13.1/) |
| [httpx2](https://pypi.org/project/httpx2/2.13.1/) | 2.13.1 | BSD-3-Clause | Transitive | [1](httpx2-2.13.1/) |
| [idna](https://pypi.org/project/idna/3.20/) | 3.20 | BSD-3-Clause | Transitive | [1](idna-3.20/) |
| [jsonschema](https://pypi.org/project/jsonschema/4.26.0/) | 4.26.0 | MIT | Transitive | [1](jsonschema-4.26.0/) |
| [jsonschema-specifications](https://pypi.org/project/jsonschema-specifications/2025.9.1/) | 2025.9.1 | MIT | Transitive | [1](jsonschema-specifications-2025.9.1/) |
| [mcp](https://pypi.org/project/mcp/2.2.0/) | 2.2.0 | MIT | Direct SDK | [1](mcp-2.2.0/) |
| [mcp-types](https://pypi.org/project/mcp-types/2.2.0/) | 2.2.0 | MIT | Transitive | [1](mcp-types-2.2.0/) |
| [opentelemetry-api](https://pypi.org/project/opentelemetry-api/1.45.0/) | 1.45.0 | Apache-2.0 | Transitive | [1](opentelemetry-api-1.45.0/) |
| [pycparser](https://pypi.org/project/pycparser/3.0/) | 3.0 | BSD-3-Clause | Transitive | [1](pycparser-3.0/) |
| [pydantic](https://pypi.org/project/pydantic/2.13.5/) | 2.13.5 | MIT | Transitive | [1](pydantic-2.13.5/) |
| [pydantic_core](https://pypi.org/project/pydantic-core/2.46.5/) | 2.46.5 | MIT | Transitive | [1](pydantic-core-2.46.5/) |
| [PyJWT](https://pypi.org/project/pyjwt/2.15.1/) | 2.15.1 | MIT | Transitive | [2](pyjwt-2.15.1/) |
| [python-multipart](https://pypi.org/project/python-multipart/0.0.32/) | 0.0.32 | Apache-2.0 | Transitive | [1](python-multipart-0.0.32/) |
| [pywin32](https://pypi.org/project/pywin32/312/) | 312 | PSF metadata; multiple component licenses, including LGPL 2.1 | Transitive | [17](pywin32-312/) |
| [referencing](https://pypi.org/project/referencing/0.37.0/) | 0.37.0 | MIT | Transitive | [1](referencing-0.37.0/) |
| [rpds-py](https://pypi.org/project/rpds-py/2026.6.3/) | 2026.6.3 | MIT | Transitive | [1](rpds-py-2026.6.3/) |
| [sse-starlette](https://pypi.org/project/sse-starlette/3.5.0/) | 3.5.0 | BSD-3-Clause | Transitive | [2](sse-starlette-3.5.0/) |
| [starlette](https://pypi.org/project/starlette/1.7.0/) | 1.7.0 | BSD-3-Clause | Transitive | [1](starlette-1.7.0/) |
| [truststore](https://pypi.org/project/truststore/0.10.4/) | 0.10.4 | MIT | Transitive | [1](truststore-0.10.4/) |
| [typing_extensions](https://pypi.org/project/typing-extensions/4.16.0/) | 4.16.0 | PSF-2.0 | Transitive | [1](typing-extensions-4.16.0/) |
| [typing-inspection](https://pypi.org/project/typing-inspection/0.4.4/) | 0.4.4 | MIT | Transitive | [1](typing-inspection-0.4.4/) |
| [uvicorn](https://pypi.org/project/uvicorn/0.54.0/) | 0.54.0 | BSD-3-Clause | Transitive | [1](uvicorn-0.54.0/) |

## Build and installer tooling

The following installed tools are outside the active application runtime closure and are recorded separately in the inventory. Their code and notices are not copied here:

| Tool | Version | Declared license |
| --- | --- | --- |
| [build](https://pypi.org/project/build/1.6.1/) | 1.6.1 | MIT |
| [colorama](https://pypi.org/project/colorama/0.4.6/) | 0.4.6 | BSD-3-Clause (installed text; no SPDX metadata) |
| [packaging](https://pypi.org/project/packaging/26.3/) | 26.3 | Apache-2.0 OR BSD-2-Clause |
| [pip](https://pypi.org/project/pip/25.0.1/) | 25.0.1 | MIT |
| [pyproject_hooks](https://pypi.org/project/pyproject-hooks/1.3.3/) | 1.3.3 | MIT |
| [setuptools](https://pypi.org/project/setuptools/84.0.0/) | 84.0.0 | MIT |
| [wheel](https://pypi.org/project/wheel/0.48.0/) | 0.48.0 | MIT |

`setuptools>=77` is the declared isolated-build requirement. Its installed verification version is listed above; isolated builds may select a different compatible version.

## Evidence and limits

Copies are byte-for-byte from the installed distributions. Each copied file has a SHA-256 hash and its distribution-relative source path in the inventory; when an installed RECORD SHA-256 is present, it was checked against the file. Relative paths are used throughout to avoid publishing local user paths. Package project URLs and version-specific PyPI pages provide upstream references. Installed RECORD verification is not a claim that the complete upstream wheel was independently attested.

Python itself is separately installed under its applicable PSF and third-party licenses. No interpreter, standard-library copy, old drawDB application, frontend dependency, font or external asset is included in this snapshot. License notices apply to their respective components. This documentation does not claim copyright immunity, legal clearance, or formal clean-room certification.
