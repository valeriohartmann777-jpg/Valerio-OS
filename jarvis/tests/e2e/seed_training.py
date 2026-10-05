"""Seeds an E2E data folder with one finished model-training run.

The prices are a synthetic random walk (the E2E has no market data), so the
honest verdict is "no edge" — which is what the Learning page must show.
Usage: python seed_training.py <JARVIS_DATA_DIR>
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from jarvis.briefing.levels import LevelRules
from jarvis.storage.database import Database
from jarvis.training.dataset import Dataset, TouchRules, build
from jarvis.training.model import Periods, train
from jarvis.training.store import Bundle, TrainingStore
from tests.learning_data import futures_bars


async def main(folder: Path) -> None:
    start, end = date(2023, 1, 2), date(2024, 12, 31)
    nq = futures_bars(start, end, 18000.0, noise=2.0, seed=1)
    gold = futures_bars(start, end, 2000.0, noise=0.3, seed=2)
    nq_rules, gold_rules = LevelRules(100, 500, 3.0, True), LevelRules(10, 50, 0.8, False)
    data = Dataset.concat(
        [
            build(nq, "NQ", nq_rules, (570, 960), TouchRules()),
            build(gold, "XAUUSD", gold_rules, (180, 810), TouchRules(), market_index=1),
        ]
    )
    trained = train(data, Periods(oos_start=date(2024, 1, 1), holdout_start=date(2024, 7, 1)))
    db = Database(folder / "jarvis.db")
    await db.connect()
    try:
        store = TrainingStore(db, folder / "training")
        run_id, number = await store.start_run()
        until = end.isoformat()
        await store.finish_run(run_id, data_until=until, report=trained.report, seconds=24.0)
        if trained.model is not None:
            store.save(Bundle(trained, number, until))
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
