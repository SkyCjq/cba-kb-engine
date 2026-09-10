SCRIPTS = 50_scripts_自动化脚本

.PHONY: probe fetch diff pdf-audit inbox-dry inbox-scan inbox-canary inbox inbox-archive-canary inbox-archive test-v1-1 ingest classify bundles upload upload-dry sync wechat
probe:      ; python3 "$(SCRIPTS)/fetch_cba.py" --probe 6a97f61ed5
fetch:      ; python3 "$(SCRIPTS)/fetch_cba.py" --all
diff:       ; python3 "$(SCRIPTS)/fetch_cba.py" --diff 2026-2027
PDF_DIR ?= .
pdf-audit:  ; python3 "$(SCRIPTS)/audit_pdf_text_layers.py" "$(PDF_DIR)" --csv PDF_TEXT_LAYER_AUDIT.csv

# v1.1 Register-first：默认不移动 Drive 原件。
inbox-dry:  ; python3 "$(SCRIPTS)/process_inbox.py" --scan --dry-run
inbox-scan: ; python3 "$(SCRIPTS)/process_inbox.py" --scan
inbox-canary: ; python3 "$(SCRIPTS)/process_inbox.py" --extract-canary
inbox:      ; python3 "$(SCRIPTS)/process_inbox.py" --process
# 只有显式 archive 命令才修改 Drive parent；先跑 mutation canary。
inbox-archive-canary: ; python3 "$(SCRIPTS)/process_inbox.py" --archive-canary
inbox-archive: ; python3 "$(SCRIPTS)/process_inbox.py" --archive

test-v1-1: ; cd "$(SCRIPTS)" && python3 -m unittest -v test_fetch_cba_parser.py test_process_inbox_v1_1.py

ingest:     ; python3 "$(SCRIPTS)/ingest_wechat.py" ~/Downloads
classify:   ; python3 "$(SCRIPTS)/classify_notes.py"
bundles:    ; @echo "v1.1 FINAL: legacy bundles build is disabled; 40_ai is derived and must not be rebuilt from archived pre-MASTER tables."; exit 2
upload-dry: ; python3 "$(SCRIPTS)/upload_drive.py" --dry-run
upload:     ; python3 "$(SCRIPTS)/upload_drive.py" --verify-folders
# 微信链路：ima 优先；浏览器剪藏仍保留为已可运行入口，直到 ima→Drive 正文归档跑通。
wechat: ingest classify bundles upload-dry
# 全量：先抓取/生成，再 dry-run。确认无误后单独 make upload。
sync: ; @echo "v1.1 FINAL: legacy full sync is disabled. Run source acquisition explicitly; rebuild MASTER via the approved pipeline before publishing."; exit 2
