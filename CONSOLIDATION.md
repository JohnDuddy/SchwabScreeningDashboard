# Consolidation Notes

This repository is one local Schwab dashboard application with several tabs and scanners:

- Covered-call dashboard
- Open options dashboard
- Cash-secured put screener
- Momentum and Momentum Pro screeners
- Expiring-options scanner
- 0DTE anomaly scanner
- Equity ranking and AI write-up tools

## Launching

All Windows launchers now delegate to one script:

```powershell
scripts\launch_app.ps1
```

The existing BAT files remain as compatibility wrappers for desktop shortcuts:

- `run.bat` opens `https://127.0.0.1/`
- `run_dashboard.bat` opens `https://127.0.0.1/`
- `run_expiring_options.bat` opens `https://127.0.0.1/expiring-options`

## Source Files

Keep source code, templates, tests, samples, and package modules in version control:

- `app.py`
- `runtime_state.py`
- `scan_cache.py`
- `momentum.py`, `momentum_v2.py`, `momentum_cli.py`
- `cspscreener/`
- `expiring_options/`
- `equity/`
- `zerodte/`
- `templates/`
- `tests/`
- `samples/`
- `scripts/`

## Generated Or Local Runtime Files

These are generated locally and should stay out of source control:

- `.cache_*.json`
- `.schwab_tokens.json`
- `.company_name_cache.json`
- `.universe_cache.json`
- `data/*.sqlite`
- `exports/`
- `output/`
- `*.log`
- `cert.pem`
- `key.pem`
- `momentum_history.xlsx`
- `venv/`

## State Helper

Repeated background-scan state dictionaries are centralized in `runtime_state.py`.
Use `make_scan_state()` for new scan tabs and `reset_scan_state()` when starting a scan.
