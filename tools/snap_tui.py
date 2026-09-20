from __future__ import annotations

import asyncio
from pathlib import Path

from localpilot.app import LocalPilotApp
from localpilot.session import project_dir


async def main() -> int:
    project = project_dir(".")
    out = project / "runs" / "tui-preview"
    out.mkdir(parents=True, exist_ok=True)
    app = LocalPilotApp(project=project)
    async with app.run_test(size=(140, 46)) as pilot:
        await pilot.pause(2.4)
        for name in ("machine", "models", "route", "runs", "testing", "errors"):
            app.show_page(name)
            await pilot.pause(0.35)
            (out / f"{name}.svg").write_text(app.export_screenshot(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
