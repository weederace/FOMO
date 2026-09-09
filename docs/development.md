# Development

Use Python 3.12, `pytest`, and `ruff check .`. Crawl mode needs a local browser, so run
`playwright install chromium` after `pip install -e ".[dev]"`; use `DATA_PROVIDER=mock`
for offline development. Run `alembic upgrade head` against PostgreSQL for migrations.

On Windows, `run.bat` does that setup and opens `scripts/launcher.py`, a Tkinter window
that runs each script as a subprocess and streams its output. The launcher must stay
dependency-free: it reads `.env` by hand rather than importing `app.config`, and it sets
`TCL_LIBRARY`/`TK_LIBRARY` because a Windows venv does not carry Tcl. `run.bat <command>`
skips the window for terminal use, and `run.bat menu` gives a text menu.

The crawl provider keeps its DOM parsing in pure functions (`build_trader`,
`build_alert`, `parse_amount`), so tests cover them without launching a browser; add
cases there rather than writing browser-driven tests. Selectors and the page-side
scripts live at the top of `app/providers/crawl.py` and are the first thing to check
if a render starts returning no rows.

Do not add undocumented live endpoints or secrets; update `docs/data_sources.md` when a
source is legitimately verified, and record rendered-page evidence in
`docs/public_crawl_report.md`.
