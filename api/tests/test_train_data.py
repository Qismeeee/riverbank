import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_training_set_is_deterministic_and_labelled(tmp_path):
    cmd = [sys.executable, str(ROOT / "data/synth/train.py"), "--n", "200", "--seed", "3", "--out"]
    subprocess.run(cmd + [str(tmp_path / "a")], check=True, capture_output=True)
    subprocess.run(cmd + [str(tmp_path / "b")], check=True, capture_output=True)
    a = (tmp_path / "a" / "train_shops.csv").read_text(encoding="utf-8")
    assert a == (tmp_path / "b" / "train_shops.csv").read_text(encoding="utf-8")
    rows = list(csv.DictReader(open(tmp_path / "a" / "train_shops.csv", encoding="utf-8")))
    assert len(rows) == 200
    rate = sum(int(r["label_default"]) for r in rows) / len(rows)
    assert 0.05 < rate < 0.4
    assert all(0 < int(r["amount"]) <= 300_000_000 for r in rows)
    assert all(
        int(r["weekly_deduction"]) * int(r["tenor_weeks"]) >= int(r["amount"]) * 0.9 for r in rows
    )
    weeks = list(csv.DictReader(open(tmp_path / "a" / "train_weeks.csv", encoding="utf-8")))
    per_shop = {}
    for w in weeks:
        per_shop.setdefault(w["shop_id"], set()).add(w["phase"])
    assert all(v == {"history", "repayment"} for v in per_shop.values())
