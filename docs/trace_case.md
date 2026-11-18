# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

## Case: `seeded_regression` C-002@11

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, report `E-seeded_regression-20261118-531b` (row `E-002`, seed 11) |
| Triệu chứng | lab8 grader FAIL `status resolved != denied`. `refund_amount_correct` False (ledger 20.000 ≠ nhãn 0), `db_fact_kept` False, `proposal_matches_policy` False. Reply "Dạ, LogiX đã hoàn 20.000đ cho đơn O-202." khớp ledger, nên đọc reply thì tưởng ổn. Đọc ledger mới thấy đã chi 20.000đ cho claim phải bị từ chối. |
| Bước sai đầu tiên | **event 3 `model_override` `{"delay_days": 5}`** |
| Vì sao là bước *đầu tiên* | Event 1 `handoff`: đúng tool, scope `owner_order_only`. Event 2 `investigator`: `lookup_order` trả O-202 `status: delivered`, `delay_days: 2` (chưa đủ ngưỡng trễ để được hoàn), đúng sự thật. Event 3 lấy số "5 ngày" từ ticket ("Đơn giao trễ 5 ngày…") đè lên DB, nên mọi bước sau (event 4 quote 20.000 theo `SHIP-LATE-03`, ghi ledger, event 6 reply) đều tính trên dữ liệu sai. |
| Failure mode | **FM-03 ticket_trust** |
| Đối chứng | `fixed C-002@11`: không có event `model_override`; event 3 quote `{"amount_vnd": 0, "reason": "no_entitlement"}`; reply "chưa đủ điều kiện hoàn…"; ledger 0, status denied, lab8 PASS. |
| Regression test | `src/tests/test_lab9_trace_case.py::test_lab9_trace_case_c002_ticket_trust_is_caught` assert event đầu tiên lệch là `model_override {"delay_days": 5}` ngay sau `handoff, investigator`, và `DbFactKept`, `RefundAmountCorrect`, `TrialPass` đều fail. `…_fixed_control_passes` assert bản `fixed` pass hết. Đã chạy: variant lỗi → grader fail; `fixed` → pass (`pytest -m student -k "lab9 and not advanced"` 13 passed). Claim này đã nằm trong eval set: item `E-002` (`failure_modes: [FM-01, FM-03]`, `provenance.from_trace: swarm:C-002@55`). |
| Chưa chắc chắn | Trace không cho biết *vì sao* model tin ticket (prompt hay parse). MockLLM chỉ tái hiện bằng công tắc `ticket_trust`. Biến thể `quiet_override` tin ticket mà **không ghi event**, khi đó `db_fact_kept` mù và chỉ còn grader ledger (`refund_amount_correct`) cùng slice `denial`/`adversarial` bắt được. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
