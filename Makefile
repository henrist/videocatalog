.PHONY: build build-gpu split transcribe gallery clean help

IMAGE := videocatalog
OUTPUT := output
CACHE := cache

# Default input file (override with: make split INPUT=video.avi)
INPUT ?=

help:
	@echo "Usage:"
	@echo "  make build                    Build Docker image"
	@echo "  make split INPUT=video.avi    Split video + transcribe + gallery"
	@echo "  make transcribe               Transcribe existing videos"
	@echo "  make gallery                  Regenerate gallery only"
	@echo "  make clean                    Remove output files"
	@echo ""
	@echo "Options:"
	@echo "  INPUT=file.avi                Input video file"
	@echo "  OUTPUT=dir                    Output directory (default: output)"
	@echo "  ARGS='--dry-run'              Extra arguments"

build:
	docker build -t $(IMAGE) .

build-gpu:
	docker build -f Dockerfile.cuda -t $(IMAGE)-gpu .

split:
ifndef INPUT
	$(error INPUT is required. Usage: make split INPUT=video.avi)
endif
	@mkdir -p $(OUTPUT) $(CACHE)
	docker run --rm -e PYTHONUNBUFFERED=1 \
		-v "$(CURDIR)/$(INPUT):/data/input$(suffix $(INPUT))" \
		-v "$(CURDIR)/$(OUTPUT):/data/output" \
		-v "$(CURDIR)/$(CACHE):/root/.cache/huggingface" \
		$(IMAGE) "/data/input$(suffix $(INPUT))" --output-dir /data/output $(ARGS)

transcribe:
	@mkdir -p $(OUTPUT) $(CACHE)
	docker run --rm -e PYTHONUNBUFFERED=1 \
		-v "$(CURDIR)/$(OUTPUT):/data/output" \
		-v "$(CURDIR)/$(CACHE):/root/.cache/huggingface" \
		$(IMAGE) --output-dir /data/output --transcribe-only $(ARGS)

gallery:
	@mkdir -p $(OUTPUT)
	docker run --rm -e PYTHONUNBUFFERED=1 \
		-v "$(CURDIR)/$(OUTPUT):/data/output" \
		$(IMAGE) --output-dir /data/output --gallery-only --skip-transcribe $(ARGS)

dry-run:
ifndef INPUT
	$(error INPUT is required. Usage: make dry-run INPUT=video.avi)
endif
	docker run --rm \
		-v "$(CURDIR)/$(INPUT):/data/input$(suffix $(INPUT))" \
		$(IMAGE) "/data/input$(suffix $(INPUT))" --output-dir /data/output --dry-run $(ARGS)

clean:
	rm -rf $(OUTPUT)/*.mp4 $(OUTPUT)/*.txt $(OUTPUT)/thumbs $(OUTPUT)/gallery.html

clean-all:
	rm -rf $(OUTPUT) $(CACHE)
