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

ROOT         = Path("root")
USER_PRIVATE = ROOT / "in/user_private"
IN_FOLDER    = ROOT / "in/folder"
IN_MEDIA     = ROOT / "in/media"
OUT          = ROOT / "out"
STATS_CSV    = ROOT / "stats/csv"
LOCAL_TMP    = ROOT / "tmp"

THREADS     = os.cpu_count()
lock_stdout = multiprocessing.Lock()
lock_gpu    = multiprocessing.Lock()

BIN_AVIFENC = "avifenc"
BIN_FFMPEG  = "podman"
DOCKER_FFMPEG = "linuxserver/ffmpeg:8.1.2"

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

def grep(pattern: re.Pattern, string: str):
	result = re.search(pattern, string)
	if (not result):
		return ""

	return result[0];

def append_line(file_path: str, string="", csv=[]) -> None:
	line=str(string)
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
		except subprocess.CalledProcessError:
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
		super().__init__(BIN_AVIFENC)
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
		super().__init__(BIN_FFMPEG)

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
			args += [DOCKER_FFMPEG, "-stats"]

		args += ffmpeg_args
		super().set(args)

class Ffmpeg_aomav1(Ffmpeg):
	def set(self, source: Path, destination: Path, crf):
		super().set(["-i", source, "-c:v", "libaom-av1", "-b:v", 0, "-crf", crf, "-quality", "good", "-speed", 0, "-c:a", "libopus", "-b:a", "128k", destination])
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

class Ffmpeg_vaav1(Ffmpeg):
	def set(self, source: Path, destination: Path, q):
		super().set(["-i", source, "-vaapi_device", "/dev/dri/renderD128", "-vf", "format=nv12,hwupload", "-c:v", "av1_vaapi", "-b:v", 0, "-q:v", int(q), "-g:v", 10000000, "-compression_level:v", 29, "-c:a", "libopus", "-b:a", "128k", destination])
		self.stderr = ""
		return self
	def run(self):
		with lock_gpu:
			result = super().run()

		self.stderr = result.stderr.decode('utf-8')
		return result
	def check_dependencies(self):
		ret = "\n"
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
			functional.set(Ffmpeg_random(), False)
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
			functional.set(Ffmpeg_random(), False)
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
	def __init__(self, path: Path, outdir: Path, psnr_min, psnr_target):
		super().__init__([Avifenc, Exiftool_orientation, Magick_convert, Magick_mogrify_autoorient], path, outdir)

		self.psnr_min    = psnr_min
		self.psnr_target = psnr_target

		self.cache: dict[list[str], Cache_avif] = {}
		self.encode_yuv = 444
	def outSuffix(self, path):
		return path.with_suffix(".avif")
	def infoSuffix(self, path):
		return path.with_suffix(".avif.txt")
	def logSuffix(self, path):
		return path.with_suffix(".avif.log")
	def run_internal(self):
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
			append_line(self.op_info, string="\n" + "YUV " + i)

			try:
				msg_info.print(root_scalar(self.run_operation, bracket=[0, 100], method='brentq', xtol=0.1, maxiter=int(math.log(100,2))))
			except ValueError:
				failures += 1

		if (failures == 3):
			return False

		# find best
		best = None
		for i in self.cache:
			j = self.cache[i]
			if (self.path.stat().st_size < len(j.outBytes)):
				continue
			if (j.psnr < self.psnr_min):
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
			append_line(self.op_info, string="best: "+str(best.yuv)+" "+str(best.q)+" "+str(best.psnr))
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

		append_line(self.op_info, string="doing: " + str(q))

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

		append_line(self.op_info, string="done")
		write_binary_file(self.op_log, b''+result.stdout+result.stderr)

		# compare against original
		psnr = float("+inf")
		if (self.path.stat().st_size < self.op_destination.stat().st_size):
			append_line(self.op_info, string="bigger than source")
		else:
			ffmpeg_psnr = Ffmpeg_psnr().set(self.op_source, self.op_destination)
			ffmpeg_psnr.run()
			psnr = ffmpeg_psnr.psnr()
			append_line(self.op_info, string="psnr " + str(psnr))

		y = psnr - self.psnr_target
		# Store results to cache
		self.cache[input_hash] = Cache_avif(read_binary_file(self.op_source), self.encode_yuv, q, read_binary_file(self.op_destination), psnr, y)

		self.op_destination.unlink()
		return y

class To_vaav1(Operation):
	def __init__(self, path: Path, outdir: Path,  psnr_min, psnr_target, vmaf_min, vmaf_target):
		super().__init__([Ffmpeg_vaav1, Ffmpeg_psnr, Ffmpeg_vmaf], path, outdir)
		self.psnr_min    = psnr_min
		self.psnr_target = psnr_target
		self.vmaf_min    = vmaf_min
		self.vmaf_target = vmaf_target

		self.cache: dict[list[str], Cache_vaav1] = {}
	def outSuffix(self, path):
		return path.with_suffix(".vaav1.mkv")
	def infoSuffix(self, path):
		return path.with_suffix(".vaav1.txt")
	def logSuffix(self, path):
		return path.with_suffix(".vaav1.log")
	def run_internal(self):
		# test for gpu support

		stat_inType = self.path.suffix
		stat_inSize = self.path.stat().st_size

		# brentq
		try:
			msg_info.print(root_scalar(self.run_operation, bracket=[1, 255], method='brentq', xtol=0.1, maxiter=int(math.log(256,2))))
		except ValueError:
			return False

		# best
		best = None
		for i in self.cache:
			j = self.cache[i]
			if (self.path.stat().st_size < len(j.outBytes)):
				continue
			if (j.psnr < self.psnr_min):
				continue
			if (j.vmaf < self.vmaf_min):
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
			append_line(self.op_info, string="best: "+str(best.q)+" "+str(best.psnr)+" "+str(best.vmaf))
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

		append_line(self.op_info, string="doing: " + str(q))
		result = Ffmpeg_vaav1().set(self.op_source, self.op_destination, q).run()
		append_line(self.op_info, string="done")
		write_binary_file(self.op_log, b''+result.stdout+result.stderr)
		# detect error

		psnr = float("+inf")
		vmaf = float("+inf")
		if (self.path.stat().st_size < self.op_destination.stat().st_size):
			append_line(self.op_info, string="bigger than source")
		else:
			ffmpeg_psnr = Ffmpeg_psnr().set(self.op_source, self.op_destination)
			ffmpeg_psnr.run()
			psnr = ffmpeg_psnr.psnr()
			append_line(self.op_info, string="psnr " + str(psnr))

		if (psnr >= self.psnr_min) and (math.isfinite(psnr)):
			ffmpeg_vmaf = Ffmpeg_vmaf().set(self.op_source, self.op_destination)
			ffmpeg_vmaf.run()
			vmaf = ffmpeg_vmaf.vmaf()
			append_line(self.op_info, string="vmaf " + str(vmaf))

		y = vmaf - self.vmaf_target

		# Store results to cache
		self.cache[input_hash] = Cache_vaav1(read_binary_file(self.op_source), q, read_binary_file(self.op_destination), psnr, vmaf, y)
		self.op_destination.unlink()
		return y

class To_aomav1(Operation):
	def __init__(self, path: Path, outdir: Path,  psnr_min, psnr_target, vmaf_min, vmaf_target):
		super().__init__([Ffmpeg_aomav1, Ffmpeg_psnr, Ffmpeg_vmaf], path, outdir)

		self.psnr_min    = psnr_min
		self.psnr_target = psnr_target
		self.vmaf_min    = vmaf_min
		self.vmaf_target = vmaf_target

		self.cache: dict[list[str], Cache_aomav1] = {}
	def outSuffix(self, path):
		return path.with_suffix(".aomav1.mkv")
	def infoSuffix(self, path):
		return path.with_suffix(".aomav1.txt")
	def logSuffix(self, path):
		return path.with_suffix(".aomav1.log")
	def run_internal(self):
		stat_inType = self.path.suffix
		stat_inSize = self.path.stat().st_size

		# brentq
		try:
			msg_info.print(root_scalar(self.run_operation, bracket=[1, 63], method='brentq', xtol=0.1, maxiter=int(math.log(64,2))))
		except ValueError:
			return False

		# best
		best = None
		for i in self.cache:
			j = self.cache[i]
			if (self.path.stat().st_size < len(j.outBytes)):
				continue
			if (j.psnr < self.psnr_min):
				continue
			if (j.vmaf < self.vmaf_min):
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
			append_line(self.op_info, string="best: "+str(best.crf)+" "+str(best.psnr)+" "+str(best.vmaf))
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

		append_line(self.op_info, string="doing: " + str(crf))
		result = Ffmpeg_aomav1().set(self.op_source, self.op_destination, crf).run()
		append_line(self.op_info, string="done")
		write_binary_file(self.op_log, b''+result.stdout+result.stderr)
		# detect error

		psnr = float("+inf")
		vmaf = float("+inf")
		if (self.path.stat().st_size < self.op_destination.stat().st_size):
			append_line(self.op_info, string="bigger than source")
		else:
			ffmpeg_psnr = Ffmpeg_psnr().set(self.op_source, self.op_destination)
			ffmpeg_psnr.run()
			psnr = ffmpeg_psnr.psnr()
			append_line(self.op_info, string="psnr " + str(psnr))

		if (psnr >= self.psnr_min) and (math.isfinite(psnr)):
			ffmpeg_vmaf = Ffmpeg_vmaf().set(self.op_source, self.op_destination)
			ffmpeg_vmaf.run()
			vmaf = ffmpeg_vmaf.vmaf()
			append_line(self.op_info, string="vmaf " + str(vmaf))

		y = vmaf - self.vmaf_target

		# Store results to cache
		self.cache[input_hash] = Cache_aomav1(read_binary_file(self.op_source), crf, read_binary_file(self.op_destination), psnr, vmaf, y)
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

	def outCollision(self):
		if (self.path == Path()) or (self.outdir == Path()):
			return True

		for i in self.operations():
			if (i.outCollision(self.path, self.outdir)):
				return True
		return False
	def run(self):
		self.preRun()
		for i in self.operations():
			if (i.run()):
				return True
		return False

	def compatible(self, path:Path):
		if (not path.is_file()):
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

	def set(self, path:Path):
		# Compatible suffix
		if (not self.compatible(path)):
			return False

		# Late initialization
		suffixes = ['', ''] + path.suffixes
		if (suffixes[-2] == ".hq"):
			self.hq = True
		self.path = Path(self.tempdir.name) / path.name
		self.outdir = OUT / path.suffix
		self.outdir = self.outdir / path.parent.relative_to(IN_MEDIA)

		if (self.outCollision()):
			return False

		os.rename(path, self.path)

		return True

	def psnr_min(self):
		if (self.hq):
			bits = 16
			return 47+(bits*1.2)
		return 44
	def psnr_target(self):
		if (self.hq):
			bits = 16
			return 48+(bits*1.2)
		return 45

class Image(File):
	def operations(self):
		to_avif = To_avif(self.path, self.outdir, self.psnr_min(), self.psnr_target())
		copy = Copy(self.path, self.outdir)
		return [to_avif, copy]
	def preRun(self):
		return

class Video(File):
	def operations(self):
		to_vaav1 = To_vaav1(self.path, self.outdir, self.psnr_min(), self.psnr_target(), self.vmaf_min(), self.vmaf_target())
		to_aomav1 = To_aomav1(self.path, self.outdir, self.psnr_min(), self.psnr_target(), self.vmaf_min(), self.vmaf_target())
		copy = Copy(self.path, self.outdir)
		return [to_vaav1, to_aomav1, copy]
	def preRun(self):
		return
	def psnr_min(self):
		return 30
	def psnr_target(self):
		return 45
	def vmaf_min(self):
		return 94
	def vmaf_target(self):
		return 95

class Other(File):
	def set(self, path:Path):
		if (not path.is_file()):
			return False

		# Late initialization
		self.path = Path(self.tempdir.name) / path.name
		self.outdir = OUT / "other"
		self.outdir = self.outdir / path.parent.relative_to(IN_MEDIA)

		if (self.outCollision()):
			return False

		os.rename(path, self.path)

		return True
	def operations(self):
		copy = Copy(self.path, self.outdir)
		return [copy]
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
		if (type(transcode.datatype) is nop):
			time_start = time.time()
			with lock_folder:
				folders = [i for i in IN_FOLDER.iterdir() if i.is_dir()]
				for i in folders:
					if (transcode.set(i)):
						break
			time_total += time.time() - time_start
			transcode.run()

		if (type(transcode.datatype) is nop):
			time_start = time.time()
			with lock_media:
				medias = [i for i in IN_MEDIA.rglob("*") if i.is_file()]
				for i in medias:
					if (transcode.set(i)):
						break
			time_total  += time.time() - time_start
			transcode.run()

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
			except ProcessLookipError:
				pass
		sigint_raised = True

	signal.signal(signal.SIGINT, signal_handler)

	msg_status.print("launching")
	os.makedirs(USER_PRIVATE, exist_ok=True)
	os.makedirs(IN_FOLDER,    exist_ok=True)
	os.makedirs(IN_MEDIA,     exist_ok=True)
	os.makedirs(STATS_CSV,    exist_ok=True)

	if (LOCAL_TMP.is_dir()):
		shutil.rmtree(LOCAL_TMP)
	os.makedirs(LOCAL_TMP,         exist_ok=True)

	lock_folder = multiprocessing.Lock()
	lock_media  = multiprocessing.Lock()
	processes = [multiprocessing.Process(target=multiplexer, args=(lock_folder, lock_media)) for i in range(THREADS)]

	if (check_environment(args)):
		for p in processes:
			p.start()

		signal.pause()
		if (sigint_raised):
			msg_status.print("Received SIGINT. Wait for all threads to finish current processing.")

		for p in processes:
			p.join()

	msg_status.print("exiting")
