# Eval

`golden/` là bộ case chuẩn. Mỗi file là một case: `input` đưa vào engine, `expected` là số phải ra đúng.
`api/tests/test_golden.py` đọc thư mục này nên CI fail nếu ai đổi engine làm lệch số.

Thêm case: chép một file, đổi `case_id`, điền số đã tính tay theo docs/Riverbank-TDD.md FLOW-03, chạy `make test`.
Case có LLM (memo, doc verifier) để ở `golden/llm/` và chạy bằng `eval/run.py` với cache, không chạy trong CI.
