import os

class PLATFORM:
	BIN_AVIFENC       = "bin/libavif/1.4.2/avifenc"
	BIN_FFMPEG        = "podman"
	DOCKER_FFMPEG     = "linuxserver/ffmpeg:8.0.1"
	MAX_FILE_BYTES    = 1024 * 1048576 # 1 GB

	THREADS           = os.cpu_count()
	CMD_CACHE_ENTRIES = THREADS * 100

	# VAAV1_RESMOD:
	# If hardware only supports multiples of VAAV1_RESMOD_HORIZONTAL x VAAV1_RESMOD_VERTICAL
	VAAV1_RESMOD_HORIZONTAL = 16
	VAAV1_RESMOD_VERTICAL   = 16

