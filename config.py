import os

class CONFIG:
	BIN_AVIFENC    = "avifenc"
	BIN_FFMPEG     = "podman"
	DOCKER_FFMPEG  = "linuxserver/ffmpeg:8.1.2"
	MAX_FILE_BYTES = 10 * 1048576 # 10 MB

	THREADS        = os.cpu_count()

	# VAAV1_RESOLUTION_MODULO:
	# If hardware only supports multiples of VAAV1_RESOLUTION_MODULO
	VAAV1_RESOLUTION_MODULO = 16

	# VAAV1_CROP_PIXELS
	# If unsupported resolution, crop up to VAAV1_CROP_PIXELS
	VAAV1_CROP_PIXELS = 2