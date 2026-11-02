# HW-006: Zotero change detection

This spike asks how Hyper-Wiki can notice changes to a Zotero library while Zotero runs, without writing to Zotero or locking it. It compares four candidates:
- polling Zotero's local API;
- watching and copying Zotero's database;
- a plugin's `Zotero.Notifier` events;
- watching attachment files.

It measures detection latency, what each candidate sees of trash, restore, and permanent deletion, how attachment paths resolve, and whether reading is safe while Zotero writes. Results are in [results.md](results.md); the decision is [ADR 0004](../../docs/decisions/0004-zotero-change-detection.md).

## Files

- **[`spike.py`](spike.py):** the main harness.
  - Starts a real Zotero on a throwaway profile and a throwaway data directory.
  - Applies 25 scripted changes and watches every detector.
  - Pauses the detectors to test catching up after downtime.
  - Copies the database repeatedly while Zotero writes heavily.
  - Writes `results/summary.json`, which holds timings and counts from synthetic data only.
- **[`driver/`](driver):** a test-only Zotero plugin. It logs every notifier event to a file and serves `POST /hw006/act`, which applies changes through Zotero's JavaScript API. It stays inactive unless the open data directory contains the `hw006-throwaway` marker that `spike.py` writes.
- **[`observe.py`](observe.py):** read-only measurements on a real library while Zotero runs. It makes GET requests to the local API, reads byte copies of `zotero.sqlite`, and prints counts and timings.

## Safety

`spike.py` never touches the user's Zotero profile or library:
- **Isolation:** the throwaway profile gets its own data directory (`-datadir`), its own local server port (23124 by default; 23119 is refused), and the driver plugin.
- **No side installs:** it turns off the Word and LibreOffice plugin installs that a fresh profile otherwise attempts.
- **Check before use:** before using the instance, it checks through the driver that Zotero opened the throwaway data directory, and stops it otherwise.
- **Private output:** everything goes in a run directory under `--private`, which must be outside the repository.

A Zotero window opens for about three minutes. The user's own Zotero may keep running.

`observe.py` only reads. Run it while Zotero runs with **Settings → Advanced → Allow other applications on this computer to communicate with Zotero** turned on, which enables the local API.

## Run

macOS:

```sh
uv run --locked python experiments/hw006-change-detection/spike.py --private /path/outside/the/repo
uv run --locked python experiments/hw006-change-detection/observe.py --zotero-dir ~/Zotero
```

Windows (PowerShell). `--zotero` defaults to `zotero.exe` under Program Files, then under `%LOCALAPPDATA%`:

```powershell
uv run --locked python experiments\hw006-change-detection\spike.py --private C:\path\outside\the\repo --summary experiments\hw006-change-detection\results\summary-windows.json
uv run --locked python experiments\hw006-change-detection\observe.py --zotero-dir $HOME\Zotero
```

Linux: pass `--zotero /path/to/zotero` if `zotero` is not on `PATH`.

The `--window` option sets how many seconds each change is watched; it defaults to 4.
