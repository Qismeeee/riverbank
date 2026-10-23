from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score, roc_curve
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "data" / "synth"))

from services.features import LEAK_FIELDS, RAW_FIELDS, FeatureBuilder  # noqa: E402

LABEL = "label_default"
SEED = 7


@dataclass(frozen=True)
class SplitInfo:
    path: str
    sha256: str
    rows: int
    default_rate: float

    def as_dict(self) -> dict:
        return asdict(self)


def load_split(path: Path) -> tuple[pd.DataFrame, SplitInfo]:
    raw = path.read_bytes()
    df = pd.read_csv(path)
    missing = [c for c in RAW_FIELDS + [LABEL] if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: thiếu cột {missing}")
    if not df["shop_id"].is_unique:
        raise ValueError(f"{path}: shop_id trùng")
    info = SplitInfo(
        path=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        sha256=hashlib.sha256(raw).hexdigest()[:16],
        rows=int(len(df)),
        default_rate=round(float(df[LABEL].mean()), 4),
    )
    return df, info


def generate_split(n: int, seed: int) -> pd.DataFrame:
    from train import Calibration, TrainingSetGenerator

    gen = TrainingSetGenerator(Calibration(ROOT / "data/synth/calibration.json"), seed)
    shops, _ = gen.generate(n)
    return pd.DataFrame([asdict(s) for s in shops])


def quality_report(df: pd.DataFrame) -> dict:
    nums = df.select_dtypes("number")
    return {
        "rows": int(len(df)),
        "nulls": int(df.isna().sum().sum()),
        "duplicate_shop_id": int(df["shop_id"].duplicated().sum()),
        "label_values": sorted(df[LABEL].unique().tolist()),
        "negative_money": int(
            (nums[["amount", "payout_mean_12w", "cash_on_hand"]] < 0).sum().sum()
        ),
        "amount_over_cap": int((df["amount"] > 300_000_000).sum()),
        "tenor_range": [int(df["tenor_weeks"].min()), int(df["tenor_weeks"].max())],
        "cv_range": [
            round(float(df["payout_cv_12w"].min()), 3),
            round(float(df["payout_cv_12w"].max()), 3),
        ],
        "leak_columns_present": [c for c in LEAK_FIELDS if c in df.columns],
    }


def ks_stat(y: np.ndarray, p: np.ndarray) -> float:
    fpr, tpr, _ = roc_curve(y, p)
    return float((tpr - fpr).max())


def ece_quantile(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.unique(np.quantile(p, np.linspace(0, 1, bins + 1)))
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, len(edges) - 2)
    total = 0.0
    for b in range(len(edges) - 1):
        m = idx == b
        if m.any():
            total += m.mean() * abs(y[m].mean() - p[m].mean())
    return float(total)


def _logit(p: np.ndarray) -> np.ndarray:
    q = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(q / (1 - q))


def calibration_line(y: np.ndarray, p: np.ndarray) -> tuple[float, float]:
    lr = LogisticRegression(C=1e6, max_iter=1000).fit(_logit(p).reshape(-1, 1), y)
    return float(lr.coef_[0][0]), float(lr.intercept_[0])


def bootstrap_ci(
    y: np.ndarray, p: np.ndarray, fn, n: int = 300, seed: int = 0
) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if y[i].min() == y[i].max():
            continue
        vals.append(fn(y[i], p[i]))
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return float(lo), float(hi)


def evaluate(y, p, n_boot: int = 300) -> dict:
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    auc = roc_auc_score(y, p)
    lo, hi = bootstrap_ci(y, p, roc_auc_score, n_boot)
    slope, intercept = calibration_line(y, p)
    return {
        "auc": round(float(auc), 4),
        "auc_ci95": [round(lo, 4), round(hi, 4)],
        "gini": round(float(2 * auc - 1), 4),
        "ks": round(ks_stat(y, p), 4),
        "pr_auc": round(float(average_precision_score(y, p)), 4),
        "brier": round(float(brier_score_loss(y, p)), 4),
        "ece": round(ece_quantile(y, p), 4),
        "cal_slope": round(slope, 3),
        "cal_intercept": round(intercept, 3),
    }


def brier_floor(oracle_pd: np.ndarray) -> float:
    return float(np.mean(oracle_pd * (1 - oracle_pd)))


def reliability_table(y, p, bins: int = 10) -> pd.DataFrame:
    df = pd.DataFrame({"y": np.asarray(y), "p": np.asarray(p)})
    df["bin"] = pd.qcut(df["p"], bins, duplicates="drop")
    t = df.groupby("bin", observed=True).agg(
        n=("y", "size"), pd_mean=("p", "mean"), actual=("y", "mean")
    )
    se = np.sqrt(t["actual"] * (1 - t["actual"]) / t["n"])
    t["actual_lo"], t["actual_hi"] = t["actual"] - 1.96 * se, t["actual"] + 1.96 * se
    return t.round(4)


def cutoff_table(y, p, thresholds=(0.1, 0.15, 0.2, 0.25, 0.3, 0.4)) -> pd.DataFrame:
    y, p = np.asarray(y), np.asarray(p)
    rows = []
    for t in thresholds:
        approved = p < t
        rows.append(
            {
                "pd_cutoff": t,
                "approval_rate": approved.mean(),
                "bad_rate_approved": y[approved].mean() if approved.any() else np.nan,
                "bad_rate_rejected": y[~approved].mean() if (~approved).any() else np.nan,
                "defaults_blocked": y[~approved].sum() / max(1, y.sum()),
            }
        )
    return pd.DataFrame(rows).round(4)


def decile_table(y, p) -> pd.DataFrame:
    df = pd.DataFrame({"y": np.asarray(y), "p": np.asarray(p)})
    df["decile"] = pd.qcut(df["p"].rank(method="first"), 10, labels=range(1, 11))
    t = df.groupby("decile", observed=True).agg(
        n=("y", "size"), pd_mean=("p", "mean"), bad_rate=("y", "mean")
    )
    t["lift"] = t["bad_rate"] / df["y"].mean()
    t["cum_defaults_share"] = t["n"].mul(t["bad_rate"])[::-1].cumsum()[::-1] / df["y"].sum()
    return t.round(4)


def psi(expected, actual, bins: int = 10) -> float:
    edges = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    e = np.histogram(expected, edges)[0] / len(expected)
    a = np.histogram(actual, edges)[0] / len(actual)
    e, a = np.clip(e, 1e-4, None), np.clip(a, 1e-4, None)
    return float(np.sum((a - e) * np.log(a / e)))


def psi_table(train: pd.DataFrame, test: pd.DataFrame) -> pd.Series:
    return (
        pd.Series({c: psi(train[c].values, test[c].values) for c in train.columns})
        .sort_values(ascending=False)
        .round(4)
    )


def univariate_auc(X: pd.DataFrame, y) -> pd.DataFrame:
    y = np.asarray(y)
    rows = {}
    for c in X.columns:
        if X[c].nunique() < 2:
            continue
        a = roc_auc_score(y, X[c])
        rows[c] = {
            "auc": a,
            "signal": abs(a - 0.5),
            "direction": "tăng rủi ro" if a > 0.5 else "giảm rủi ro",
        }
    return pd.DataFrame(rows).T.sort_values("signal", ascending=False).round(4)


def segment_auc(y, p, segment: pd.Series) -> pd.DataFrame:
    df = pd.DataFrame({"y": np.asarray(y), "p": np.asarray(p), "seg": segment.values})
    rows = []
    for s, g in df.groupby("seg"):
        rows.append(
            {
                "segment": s,
                "n": len(g),
                "default_rate": g.y.mean(),
                "auc": roc_auc_score(g.y, g.p) if g.y.nunique() > 1 else np.nan,
            }
        )
    return pd.DataFrame(rows).round(4)


class LogisticTrainer:
    name = "logistic"

    def make(self):
        return make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=3000))

    def cv_auc(self, X, y, folds: int = 5) -> float:
        skf = StratifiedKFold(folds, shuffle=True, random_state=SEED)
        return float(
            np.mean(
                [
                    roc_auc_score(
                        y.iloc[va],
                        self.make().fit(X.iloc[tr], y.iloc[tr]).predict_proba(X.iloc[va])[:, 1],
                    )
                    for tr, va in skf.split(X, y)
                ]
            )
        )


class LgbmTrainer:
    name = "lightgbm"
    FIXED = dict(
        objective="binary",
        learning_rate=0.03,
        n_estimators=1000,
        subsample=0.8,
        subsample_freq=1,
        random_state=SEED,
        verbose=-1,
        n_jobs=4,
    )
    SPACE = {
        "num_leaves": [4, 7, 15, 31],
        "max_depth": [2, 3, 4, 6],
        "min_child_samples": [20, 50, 100, 200],
        "colsample_bytree": [0.6, 0.8, 1.0],
        "reg_lambda": [0.0, 1.0, 5.0, 20.0],
    }

    def make(self, params: dict | None = None):
        return lgb.LGBMClassifier(**{**self.FIXED, **(params or {})})

    def oof(self, params: dict, X, y, folds: int = 5) -> tuple[np.ndarray, list[int]]:
        skf = StratifiedKFold(folds, shuffle=True, random_state=SEED)
        out, iters = np.zeros(len(y)), []
        for tr, va in skf.split(X, y):
            m = self.make(params).fit(
                X.iloc[tr],
                y.iloc[tr],
                eval_set=[(X.iloc[va], y.iloc[va])],
                callbacks=[lgb.early_stopping(50, verbose=False)],
            )
            out[va] = m.predict_proba(X.iloc[va])[:, 1]
            iters.append(int(m.best_iteration_))
        return out, iters

    def random_search(self, X, y, n_trials: int = 24, seed: int = SEED) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        rows = []
        for _ in range(n_trials):
            params = {k: rng.choice(v).item() for k, v in self.SPACE.items()}
            oof, iters = self.oof(params, X, y)
            rows.append(
                {
                    **params,
                    "oof_auc": roc_auc_score(y, oof),
                    "oof_brier": brier_score_loss(y, oof),
                    "best_iter": int(np.mean(iters)),
                }
            )
        return pd.DataFrame(rows).sort_values("oof_auc", ascending=False).reset_index(drop=True)

    def params_from_row(self, row) -> dict:
        return {
            k: (int(row[k]) if isinstance(v[0], int) else float(row[k]))
            for k, v in self.SPACE.items()
        }

    def fit_final(self, params: dict, n_rounds: int, X, y):
        return self.make({**params, "n_estimators": max(50, int(n_rounds))}).fit(X, y)


class CalibrationSelector:
    def __init__(self, seed: int = SEED):
        self.seed = seed
        self.method = "none"
        self.iso: IsotonicRegression | None = None
        self.platt: tuple[float, float] | None = None
        self.table: pd.DataFrame | None = None

    def fit(self, oof: np.ndarray, y: np.ndarray) -> CalibrationSelector:
        rng = np.random.default_rng(self.seed)
        idx = rng.permutation(len(y))
        a, b = idx[: len(y) // 2], idx[len(y) // 2 :]
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(oof[a], y[a])
        lr = LogisticRegression(C=1e6, max_iter=1000).fit(_logit(oof[a]).reshape(-1, 1), y[a])
        platt = (float(lr.coef_[0][0]), float(lr.intercept_[0]))
        cands = {
            "none": oof[b],
            "platt": self._platt(oof[b], platt),
            "isotonic": iso.predict(oof[b]),
        }
        self.table = pd.DataFrame(
            {
                k: {
                    "brier_holdout": brier_score_loss(y[b], v),
                    "ece_holdout": ece_quantile(y[b], v),
                }
                for k, v in cands.items()
            }
        ).T.round(4)
        best = self.table["brier_holdout"].idxmin()
        if self.table.loc[best, "brier_holdout"] < self.table.loc["none", "brier_holdout"] - 1e-4:
            self.method = best
        self.iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(oof, y)
        lr = LogisticRegression(C=1e6, max_iter=1000).fit(_logit(oof).reshape(-1, 1), y)
        self.platt = (float(lr.coef_[0][0]), float(lr.intercept_[0]))
        return self

    @staticmethod
    def _platt(p, ab):
        z = ab[0] * _logit(np.asarray(p)) + ab[1]
        return 1 / (1 + np.exp(-z))

    def apply(self, p: np.ndarray) -> np.ndarray:
        if self.method == "isotonic":
            return self.iso.predict(p)
        if self.method == "platt":
            return self._platt(p, self.platt)
        return np.asarray(p)

    def to_json(self) -> dict:
        return {
            "enabled": self.method != "none",
            "method": self.method,
            "x": self.iso.X_thresholds_.tolist(),
            "y": self.iso.y_thresholds_.tolist(),
            "platt_a": self.platt[0],
            "platt_b": self.platt[1],
        }


def learning_curve(
    trainer_params: dict, train: pd.DataFrame, test: pd.DataFrame, sizes=(2000, 5000, 10000, 20000)
) -> pd.DataFrame:
    fb = FeatureBuilder()
    Xte, yte = fb.frame(test), test[LABEL].values
    rows = []
    for n in sizes:
        sub = train.iloc[: min(n, len(train))]
        X, y = fb.frame(sub), sub[LABEL]
        lr = LogisticTrainer().make().fit(X, y).predict_proba(Xte)[:, 1]
        gb = (
            LgbmTrainer()
            .fit_final(trainer_params["params"], trainer_params["n_rounds"], X, y)
            .predict_proba(Xte)[:, 1]
        )
        rows.append(
            {
                "train_rows": len(sub),
                "logistic_auc": roc_auc_score(yte, lr),
                "lightgbm_auc": roc_auc_score(yte, gb),
                "lightgbm_brier": brier_score_loss(yte, gb),
            }
        )
    return pd.DataFrame(rows).round(4)


def save_artifacts(out: Path, model, calibrator: CalibrationSelector, meta: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    model.booster_.save_model(str(out / "pd_lgbm.txt"))
    (out / "pd_calibrator.json").write_text(json.dumps(calibrator.to_json()), encoding="utf-8")
    (out / "pd_meta.json").write_text(
        json.dumps({"trained_on": date.today().isoformat(), **meta}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def shap_values(model, X: pd.DataFrame) -> pd.DataFrame:
    contrib = model.booster_.predict(X, pred_contrib=True)
    return pd.DataFrame(contrib[:, :-1], columns=X.columns, index=X.index)
