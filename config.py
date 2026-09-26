class metric():
	def __init__(self, default, hq, contribution, power_mean):
		self.default      = default
		self.hq           = hq
		self.contribution = contribution
		self.power_mean   = power_mean
	def get_min(self, hq):
		return self.hq[0] if hq else self.default[0]
	def get_target(self, hq):
		return self.hq[1] if hq else self.default[1]
class filter_t(metric):
	def __init__(self, below_discard, below_discard_hq, contribution=0, power_mean=-5000):
		super().__init__([below_discard]*3, [below_discard_hq]*3, contribution, power_mean)
class target_t(metric):
	def __init__(self, target, target_hq, contribution=1, power_mean=-5000):
		super().__init__([target-0.1, target, target+5.0], [target_hq-0.1, target_hq, target_hq+5.0], contribution, power_mean)

class CONFIG:
	# Keep the original file if (out_bytes / in_bytes) >= COMPRESSION_RATIO_THRESHOLD
	COMPRESSION_RATIO_THRESHOLD = 0.8

	# All filters operate by computing a mean of all channels (RGB / etc)
	# The per-frame means are then averaged using power mean
	# Filters are cutoff values. Meet or beat to pass.
	PSNR        = filter_t(50.33, 50.33)
	SSIM_DB     = filter_t(21.27, 30.95)
	PSNR_HVS_CB = filter_t(49.87, 55.12)
	# Targets allow for values near them
	VMAF_DB     = target_t(12.91, 15.25)

	# VAAV1_CROP_PIXELS
	# If unsupported resolution, crop up to VAAV1_CROP_PIXELS
	VAAV1_CROP_PIXELS = 2
