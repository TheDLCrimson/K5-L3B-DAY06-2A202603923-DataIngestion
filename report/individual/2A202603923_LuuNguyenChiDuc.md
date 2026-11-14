# Báo cáo cá nhân - Day 6

- **Họ và tên:** Lưu Nguyễn Chí Đức
- **MSSV:** 2A202603923
- **Lớp:** L3B
- **Chủ đề:** Luật Đất đai 2013 và 2024, so sánh ba chiến lược chuẩn bị dữ liệu cho truy xuất.

## Dữ liệu và pipeline

- **Chủ đề, nguồn/quyền, số trang và quy tắc tính trang:**
  - Corpus gồm Luật Đất đai số 45/2013/QH13 (Công báo số 1011 + 1012, Điều 1-212) và Luật Đất đai số 31/2024/QH15 (Công báo số 363 + 364, 365 + 366, 367 + 368, Điều 1-260), tải từ Cơ sở dữ liệu quốc gia về pháp luật (vbpl.vn) ngày 14/11/2026.
  - Văn bản quy phạm pháp luật không thuộc phạm vi bảo hộ quyền tác giả (Luật Sở hữu trí tuệ, Điều 15 khoản 2), nên được lưu và chia sẻ trong repo; không có dữ liệu cá nhân ngoài tên người ký là Chủ tịch Quốc hội.
  - Quy tắc tính trang: một trang PDF vật lý được tính là trang nội dung nếu sau khi bỏ tiêu đề Công báo, số trang in và dòng "(Tiếp theo / Xem tiếp Công báo ...)" còn ít nhất 500 ký tự nội dung luật.
  - Kết quả: 370/373 trang nội dung, 807.033 ký tự; loại 3 trang (2 trang trống cuối số Công báo, 1 trang thông tin nhà xuất bản). Chi tiết từng file, sha256, danh sách trang được tính ở [data/manifest.json](../../data/manifest.json).
  - Luật 2024 được dùng ở bản gốc khi ban hành (Điều 252: hiệu lực 01/01/2025); các luật sửa đổi sau đó không nằm trong corpus, nên mọi đáp án chỉ trích bản gốc này.
- **Chuẩn hóa, lỗi và giới hạn:**
  - Đọc PDF bằng PyMuPDF 2.1 thay vì pypdf, vì pypdf tách rời âm tiết tiếng Việt ("Nguyên t ắc b ồi th ường"): 63-73 chữ cái lẻ trên 1000 token với pypdf so với 1,0-1,7 với PyMuPDF, và phần còn lại của PyMuPDF là từ thật như "điểm a", "ô nhiễm" ([results/extraction_check.csv](../../results/extraction_check.csv)).
  - Chuẩn hóa Unicode NFC, thống nhất khoảng trắng, bỏ tiêu đề Công báo, ghép các dòng bị ngắt thành một dòng cho mỗi chương, mục, điều, khoản, điểm.
  - Kiểm tra: tìm thấy đủ Điều 1-212 (2013) và 1-260 (2024), mỗi điều đúng một lần và đúng thứ tự; mọi trang nội dung có số trang Công báo in.
  - Mọi trang đều được ghi trạng thái và lý do trong [results/ingestion_log.csv](../../results/ingestion_log.csv); không có trang lỗi đọc, không cần OCR.
  - Giới hạn: một số tiêu đề điều bị xuống dòng trước chữ hoa (ví dụ "theo\nLuật số 21/20..." ở Điều 243) bị tách thành hai dòng.
- **Embedding model/provider/version/số chiều, vector store/version, metric, lệnh chạy và chi phí:**
  - `intfloat/multilingual-e5-base` chạy local qua sentence-transformers 6.2.0, revision `d128750597153bb5987e10b1c3493a34e5a4502a`, 768 chiều, vector chuẩn hóa L2; tiền tố `query:` cho câu hỏi và `passage:` cho chunk (kèm một dấu cách) theo model card.
  - Chunk dài nhất 317 token (B) và 198 token (A), dưới giới hạn 512, nên không chunk nào bị cắt ngầm; code báo lỗi nếu vượt.
  - PostgreSQL 16.16 + pgvector 0.8.7 (Docker), một bảng `chunks` khóa `(strategy, chunk_id)`, metric cosine (`<=>`), tìm kiếm chính xác (không dùng index ANN) để filter không làm hụt ứng viên.
  - Chạy trên GPU NVIDIA GeForce GTX 1650; chi phí 0 USD (model và database đều local, không đo điện năng).
  - Lệnh chạy ở [README.md](../../README.md#bài-làm-cách-chạy).
- **Metadata bắt buộc, trường lọc bổ sung, nguồn giá trị và lý do chọn:**
  - Mỗi chunk có `document_id`, `source` (tên file PDF), `page` và `page_end` (trang PDF vật lý), `year`, `law_number`, `article` (điều tại đầu chunk), `articles`, `chapter`, vị trí ký tự và `content_hash`.
  - Trường lọc là `year` (2013 hoặc 2024), lấy từ số hiệu luật của từng file, không suy đoán.
  - Lý do: hai phiên bản luật có nhiều khoản gần như trùng nhau nhưng khác số liệu (ví dụ 10 lần so với 15 lần hạn mức, 45 ngày so với 30 ngày), nên câu hỏi nêu rõ phiên bản cần một filter đáng tin.

## Cấu hình và benchmark

- **A / B / C (tham số cụ thể, khác biệt duy nhất B–C):**
  - Cả A và B cắt cùng một luồng văn bản liên tục của từng file (các trang nội dung nối theo thứ tự), nên chunk có thể vắt qua trang và ghi khoảng trang.
  - **A:** cửa sổ ký tự cố định 500, overlap 50, không quan tâm từ hay khoản: 1.797 chunk.
  - **B:** theo cấu trúc luật, tối đa 1.000 ký tự; một điều vừa thì giữ nguyên, nếu dài thì gom các khoản liên tiếp, khoản quá dài thì tách theo điểm, rồi theo câu; chunk sau của cùng điều được lặp tiêu đề điều làm tiền tố: 1.264 chunk.
  - **C:** đúng các chunk của B, chỉ thêm filter `year` khi câu hỏi nêu phiên bản luật (10/20 câu); 10 câu còn lại chạy y hệt B.
  - A–B chỉ đổi cách chia chunk (cùng luồng, model, store, metric, câu hỏi); lưu ý trần kích thước của B (1.000) lớn hơn cửa sổ của A (500), nên A–B đổi cả ranh giới lẫn kích thước. B–C chỉ đổi filter.
  - Tham số và giả thuyết được ghi trước khi chạy retrieval trong [artifacts/experiment_log.csv](../../artifacts/experiment_log.csv).
- **≥20 câu: phân chia ≥15 dev / ≥5 test, câu metadata filter và cách kiểm chứng evidence:**
  - 20 câu, 15 dev / 5 test, chốt trong [benchmark/questions.json](../../benchmark/questions.json) trước lần chạy đầu: 9 câu trực tiếp, 5 câu diễn đạt lại, 6 câu điều kiện hoặc ngoại lệ.
  - 10 câu có filter (8 dev, 2 test); filter chỉ được gắn khi chính câu hỏi nêu phiên bản ("Theo Luật Đất đai 2013 ..."), nên giá trị lọc do người hỏi cung cấp, không lấy từ gold.
  - Mọi câu không có filter đều có cùng đáp án ở cả hai luật, và cả hai trang được ghi làm gold hợp lệ.
  - Chia theo chủ đề: mỗi cặp câu khác phiên bản nằm cùng một tập, không câu test nào trùng điều luật với câu dev; rủi ro còn lại là q13 (dev) và q18 (test) đều nói về dự án đầu tư.
  - Kiểm chứng evidence: [starter/src/benchmark.py](../../starter/src/benchmark.py) xác nhận 29/29 đoạn trích nằm đúng trang gold sau chuẩn hóa, không đoạn nào bị chép vào câu hỏi, và in vị trí điều/khoản để đối chiếu với đáp án ([benchmark/verification.csv](../../benchmark/verification.csv)).
- **Tiêu chí chọn chiến lược đã chốt trước khi xem test:**
  - Ghi trong [benchmark/README.md](../../benchmark/README.md) trước lần chạy đầu: (1) Evidence Hit@3 trên dev, (2) Hit@1 trên dev, chênh lệch không quá 1/15 coi là hòa, (3) coherence trung bình, (4) ít chunk và thời gian ingest thấp hơn.
  - Trên dev, B và C hòa ở bước 1 và 2 (Hit@3 14 so với 15, Hit@1 12 so với 13); bước 3 và 4 không thể tách vì C dùng chính chunk của B.
  - Sau khi chạy dev và trước khi chạy test (11:57 ngày 14/11), tôi bổ sung bước 5: giữa các cấu hình dùng chung chunk, chọn cấu hình có top-3 nằm đúng phiên bản luật được hỏi nhiều hơn trên các câu dev có filter (C 8/8, B 3/8). C được chọn theo bước này; bản ghi ở [results/selection.json](../../results/selection.json).
  - Lệnh chạy test từ chối chạy khi chưa có lựa chọn được ghi, nên kết quả test không ảnh hưởng tới lựa chọn.

## Kết quả

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian | Chi phí |
| --- | --- | --- | --- | --- | --- | --- |
| A | dev | 8/15 | 11/15 | 1.797 | ingest 35,7 s (embed 34,5 s) | 0 USD |
| A | test | 1/5 | 4/5 | 1.797 | dùng lại index | 0 USD |
| B | dev | 12/15 | 14/15 | 1.264 | ingest 35,2 s (embed 34,1 s) | 0 USD |
| B | test | 3/5 | 4/5 | 1.264 | dùng lại index | 0 USD |
| C | dev | 13/15 | 15/15 | 1.264 (của B) | không ingest thêm | 0 USD |
| C | test | 4/5 | 4/5 | 1.264 (của B) | không ingest thêm | 0 USD |

Chia chunk mất 1,2 s cho cả hai chiến lược; mỗi truy vấn mất trung vị khoảng 23 ms để embed câu hỏi và 35 ms để tìm trong pgvector.
Nạp lại cùng corpus không ghi gì: 0 chèn, 0 cập nhật, 0 xóa, số dòng giữ nguyên 1.797 (A) và 1.264 (B).
Mô phỏng tài liệu thay đổi (5 chunk, sửa 1, bỏ 1) cho đúng 1 cập nhật, 1 xóa, còn 4 dòng ([results/run_info_dev.json](../../results/run_info_dev.json)).

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N hoặc N/A) | Grounding 0/1/2 (số câu) và trung bình /2 |
| --- | --- | --- | --- | --- |
| A | 1,2 (10) | 15/15 | N/A (không áp filter) | 0/1/4, 1,8 |
| B | 1,8 (10) | 15/15 | N/A (không áp filter) | 1/0/4, 1,6 |
| C | 1,8 (10, cùng chunk với B) | 15/15 | 2/2 | 1/0/4, 1,6 |

Trên các câu có filter, tỷ lệ câu mà cả top-3 thuộc đúng phiên bản được hỏi là 3/8 (A), 3/8 (B), 8/8 (C) trên dev và 0/2, 0/2, 2/2 trên test.

Đường dẫn trace, nhãn thủ công và cách chọn mẫu:

- Trace top-3 đầy đủ (câu hỏi, chunk ID, text, score, metadata, nhãn hit): [results/traces/](../../results/traces/); chỉ số: [results/metrics_dev.json](../../results/metrics_dev.json), [results/metrics_test.json](../../results/metrics_test.json).
- Coherence: 10 vị trí là điểm giữa của 10 khoản được chọn ngẫu nhiên với seed cố định 20261114 trước khi có bất kỳ điểm số nào ([benchmark/coherence_positions.json](../../benchmark/coherence_positions.json)); nhãn và lý do ở [results/audit/coherence_labels.csv](../../results/audit/coherence_labels.csv).
- Grounding: 5 câu test × top-3 của mỗi cấu hình, nhãn ở [results/audit/grounding_labels.csv](../../results/audit/grounding_labels.csv).
- Metadata: 5 câu test × top-3 × 3 cấu hình = 45 bản ghi; `source`, khoảng trang, `year` và `article` được đối chiếu với văn bản trang đã trích xuất (sinh bởi bước đọc PDF, độc lập với bước chia chunk) và manifest ([results/audit/metadata_check.csv](../../results/audit/metadata_check.csv)).
- Người chấm nhãn là chính tác giả, nên có nguy cơ thiên lệch; nhãn coherence và grounding được AI gợi ý bản nháp, tác giả đọc lại từng chunk trong [results/audit/review_sheet.md](../../results/audit/review_sheet.md) và xác nhận.

## Phân tích và kết luận

- **So sánh A–B và B–C; 2 ví dụ sai có ID câu và nguồn:**
  - **A–B (chỉ đổi chia chunk):** B tăng Hit@1 dev từ 8/15 lên 12/15 và Hit@3 từ 11/15 lên 14/15, coherence từ 1,2 lên 1,8, với ít chunk hơn (1.264 so với 1.797) và thời gian ingest tương đương.
    Ngay trước retrieval, 4/15 đoạn evidence dev (q01, q05, q06, q08) không nằm trọn trong bất kỳ chunk A nào, nên Hit@3 của A trên dev không thể vượt 11/15.
  - **B–C (chỉ đổi filter):** C chỉ khác B ở các câu có filter: Hit@3 trên câu có filter tăng từ 7/8 lên 8/8 (dev), và top-3 luôn đúng phiên bản được hỏi (8/8 so với 3/8).
    Trên câu không có filter, B và C trả về đúng cùng kết quả (7/7 dev).
  - **Ví dụ sai 1, q05 (dev, ldd2013 trang 107, Điều 191 khoản 3):** câu hỏi theo Luật 2013, nhưng B xếp hạng 1 khoản về đất trồng lúa của Luật 2024 (`B-ldd2024_cb363-0122`, Điều 45) và không có evidence trong top-3; C lọc `year = 2013` và tìm được Điều 191 ở hạng 3.
    A cũng trượt câu này vì cửa sổ 500 ký tự cắt đôi đoạn evidence.
  - **Ví dụ sai 2, q12 (test, ldd2013 trang 52 hoặc ldd2024_cb365 trang 43):** câu diễn đạt lại "ba anh em cùng đứng tên ... sổ đỏ" trượt ở cả ba cấu hình.
    B và C tìm đúng Điều 135 nhưng lấy khoản 5 về hộ gia đình (`B-ldd2024_cb365-0127`, trang 44-45) thay vì khoản 2 về người có chung quyền, nên grounding bằng 0; A lấy đúng trang nhưng cửa sổ cắt trước câu "cấp cho mỗi người 01 Giấy chứng nhận", nên grounding bằng 1.
  - Đây là trường hợp grounding không đi cùng Hit@3: A có grounding trung bình 1,8 so với 1,6 của B và C chỉ nhờ một câu.
- **Chiến lược chọn, đánh đổi, điều kiện áp dụng:**
  - Chọn C: chia chunk theo cấu trúc luật và lọc theo `year` khi người dùng nêu phiên bản.
  - Đánh đổi: C cần người dùng (hoặc ngữ cảnh) cung cấp phiên bản trước khi truy xuất; nếu câu hỏi không nêu, C chạy như B và có thể trả về khoản của phiên bản cũ ở hạng cao.
  - Chunk B lớn hơn (trung vị 758 ký tự) giúp giữ trọn khoản nhưng có thể làm loãng tín hiệu với câu hỏi chỉ khớp một ý nhỏ trong một điều dài, như q12.
  - Kết luận chỉ áp dụng cho corpus văn bản luật có cấu trúc điều/khoản rõ và có nhiều phiên bản; không suy rộng ra loại tài liệu khác.
- **Hạn chế của dữ liệu, nhãn, test nhỏ, đo thời gian/chi phí:**
  - Tập test chỉ 5 câu: một câu bằng 20 điểm phần trăm; cả ba cấu hình hòa nhau ở Hit@3 test (4/5), khác biệt test chỉ nằm ở Hit@1 (1, 3, 4 trên 5), nên không thể khẳng định C tốt nhất nói chung.
  - Tiêu chí chọn phải bổ sung bước 5 sau khi xem dev vì quy tắc ban đầu không lường trước B và C dùng chung chunk; bước này được ghi kèm thời điểm và trước khi chạy test.
  - Câu hỏi, đáp án và nhãn đều do tác giả viết trong lúc tìm trong corpus, nên bộ câu hỏi nghiêng về các khoản dễ tìm bằng từ khóa; câu diễn đạt lại chỉ giảm một phần thiên lệch này.
  - `page` là số trang PDF vật lý, lệch 2 so với số trang in trên Công báo (ví dụ trang PDF 112 là trang Công báo 114), nên người đối chiếu bản giấy dễ nhầm; [results/ingestion_log.csv](../../results/ingestion_log.csv) và metadata_check.csv ghi cả hai số trang.
  - Thời gian đo một lần trên một máy (GTX 1650), không lặp lại; chi phí điện năng không đo.
- **Nếu làm lại hoặc mở rộng, cải tiến gì?**
  - Thêm câu hỏi diễn đạt lại bằng ngôn ngữ đời thường và một tập test mới lớn hơn, vì đây là chỗ cả ba cấu hình yếu (q12).
  - Thử hybrid dense + BM25 trên chunk B, chọn tham số trên dev, vì số liệu và thuật ngữ luật ("45 ngày", "hạn mức") hợp với tìm theo từ khóa.
  - Đưa số trang Công báo in vào metadata chunk để trích dẫn khớp với bản giấy, và thêm trường hiệu lực (`effective_from`, `effective_to`) để tự lọc phiên bản đang có hiệu lực khi người dùng không nêu năm.

## Sử dụng AI

Công cụ: Claude Code (Claude) trong VS Code, dùng xuyên suốt bài.

| Loại sử dụng | AI làm gì | Tôi kiểm chứng thế nào |
| --- | --- | --- |
| Tìm nguồn | Tìm bản Công báo chính thức của hai luật, phát hiện một file "Luật Đất đai 2013" trên mạng thực ra là luật 2003 | Tải bản gốc từ vbpl.vn, đối chiếu số Công báo, số điều và sha256 |
| Viết code | Viết pipeline đọc PDF, chuẩn hóa, chia chunk A/B, embedding, pgvector, đánh giá, audit | Chạy toàn bộ pipeline với các kiểm tra tự động: đủ Điều 1-212 và 1-260, không mất ký tự, chunk B không chồng nhau, nạp lại không ghi thêm; mỗi phần được tôi duyệt và commit riêng |
| Soạn benchmark | Gợi ý 20 câu hỏi từ các khoản trong corpus kèm đoạn evidence | Script xác nhận 29/29 evidence đúng trang; tôi duyệt và commit bộ câu hỏi cùng cách chia dev/test trước lần chạy đầu |
| Kiểm tra và nhãn | Gợi ý nhãn coherence và grounding kèm lý do, kiểm tra metadata tự động | Tôi đọc các chunk trong review sheet và xác nhận cả 35 nhãn |
| Quyết định | Chỉ ra quy tắc chọn không tách được B và C | Tôi quyết định bổ sung bước 5 và chọn C, ghi lại trước khi chạy test |
| Viết báo cáo | Soạn bản nháp báo cáo từ kết quả đã chạy | Đối chiếu mọi con số với file kết quả trong repo |
