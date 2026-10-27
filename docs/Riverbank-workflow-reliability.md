# Workflow reliability và evaluation gate

Thay đổi từ main `1d8f85d`, trên nhánh `fix/evaluation-workflow-reliability`. Không đổi công thức CashFlow, policy, nhãn golden nghiệp vụ hoặc quyền quyết định của người duyệt.

## Hồ sơ khởi tạo và recovery

Sau khi kiểm input, consent và feature scope, service lưu case `ANALYZING` cùng snapshot feature trước khi chạy assessment. `Case.progress` nằm trong JSON header hiện có; không cần migration SQL. Public version sau create thành công vẫn là 1.

Checkpoint lưu kết quả cashflow, risk, CIC gate và CIC inquiry sau từng bước. Mỗi bước có input hash; resume kiểm engine version/config, policy hash và risk version để tránh trộn kết quả khác cấu hình. Policy và memo được dựng lại từ kết quả đã khóa, không tái dùng các evidence ID của một draft chưa commit.

Resume kiểm lại consent đã lưu và consent hiện hành từ nguồn dữ liệu; consent hết hạn, bị thu hồi hoặc đổi reference không được dùng để tiếp tục side effect.

Postgres dùng advisory lock theo case, giữ trên cùng connection qua các lần commit checkpoint. Request cạnh tranh nhận `ASSESSMENT_IN_PROGRESS` (409), có thể retry cùng Idempotency-Key. Không có hai assessment đồng thời cho cùng case. Một exception sau CIC không xóa case hoặc kết quả CIC đã checkpoint. Kết quả cuối, evidence, memo và state transition vẫn lưu cùng transaction.

Worker khi khởi động gọi `resume_pending()` cho tối đa 100 case `ANALYZING`. Client retry create cùng key cũng resume được. Case đang dở xuất hiện trong queue và seller-view trả explanation rỗng cho tới khi có memo, không phát nội dung chưa được kiểm.

Nếu một CIC inquiry có intent STARTED nhưng chưa lưu được kết quả, nguồn không khai báo replay-safe sẽ nhận `CIC_RECONCILIATION_REQUIRED` (503). Không tự gọi lại để đoán lần trước có thành công không. `FixedCicSource` và `ProfileCicSource` là nguồn dữ liệu thuần local nên replay-safe. Với nguồn ngoài chưa có hợp đồng idempotency, các inquiry sau khởi tạo hoặc inquiry thủ công bị chặn bằng `CIC_IDEMPOTENCY_CONTRACT_REQUIRED`.

Trước khi nối CIC thật, adapter phải có hợp đồng deduplication/reconciliation kiểm chứng được. Cờ replay-safe là trách nhiệm của adapter; không được bật chỉ để bỏ qua lỗi. Các case reconciliation/version-changed cần vận hành kiểm tra và quy trình nghiệp vụ riêng, không sửa trực tiếp evidence append-only.

Test Postgres chạy child process, ghi CIC checkpoint rồi `os._exit(77)` trước memo; service mới gọi recovery, hoàn tất cùng case và không gọi CIC lần hai. Đây là kiểm chứng crash/restart local; chưa thay thế fault injection trên hạ tầng production.

## Fencing cho statement worker

`Job.attempts` là generation của lần claim. `complete` và `fail` bắt buộc nhận attempt, chỉ cập nhật khi status RUNNING, generation khớp và lease còn hạn. Worker cũ không thể complete/fail sau reclaim hoặc sau khi worker mới hoàn tất.

Statement handler truyền commit guard vào repository transaction. Với Postgres, guard khóa row job và kiểm lease trên **cùng connection/transaction** ghi assessment/evidence/case; giữ khóa tới commit. Memory dùng queue lock và kiểm trước khi ghi case. Nếu lease mất trong khi OCR đang chạy hoặc trước commit, kết quả nghiệp vụ không được ghi. File gốc lưu theo content hash và OCR/model calls có thể được thực hiện lại; không tuyên bố exactly-once cho các API model.

## Copilot facts

LLM được cung cấp `approved_sentences` do code tạo từ facts hiện tại. Output chỉ được ghép từ các câu đã duyệt; validator kiểm text sau chuẩn hóa đơn vị, actor/field/unit và thông tin bắt buộc theo loại câu hỏi. Không nhận văn bản tùy ý chỉ vì chứa đúng tập số.

Câu bịa phí bằng chữ, tráo tiền/kỳ hạn, gán đề xuất thành nhu cầu seller, lời duyệt trái trạng thái hoặc trả lời ngoài phạm vi đều rơi về `FactsReply`. Hỏi lãi/phí khi chưa có biểu phí nhận câu “Chưa có biểu phí từ ngân hàng trong hồ sơ này.” Không lấy nguyên văn memo legacy làm câu được phép để tránh kế thừa lỗi semantic cũ.

Đổi lại, lời văn bị giới hạn theo bộ câu do code quản lý; câu paraphrase ngoài bộ câu sẽ fallback dù có thể đúng nghĩa. Muốn mở rộng nội dung cần thêm câu với facts/roles rõ ràng và case QA tương ứng.

## Evaluation và CI

- `make test`: cùng lint, format và pytest cho local/CI/deploy. `PYTHON` có thể chỉ định interpreter, ví dụ `make test PYTHON=python` trong CI.
- `make eval-gate`: memo gate chạy đầy đủ statement workflow, so mọi expected field; workflow gate kiểm trình tự CASHFLOW/RISK/CIC_GATE/CIC_INQUIRY/POLICY/MEMO, forbidden steps và human decision boundary. Bộ QA static `eval/golden/copilot/ask.json` chứa 80 nhãn cho 16 case hợp lệ × 5 câu hỏi; nhãn lấy từ golden input/expected, không lấy từ output của model khi chấm.
- `make test-postgres`: yêu cầu `WORKFLOW_TEST_DATABASE_URL`, chạy workflow/ETL trên Postgres local riêng. CI có Postgres service và deploy phải qua bước này trước build.
- `make llm-eval ARGS="--judge"`: dùng model thật, chạy case tới trạng thái cuối, chấm câu trả lời bằng nhãn static và judge. Đòi extraction exact-match 100%, không thiếu câu/case, không có critical QA mismatch/judge error/judge flag; fallback tối đa 20%. Đây là ngưỡng release ban đầu, không phải số đo chất lượng model đã đạt.
- Không chạy judge, thiếu nhãn, thiếu kết quả, case selector rỗng, constructor/section lỗi hoặc judge lỗi làm gate fail. `--report-only` cho phép lưu báo cáo khảo sát nhưng report vẫn có `quality_gate.passed=false` khi không đạt. Memo gate cũng fail nếu không chạy case nào.

Report LLM lưu commit, dataset hash gồm labels, judge prompt hash, prompt version, model thật trả về, cached/live calls, token usage, latency p50/p95 và estimated cost. Giá model chưa biết hoặc usage thiếu được giữ là unknown, không giả thành 0. Judge đánh giá cả câu fallback và có câu hỏi về actor/unit, relevance và completeness; đếm riêng attempted/succeeded/errors.

Các nhãn QA được commit và review cùng code, chưa phải một đợt human review độc lập mới. Cần chạy live eval, kiểm 10 case với người độc lập và hiệu chỉnh judge bằng adversarial human labels trước khi dùng số liệu để chứng nhận production.

## Observability và readiness

Run có `trace_id` và `steps[]`: step, span ID, input/output hash, thời điểm, duration, attempt, cached và error code. Không có raw plan, feature, seller text hay credentials trong spans. Usage từ checkpoint của attempt trước được giữ lại khi resume, tránh bỏ chi phí model đã thực hiện. `workflow_elapsed_ms` trên run INITIAL tính từ lúc tạo case, gồm thời gian bị ngắt; `duration_ms` vẫn đo attempt assessment hiện tại.

`GET /v1/studio/traffic` thêm `summary.workflow`: started/completed/in_progress, completion rate, stuck initial assessments, recovered initial assessments, last failed steps và latency target breaches. Đây là metric trên case đã persist, không phải availability của toàn HTTP service.

`latency_target_breaches` dùng `workflow_elapsed_ms` của run INITIAL để tính cả thời gian gián đoạn/recovery; run cũ chưa có trường này dùng `duration_ms`. Phân phối latency p50/p95 hiện có tiếp tục đo thời gian assessment.

`GET /v1/studio/queue-health` chỉ dành cho người ngân hàng, trả count theo trạng thái job, expired leases, retried/recovered và oldest queued seconds. Không trả content hoặc payload tài chính.

`GET /health` tiếp tục là liveness hiện có. `GET /ready` trả 503 nếu dependency check degraded; Docker healthcheck và deploy smoke dùng readiness. CI/deploy không chấp nhận readiness degraded chỉ vì HTTP liveness vẫn 200.

Targets 90 giây / 0,10 USD có sẵn là targets cấu hình, chưa phải SLO đạt được trên production. Còn cần baseline/load test, cảnh báo backlog/stuck/error budget, quan trắc chi phí đầy đủ của dependency thật và liên tục đối chiếu model/prompt/candidate với baseline.

Kafka outbox/Valkey ModelGate của PR #37 không nằm trong nhánh này.

## Kiểm chứng local

- `make test`: 421 passed, 13 skipped; Ruff check/format pass, 131 file đúng format.
- `make test-postgres`: 27 passed, gồm đủ 13 bài integration opt-in ban đầu bị skip; các bài khác trùng lượt offline, không cộng trùng thành tổng test độc lập.
- `make eval-gate`: pass 20/20 golden workflow/memo và 80 QA labels offline.
- Có test process exit sau CIC rồi startup recovery; stale claim không thể complete/fail hoặc ghi case; consent bị revoke không được resume; config engine đổi không được trộn checkpoint.
- Windows chạy GNU Make portable trong `.worktrees/tools/make-portable`, Git Bash làm shell, `PYTHON=../../pr-37-review/api/.venv/Scripts/python.exe` từ worktree. Interpreter này cài đúng dev requirements, thêm hai dependency của PR #37; không dùng key hoặc service production.
- Model LightGBM giữ đúng hash Git blob. `.gitattributes` khóa LF cho model text để checkout Windows không làm native loader abort.
- Log local ở `eval/out/local-validation/` (gitignored). PostgreSQL dùng container tạm, không dùng DB/volume ứng dụng; container được dừng và tự xóa sau kiểm tra.
- Chưa chạy live LLM/Jev, chưa có human review độc lập mới, chưa load test hay xác nhận SLO production.
