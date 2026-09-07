import os

class metric():
	def __init__(self, default, hq):
		self.default = default
		self.hq      = hq
	def get_min(self, hq):
		return self.hq[0] if hq else self.default[0]
	def get_target(self, hq):
		return self.hq[1] if hq else self.default[1]
class filter_t(metric):
	def __init__(self, below_discard, below_discard_hq):
		super().__init__([below_discard]*3, [below_discard_hq]*3)
class target_t(metric):
	def __init__(self, target, target_hq):
		super().__init__([target-0.1, target, target+5.0], [target_hq-1.0, target_hq, target_hq+5.0])

class CONFIG:
	BIN_AVIFENC    = "avifenc"
	BIN_FFMPEG     = "podman"
	DOCKER_FFMPEG  = "linuxserver/ffmpeg:8.1.2"
	MAX_FILE_BYTES = 10 * 1048576 # 10 MB

	THREADS        = os.cpu_count()
	CMD_CACHE      = THREADS * 100

	# Keep the original file if (out_bytes / in_bytes) >= COMPRESSION_RATIO_THRESHOLD
	COMPRESSION_RATIO_THRESHOLD = 0.8

	PSNR    = filter_t(30, 56) # If PSNR falls below this, 
	SSIM_DB = target_t(17, 25)
	VMAF_DB = target_t(15, 17)

	# VAAV1_RESOLUTION_MODULO:
	# If hardware only supports multiples of VAAV1_RESOLUTION_MODULO
	VAAV1_RESMOD_HORIZONTAL = 16
	VAAV1_RESMOD_VERTICAL   = 16

	# VAAV1_CROP_PIXELS
	# If unsupported resolution, crop up to VAAV1_CROP_PIXELS
	VAAV1_CROP_PIXELS = 2