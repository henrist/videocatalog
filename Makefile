.PHONY: build build-gpu run serve clean

IMAGE := videocatalog
OUTPUT := output
CACHE := cache
PORT := 8000

help:
	@echo "Usage:"
	@echo "  make build                        Build Docker image"
	@echo "  make run INPUT=video.avi          Process a video"
	@echo "  make run ARGS='--gallery-only'    Regenerate gallery"
	@echo "  make run ARGS='--transcribe-only' Transcribe existing clips"
	@echo "  make serve                        Start web server"
	@echo "  make clean                        Remove output files"
	@echo ""
	@echo "Options:"
	@echo "  INPUT=file.avi                    Input video file"
	@echo "  OUTPUT=dir                        Output directory (default: output)"
	@echo "  ARGS='--dry-run'                  Extra arguments"

build:
	docker build -t $(IMAGE) .

build-gpu:
	docker build -f Dockerfile.cuda -t $(IMAGE)-gpu .

run:
	@mkdir -p $(OUTPUT) $(CACHE)
	docker run --rm \
		$(if $(INPUT),-v "$(CURDIR)/$(INPUT):/data/input$(suffix $(INPUT))") \
		-v "$(CURDIR)/$(OUTPUT):/data/output" \
		-v "$(CURDIR)/$(CACHE):/root/.cache/huggingface" \
		$(IMAGE) $(if $(INPUT),"/data/input$(suffix $(INPUT))") --output-dir /data/output $(ARGS)

serve:
	@mkdir -p $(OUTPUT)
	docker run --rm -it \
		-v "$(CURDIR)/$(OUTPUT):/data/output" \
		-p 127.0.0.1:$(PORT):$(PORT) \
		$(IMAGE) --output-dir /data/output --serve --host 0.0.0.0 --port $(PORT)

clean:
	rm -rf $(OUTPUT)/*/ $(OUTPUT)/gallery.html $(OUTPUT)/catalog.json
