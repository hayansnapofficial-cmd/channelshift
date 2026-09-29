# Third-party software

The editor, schema model, template definitions, SQL/Java exporters, persistence and HTTP server are newly implemented for this repository. No drawDB source, frontend bundle, validators, images, fonts or legacy runtime are distributed here.

The stdio adapter uses the official Model Context Protocol Python SDK (`mcp==2.2.0`, https://github.com/modelcontextprotocol/python-sdk). Dependencies are installed separately by pip and are not vendored in the project wheel. Their copyright notices and licenses remain applicable. The release includes a dependency inventory and copies of license files present in the verified installation under `docs/dependency-licenses/`.

Python itself is separately installed software under its applicable PSF and third-party licenses. The standard library is not copied into the release. Generated schema/SQL/Java is derived from the user's schema and this repository's original templates, not from a bundled third-party application.

MIT licensing of this new implementation does not change the AGPL licensing of any previously installed drawDB-derived application. That application and its notices remain outside this repository. This engineering boundary is not a legal clearance opinion.

The development member-service integration additionally depends on `apify-client==2.5.1` and `httpx==0.28.1`. Additional installed dependency notices and SHA-256 hashes are preserved under `docs/service-dependency-licenses/`. This supplements the original release inventory; these libraries are installed by pip and are not vendored as runtime code.
