from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pdlib  # noqa: E402
from pdlib import LABEL, ROOT, FeatureBuilder, LgbmTrainer, LogisticTrainer  # noqa: E402

MODEL_VERSION = "pd-lgbm-v2"


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def train(
    train_path: Path, test_path: Path, out: Path, n_trials: int = 24, quiet: bool = False
) -> dict:
    log = (lambda *a: None) if quiet else print
    train_df, train_info = pdlib.load_split(train_path)
    test_df, test_info = pdlib.load_split(test_path)
    fb = FeatureBuilder()
    Xtr, ytr = fb.frame(train_df), train_df[LABEL]
    Xte, yte = fb.frame(test_df), test_df[LABEL].values
    log(f"train {train_info.rows} default {train_info.default_rate}")
    log(f"test {test_info.rows} default {test_info.default_rate}")

    lr = LogisticTrainer()
    lr_model = lr.make().fit(Xtr, ytr)
    lr_eval = pdlib.evaluate(yte, lr_model.predict_proba(Xte)[:, 1])
    log(f"logistic test auc {lr_eval['auc']} brier {lr_eval['brier']}")

    gb = LgbmTrainer()
    trials = gb.random_search(Xtr, ytr, n_trials=n_trials)
    best = trials.iloc[0]
    params = gb.params_from_row(best)
    oof, iters = gb.oof(params, Xtr, ytr)
    n_rounds = int(np.mean(iters))
    model = gb.fit_final(params, n_rounds, Xtr, ytr)
    p_raw = model.predict_proba(Xte)[:, 1]
    cal = pdlib.CalibrationSelector().fit(oof, ytr.values)
    p_cal = cal.apply(p_raw)
    raw_eval, cal_eval = pdlib.evaluate(yte, p_raw), pdlib.evaluate(yte, p_cal)
    log(f"lightgbm {params} rounds {n_rounds} | oof auc {best['oof_auc']:.4f}")
    log(f"  test auc {raw_eval['auc']} brier {raw_eval['brier']} ece {raw_eval['ece']}")
    log(f"calibrator {cal.method} | test brier {cal_eval['brier']} ece {cal_eval['ece']}")
    log(f"  slope {cal_eval['cal_slope']} intercept {cal_eval['cal_intercept']}")

    shap = pdlib.shap_values(model, Xte).abs().mean().sort_values(ascending=False)
    meta = {
        "model_version": MODEL_VERSION,
        "git_sha": git_sha(),
        "features": fb.columns,
        "raw_fields": pdlib.RAW_FIELDS,
        "excluded_leak_fields": pdlib.LEAK_FIELDS,
        "params": {**params, "n_estimators": n_rounds},
        "data": {"train": train_info.as_dict(), "test": test_info.as_dict()},
        "metrics": {
            "logistic_test": lr_eval,
            "lightgbm_test_raw": raw_eval,
            "lightgbm_test_calibrated": cal_eval,
            "lightgbm_oof_auc": round(float(best["oof_auc"]), 4),
        },
        "calibration": {"method": cal.method, "holdout": cal.table.to_dict()},
        "shap_mean_abs": shap.round(4).to_dict(),
        "label": "BL-11 (3 kỳ trích thiếu liên tiếp), xem docs/Riverbank-risk-model.md",
    }
    pdlib.save_artifacts(out, model, cal, meta)
    log(f"saved -> {out}")
    return meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default=str(ROOT / "data/synth/train/train_shops.csv"))
    ap.add_argument("--test", default=str(ROOT / "data/synth/test/train_shops.csv"))
    ap.add_argument("--out", default=str(ROOT / "api" / "models"))
    ap.add_argument("--trials", type=int, default=24)
    a = ap.parse_args(argv)
    meta = train(Path(a.train), Path(a.test), Path(a.out), a.trials)
    print(json.dumps(meta["metrics"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
