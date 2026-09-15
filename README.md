# 3LS Server

3LS Server is designed to self-host the catalog and delivery of your own
Nintendo 3DS CIA backups over a local network. It is the backend for 3LS, a fork
of 3HS, and remains compatible with the existing 3HS binary protocol and client
expectations.

The server provides a browsable title catalog, resumable downloads, catalog
management through Django admin, and revocable HSAPI token authentication. No
CIA packages are included.

The project is intentionally focused: a compact Django service for running a
private, 3HS-compatible library on infrastructure you control.

See [Deploying 3LS Server](DEPLOYMENT.md) for the Docker-based setup and service
commands.

## License

Copyright (C) 2026 pelarejo

The 3LS backend source code is licensed under the
[GNU General Public License v3.0 or later](LICENSE). It is distributed without
any warranty; see the license for details.

Catalog metadata, CIA backups, and other content you add are not covered merely
by the backend source code license. Those materials remain subject to their own
applicable rights and license terms.
