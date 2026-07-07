import os

class CONFIG:
	BIN_AVIFENC    = "avifenc"
	BIN_FFMPEG     = "podman"
	DOCKER_FFMPEG  = "linuxserver/ffmpeg:8.1.2"
	MAX_FILE_BYTES = 10 * 1048576 # 10 MB

	THREADS        = os.cpu_count()
