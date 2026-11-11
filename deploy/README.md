# Ostraka as a team server

One Ostraka on a central host, used by the team through their browsers, the desktop apps' "Connect to your team's Ostraka", and the `ost` responder client.

## Linux

Docker Engine with the compose plugin (Docker's own packages; on Rocky, RHEL and Alma, not Podman), then from a source tree:

```bash
sudo ./deploy/linux/install.sh ostraka.example.org --admin you@example.org
```

That copies the tree to `/opt/ostraka` (`--dir` for elsewhere: it must be new, empty or an earlier Ostraka tree; `--source` takes a tree, a `.tar.gz` of one or a git URL), writes `.env` (mode 600) with random `POSTGRES_PASSWORD` and `OSTRAKA_SECRET_KEY`, `OSTRAKA_PUBLIC_URL=https://ostraka.example.org` and `COMPOSE_PROJECT_NAME=ostraka`, starts the stack, waits for `/api/health`, and prints the first admin's invitation link. Running it again keeps the secrets: changing `OSTRAKA_SECRET_KEY` would make every stored TOTP secret unreadable. `deploy/linux/install.sh --help` lists the options; `ostraka server init` runs it with the same arguments.

`COMPOSE_PROJECT_NAME` in `.env` keeps the stack one compose project, `ostraka`, whether setup, an admin's `docker compose` in the tree or the `ostraka` command runs it; one already in `.env` is kept, since the data volumes are named after it. Every service is `restart: unless-stopped`, so Docker brings the stack back after a reboot.

Ostraka itself listens on `http://127.0.0.1:24747` only. HTTPS comes from a reverse proxy in front of it, either your own (below) or Caddy in the stack with `--caddy`.

### Caddy in the stack

```bash
sudo ./deploy/linux/install.sh ostraka.example.org --admin you@example.org --caddy
```

`--caddy` (or `--caddy acme`) gets a Let's Encrypt certificate, so the name must resolve to this host and reach it on 80 and 443. `--caddy internal` uses Caddy's own CA instead; clients then need its root certificate:

```bash
cd /opt/ostraka && docker compose exec -T caddy cat /data/caddy/pki/authorities/local/root.crt > ostraka-root.crt
```

Distribute it through MDM, Group Policy or `update-ca-certificates`; `ost` takes it as `OSTRAKA_CA_BUNDLE`. Caddy runs from `docker-compose.caddy.yml`, which setup adds through `COMPOSE_FILE` in `.env`, so `docker compose` and `ostraka start` in the tree include it from then on. When firewalld or ufw is active, setup opens 80 and 443. `--no-caddy` takes it out again.

### Your own reverse proxy

Ostraka needs the WebSocket at `/ws` upgraded, uploads up to 100 MB, and `OSTRAKA_PUBLIC_URL` set to the address the proxy serves (sign-in cookies are marked Secure when it starts `https://`).

Caddy on the host (`/etc/caddy/Caddyfile`), which also gets the certificate:

```caddy
ostraka.example.org {
    reverse_proxy 127.0.0.1:24747
}
```

nginx:

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name ostraka.example.org;
    ssl_certificate     /etc/ssl/ostraka/fullchain.pem;
    ssl_certificate_key /etc/ssl/ostraka/privkey.pem;
    client_max_body_size 100m;

    location / {
        proxy_pass http://127.0.0.1:24747;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_read_timeout 1h;
    }
}

# in the http block
map $http_upgrade $connection_upgrade { default upgrade; '' close; }

server {
    listen 80;
    server_name ostraka.example.org;
    return 301 https://$host$request_uri;
}
```

On SELinux hosts, nginx needs `setsebool -P httpd_can_network_connect 1` to reach the loopback port.

### Removing it

```bash
sudo ./deploy/linux/uninstall.sh            # stop the stack, keep the data
sudo ./deploy/linux/uninstall.sh --purge    # and delete the volumes and .env, after asking
```

It runs `docker compose down` in `/opt/ostraka` (`--dir` for elsewhere) under the project in `.env`. `--purge` also removes the stack's volumes (the database, Redis and Caddy's certificates) and `.env` with its secrets; it asks first, and `--yes` answers for it where there's no terminal. The code dir is always left in place, as are firewall rules opened for Caddy; remove them by hand if you want them gone.

## Windows

Windows Server 2022 or 2025, or Windows 10/11, with WSL 2 and an Ubuntu distribution (`wsl --install -d Ubuntu-24.04`). From PowerShell in a source tree cloned with LF line endings:

```powershell
.\deploy\windows\install-service.ps1 -Url ostraka.example.org -Admin you@example.org -Caddy acme -InstallDocker
```

Run it as administrator. It checks WSL and the distribution, installs Docker Engine inside it with `-InstallDocker` (Docker Desktop isn't licensed for Windows Server), then runs `deploy/linux/install.sh` there as root.

WSL starts no distribution at boot and stops one nobody is using, so it then registers a Scheduled Task, `Ostraka`, that runs at startup whether anyone signs in or not. It starts the distribution and stays attached to it, Docker (under systemd, which `-InstallDocker` turns on) starts, and the stack comes back by its restart policy. WSL distributions belong to a user, so the task runs as the account that owns the distribution rather than SYSTEM, with its password stored in the task: it asks for it, or takes `-TaskCredential`. The task and its script live in `%ProgramData%\Ostraka`, writable by administrators only. `-NoService` leaves the task out, and Ostraka then runs only while WSL happens to be up.

At the end it says how to reach the server from other machines. WSL 2 is behind its own NAT, so that's a reverse proxy on Windows to `127.0.0.1:24747`, mirrored networking on Windows 11, or on Windows Server with Caddy, `-PortProxy`: 80 and 443 on every Windows address are forwarded to the distribution and allowed through Windows Firewall, and the boot task points the proxy at the distribution's new address at each startup.

### Removing it

```powershell
.\deploy\windows\uninstall-service.ps1            # stop the stack, keep the data
.\deploy\windows\uninstall-service.ps1 -Purge     # and delete the volumes and .env, after asking
```

From an administrator PowerShell. It runs `deploy/linux/uninstall.sh` inside the distribution (`-Purge` and `-Yes` passed on as `--purge` and `--yes`), then removes the boot task, the port proxy and its firewall rule, and `%ProgramData%\Ostraka`. A purge that's declined leaves everything as it was. The distribution, Docker in it and the code dir stay.
