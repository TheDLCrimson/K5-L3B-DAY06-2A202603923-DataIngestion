# Day 06 · Data Ingestion Engineering

**Lab cá nhân** · **4 giờ** · **Dữ liệu tự chọn**

Biến tài liệu thành kho tri thức có thể truy xuất cho AI agent, rồi đo xem cách chuẩn bị dữ liệu nào hiệu quả nhất.

## Bài làm: cách chạy

Bài của Lưu Nguyễn Chí Đức (2A202603923, L3B), corpus Luật Đất đai 2013 và 2024; báo cáo ở [report/individual/2A202603923_LuuNguyenChiDuc.md](report/individual/2A202603923_LuuNguyenChiDuc.md).

Cần Docker, Python 3.12 và khoảng 1,1 GB để tải model `intfloat/multilingual-e5-base` ở lần chạy đầu (GPU tùy chọn; bài chạy trên GTX 1650).
Cài `torch` phù hợp với máy trước, rồi chạy từ thư mục `starter/`:

```bash
cd starter
python -m pip install -r requirements.txt
cp .env.example .env                       # Windows PowerShell: Copy-Item .env.example .env
docker compose -p day6_student_starter up -d --wait
python scripts/check_setup.py              # PostgreSQL + pgvector sẵn sàng

python -m src.manifest                     # đọc PDF -> data/manifest.json, results/ingestion_log.csv, data/processed/pages.jsonl
python -m src.extraction_check             # so sánh pypdf và PyMuPDF -> results/extraction_check.csv
python -m src.benchmark                    # kiểm chứng 20 câu hỏi -> benchmark/verification.csv
python -m src.chunking                     # chunk A và B -> results/chunks/, results/chunk_stats.json
python -m src.run --split dev              # embed, lưu pgvector, chạy A/B/C trên dev -> results/traces/, metrics_dev.json, selection.json
python -m src.run --split test             # chỉ chạy khi results/selection.json đã có lựa chọn
python -m src.audit                        # kiểm tra metadata, tổng hợp nhãn -> results/audit/

# Mở rộng tùy chọn: hybrid dense + BM25 (cấu hình D)
python -m src.benchmark --holdout          # kiểm chứng 10 câu holdout -> benchmark/holdout_verification.csv
python -m src.hybrid --stage dev           # chọn cách fusion trên dev -> results/hybrid/selection.json
python -m src.hybrid --stage eval          # so sánh C và D trên dev, test, holdout -> results/hybrid/
```

| Nội dung | Đường dẫn |
| --- | --- |
| Corpus gốc (4 file Công báo, tải từ vbpl.vn) và manifest | [data/raw/](data/raw/), [data/manifest.json](data/manifest.json) |
| Mã nguồn pipeline | [starter/src/](starter/src/) |
| Câu hỏi, chia dev/test, gold evidence, vị trí coherence | [benchmark/](benchmark/) |
| Cấu hình và giả thuyết A/B/C | [artifacts/experiment_log.csv](artifacts/experiment_log.csv) |
| Nhật ký đọc, chunk, trace, chỉ số, thông tin chạy, nhãn audit | [results/](results/) |

| 📄 Dữ liệu | 🧩 Chiến lược | 🎯 Benchmark |
| --- | --- | --- |
| Tối thiểu **30 trang nội dung** | Tối thiểu **3 cấu hình** | Tối thiểu **20 câu hỏi** |

## Bắt đầu nhanh

Dùng [starter PostgreSQL + pgvector](starter/README.md) để dựng database và hoàn thiện các hàm TODO, hoặc tự chọn vector store tương đương. Starter chỉ là khung: **bạn tự hiện thực pipeline và benchmark**.

## 1. Xây pipeline

![Đọc tài liệu, chuẩn hóa, chia chunk, tạo vector, lưu và kiểm tra](assets/ingestion-pipeline.svg)

Chọn tài liệu được phép sử dụng; giữ **ID tài liệu, nguồn, trang/vị trí** và metadata phù hợp. Dùng embedding model thật qua API hoặc chạy local; lưu và truy xuất bằng pgvector, Qdrant, Chroma hoặc một store tương đương.

## 2. So sánh và đánh giá

![Ba chiến lược được đánh giá trên cùng corpus và benchmark dev/test](assets/benchmark-workflow.svg)

Dùng cùng dữ liệu, model và câu hỏi cho cả ba cấu hình. Tách **15 câu dev / 5 câu test** trước khi chạy; chọn cấu hình trên dev, rồi đánh giá test sau khi cố định. Mỗi câu hỏi cần có đoạn bằng chứng và trang nguồn; thiết kế bộ câu hỏi và đánh giá theo [BENCHMARK.md](BENCHMARK.md).

Đánh giá theo năm khía cạnh: **độ chính xác truy xuất**, **tính mạch lạc của chunk**, **tính hữu dụng của metadata**, **chất lượng grounding** và **tác động của chiến lược dữ liệu**. Giải thích lựa chọn bằng kết quả, ví dụ lỗi và các đánh đổi.

## 3. Chuẩn bị bài nộp

1. Chạy pipeline và lưu cấu hình, kết quả top-3, trace cùng nhãn đánh giá thủ công.
2. Điền [thông tin học viên](STUDENT.md), [nhật ký thí nghiệm](artifacts/experiment_log.csv) và bản sao [mẫu báo cáo cá nhân](report/individual/MSSV_HoTen.md).
3. Kiểm tra mã nguồn, cách chạy, manifest dữ liệu và checklist trong [SUBMISSION.md](SUBMISSION.md).

## Tài liệu hướng dẫn

| Bạn cần tìm | Tài liệu |
| --- | --- |
| Setup baseline PostgreSQL + pgvector | [starter/README.md](starter/README.md) |
| Các bước làm và cách tính chỉ số | [GUIDE.md](GUIDE.md) |
| Tiến độ trong 4 giờ | [CHECKPOINTS.md](CHECKPOINTS.md) |
| Năm tiêu chí benchmark và bộ câu hỏi | [BENCHMARK.md](BENCHMARK.md) |
| Tiêu chí chấm điểm | [RUBRIC.md](RUBRIC.md) |
| Quy tắc sử dụng dữ liệu, AI và API | [RULES.md](RULES.md) |
| Cách nộp bài | [SUBMISSION.md](SUBMISSION.md) |

[data/format-example.json](data/format-example.json) chỉ minh họa định dạng, không phải corpus đủ điều kiện. Phần xử lý và benchmark do bạn tự hiện thực; không bắt buộc xây chatbot.
