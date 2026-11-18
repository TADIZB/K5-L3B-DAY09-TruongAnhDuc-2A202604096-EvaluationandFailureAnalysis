# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5` (backend REPLAY, positive class = FAIL).

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | **PASS** | dùng (chỉ cho giọng văn, non-blocking) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | **FAIL** (κ, R) | loại |

Ma trận nhầm lẫn:
- tone_ok: tp=24, fp=4, fn=6, tn=66. Tỉ lệ FAIL quan sát 0.28 → hiệu chỉnh Rogan-Gladen 0.30 (sens 0.8, spec 66/70 = 0.943).
- helpful: tp=9, fp=0, fn=21, tn=70. Tỉ lệ FAIL quan sát 0.09 → hiệu chỉnh 0.30. Judge chỉ thấy 1/3 số reply tệ.

## ≥5 case audit thủ công

| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha…" | PASS | FAIL | Người | Xưng "em" + "nha" quá suồng sã với khách khiếu nại. Rubric thêm: *xưng hô phải là "LogiX" / "anh/chị"; "em/bạn/nha/nhé" ⇒ FAIL*. |
| 2 | H-017 "Dạ, bạn ơi LogiX đã hoàn 25.000đ… rồi nha." | PASS | FAIL | Người | Cùng lỗi xưng hô ("bạn ơi"). Dùng chung quy tắc ở #1, thêm 2 ví dụ few-shot FAIL có "bạn". |
| 3 | H-029 "Dạ, vui lòng chờ… không cần nhắn thêm." | PASS | FAIL | Người | Judge chỉ thấy từ lịch sự mở đầu. Rubric: *câu mệnh lệnh / chặn khách liên hệ ("không cần nhắn thêm") ⇒ FAIL, dù có "Dạ"*. |
| 4 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người | Judge phạt vì thiếu xin lỗi. Rubric: *xin lỗi không bắt buộc khi reply đã đủ ý và lịch sự*. |
| 5 | H-051 "Dạ, LogiX đã refund 90.000đ…" | FAIL | PASS | Người | CS LogiX cho phép từ "refund". Rubric thêm whitelist thuật ngữ tiếng Anh được phép (refund, ship, voucher). |
| 6 | H-063 "…đã hoàn 20.000đ và sẽ hoàn thêm…" | PASS | FAIL | Người | Hứa ngoài kết quả hệ thống: không phải lỗi giọng văn mà là lỗi nội dung. Chuyển sang grader code (FM-08 `reply_matches_ledger` / cam kết không có trong ledger), không bắt judge tone gánh. |
| 7 | H-070 "…đã hoàn 90.000đ… ạ 😂😂" | PASS | FAIL | Người | Rubric: *emoji cười trong ngữ cảnh khiếu nại ⇒ FAIL*. |
| 8 | H-081 "Dạ, đơn O-107 chưa đủ điều kiện hoàn…" | FAIL | PASS | **Judge** | Nhãn người sai (đọc nhầm ticket). Hành động: sửa nhãn người trong bộ calibration, ghi lý do. Không đổi rubric. |
| 9 | H-088 "Dạ, dạ, LogiX đã hoàn…" | FAIL | PASS | Người (lỗi gõ nhỏ) | Rubric: *lặp từ do gõ không làm FAIL tone*. |
| 10 | H-094 "Da, LogiX da hoan…" (mất dấu) | PASS | FAIL | Người | Rubric: *tiếng Việt không dấu / dịch máy ⇒ FAIL*. |

Audit helpful (mẫu 21 FN): HL-015, HL-020, HL-062, HL-100… judge cho PASS reply nói "đã hoàn 55.000đ" trong khi **ledger ghi 40.000đ**; HL-047/091/097 "Ok bạn, đơn … xử lý rồi nhé" không cho khách biết kết quả. Judge chỉ đọc văn bản, không thấy ledger, và thiên vị reply dài, nhiều câu xin lỗi.

## Kết luận
- **helpful bị loại**: recall 0.30 (bỏ sót 21/30 reply tệ), κ = 0.375 < 0.6. Precision 1.0 vô nghĩa khi judge gần như luôn nói PASS. Làm gate bằng judge này thì 70% lỗi lọt qua. Phần lớn lỗi nó bỏ sót là **số tiền sai so với ledger**. Đó là việc của code (`ReplyMatchesLedger`, FM-08), vì tiền luôn chấm bằng code.
- **tone_ok dùng được** cho giọng văn, nhưng **non-blocking**. Lý do: n = 100 replay, 10 bất đồng thì 9 do người đúng, và rubric cần sửa theo bảng trên trước khi tin hơn.
- Sửa rubric: thêm quy tắc xưng hô, câu mệnh lệnh, emoji, không dấu; bỏ yêu cầu "phải xin lỗi"; whitelist thuật ngữ. Calibrate lại trên tập nhãn mới.
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai. Cần nhãn người mới để có tập held-out thật, và cần kiểm lại sau mỗi lần sửa rubric.
