# Running the factory on macOS, Linux and Windows

Everything runs in containers under Podman, so the steps differ only in how you install Podman. The host needs Podman, podman-compose,
make, git and openssl: no Python, Node or database on the host. Windows runs the same steps inside WSL 2.

| What you get | Address |
|---|---|
| Dashboard | http://localhost:3000 |
| 3D view | http://localhost:5173 |
| API (with interactive docs) | http://localhost:8000/docs |
| Workflows from Temporal (read-only) | Dashboard → Workflows; the full Temporal UI is linked there |

Every port is bound to `127.0.0.1`: nothing is reachable from other machines.

**Machine:** 4 CPUs, 8 GB of memory for containers and 40 GB of free disk is comfortable. The first `make up` downloads and builds about
15 images and takes 10 to 20 minutes; later runs take a minute or two.

## 1. Install Podman

### macOS (Apple silicon or Intel)

```sh
brew install podman podman-compose      # https://brew.sh; make, git and openssl come with the Xcode command line tools
podman machine init --cpus 4 --memory 8192 --disk-size 60
podman machine start
```

Podman on macOS runs a small Linux VM. Start it again after a reboot with `podman machine start`. To give it more memory later:
`podman machine stop && podman machine set --memory 12288 && podman machine start`.

### Linux

Ubuntu 24.04 or Debian 12:

```sh
sudo apt update && sudo apt install -y podman make git openssl pipx
pipx install podman-compose && pipx ensurepath   # the apt podman-compose is too old; open a new terminal after ensurepath
```

Fedora, RHEL 9 or Rocky:

```sh
sudo dnf install -y podman podman-compose make git openssl
```

Then, on any distribution, start Podman's API socket for your user. The factory starts each agent's sandbox through it:

```sh
systemctl --user enable --now podman.socket
loginctl enable-linger "$USER"     # keeps the socket and the stack running after you log out
```

Run Podman as your normal user (rootless). Do not use `sudo` for any `make` command.

### Windows 10 or 11

The factory runs inside WSL 2 (a Linux system built into Windows). Your Windows browser reaches it at the same `localhost` addresses.

1. In PowerShell as administrator, install Ubuntu, then restart if asked:

   ```powershell
   wsl --install -d Ubuntu-24.04
   ```

2. Open **Ubuntu** from the Start menu and create your Linux user. Turn on systemd, which the Podman socket needs:

   ```sh
   printf '[boot]\nsystemd=true\n' | sudo tee /etc/wsl.conf
   ```

   In PowerShell: `wsl --shutdown`, then open Ubuntu again.

3. Give WSL enough memory: create `%UserProfile%\.wslconfig` in Windows with

   ```ini
   [wsl2]
   memory=8GB
   processors=4
   ```

   and run `wsl --shutdown` once more.

4. In Ubuntu, follow the **Linux → Ubuntu** steps above, including `systemctl --user enable --now podman.socket`.

5. Keep the repository inside Linux (`~/sif`), not under `/mnt/c/...`. Builds on the Windows drive are many times slower and file
   permissions break. Open it from Windows as `\\wsl$\Ubuntu-24.04\home\<you>\sif`, or with VS Code's WSL extension (`code .` in Ubuntu).

Podman Desktop for Windows is not needed. If you already use it, run the commands in its WSL machine the same way.

## 2. First run (all systems)

```sh
git clone https://github.com/kuttybuski/sis-factory.git && cd sis-factory
cp .env.example .env
make init        # adds generated secrets and this machine's Podman socket to .env; never overwrites a value you set
make up          # builds and starts the stack
```

Open the dashboard at http://localhost:3000 and the 3D view at http://localhost:5173. Out of the box nothing touches Jira or GitHub:

- **Demo agent.** `SIF_AGENT=scripted` calls no model and costs nothing. It only follows lines like
  `edit src/app.py: replace "a" with "b"` in a ticket.
- **Dry run.** `SIF_DRY_RUN=true`: jira-connector reads Jira but only logs what it would do.
- **Something to look at:** press **Demo** in the 3D view, or `make seed` for sample tasks on the dashboard (open it with `?demo=1`;
  `make unseed` removes them).

## 3. Connect it to real work

Each step changes what the factory does outside your machine, so take them in order:

1. **Jira and GitHub.** Fill in `JIRA_*` and `GITHUB_TOKEN` in `.env`, or add a team and its connections on **Admin → Teams** and
   **Admin → Connections** (docs/connections.md). With dry run still on, `make logs s=jira-connector` shows which tickets it would pick up.
2. **The real agent.** Set `SIF_AGENT=claude`, `INSTALL_CLAUDE=true`, `LLM_UPSTREAM_URL` (for example `https://api.anthropic.com`),
   `LLM_UPSTREAM_KEY` and `MODEL_IMPLEMENTATION` (default `claude-haiku-5-5`), then `make up`. The key stays in sandbox-gateway;
   sandboxes never see it. `SIF_MAX_BUDGET_USD` caps the spend per ticket.
3. **Go live.** Set `SIF_DRY_RUN=false` and `make up`. Tickets labelled `sif` in a team's Jira projects are picked up.

Before anyone else uses the dashboard, follow docs/security.md, "Running in production": sign-in, a GitHub App and protected branches.

## Everyday commands

| Command | Does |
|---|---|
| `make up` | Build and (re)start everything; stray agent sandboxes are removed first. Data is kept |
| `make down` | Stop the stack (data is kept; `podman-compose down -v` deletes it) |
| `make logs` / `make logs s=jira-connector` | Follow the output of all services or one (Ctrl-C stops following, not the stack) |
| `make test` | Python and 3D view unit tests (`make test-py`, `make test-3d`) |
| `make test-3d-visual` | Screenshot tests of the 3D view; `UPDATE=1` refreshes the reference images |
| `make build-3d` / `make lint-charts` | Type-check and build the 3D view / check the Helm charts |
| `make e2e` | The whole stack end to end against mock Jira and GitHub |
| `make backup` / `make restore FILE=…` | Database dump to `backups/` and back |

A task's full trail, including what happened inside its sandbox, is on its page in the dashboard (Tasks → the ticket), in the 3D view (click the robot →
**Logs**), and on its workflow (Workflows → `task-<KEY>`: every step Temporal recorded, and the log).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Cannot connect to Podman` | macOS: `podman machine start`. Linux/WSL: `systemctl --user start podman.socket` |
| Tasks fail with "could not start a sandbox" | `PODMAN_SOCKET` in `.env` must be this machine's socket: delete the line and run `make init` again |
| `address already in use` on 3000, 5173 or 8000 | Another program uses the port; stop it, or change the left-hand port in `compose.yaml` |
| Builds stop with "no space left on device" | `podman system prune` (keeps volumes); on macOS give the machine a larger disk |
| Services restart or get killed | Too little memory: macOS `podman machine set --memory`, WSL `.wslconfig` |
| `CERTIFICATE_VERIFY_FAILED` in `make logs s=embeddings` | A proxy or VPN inspects HTTPS. Download the embeddings model on another network and set `EMBEDDINGS_MODEL_PATH`, or add your company's root certificate to the image |
| `Permission denied` on `/work` (Fedora, RHEL) | SELinux; the Makefile already disables labels for its own containers. Update to the current Makefile |
| WSL: the dashboard does not open from Windows | Check `make up` in Ubuntu printed the addresses; then try `networkingMode=mirrored` under `[wsl2]` in `.wslconfig` |
| WSL: very slow builds | The repository is under `/mnt/c`; clone it into `~` inside Ubuntu |
