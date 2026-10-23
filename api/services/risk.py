from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .features import FeatureBuilder

MODEL_DIR = Path(__file__).resolve().parents[1] / "models"


@dataclass(frozen=True)
class RiskFactor:
    name: str
    direction: str
    shap: float
    value: float


@dataclass(frozen=True)
class RiskResult:
    pd: float
    pd_raw: float
    factors: list[RiskFactor]
    model_version: str


class RiskModel:
    def __init__(
        self,
        booster,
        meta: dict,
        calibrator: dict | None = None,
        builder: FeatureBuilder | None = None,
    ):
        self.booster = booster
        self.meta = meta
        self.builder = builder or FeatureBuilder()
        self.features: list[str] = meta["features"]
        if self.features != self.builder.columns:
            raise ValueError("pd_meta.json features không khớp FeatureBuilder, train lại model")
        self.calibrator = calibrator if calibrator and calibrator.get("enabled") else None

    @classmethod
    def load(cls, model_dir: Path = MODEL_DIR) -> RiskModel:
        import lightgbm as lgb

        booster = lgb.Booster(model_file=str(model_dir / "pd_lgbm.txt"))
        meta = json.loads((model_dir / "pd_meta.json").read_text(encoding="utf-8"))
        cal_path = model_dir / "pd_calibrator.json"
        calibrator = json.loads(cal_path.read_text(encoding="utf-8")) if cal_path.exists() else None
        return cls(booster, meta, calibrator)

    def predict(self, raw: dict, top_k: int = 5) -> RiskResult:
        row = np.array([self.builder.vector(raw)], dtype=float)
        p_raw = float(self.booster.predict(row)[0])
        contrib = self.booster.predict(row, pred_contrib=True)[0][:-1]
        order = np.argsort(-np.abs(contrib))[:top_k]
        factors = [
            RiskFactor(
                name=self.features[i],
                direction="tăng rủi ro" if contrib[i] > 0 else "giảm rủi ro",
                shap=round(float(contrib[i]), 4),
                value=float(row[0][i]),
            )
            for i in order
        ]
        return RiskResult(
            pd=round(self._calibrate(p_raw), 4),
            pd_raw=round(p_raw, 4),
            factors=factors,
            model_version=self.meta.get("model_version", self.meta.get("trained_on", "unknown")),
        )

    def _calibrate(self, p: float) -> float:
        if not self.calibrator:
            return p
        if self.calibrator.get("method") == "platt":
            q = min(max(p, 1e-6), 1 - 1e-6)
            z = self.calibrator["platt_a"] * np.log(q / (1 - q)) + self.calibrator["platt_b"]
            return float(1 / (1 + np.exp(-z)))
        return float(np.interp(p, self.calibrator["x"], self.calibrator["y"]))
