"""Optional always-on scheduler.

The primary path is stateless ``ajax agent run-once`` driven by cron or a systemd
timer — no daemon assumption, and safe in a sandbox where a long-lived process
may be killed. This wrapper exists for users who prefer one resident process.

Like the runner, this never touches a live client.
"""

from __future__ import annotations

import logging
from datetime import date

from ajax.config import Config

log = logging.getLogger(__name__)

# Signals come from the prior close, so the run happens shortly after the open —
# matching the backtest's close-signal / next-open-entry convention exactly.
DEFAULT_HOUR = 9
DEFAULT_MINUTE = 32
DEFAULT_TZ = "America/New_York"

CRONTAB_EXAMPLE = """\
# Ajax daily paper-trading run, weekdays at 09:32 America/New_York.
# Signals are computed from the prior close and orders placed shortly after the
# open, which is the same convention the backtest simulates.
32 9 * * 1-5 cd {repo} && TZ=America/New_York {python} -m ajax agent run-once >> logs/agent.log 2>&1
"""


def serve(cfg: Config, *, hour: int = DEFAULT_HOUR, minute: int = DEFAULT_MINUTE,
          timezone: str = DEFAULT_TZ) -> None:
    """Block, running the agent on a weekday schedule."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    from ajax.agent.runner import build_chain_source, load_prices_for_scan, run_once
    from ajax.agent.trade_log import TradeLog

    trade_log = TradeLog(cfg.paths.resolve("data_cache") / "ajax_trades.db")
    scheduler = BlockingScheduler(timezone=timezone)

    def job() -> None:
        log.info("scheduled run starting")
        try:
            prices = load_prices_for_scan(cfg)
            summary = run_once(
                cfg, trade_log, as_of=date.today(),
                chain_source=build_chain_source(cfg, prices),
            )
            log.info(
                "run complete: opened=%d closed=%d skipped=%d",
                summary.opened, summary.closed, summary.skipped,
            )
        except Exception:
            log.exception("scheduled run failed")

    scheduler.add_job(
        job,
        CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=timezone),
        id="ajax-daily",
        max_instances=1,
        coalesce=True,
    )
    log.info("scheduler started: weekdays %02d:%02d %s", hour, minute, timezone)
    scheduler.start()
