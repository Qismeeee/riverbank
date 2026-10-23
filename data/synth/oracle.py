from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from train import SHORT_PAID_DEFAULT_PERIODS, Calibration, TrainingSetGenerator  # noqa: E402


class OracleGenerator(TrainingSetGenerator):
    def __init__(self, calibration: Calibration, seed: int = 7, n_sim: int = 200):
        super().__init__(calibration, seed)
        self.n_sim = n_sim
        self.oracle_pd: list[float] = []

    def _repayment(self, mean_payout, cv, result, risk, rng):
        out = super()._repayment(mean_payout, cv, result, risk, rng)
        sim = random.Random(f"oracle-{len(self.oracle_pd)}")
        hits = 0
        for _ in range(self.n_sim):
            _, _, short = super()._repayment(mean_payout, cv, result, risk, sim)
            hits += short >= SHORT_PAID_DEFAULT_PERIODS
        self.oracle_pd.append(hits / self.n_sim)
        return out


def oracle_rows(n: int, seed: int, calibration: Path, n_sim: int = 200) -> list[dict]:
    gen = OracleGenerator(Calibration(calibration), seed, n_sim)
    shops, _ = gen.generate(n)
    return [
        {"shop_id": s.shop_id, "label_default": s.label_default, "oracle_pd": p}
        for s, p in zip(shops, gen.oracle_pd, strict=True)
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--n-sim", type=int, default=200)
    ap.add_argument("--calibration", default=str(Path(__file__).parent / "calibration.json"))
    ap.add_argument("--out", default=str(Path(__file__).parent / "test" / "oracle.csv"))
    a = ap.parse_args(argv)
    rows = oracle_rows(a.n, a.seed, Path(a.calibration), a.n_sim)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["shop_id", "label_default", "oracle_pd"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
