from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Callable
from uuid import uuid4

from policy import RISK_RULE_IDS, PolicyInput, default_engine
from services.cashflow import CashFlowEngine, CashFlowInput
from services.guardrail import note_block_details
from services.statements.reader import StatementReader
from services.statements.storage import DocumentStore, NoDocumentStore

from .evidence import digest, emit
from .gates import CicGate, GateInput, OpenCicGate
from .memo import CodeMemoWriter, MemoComposer, default_validator
from .models import (
    Case,
    CicInquiry,
    DecisionInput,
    Plan,
    Statement,
    StatementUpload,
    WorkflowError,
)
from .repository import CaseRepository
from .risk import RiskInput, RiskModel
from .sources import CicSource, FeatureSource, LosAdapter, validate_features

ENGINE_VERSION = "cashflow-v2-base-int"
POLICY_VERSION = "riverbank-r01-r10-v4"
NOT_QUERIED = "not_queried"
DEFERRED = "deferred_until_statement"
CIC_RULE_IDS = frozenset({"R06", "R07"})

def _valid_risk(risk: dict) -> bool:
    if risk.get("missing_fields"):
        return risk["pd_bps"] is None and not risk["factors"]
    return risk["pd_bps"] is not None and 0 <= risk["pd_bps"] <= 10_000 and bool(risk["factors"])


def _blocking(results: list, risk: dict) -> list:
    risk_missing = bool(risk.get("missing_fields"))
    return [
        r
        for r in results
        if r.hard
        and not r.passed
        and not (risk_missing and r.missing_data and r.rule_id in RISK_RULE_IDS)
    ]


def _route(
    results: list, risk: dict, statement: dict | None, deferred: frozenset = frozenset()
) -> tuple[str, str | None]:
    risk_missing = bool(risk.get("missing_fields"))
    failures = _blocking(results, risk)
    awaiting_statement = any(r.rule_id == "R05" for r in failures) and all(
        r.missing_data and (r.rule_id == "R05" or r.rule_id in deferred) for r in failures
    )
    if (failures and not awaiting_statement) or (statement and not statement["matched"]):
        return "ESCALATED", "HARD_POLICY_FAILURE"
    if risk_missing:
        return "ESCALATED", "RISK_DATA_MISSING"
    if awaiting_statement:
        return "AWAITING_STATEMENT", "STATEMENT_REQUIRED"
    return "PENDING_REVIEW", None


def utc_now() -> datetime:
    return datetime.now(UTC)


class UnderwritingService:
    def __init__(
        self,
        repository: CaseRepository,
        features: FeatureSource,
        risk: RiskModel,
        cic: CicSource,
        los: LosAdapter,
        clock: Callable[[], datetime] = utc_now,
        cic_gate: CicGate | None = None,
        memo: MemoComposer | None = None,
        statements: StatementReader | None = None,
        documents: DocumentStore | None = None,
    ):
        self.statements = statements
        self.documents = documents or NoDocumentStore()
        self.memo = memo or MemoComposer(CodeMemoWriter(), default_validator())
        self.repository, self.features = repository, features
        self.risk, self.cic, self.los, self.clock = risk, cic, los, clock
        self.cic_gate = cic_gate or OpenCicGate()
        self.engine = CashFlowEngine()
        self.policy = default_engine()
        policy_dir = Path(__file__).resolve().parents[2] / "policy"
        self.policy_hash = digest(
            {
                "config": asdict(self.policy.cfg),
                "rules": (policy_dir / "rules.py").read_text(encoding="utf-8"),
                "engine": (policy_dir / "engine.py").read_text(encoding="utf-8"),
            }
        )

    def _event(self, case: Case, actor: str, kind: str, previous: str, reason: str) -> None:
        case.events.append(
            {
                "event_id": str(uuid4()),
                "actor": actor,
                "event_type": kind,
                "previous_status": previous,
                "next_status": case.status,
                "reason": reason,
                "occurred_at": self.clock().isoformat(),
                "evidence_ids": [
                    e["evidence_id"]
                    for e in case.evidence
                    if case.runs and e["run_id"] == case.runs[-1]["run_id"]
                ],
            }
        )

    def create(self, plan: Plan, key: str) -> Case:
        blocked = note_block_details(plan.note)
        if blocked:
            raise WorkflowError("BLOCKED_INPUT", 422, blocked)
        request_hash = digest(plan.model_dump())
        existing = self.repository.find_request(plan.seller_id, key, request_hash)
        if existing:
            return existing
        now = self.clock()
        consent, features = self.features.fetch(plan.seller_id, plan.shop_id, now)
        validate_features(features, plan.seller_id, plan.shop_id, now)
        case = Case(str(uuid4()), plan.seller_id, plan.shop_id, plan.model_dump(), now.isoformat())
        case.consent = deepcopy(consent)
        self._event(case, plan.seller_id, "STATE_CHANGED", "DRAFT", "INPUT_AND_CONSENT_VALID")
        self._assess(case, features)
        return self.repository.create(case, key, request_hash)

    def _policy_input(
        self,
        case: Case,
        features: dict,
        cf: dict,
        risk: dict,
        cic: dict,
        statement: dict | None,
    ) -> PolicyInput:
        return PolicyInput(
            amount=cf["amount_proposed"],
            tenor_weeks=cf["tenor_weeks"],
            weekly_deduction=max(cf["weekly_deductions"], default=0),
            payout_stress=cf["payout_stress"],
            platform_supported_amount=features["platform_supported_amount"],
            shop_age_months=features.get("shop_age_months"),
            return_rate=features.get("return_rate"),
            docs_verified=statement["matched"] if statement else None,
            cic_group=cic.get("bureau_group"),
            cic_outstanding=cic.get("outstanding"),
            requested_amount=max(case.plan["requested_amount"], cf["amount_proposed"]),
            pd_bps=risk.get("pd_bps"),
        )

    @staticmethod
    def _skipped_cic(case: Case, source: str, gate: dict | None) -> dict:
        return {
            "seller_id": case.seller_id,
            "source": source,
            "bureau_group": None,
            "outstanding": None,
            "queried_at": None,
            "gate": gate,
        }

    def _inquire_cic(self, case: Case, inp: GateInput, now: datetime) -> dict:
        gate = self.cic_gate.evaluate(inp)
        if not gate.eligible:
            return self._skipped_cic(case, NOT_QUERIED, gate.payload())
        return {**self.cic.inquire(case.seller_id, now), "gate": gate.payload()}

    def _statement_required(self, case: Case, features: dict, cf: dict, risk: dict) -> bool:
        outcome = self.policy.evaluate(self._policy_input(case, features, cf, risk, {}, None))
        return any(r.rule_id == "R05" and r.missing_data for r in outcome.results)

    def _resolve_cic(
        self,
        case: Case,
        inp: GateInput,
        now: datetime,
        override: dict | None,
    ) -> dict:
        # A gate that can decline judges a statement case only once the statement is compared.
        if override:
            return deepcopy(override)
        prior = case.runs[-1]["cic"] if case.runs else None
        if prior and prior["source"] != DEFERRED:
            return deepcopy(prior)
        if (
            self.cic_gate.waits_for_statement
            and inp.statement is None
            and self._statement_required(case, inp.features, inp.cashflow, inp.risk)
        ):
            return self._skipped_cic(case, DEFERRED, None)
        return self._inquire_cic(case, inp, now)

    def _assess(
        self,
        case: Case,
        features: dict,
        statement: dict | None = None,
        counter: DecisionInput | None = None,
        cic_override: dict | None = None,
    ) -> dict:
        now = self.clock()
        run_id = str(uuid4())
        inp = CashFlowInput(
            cash_on_hand=case.plan["cash_on_hand"],
            supplier_schedule=case.plan["supplier_schedule"],
            sell_weeks=case.plan["sell_weeks"],
            payout_weekly_history=[w["amount"] for w in features["weekly_payouts"]],
            requested_amount=case.plan["requested_amount"],
        )
        result = self.engine.compute(inp)
        cf = asdict(result)
        if counter:
            if not result.loan_needed:
                raise WorkflowError("NO_LOAN_NEEDED")
            terms = self.engine.counter_terms(
                counter.final_amount,
                counter.final_tenor_weeks,
                result.payout_stress,
            )
            cf.update(
                amount_proposed=terms.amount,
                tenor_weeks=terms.tenor_weeks,
                weekly_deductions=terms.weekly_deductions,
                weekly_deduction=terms.weekly_deduction,
                tenor_trace=[],
            )
        snapshot = {"plan": case.plan, "features": features, "consent": case.consent}
        run = {
            "run_id": run_id,
            "run_no": len(case.runs) + 1,
            "trigger": (
                "COUNTER"
                if counter
                else "CIC_INQUIRY"
                if cic_override
                else "STATEMENT"
                if statement
                else "INITIAL"
            ),
            "input_snapshot": deepcopy(snapshot),
            "input_hash": digest(snapshot),
            "engine_version": ENGINE_VERSION,
            "config": asdict(self.engine.cfg),
            "policy_version": POLICY_VERSION,
            "policy_hash": self.policy_hash,
            "cashflow": cf,
            "risk": None,
            "cic": None,
            "policy": None,
            "statement": deepcopy(statement),
            "created_at": now.isoformat(),
        }
        refs = {
            "E4": emit(case, run_id, "E4", features, now.isoformat()),
            "E5": emit(case, run_id, "E5", cf, now.isoformat()),
        }
        previous = case.status
        case.reason = None
        if result.loan_needed:
            # PD is re-scored on each run's terms. A new run keeps the first CIC snapshot taken.
            risk = self.risk.predict(RiskInput(features, case.plan, cf))
            if not _valid_risk(risk):
                raise WorkflowError("INVALID_RISK_RESULT", 422)
            cic = self._resolve_cic(
                case, GateInput(features, cf, risk, statement), now, cic_override
            )
            if cic.get("seller_id") != case.seller_id:
                raise WorkflowError("CIC_SCOPE_MISMATCH", 403)
            run["risk"], run["cic"] = risk, cic
            refs["E6"] = emit(case, run_id, "E6", risk, now.isoformat())
            refs["E6a"] = emit(case, run_id, "E6a", cic, now.isoformat())
            policy_input = self._policy_input(case, features, cf, risk, cic, statement)
            policy_input.evidence_ids.update(
                {
                    "R01": refs["E5"]["evidence_id"],
                    "R04": refs["E5"]["evidence_id"],
                    "R08": refs["E5"]["evidence_id"],
                    "R09": refs["E5"]["evidence_id"],
                    "R02": refs["E4"]["evidence_id"],
                    "R03": refs["E4"]["evidence_id"],
                    "R06": refs["E6a"]["evidence_id"],
                    "R07": refs["E6a"]["evidence_id"],
                    "R05": refs["E4"]["evidence_id"],
                    "R10": refs["E6"]["evidence_id"],
                }
            )
            if statement:
                refs["E8"] = emit(case, run_id, "E8", statement, now.isoformat())
                policy_input.evidence_ids["R05"] = refs["E8"]["evidence_id"]
            outcome = self.policy.evaluate(policy_input)
            run["policy_input"] = asdict(policy_input)
            policy = {
                "results": [asdict(r) for r in outcome.results],
                "hard_fail": outcome.hard_fail,
                "policy_hash": self.policy_hash,
                "summary": "; ".join(
                    f"{r.rule_id}: {'đạt' if r.passed else 'cần xử lý'}" for r in outcome.results
                ),
            }
            run["policy"] = policy
            refs["E7"] = emit(case, run_id, "E7", policy, now.isoformat())
            deferred = CIC_RULE_IDS if cic["source"] == DEFERRED else frozenset()
            case.status, case.reason = _route(outcome.results, risk, statement, deferred)
        else:
            case.status = "NO_LOAN_NEEDED"
        case.runs.append(run)
        case.documents.extend(self.memo.compose(case, run, refs, now.isoformat()))
        if not case.documents[-1]["validated"]:
            case.status, case.reason = "ESCALATED", "MEMO_VALIDATION_FAILED"
        self._event(
            case, "ORCHESTRATOR", "STATE_CHANGED", previous, case.reason or "ASSESSMENT_COMPLETED"
        )
        return run

    def inquire_cic(self, case_id: str, request: CicInquiry, actor: str) -> Case:
        with self.repository.transaction(case_id, request.expected_version) as case:
            prior = case.runs[-1] if case.runs else None
            if (
                case.status != "ESCALATED"
                or case.decision
                or not prior
                or not prior["cic"]
                or prior["cic"]["source"] not in {NOT_QUERIED, DEFERRED}
            ):
                raise WorkflowError("CIC_INQUIRY_NOT_ALLOWED")
            now = self.clock()
            cic = {
                **self.cic.inquire(case.seller_id, now),
                "gate": prior["cic"]["gate"],
                "override": {
                    "actor": actor,
                    "reason": request.reason,
                    "requested_at": now.isoformat(),
                },
            }
            self._assess(
                case, prior["input_snapshot"]["features"], prior["statement"], cic_override=cic
            )
            self._event(
                case, actor, "CIC_INQUIRY_OVERRIDE", case.status, "CIC_QUERIED_BY_UNDERWRITER"
            )
        return self.repository.get(case_id)

    @staticmethod
    def _statement_period(case: Case, period_start: str, period_end: str) -> tuple:
        if case.status not in {"AWAITING_STATEMENT", "ESCALATED"}:
            raise WorkflowError("STATEMENT_NOT_REQUESTED")
        prior = case.runs[-1]
        if not prior["policy"] or not any(
            r["rule_id"] == "R05" and not r["passed"] for r in prior["policy"]["results"]
        ):
            raise WorkflowError("STATEMENT_NOT_REQUESTED")
        try:
            start, end = date.fromisoformat(period_start), date.fromisoformat(period_end)
        except ValueError as exc:
            raise WorkflowError("INVALID_STATEMENT_PERIOD", 422) from exc
        rows = prior["input_snapshot"]["features"]["weekly_payouts"]
        selected = [r for r in rows if start <= date.fromisoformat(r["week_start"]) < end]
        if (
            start.weekday() != 0
            or end.weekday() != 0
            or end <= start
            or len(selected) * 7 != (end - start).days
        ):
            raise WorkflowError("STATEMENT_PERIOD_NOT_COVERED", 422)
        return start, end, selected

    def _record_statement(
        self, case: Case, period: tuple, marketplace_total: int, details: dict, actor: str
    ) -> None:
        start, end, selected = period
        platform = sum(r["amount"] for r in selected)
        difference = abs(platform - marketplace_total)
        bps = self.policy.cfg.statement_mismatch_bps
        matched = difference * 10_000 <= platform * bps if platform else difference == 0
        statement = {
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "platform_total": platform,
            "marketplace_total": marketplace_total,
            "difference": difference,
            "threshold_bps": bps,
            "matched": matched,
            **details,
            "rows": deepcopy(selected),
            "message": "khớp trong ngưỡng" if matched else "không khớp; cần xác minh",
        }
        self._assess(case, case.runs[-1]["input_snapshot"]["features"], statement)
        self._event(case, actor, "STATEMENT_RECORDED", case.status, "STATEMENT_COMPARED")

    def submit_statement(self, case_id: str, request: Statement, actor: str) -> Case:
        with self.repository.transaction(case_id, request.expected_version) as case:
            period = self._statement_period(case, request.period_start, request.period_end)
            details = {
                "document_ref": request.document_ref,
                "source": "underwriter_entered_total",
                "verified_by": actor,
            }
            self._record_statement(case, period, request.marketplace_total, details, actor)
        return self.repository.get(case_id)

    def check_upload(self, case_id: str, request: StatementUpload, content: bytes) -> tuple:
        if self.statements is None:
            raise WorkflowError("STATEMENT_READER_UNAVAILABLE", 503)
        current = self.repository.get(case_id)
        if current.version != request.expected_version:
            raise WorkflowError("CASE_VERSION_CONFLICT")
        period = self._statement_period(current, request.period_start, request.period_end)
        self.statements.inspect(content)
        return period

    def upload_statement(
        self,
        case_id: str,
        request: StatementUpload,
        content: bytes,
        actor: str,
        job_id: str | None = None,
    ) -> Case:
        period = self.check_upload(case_id, request, content)
        read = self.statements.read(content)
        details = self.statements.reconcile(read, period[0], period[1])
        total = details.pop("marketplace_total")
        details["original"] = self.documents.put(read.document, case_id)
        if job_id:
            details["job_id"] = job_id
        with self.repository.transaction(case_id, request.expected_version) as case:
            period = self._statement_period(case, request.period_start, request.period_end)
            self._record_statement(
                case,
                period,
                total,
                {**details, "source": "uploaded_file", "submitted_by": actor},
                actor,
            )
        return self.repository.get(case_id)

    def decide(self, case_id: str, request: DecisionInput, actor: str, key: str) -> Case:
        current = self.repository.get(case_id)
        request_hash = digest({"request": request.model_dump(), "actor": actor})
        if current.decision and current.decision["idempotency_key"] == key:
            if current.decision["request_hash"] != request_hash:
                raise WorkflowError("IDEMPOTENCY_KEY_REUSED")
            return current
        with self.repository.transaction(case_id, request.expected_version) as case:
            if case.status not in {"PENDING_REVIEW", "ESCALATED"} or case.decision:
                raise WorkflowError("DECISION_NOT_ALLOWED")
            previous = case.status
            run = case.runs[-1]
            accepted = request.decision_type != "REJECT"
            if accepted:
                if run["cashflow"]["gap"] == 0:
                    raise WorkflowError("NO_LOAN_NEEDED")
                if case.status == "ESCALATED" and not request.verification_note.strip():
                    raise WorkflowError("VERIFICATION_NOTE_REQUIRED")
                if (
                    request.decision_type == "ACCEPT_PROPOSAL"
                    and run["policy_hash"] != self.policy_hash
                ):
                    raise WorkflowError("POLICY_CHANGED_REASSESSMENT_REQUIRED")
                if request.decision_type == "ACCEPT_COUNTER":
                    try:
                        run = self._assess(
                            case, run["input_snapshot"]["features"], run["statement"], request
                        )
                    except ValueError as exc:
                        raise WorkflowError("COUNTER_TERMS_INVALID") from exc
                # Re-evaluate even the original proposal against the current executable rules.
                outcome = self.policy.evaluate(PolicyInput(**run["policy_input"]))
                blocking = _blocking(outcome.results, run["risk"])
                if blocking:
                    raise WorkflowError(
                        "HARD_POLICY_FAILURE", details=[r.rule_id for r in blocking]
                    )
                if run["statement"] and not run["statement"]["matched"]:
                    raise WorkflowError("STATEMENT_MISMATCH")
                doc = case.documents[-1]
                if not doc["validated"] or doc["run_id"] != run["run_id"]:
                    raise WorkflowError("MEMO_NOT_VALIDATED")
                self.memo.validator.validate(case, doc)
                final_policy = [asdict(r) for r in outcome.results]
            else:
                final_policy = None
            now = self.clock()
            cf = run["cashflow"]
            case.decision = {
                "decision_id": str(uuid4()),
                "run_id": run["run_id"],
                "document_id": case.documents[-1]["document_id"],
                "decision_type": request.decision_type,
                "actor": actor,
                "reason": request.reason,
                "verification_note": request.verification_note,
                "final_amount": cf["amount_proposed"] if accepted else None,
                "final_tenor_weeks": cf["tenor_weeks"] if accepted else None,
                "weekly_deductions": cf["weekly_deductions"] if accepted else [],
                "policy_results_final": final_policy,
                "policy_hash": self.policy_hash,
                "decided_at": now.isoformat(),
                "expires_at": (now + timedelta(days=14)).isoformat() if accepted else None,
                "idempotency_key": key,
                "request_hash": request_hash,
            }
            case.status = "ACCEPTED" if accepted else "REJECTED"
            evidence_ids = [e["evidence_id"] for e in case.evidence if e["run_id"] == run["run_id"]]
            payload = {
                "case_id": case.case_id,
                "seller_id": case.seller_id,
                "shop_id": case.shop_id,
                "decision": deepcopy(case.decision),
                "risk": run["risk"],
                "cic": run["cic"],
                "policy": run["policy"],
                "document": deepcopy(case.documents[-1]),
                "evidence_ids": evidence_ids,
            }
            case.handoff = {
                "idempotency_key": str(uuid4()),
                "status": "PENDING",
                "payload": payload,
                "payload_hash": digest(payload),
                "attempts": 0,
                "last_error": None,
                "los_reference": None,
                "lease_until": None,
            }
            self._event(case, actor, "DECISION_RECORDED", previous, request.decision_type)
        return self.repository.get(case_id)

    def handoff(self, case_id: str, version: int, actor: str) -> Case:
        current = self.repository.get(case_id)
        if current.handoff and current.handoff["status"] == "SUCCEEDED":
            return current
        with self.repository.transaction(case_id, version) as case:
            if case.status not in {"ACCEPTED", "REJECTED"} or not case.handoff:
                raise WorkflowError("HUMAN_DECISION_REQUIRED")
            now = self.clock()
            if (
                case.decision["expires_at"]
                and datetime.fromisoformat(case.decision["expires_at"]) <= now
            ):
                raise WorkflowError("DECISION_EXPIRED_REASSESSMENT_REQUIRED")
            handoff = case.handoff
            if handoff["lease_until"] and datetime.fromisoformat(handoff["lease_until"]) > now:
                raise WorkflowError("HANDOFF_IN_PROGRESS")
            if digest(handoff["payload"]) != handoff["payload_hash"]:
                raise WorkflowError("HANDOFF_HASH_MISMATCH")
            handoff.update(
                status="SENDING",
                attempts=handoff["attempts"] + 1,
                lease_until=(now + timedelta(seconds=60)).isoformat(),
            )
            self._event(case, actor, "HANDOFF_ATTEMPT", case.status, "SENDING")
            payload, key = deepcopy(handoff["payload"]), handoff["idempotency_key"]
        try:
            reference, error = self.los.send(payload, key), None
            if not isinstance(reference, str) or not reference.strip():
                reference, error = None, "LOS_INVALID_RESPONSE"
        except Exception:
            reference, error = None, "LOS_UNAVAILABLE"
        with self.repository.transaction(case_id, version + 1) as case:
            case.handoff.update(
                status="FAILED" if error else "SUCCEEDED",
                last_error=error,
                los_reference=reference,
                lease_until=None,
            )
            self._event(
                case,
                actor,
                "HANDOFF_RESULT",
                case.status,
                "HANDOFF_ERROR" if error else "HANDOFF_OK",
            )
        return self.repository.get(case_id)
