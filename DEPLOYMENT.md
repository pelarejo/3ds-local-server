# Deploying 3LS Server

3LS Server runs as a single Docker Compose service for a trusted local network.
Docker Desktop (or Docker Engine with Compose) is the only host dependency.

## First setup

From this directory, run:

```sh
./threels setup
```

Setup detects a likely LAN IPv4 address and asks you to confirm the hostname or
LAN IP that clients use. If detection is unavailable, you must enter a reachable
address; `localhost` is not presented as an on-device default. Setup also asks
for the published port and the host content, system, and data directories. Their
defaults are `./content`, `./system`, and `./data`, and paths containing spaces
are supported. It creates the selected directories, writes a private
`.threels.env` with their resolved absolute paths and a persistent random Django
secret, builds the image, and starts the server.
The private config is mode `0600`, ignored by Git, and contains no client token.

Create the administrator used at `/admin/`:

```sh
./threels create-admin
```

Create a client login and one-time HSAPI token:

```sh
./threels create-client
```

The command may also be scripted with `--username`, `--token-name`, and optional
`--rotate`. Rotation revokes that user's prior active tokens. Copy the displayed
token immediately: the server stores only its hash and the helper does not save
it. Download the compatible client separately, then enter the server IP or
hostname, port, username, and token on the device.

## Daily commands

```sh
./threels start
./threels stop
./threels restart
./threels status
./threels logs
```

The configured content directory is mounted read-write for your CIA backups and
manual admin imports. The data directory contains the SQLite database and
collected admin static files. The system directory is mounted read-only for
optional source metadata. Back up the data directory consistently while the
service is stopped, and back up content separately.

Container startup applies migrations, refreshes the stable catalog taxonomy,
and collects static files. It never creates users, issues tokens, imports
metadata, or synchronizes content automatically. Gunicorn runs one worker by
default to suit this SQLite-backed local deployment.

To change the port or allowed client address, stop the service, edit the matching
values in `.threels.env`, and start it again. Keep `DJANGO_DEBUG=false` and do not
share `.threels.env`.
