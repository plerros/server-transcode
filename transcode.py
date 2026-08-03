import argparse
import contextlib
import math
import multiprocessing
import os
from   pathlib import Path
import pathlib
import re
from   scipy.optimize import root_scalar
import shutil
import signal
import subprocess
import tempfile
import time

from config import CONFIG

ROOT         = Path("root")
USER_PRIVATE = ROOT / "in/user_private"
IN_FOLDER    = ROOT / "in/folder"
IN_MEDIA     = ROOT / "in/media"
OUT          = ROOT / "out"
STATS_CSV    = ROOT / "stats/csv"
LOCAL_TMP    = ROOT / "tmp"

lock_stdout = multiprocessing.Lock()
lock_gpu    = multiprocessing.Lock()

lock_folder = multiprocessing.Lock()
lock_media  = multiprocessing.Lock()

def Bold(string: str):
	return ("\033[1m"  + string + "\033[0m")
def Cyan(string: str):
	return ("\033[96m" + string + "\033[0m")
def Green(string: str):
	return ("\033[92m" + string + "\033[0m")
def Red(string: str):
	return ("\033[91m" + string + "\033[0m")
def Yellow(string: str):
	return ("\033[93m" + string + "\033[0m")

class msg:
	def __init__(self, indicator="", effects=[]):
		self.indicator = indicator
		self.effects   = effects
	def apply_effects(self, string):
		for i in self.effects:
			string = i(string)
		return string
	def print(self, string):
		self.string(string, print_out=True)
	def string(self, string, print_out=False):
		indicator = self.apply_effects(self.indicator)
		indicator_length = len(self.indicator)
		string = str(string)

		indicator2 = (indicator_length) * ' '
		ret = ""
		for i in string.splitlines():
			ret += indicator + " " + i + "\n"
			indicator = indicator2
		if (print_out):
			with lock_stdout:
				print(ret, end='', flush=True)
		return (ret)

msg_error  = msg("[error ]", [Bold, Red])
msg_exec   = msg("[ exec ]", [Bold, Green])
msg_info   = msg("[ info ]", [Bold, Yellow])
msg_status = msg("[status]")
msg_stdout = msg("[stdout]", [Bold])
msg_stderr = msg("[stderr]", [Bold])

def grep(pattern: re.Pattern, string: str, idx=0):
	result = re.findall(pattern, string)
	if (not result):
		return ""
	return result[idx]

def append_line(file_path: str, strings=[""], csv=[]) -> None:
	line=""
	for i in strings:
		line += str(i)
	for i in csv:
		line += ","+str(i)

	msg_info.print(line)
	with open(file_path, mode='a', encoding='utf-8') as f:
		f.write(line+"\n")

def read_binary_file(path: str) -> bytes:
	with open(path, "rb") as f:
		return f.read()

def write_binary_file(path: str, data: bytes) -> None:
	with open(path, "wb") as f:
		f.write(data)

def container_permit(file: Path):
	return ["-v", str(file.parent)+':'+str(file.parent)+":z"]

# Globally accessible mapping to denote if an object is fully functional
class Functional():
	def __init__(self):
		self.values: dict[type, bool] = {}
	def is_True(self, obj):
		tmp = self.values.get(type(obj).__name__)
		if (tmp is None):
			return False
		return tmp
	def is_False(self, obj):
		tmp = self.values.get(type(obj).__name__)
		if (tmp is None):
			return False
		return (not tmp)
	def is_None(self, obj):
		tmp = self.values.get(type(obj).__name__)
		return (tmp is None)
	def set(self, obj, value: bool):
		self.values[type(obj).__name__] = value

functional = Functional()

class Command():
	def __init__(self, execName):
		self.execName = execName
		self.args     = []
	def set(self, args):
		self.args = args
	def run(self):
		strings = [self.execName] + [str(i) for i in self.args]
		msg_exec.print(' '.join(strings))
		result = subprocess.run(strings, capture_output=True, check=True, start_new_session=True)
		tmp = result.stdout.decode('utf‑8')
		if (tmp != ""):
			msg_stdout.print(tmp)
		tmp = result.stderr.decode('utf‑8')
		if (tmp != ""):
			msg_stderr.print(tmp)
		return result
	def check_dependencies(self):
		return ""
	def check_exec_exists(self):
		ret = ""
		if (shutil.which(self.execName) is None):
			ret = msg_error.string("executable " + self.execName + " missing")
			functional.set(self, False)
		return ret
	def check_exec_args(self):
		return ""
	def works_str(self):
		string = ""
		print_out = False
		if (functional.is_None(self)):
			print_out = True
		if (functional.is_False(self)):
			string = "failed earlier"
		try:
			for i in [self.check_dependencies, self.check_exec_exists, self.check_exec_args]:
				if (not functional.is_None(self)):
					break
				string += i()
		except subprocess.CalledProcessError as e:
			string = str(e)
			functional.set(self, False)

		if (functional.is_None(self)):
			functional.set(self, True)

		msg_type = msg_error
		if (functional.is_True(self)):
			msg_type = msg_info
			string = "OK"

		return msg_type.string(type(self).__name__ + ": " + string, print_out=print_out)

	def works(self):
		self.works_str()
		return functional.is_True(self)

class Avifenc(Command):
	def __init__(self):
		super().__init__(CONFIG.BIN_AVIFENC)
	def set(self, source: Path, destination: Path, yuv, q):
		super().set(["-j", "1", "--yuv", yuv, "-q", q, "--speed", "0", "--codec", "aom", source, destination])
		return self
	def check_dependencies(self):
		ret = "\n"
		if (not Ffmpeg_psnr().works()):
			functional.set(self, False)
			ret += Ffmpeg_psnr().works_str()
		return ret

class Cmd_7za(Command):
	def __init__(self):
		super().__init__("7za")
	def set(self, folder: Path, out_7z: Path):
		super().set(["a", "-t7z", "-m0=lzma2", "-mx=9", "-mfb=273", "-md=29", "-ms=8g", "-mmt=off", "-mmtf=off", "-mqs=on", "-bt", "-bb3", out_7z, folder])
		return self

class Exiftool_orientation(Command):
	def __init__(self):
		super().__init__("exiftool")
	def set(self, path:Path):
		super().set(["-orientation", path])
		return self

class Ffmpeg(Command):
	def __init__(self):
		super().__init__(CONFIG.BIN_FFMPEG)

	def set(self, ffmpeg_args):
		args = []
		if (self.execName in {"docker", "podman"}):
			permissions = []
			for i in ffmpeg_args:
				if (isinstance(i, (pathlib.PurePath))):
					permissions += container_permit(i)
			args += ["run", "--rm"]
			args += ["--device", "/dev/dri/renderD128"]
			args += permissions
			args += [CONFIG.DOCKER_FFMPEG, "-stats"]

		args += ffmpeg_args
		super().set(args)

class Ffmpeg_aomav1(Ffmpeg):
	def set(self, source: Path, destination: Path, crf, max_bytes=None):
		video_args = ["-i", source, "-c:v", "libaom-av1", "-b:v", 0, "-crf", crf, "-quality", "good", "-speed", 0]
		audio_args = ["-c:a", "libopus", "-b:a", "128k", destination]
		if (max_bytes is not None):
			video_args += ["-fs", max_bytes]

		super().set(video_args + audio_args)
		return self
	def check_dependencies(self):
		ret = "\n"
		if (not Ffmpeg_psnr().works()):
			functional.set(self, False)
			ret +=  Ffmpeg_psnr().works_str()
		if (not Ffmpeg_vmaf().works()):
			functional.set(self, False)
			ret += Ffmpeg_vmaf().works_str()
		return ret

class Ffmpeg_crop(Ffmpeg):
	def set(self, source: Path, destination: Path, dst_resolution):
		ffmpeg_stats = Ffmpeg_stats().set(source)
		ffmpeg_stats.run()
		resolution = ffmpeg_stats.resolution()

		offset = [0, 0]
		for i in [0, 1]:
			if (dst_resolution[i] > resolution[i]):
				dst_resolution[i] = resolution[i]
			offset[i] = int((resolution[i] - dst_resolution[i]) / 2)

		super().set(["-i", source, "-vf", "crop="+str(dst_resolution[0])+":"+str(dst_resolution[1])+":"+str(offset[0])+":"+str(offset[1]), destination])
		return self

	def check_exec_args(self):
		ret = ""
		if (not Ffmpeg_random().works()):
			functional.set(self, False)
			ret += Ffmpeg_random().works_str()
			return ret

		if (not Ffmpeg_stats().works()):
			functional.set(self, False)
			ret += Ffmpeg_stats().works_str()
			return ret

		tempdir = tempfile.TemporaryDirectory(dir=LOCAL_TMP)
		random_in  = Path(tempdir.name) / "random.mp4"
		random_out = Path(tempdir.name) / "random2.mp4"
		Ffmpeg_random().set(random_in).run()
		self.set(random_in, random_out, [1918, 1078])
		self.run()
		
		ffmpeg_stats = Ffmpeg_stats().set(random_out)
		ffmpeg_stats.run()
		resolution = ffmpeg_stats.resolution()

		if (resolution != [1918, 1078]):
			functional.set(self, False)
			msg_error.print("Ffmpeg_crop() failed self-test" + str(resolution))
		else:
			msg_info.print(self.execName + " -vf crop OK")
		return ret

class Ffmpeg_psnr(Ffmpeg):
	def set(self, original: Path, transcoded: Path):
		super().set(["-i", transcoded, "-i", original, "-filter_complex", "psnr", "-f", "null", "-"])
		return self
	def run(self):
		result = super().run()
		self.stderr = result.stderr.decode('utf-8')
		return result
	def psnr(self):
		tmp_re    = grep(r'PSNR.*'        , self.stderr)
		tmp_re    = grep(r'min:.* max'    , tmp_re)
		finite    = grep(r'[0-9]*\.[0-9]*', tmp_re)
		infinite  = grep(r'inf', tmp_re)
		if (finite == ""):
			return float(infinite)

		return float(finite)

class Ffmpeg_random(Ffmpeg):
	def set(self, path:Path):
		super().set(["-f", "lavfi", "-i", "nullsrc=s=1920x1080:d=1:r=1", "-vf", "geq=random(1)*255:128:128", path])
		return self

class Ffmpeg_stats(Ffmpeg):
	def set(self, path:Path):
		super().set(["-hide_banner", "-i", path, "-c", "copy", "-f", "null", "-y", "/dev/null"])
		return self
	def run(self):
		result = super().run()
		self.stderr = result.stderr.decode('utf-8')
		return result
	def frames(self):
		tmp_re = grep(r'frame=\s*[1-9][0-9]*', self.stderr)
		return int(grep(r'[1-9][0-9]*', tmp_re))

	def resolution(self):
		tmp_re = grep(r'Stream.*Video.*',         self.stderr)
		tmp_re = grep(r'[1-9][0-9]*x[1-9][0-9]*', tmp_re)

		width  = grep(r'[1-9][0-9]*x', tmp_re)
		width  = grep(r'[1-9][0-9]*', width)

		height = grep(r'x[1-9][0-9]*', tmp_re)
		height = grep(r'[1-9][0-9]*', height)

		return [int(width), int(height)]
	def bits(self):
		tmp_re  = grep(r'Stream.*Video.*', self.stderr)

		# yuv
		yuv_re = grep(r'yuv444p12', tmp_re) + grep(r'yuv422p12', tmp_re) + grep(r'yuv420p12', tmp_re)
		if (len(yuv_re) != 0):
			return 12
		yuv_re = grep(r'yuv422p10', tmp_re) + grep(r'yuv422p10', tmp_re) + grep(r'yuv420p10', tmp_re)
		if (len(yuv_re) != 0):
			return 10
		yuv_re = grep(r'yuv444p', tmp_re)   + grep(r'yuv422p', tmp_re)   + grep(r'yuv420p',   tmp_re)
		if (len(yuv_re) != 0):
			return 8

		# rgb
		rgb_re = grep(r'rgb48', tmp_re) + grep(r'rgba64', tmp_re)
		if (len(rgb_re) != 0):
			return 16
		rgb_re = grep(r'rgb24', tmp_re) + grep(r'rgba32', tmp_re)
		if (len(rgb_re) != 0):
			return 8

		# gbr
		gbr_re = grep(r'gbrapf16', tmp_re)
		if (len(gbr_re) != 0):
			return 32
		gbr_re = grep(r'gbrpf32', tmp_re)
		if (len(gbr_re) != 0):
			return 16

		return None

	def check_exec_args(self):
		ret = ""
		if (not Ffmpeg_random().works()):
			functional.set(self, False)
			ret += Ffmpeg_random().works_str()
			return ret

		tempdir = tempfile.TemporaryDirectory(dir=LOCAL_TMP)
		random_in  = Path(tempdir.name) / "random.mp4"
		Ffmpeg_random().set(random_in).run()
		self.set(random_in)
		self.run()
		if (self.resolution() != [1920,1080]):
			functional.set(self, False)
			msg_error.print("Ffmpeg_stats() failed self-test")
		else:
			msg_info.print("Ffmpeg_stats(): OK")
		return ret

class Ffmpeg_vaav1(Ffmpeg):
	def set(self, source: Path, destination: Path, q, max_bytes=None):
		video_args = ["-i", source, "-vaapi_device", "/dev/dri/renderD128", "-vf", "format=nv12,hwupload", "-c:v", "av1_vaapi", "-b:v", 0, "-q:v", int(q), "-g:v", 10000000, "-compression_level:v", 29]
		audio_args = ["-c:a", "libopus", "-b:a", "128k", destination]
		if (max_bytes is not None):
			video_args += ["-fs", max_bytes]
		super().set(video_args + audio_args)
		self.stderr = ""
		return self
	def run(self):
		with lock_gpu:
			result = super().run()

		self.stderr = result.stderr.decode('utf-8')
		return result
	def check_dependencies(self):
		ret = "\n"
		if (not Ffmpeg_stats().works()):
			functional.set(self, False)
			ret += Ffmpeg_stats().works_str()
		if (not Ffmpeg_psnr().works()):
			functional.set(self, False)
			ret += Ffmpeg_psnr().works_str()
		if (not Ffmpeg_vmaf().works()):
			functional.set(self, False)
			ret += Ffmpeg_vmaf().works_str()
		return ret
	def check_exec_args(self):
		ret = ""
		if (not Ffmpeg_random().works()):
			functional.set(self, False)
			ret += Ffmpeg_random().works_str()
			return ret

		tempdir = tempfile.TemporaryDirectory(dir=LOCAL_TMP)
		random_in  = Path(tempdir.name) / "random.mp4"
		random_out = Path(tempdir.name) / "random.mkv"
		Ffmpeg_random().set(random_in).run()
		self.set(random_in, random_out, 20)
		self.run()
		tmp_re = grep(r'No usable encoding profile found', self.stderr)
		if (tmp_re):
			functional.set(self, False)
			msg_error.print("ffmpeg doesn't support vaav1")
		else:
			msg_info.print(self.execName + " -vaapi_device /dev/dri/renderD128 -vf format=nv12,hwupload -c:v av1_vaapi OK")
		return ret

class Ffmpeg_vmaf(Ffmpeg):
	def set(self, original: Path, transcoded: Path):
		super().set(["-i", transcoded, "-i", original, "-lavfi", "libvmaf", "-f", "null", "-"])
		self.stderr = ""
		return self
	def run(self):
		result = super().run()
		self.stderr = result.stderr.decode('utf-8')
		return result
	def vmaf(self):
		tmp_re = grep(r'VMAF.*'        , self.stderr)
		tmp_re = grep(r'[0-9]*\.[0-9]*', tmp_re)
		return float(tmp_re)
	def check_exec_args(self):
		ret = ""
		if (not Ffmpeg_random().works()):
			functional.set(self, False)
			ret += Ffmpeg_random().works_str()
			return ret

		tempdir = tempfile.TemporaryDirectory(dir=LOCAL_TMP)
		random = Path(tempdir.name) / "random.mp4"
		Ffmpeg_random().set(random).run()
		self.set(random, random)

		try:
			self.run()
		except subprocess.CalledProcessError:
			ret = "ffmpeg doesn't support libvmaf"
			functional.set(self, False)

		return ret

class Jpegoptim(Command):
	def __init__(self):
		super().__init__("jpegoptim")
	def set(self, path: Path):
		super().set([path])
		return self

class Magick_convert(Command):
	def __init__(self):
		super().__init__("magick")
	def set(self, original: Path, converted: Path):
		super().set(["convert", original, converted])
		return self

class Magick_mogrify_autoorient(Command):
	def __init__(self):
		super().__init__("magick")
	def set(self, path:Path):
		super().set(["mogrify", "-auto-orient", path])
		return self

class Optipng(Command):
	def __init__(self):
		super().__init__("optipng")
	def set(self, path: Path):
		super().set(["-o7", path])
		return self

class Cache_avif:
	def __init__(self, inBytes, yuv, q, outBytes, psnr, y):
		self.inBytes  = inBytes
		self.yuv      = yuv
		self.q        = q
		self.outBytes = outBytes
		self.psnr     = psnr
		self.y        = y

	def __str__(self):
		ret =  "{"
		ret +=    "inBytes: " + str(self.inBytes)
		ret +=    "yuv: "     + str(self.yuv)
		ret +=    "q: "       + str(self.q)
		ret += "}"
		return ret

class Cache_vaav1:
	def __init__(self, inBytes, q, outBytes, psnr, vmaf, y):
		self.inBytes  = inBytes
		self.q        = q
		self.outBytes = outBytes
		self.psnr     = psnr
		self.vmaf     = vmaf
		self.y        = y

	def __str__(self):
		ret =  "{"
		ret +=    "inBytes: " + str(self.inBytes)
		ret +=    "q: "       + str(self.q)
		ret += "}"
		return ret

class Cache_aomav1:
	def __init__(self, inBytes, crf, outBytes, psnr, vmaf, y):
		self.inBytes  = inBytes
		self.crf      = crf
		self.outBytes = outBytes
		self.psnr     = psnr
		self.vmaf     = vmaf
		self.y        = y

	def __str__(self):
		ret =  "{"
		ret +=    "inBytes: " + str(self.inBytes)
		ret +=    "crf: "     + str(self.crf)
		ret += "}"
		return ret

def psnr_min(path: Path, mse):
	return psnr_target(path, mse) - 1.0

def psnr_target(path: Path, mse):
	if (not path.is_file()):
		raise ValueError
	
	bits = None
	ffmpeg_stats = Ffmpeg_stats().set(path)
	ffmpeg_stats.run()
	bits = ffmpeg_stats.bits()

	if (not bits):
		bits = 8

	msg_error.print("bits" + str(bits))
	max_I = pow(2.0, bits) - 1.0
	msg_error.print("max_I" + str(max_I))
	return (10.0 * math.log(pow(max_I, 2.0) / mse) / math.log(10.0))

class Operation():
	def __init__(self, dependencies, path, outdir):
		self.dependencies = dependencies
		self.path   = path
		self.outdir = outdir

		# files used by run_operation()
		self.op_source      = self.path
		self.op_destination = self.outSuffix(self.path)
		self.op_info        = self.infoSuffix(self.path)
		self.op_log         = self.logSuffix(self.path)

	def outFiles(self):
		outFiles = []
		if ((self.path == Path()) or (self.outdir == Path())):
			return outFiles

		for i in [self.op_destination, self.op_info, self.op_log]:
			outFiles += [self.outdir / i.name]
		return outFiles

	def outCollision(self, path, outdir):
		for i in self.outFiles():
			if (i.is_file()):
				return True
		return False

	def run(self):
		for i in self.dependencies:
			if(not i().works()):
				return

		try:
			ret = self.run_internal()
		except subprocess.CalledProcessError:
			ret = False

		os.makedirs(self.outdir, exist_ok=True)
		for i in [self.op_destination, self.op_info, self.op_log]:
			if (i.is_file()):
				os.rename(i, self.outdir / i.name)
		return ret

class To_avif(Operation):
	def __init__(self, path: Path, outdir: Path, hq: bool):
		super().__init__([Avifenc, Exiftool_orientation, Magick_convert, Magick_mogrify_autoorient], path, outdir)

		self.hq = hq
		self.psnr_min    = None
		self.psnr_target = None

		self.cache: dict[list[str], Cache_avif] = {}
		self.encode_yuv = 444
	def outSuffix(self, path):
		return path.with_suffix(".avif")
	def infoSuffix(self, path):
		return path.with_suffix(".avif.txt")
	def logSuffix(self, path):
		return path.with_suffix(".avif.log")
	def run_internal(self):
		mse = 2.0
		if (self.hq):
			mse = 0.1
		self.psnr_min    = psnr_min(self.path, mse)
		self.psnr_target = psnr_target(self.path, mse)

		stat_inType = self.path.suffix
		stat_inSize = self.path.stat().st_size

		# rotation
		stat_rotated = False
		result = Exiftool_orientation().set(self.op_source).run()
		if (grep(r'Rotate', result.stdout.decode('utf‑8'))):
			rotated = self.op_source.with_suffix(".rotated" + self.op_source.suffix)
			shutil.copyfile(self.op_source, rotated)
			Magick_mogrify_autoorient().set(rotated).run()

			ffmpeg_psnr = Ffmpeg_psnr().set(self.op_source, rotated)
			ffmpeg_psnr.run()
			if (ffmpeg_psnr.psnr() > self.psnr_target + 5):
				self.op_source = rotated
				stat_rotated = True

		# brentq
		failures = 0
		for i in ["444", "422", "420"]:
			self.encode_yuv = i
			append_line(self.op_info, strings=["\n", "YUV ", i])

			try:
				msg_info.print(root_scalar(self.run_operation, bracket=[0, 100], method='brentq', xtol=0.49))
			except ValueError:
				failures += 1

		if (failures == 3):
			return False

		# find best
		best = None
		for i in self.cache:
			j = self.cache[i]
			if (len(j.outBytes) == 0):
				continue
			if (not best):
				best = j
			if (math.log(len(j.outBytes), 10) * abs(j.y) < math.log(len(best.outBytes),10) * abs(best.y)):
				best = j

		ret = True

		stat_yuv     = ""
		stat_q       = ""
		stat_outSize = ""
		stat_psnr    = ""
		stat_y       = ""
		if (best):
			write_binary_file(self.op_destination, best.outBytes)
			append_line(self.op_info, strings=["best: ", best.yuv, " ", best.q, " ", best.psnr])
			stat_yuv     = best.yuv
			stat_q       = best.q
			stat_outSize = len(best.outBytes)
			stat_psnr    = best.psnr
			stat_y       = best.y
		else:
			msg_error.print("best not found")
			ret = False

		append_line(STATS_CSV / "to_avif.csv", csv=[stat_inType, stat_inSize, stat_rotated, stat_yuv, stat_q, stat_outSize, stat_psnr, stat_y])
		return ret

	def run_operation(self, q: int):
		q = int(q)
		input_hash = str(Cache_avif(read_binary_file(self.op_source), self.encode_yuv, q, None, None, None))

		if (self.cache.get(input_hash)):
			return (self.cache.get(input_hash)).y

		store_bytes = True

		append_line(self.op_info, strings=["doing: ", q])

		# cicp CP/TC/MC
		# https://github.com/AOMediaCodec/libavif/wiki/CICP
		#
		# CP, Color Primaries
		#     CP=1, sRGB
		#     CP=9, HDR10
		#     CP=12, P3
		#
		# TC, Transfer Characteristics
		#     TC=13, sRGB
		#     TC=16, HDR10
		#     TC=18, HLG
		#
		# MC, Matrix Coefficients
		#     MC=0 means no loss when converting between RGB and YUV, but AV1 encoding suffers in efficiency

		result = None
		try:
			result = Avifenc().set(self.op_source, self.op_destination, self.encode_yuv, q).run()
		except subprocess.CalledProcessError as e:
			# Try conversion to .png:
			if (grep(r'Unrecognized file format for input file: ', e.stderr.decode('utf-8'))):
				tmp = self.op_source.with_suffix(".png")
				Magick_convert().set(self.op_source, tmp).run()
				self.op_source = tmp
				input_hash = str(Cache_avif(read_binary_file(self.op_source), self.encode_yuv, q, None, None, None))
				result = Avifenc().set(self.op_source, self.op_destination, self.encode_yuv, q).run()
			else:
				raise

		append_line(self.op_info, strings=["done"])
		write_binary_file(self.op_log, b''+result.stdout+result.stderr)

		in_bytes  = self.path.stat().st_size
		out_bytes = self.op_destination.stat().st_size
		if (out_bytes > in_bytes):
			append_line(self.op_info, strings=["bigger than source"])
			store_bytes = False

		# compare against original
		psnr = None
		if (out_bytes > in_bytes):
			psnr = float("+inf")
		else:
			ffmpeg_psnr = Ffmpeg_psnr().set(self.op_source, self.op_destination)
			ffmpeg_psnr.run()
			psnr = ffmpeg_psnr.psnr()
			append_line(self.op_info, strings=["psnr ", psnr])
		if (psnr < self.psnr_min):
			store_bytes = False

		y = psnr - self.psnr_target
		# Store results to cache
		data = b''
		if (store_bytes):
			data = read_binary_file(self.op_destination)
		self.cache[input_hash] = Cache_avif(read_binary_file(self.op_source), self.encode_yuv, q, data, psnr, y)

		self.op_destination.unlink()
		return y

class To_vaav1(Operation):
	def __init__(self, path: Path, outdir: Path, hq: bool):
		super().__init__([Ffmpeg_vaav1, Ffmpeg_psnr, Ffmpeg_vmaf], path, outdir)

		self.hq = hq
		self.psnr_min    = None
		self.psnr_target = None
		self.vmaf_min    = None
		self.vmaf_target = None

		self.cache: dict[list[str], Cache_vaav1] = {}
	def outSuffix(self, path):
		return path.with_suffix(".vaav1.mkv")
	def infoSuffix(self, path):
		return path.with_suffix(".vaav1.txt")
	def logSuffix(self, path):
		return path.with_suffix(".vaav1.log")
	def run_internal(self):
		mse  = 65.0
		vmaf = 95.0
		if (self.hq):
			mse = 3.25
			vmaf = 98.0

		self.psnr_min    = psnr_min(self.path, mse)
		self.psnr_target = psnr_target(self.path, mse)
		self.vmaf_min    = vmaf - 1.0
		self.vmaf_target = vmaf

		# test for gpu support
		ffmpeg_stats = Ffmpeg_stats().set(self.path)
		ffmpeg_stats.run()
		resolution = ffmpeg_stats.resolution()

		if (resolution[0] % CONFIG.VAAV1_RESOLUTION_MODULO > CONFIG.VAAV1_CROP_PIXELS):
			return False
		if (resolution[1] % CONFIG.VAAV1_RESOLUTION_MODULO > CONFIG.VAAV1_CROP_PIXELS):
			return False
		
		op_resolution = []
		stat_cropped  = False
		for i in resolution:
			tmp = i - (i % CONFIG.VAAV1_RESOLUTION_MODULO)
			op_resolution += [tmp]
			if (tmp != i):
				stat_cropped = True

		if (stat_cropped):
			self.op_source = self.path.with_suffix(".croppped" + self.path.suffix)
			ffmpeg_crop = Ffmpeg_crop().set(self.path, self.op_source, op_resolution)
			ffmpeg_crop.run()


		stat_inType = self.path.suffix
		stat_inSize = self.path.stat().st_size

		# brentq
		try:
			msg_info.print(root_scalar(self.run_operation, bracket=[1, 255], method='brentq', xtol=0.49))
		except ValueError:
			return False

		# best
		best = None
		for i in self.cache:
			j = self.cache[i]
			if (len(j.outBytes) == 0):
				continue
			if (not best):
				best = j
			if (math.log(len(j.outBytes), 10) * abs(j.y) < math.log(len(best.outBytes),10) * abs(best.y)):
				best = j

		ret = True

		stat_q     = ""
		stat_outSize = ""
		stat_psnr    = ""
		stat_vmaf    = ""
		stat_y       = ""
		if (best):
			write_binary_file(self.op_destination, best.outBytes)
			append_line(self.op_info, strings=["best: ", best.q, " ", best.psnr, " ", best.vmaf])
			stat_q       = best.q
			stat_outSize = len(best.outBytes)
			stat_psnr    = best.psnr
			stat_vmaf    = best.vmaf
			stat_y       = best.y
		else:
			msg_info.print("best not found")
			ret = False

		append_line(STATS_CSV / "to_vaav1.csv", csv=[stat_inType, stat_inSize, stat_q, stat_outSize, stat_psnr, stat_vmaf, stat_y])
		return ret

	def run_operation(self, q: int):
		q = int(q)
		input_hash = str(Cache_vaav1(read_binary_file(self.op_source), q, None, None, None, None))

		if (self.cache.get(input_hash)):
			return (self.cache.get(input_hash)).y

		store_bytes = True

		append_line(self.op_info, strings=["doing: ", q])
		result = Ffmpeg_vaav1().set(self.op_source, self.op_destination, q, max_bytes=self.path.stat().st_size).run()
		append_line(self.op_info, strings=["done"])
		write_binary_file(self.op_log, b''+result.stdout+result.stderr)
		# detect error

		in_bytes  = self.path.stat().st_size
		out_bytes = self.op_destination.stat().st_size

		ffmpeg_stats = Ffmpeg_stats().set(self.path)
		ffmpeg_stats.run()
		in_frames = ffmpeg_stats.frames()
		ffmpeg_stats = Ffmpeg_stats().set(self.op_destination)
		ffmpeg_stats.run()
		out_frames = ffmpeg_stats.frames()

		if (out_bytes > in_bytes):
			append_line(self.op_info, strings=["bigger than source"])
			store_bytes = False
		if (out_frames != in_frames):
			append_line(self.op_info, strings=["frame mismatch: ", out_frames, " ", in_frames])
			store_bytes = False

		if (out_frames > in_frames):
			raise ValueError
		elif ((out_frames < in_frames) and (out_bytes <= in_bytes)):
			raise ValueError

		psnr = None
		if ((out_bytes > in_bytes) or (out_frames != in_frames)):
			# If out frames are less, assume -fs was triggered
			psnr = float("+inf")
		else:
			ffmpeg_psnr = Ffmpeg_psnr().set(self.op_source, self.op_destination)
			ffmpeg_psnr.run()
			psnr = ffmpeg_psnr.psnr()
			append_line(self.op_info, strings=["psnr ", psnr])
		if (psnr < self.psnr_min):
			store_bytes = False

		vmaf = None
		if ((out_bytes > in_bytes) or (out_frames < in_frames) or (psnr == float("+inf"))):
			vmaf = float("+inf")
		elif (psnr < self.psnr_min):
			vmaf = float("-inf")
		else:
			ffmpeg_vmaf = Ffmpeg_vmaf().set(self.op_source, self.op_destination)
			ffmpeg_vmaf.run()
			vmaf = ffmpeg_vmaf.vmaf()
			append_line(self.op_info, strings=["vmaf ", vmaf])
		if (vmaf < self.vmaf_min):
			store_bytes = False

		y = vmaf - self.vmaf_target

		# Store results to cache
		data = b''
		if (store_bytes):
			data = read_binary_file(self.op_destination)

		self.cache[input_hash] = Cache_vaav1(read_binary_file(self.op_source), q, data, psnr, vmaf, y)
		self.op_destination.unlink()
		return y

class To_aomav1(Operation):
	def __init__(self, path: Path, outdir: Path, hq: bool):
		super().__init__([Ffmpeg_aomav1, Ffmpeg_psnr, Ffmpeg_vmaf], path, outdir)

		self.hq = hq
		self.psnr_min    = None
		self.psnr_target = None
		self.vmaf_min    = None
		self.vmaf_target = None

		self.cache: dict[list[str], Cache_aomav1] = {}
	def outSuffix(self, path):
		return path.with_suffix(".aomav1.mkv")
	def infoSuffix(self, path):
		return path.with_suffix(".aomav1.txt")
	def logSuffix(self, path):
		return path.with_suffix(".aomav1.log")
	def run_internal(self):
		mse  = 65.0
		vmaf = 95.0
		if (self.hq):
			mse = 3.25
			vmaf = 98.0

		self.psnr_min    = psnr_min(self.path, mse)
		self.psnr_target = psnr_target(self.path, mse)
		self.vmaf_min    = vmaf - 1.0
		self.vmaf_target = vmaf

		stat_inType = self.path.suffix
		stat_inSize = self.path.stat().st_size

		# brentq
		try:
			msg_info.print(root_scalar(self.run_operation, bracket=[1, 63], method='brentq', xtol=0.49))
		except ValueError as e:
			print(e)
			return False

		# best
		best = None
		for i in self.cache:
			j = self.cache[i]
			if (len(j.outBytes) == 0):
				continue
			if (not best):
				best = j
			if (math.log(len(j.outBytes), 10) * abs(j.y) < math.log(len(best.outBytes),10) * abs(best.y)):
				best = j

		ret = True

		stat_crf     = ""
		stat_outSize = ""
		stat_psnr    = ""
		stat_vmaf    = ""
		stat_y       = ""
		if (best):
			write_binary_file(self.op_destination, best.outBytes)
			append_line(self.op_info, strings=["best: ", best.crf, " ", best.psnr, " ", best.vmaf])
			stat_crf     = best.crf
			stat_outSize = len(best.outBytes)
			stat_psnr    = best.psnr
			stat_vmaf    = best.vmaf
			stat_y       = best.y
		else:
			msg_error.print("best not found")
			ret = False

		append_line(STATS_CSV / "to_aomav1.csv", csv=[stat_inType, stat_inSize, stat_crf, stat_outSize, stat_psnr, stat_vmaf, stat_y])
		return ret

	def run_operation(self, crf: int):
		crf = int(crf)
		input_hash = str(Cache_aomav1(read_binary_file(self.op_source), crf, None, None, None, None))

		if (self.cache.get(input_hash)):
			return (self.cache.get(input_hash)).y

		store_bytes = True

		append_line(self.op_info, strings=["doing: ", crf])
		result = Ffmpeg_aomav1().set(self.op_source, self.op_destination, crf, max_bytes=self.path.stat().st_size).run()
		append_line(self.op_info, strings=["done"])
		write_binary_file(self.op_log, b''+result.stdout+result.stderr)
		# detect error

		in_bytes  = self.path.stat().st_size
		out_bytes = self.op_destination.stat().st_size

		ffmpeg_stats = Ffmpeg_stats().set(self.path)
		ffmpeg_stats.run()
		in_frames = ffmpeg_stats.frames()
		ffmpeg_stats = Ffmpeg_stats().set(self.op_destination)
		ffmpeg_stats.run()
		out_frames = ffmpeg_stats.frames()
	
		if (out_bytes > in_bytes):
			append_line(self.op_info, strings=["bigger than source"])
			store_bytes = False
		if (out_frames != in_frames):
			append_line(self.op_info, strings=["frame mismatch: ", out_frames, " ", in_frames])
			store_bytes = False

		if (out_frames > in_frames):
			raise ValueError
		elif ((out_frames < in_frames) and (out_bytes <= in_bytes)):
			raise ValueError

		psnr = None
		if ((out_bytes > in_bytes) or (out_frames != in_frames)):
			# If out frames are less, assume -fs was triggered
			psnr = float("+inf")
		else:
			ffmpeg_psnr = Ffmpeg_psnr().set(self.op_source, self.op_destination)
			ffmpeg_psnr.run()
			psnr = ffmpeg_psnr.psnr()
			append_line(self.op_info, strings=["psnr ", psnr])
		if (psnr < self.psnr_min):
			store_bytes = False

		vmaf = None
		if ((out_bytes > in_bytes) or (out_frames < in_frames) or (psnr == float("+inf"))):
			vmaf = float("+inf")
		elif (psnr < self.psnr_min):
			vmaf = float("-inf")
		else:
			ffmpeg_vmaf = Ffmpeg_vmaf().set(self.op_source, self.op_destination)
			ffmpeg_vmaf.run()
			vmaf = ffmpeg_vmaf.vmaf()
			append_line(self.op_info, strings=["vmaf ", vmaf])
		if (vmaf < self.vmaf_min):
			store_bytes = False

		y = vmaf - self.vmaf_target

		# Store results to cache
		data = b''
		if (store_bytes):
			data = read_binary_file(self.op_destination)

		self.cache[input_hash] = Cache_aomav1(read_binary_file(self.op_source), crf, data, psnr, vmaf, y)
		self.op_destination.unlink()
		return y

class Copy(Operation):
	def __init__(self, path: Path, outdir: Path):
		super().__init__([], path, outdir)
	def outSuffix(self, path):
		return path
	def infoSuffix(self, path):
		return path
	def logSuffix(self, path):
		return path
	def run(self):
		if (self.path.is_file()):
			os.makedirs(self.outdir, exist_ok=True)
			os.rename(self.path, self.outdir / self.path.name)
			return True

		return False

class In_types:
	def __init__(self):
		self.path    = Path()
		self.outdir  = Path()
		self.tempdir = tempfile.TemporaryDirectory(dir=LOCAL_TMP)

class nop(In_types):
	def run(self):
		return True

class Folder(In_types):
	def compatible(self, path:Path):
		if (not path.is_dir()):
			return False
		if (not path.is_relative_to(IN_FOLDER)):
			return False
		return True

	def set(self, path:Path):
		if (not self.compatible(path)):
			return False

		self.path = Path(self.tempdir.name) / path.name
		os.rename(path, self.path)

		self.outdir = OUT / "folder"
		return True
	def run(self):
		product = self.path.with_suffix(".7z")
		Cmd_7za().set(self.path, product).run()
		os.makedirs(self.outdir, exist_ok=True)
		os.rename(product, self.outdir / product.name)

class File(In_types):
	def __init__(self):
		self.hq = False
		super().__init__()

	def operations(self):
		return [Copy(self.path, self.outdir)]

	def outCollision(self):
		if (self.path == Path()) or (self.outdir == Path()):
			return True

		for i in self.operations():
			if (i.outCollision(self.path, self.outdir)):
				return True
		return False
	def run(self):
		operations = self.operations()

		try:
			self.preRun()
		except subprocess.CalledProcessError as e:
			msg_error.print(str(e))
			operations = [Copy(self.path, self.outdir)]

		for i in operations:
			if (i.run()):
				return True
		return False

	def compatible(self, path:Path):
		if (not path.is_file()):
			return False

		if (path.stat().st_size > CONFIG.MAX_FILE_BYTES):
			return False

		ret = False
		compatible_suffixes = self.suffixes()
		for i in self.suffixes():
			compatible_suffixes.add(str.upper(i))
		suffixes = path.suffixes
		for idx, x in enumerate(path.suffixes):
			if (''.join(str(i) for i in suffixes[idx:None])) in compatible_suffixes:
				return True
		return ret
	def out_subdir(self, path:Path):
		return path.suffix
	def set(self, path:Path):
		# Compatible suffix
		if (not self.compatible(path)):
			return False

		# Late initialization
		suffixes = ['', ''] + path.suffixes
		if (suffixes[-2] == ".hq"):
			self.hq = True
		self.path = Path(self.tempdir.name) / path.name
		self.outdir = OUT / self.out_subdir(path)
		self.outdir = self.outdir / path.parent.relative_to(IN_MEDIA)

		if (self.outCollision()):
			return False

		os.rename(path, self.path)

		return True

class Image(File):
	def operations(self):
		to_avif = To_avif(self.path, self.outdir, self.hq)
		return [to_avif] + super().operations()
	def preRun(self):
		return

class Video(File):
	def operations(self):
		to_vaav1 = To_vaav1(self.path, self.outdir, self.hq)
		to_aomav1 = To_aomav1(self.path, self.outdir, self.hq)
		return [to_vaav1, to_aomav1] + super().operations()
	def preRun(self):
		return

class Other(File):
	def compatible(self, path):
		if (not path.is_file()):
			return False
		return True
	def out_subdir(self, path:Path):
		return "other"
	def preRun(self):
		return

class Jpeg(Image):
	def suffixes(self):
		return {".jpg", ".jpeg"}
	def preRun(self):
		Jpegoptim().set(self.path).run()
class Png(Image):
	def suffixes(self):
		return {".png"}
	def preRun(self):
		Optipng().set(self.path).run()
class Tif(Image):
	def suffixes(self):
		return {".tif"}
class Webp(Image):
	def suffixes(self):
		return {".webp"}

class Avi(Video):
	def suffixes(self):
		return {".avi"}
class Mkv(Video):
	def suffixes(self):
		return {".h264.mkv"}
class Mov(Video):
	def suffixes(self):
		return {".mov"}
class Mp4(Video):
	def suffixes(self):
		return {".mp4"}
class Webm(Video):
	def suffixes(self):
		return {".webm"}
class Wmv(Video):
	def suffixes(self):
		return {".wmv"}

def subclasses(x):
	todo = x.__subclasses__()
	ret  = []

	while (len(todo) != 0):
		tmp = []
		for i in todo:
			tmp += i.__subclasses__()
			ret += [i]
		todo = tmp
	return ret

class source:
	def __init__(self, path:Path, lock):
		self.path      = path
		self.lock      = lock

class src_folder(source):
	def __init__(self):
		super().__init__(IN_FOLDER, lock_folder)
	def list(self):
		return [i for i in self.path.iterdir() if i.is_dir()]

class src_media(source):
	def __init__(self):
		super().__init__(IN_MEDIA, lock_media)
	def list(self):
		return [i for i in self.path.rglob("*") if i.is_file()]

class Transcode:
	def __init__(self):
		self.datatype = nop()
	def set(self, path:Path):
		compatible = []
		for i in [Folder] + subclasses(Image) + subclasses(Video):
			datatype = i()
			if (datatype.compatible(path)):
				compatible += [datatype]

		if (len(compatible) == 0):
			msg_info.print(str(path) + ": Unsupported filetype. Using out/other")
			compatible = [Other()]

		for i in compatible:
			datatype = i
			if (datatype.set(path)):
				self.datatype = datatype
				return True
		return False
	def run(self):
		self.datatype.run()
	def pick(self, source):
		if (type(self.datatype) is not nop):
			return 0.0

		time_start = time.time()
		with source.lock:
			for i in source.list():
				if (not self.set(i)):
					continue
				
				if (type(self.datatype) is Other):
					self.run()
					continue
				
				break
		time_total = time.time() - time_start
		self.run()
		return time_total

exit_flag = False

def multiplexer(lock_media, lock_folder):
	msg_status.print("Thread launched")

	def signal_handler(signal, frame):
		global exit_flag
		exit_flag = True

	signal.signal(signal.SIGINT, signal_handler)

	while (not exit_flag):
		transcode  = Transcode()
		time_total = 0.0
		seconds    = 10.0
		
		for i in subclasses(source):
			time_total += transcode.pick(i())

		if (type(transcode.datatype) is nop):
			target = time_total * 1000.0
			if (target > seconds):
				seconds = target

		time.sleep(seconds)
	print("exited")

def check_environment(args):
	working = 0
	total   = len(subclasses(Command))
	for i in subclasses(Command):
		working += i().works()

	msg_status.print(str(working) + "/" + str(total) + " components working")
	if ((working < total) and (not args.nofail)):
		msg_status.print(Bold("use --nofail to ignore"))
		return False
	return True

if __name__ == "__main__":
	parser = argparse.ArgumentParser()
	parser.add_argument('--nofail', action='store_true', help="Ignore errors, partial functionality.")
	args = parser.parse_args()

	sigint_raised = False
	def signal_handler(signal, frame):
		global sigint_raised
		for i in processes:
			if (not i.pid):
				continue
			if (not i.is_alive()):
				continue

			try:
				os.kill(i.pid, signal)
			except ProcessLookupError:
				pass
		sigint_raised = True

	signal.signal(signal.SIGINT, signal_handler)

	msg_status.print("launching")
	if (LOCAL_TMP.is_dir()):
		shutil.rmtree(LOCAL_TMP)
	for i in [USER_PRIVATE, IN_FOLDER, IN_MEDIA, STATS_CSV, LOCAL_TMP]:
		os.makedirs(i, exist_ok=True)

	processes = [multiprocessing.Process(target=multiplexer, args=(lock_folder, lock_media)) for i in range(CONFIG.THREADS)]

	if (check_environment(args)):
		for p in processes:
			p.start()

		signal.pause()
		if (sigint_raised):
			msg_status.print("Received SIGINT. Wait for all threads to finish current processing.")

		for p in processes:
			p.join()

	msg_status.print("exiting")
