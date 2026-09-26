import argparse
import contextlib
import csv
import hashlib
import math
import mpmath
import multiprocessing
import os
from   pathlib import Path
import pathlib
import re
from   scipy.optimize import root_scalar
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback

from config          import CONFIG
from config_platform import PLATFORM

ROOT         = Path("root")
USER_PRIVATE = ROOT / "in/user_private"
IN_FOLDER    = ROOT / "in/folder"
IN_MEDIA     = ROOT / "in/media"
OUT          = ROOT / "out"
STATS_CSV    = ROOT / "stats/csv"
LOCAL_TMP    = ROOT / "tmp"

lock_stdout = multiprocessing.Lock()
lock_gpu    = multiprocessing.Lock()
lock_cmd_cache = multiprocessing.Lock()

lock_folder = multiprocessing.Lock()
lock_media  = multiprocessing.Lock()

def Bold(string: str):
	return ("\033[1m"  + string + "\033[0m")
def LightCyan(string: str):
	return ("\033[96m" + string + "\033[0m")
def LightGreen(string: str):
	return ("\033[92m" + string + "\033[0m")
def LightMagenta(string: str):
	return ("\033[95m" + string + "\033[0m")
def LightRed(string: str):
	return ("\033[91m" + string + "\033[0m")
def LightYellow(string: str):
	return ("\033[93m" + string + "\033[0m")
def Green(string: str):
	return ("\033[32m" + string + "\033[0m")

class msg:
	def __init__(self, indicator="", effects=[]):
		self.indicator = indicator
		self.effects   = effects
	def apply_effects(self, string):
		for i in self.effects:
			string = i(string)
		return string
	def print(self, string):
		indicator2 = ""
		#if (stack):
		qualnames = []
		for i in stack_info(limit=5):
			qualnames += [str(i["qualname"])]

		# Skip from start
		for i in ["msg.print", "Command.run", "append_line"]:
			try:
				qualnames = qualnames[qualnames.index(i)+1:]
			except:
				pass

		# Skip from end		
		for i in ["BaseProcess.run", "<module>"]:
			try:
				qualnames = qualnames[:qualnames.index(i)]
			except:
				pass
		# Skip system
		qualnames = qualnames[:next((i for i, x in enumerate(qualnames) if x.startswith("_")), -1)]

		qualnames.reverse()
		indicator2 = '>'.join(qualnames)

		self.string(string, print_out=True, indicator2=indicator2)
	def string(self, string, print_out=False, indicator2=""):
		string = str(string)
		indicator = "[" + self.indicator + "]"
		if (len(indicator2) > 0):
			indicator  = "[" + self.indicator + "|"
			indicator2 = indicator2 + "]"
	
		lines  = string.splitlines()

		ret = self.apply_effects(indicator + indicator2)
		if (len(lines) != 1):
			ret += "\n"

		spaces_now = ' '
		spaces = len(indicator) * ' '

		for i in lines:
			ret += spaces_now + i + "\n"
			spaces_now = spaces
		if (print_out):
			with lock_stdout:
				print(ret, end='', flush=True)
		return (ret)

msg_cached = msg("cached", [Bold, Green])
msg_error  = msg("error ", [Bold, LightRed])
msg_exec   = msg(" exec ", [Bold, LightGreen])
msg_info   = msg(" info ", [Bold, LightYellow])
msg_status = msg("status")
msg_stdout = msg("stdout", [Bold])
msg_stderr = msg("stderr", [Bold])

def flatten(arr):
	for item in arr:
		if isinstance(item, (list, tuple)):
			yield from flatten(item)
		else:
			yield item

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

def append_binary_file(path: str, data: bytes) -> None:
	with open(path, "ab") as f:
		f.write(data)

def write_binary_file(path: str, data: bytes) -> None:
	with open(path, "wb") as f:
		f.write(data)

def hash_file(path, algorithm="sha256", chunk_size=1024 * 1024):
	h = hashlib.new(algorithm)
	with open(path, "rb") as f:
		for chunk in iter(lambda: f.read(chunk_size), b""):
			h.update(chunk)
	return h.hexdigest()

def container_permit(file: Path):
	return ["-v", str(file.parent)+':'+str(file.parent)+":z"]

def pmean(floats, power):
	mpmath.mp.dps = 30

	# Type conversion	
	power  = mpmath.mpf(power)
	values = [mpmath.mpf(x) for x in floats]
	total  = mpmath.mpf(len(values))

	# Handle geometric mean
	if power == 0:
		product = mpmath.mpf(1)
		for i in values:
			product *= mpmath.power(i, 1 / total)
		return product

	sum = mpmath.fsum(mpmath.power(i, power) for i in values)
	return float(mpmath.power(sum / total, 1 / power))

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

class Command_Cache():
	def __init__(self):
		self.cache: dict[str, object] = {}
	def put(self, string, value):
		with lock_cmd_cache:
			if (len(self.cache) > PLATFORM.CMD_CACHE_ENTRIES):
				self.cache.pop(next(iter(self.cache)))
		self.cache[string] = value
	def get(self, string):
		with lock_cmd_cache:
			return self.cache.get(string)

command_cache = Command_Cache()

def stack_info(limit=5):
	# Start at the caller of this function
	f = sys._getframe(1)
	out = []

	while f and len(out) < limit:
		code = f.f_code
		func = code.co_name

		cls = None
		for name in ("self", "cls"):
			if name in f.f_locals:
				obj = f.f_locals[name]
				cls = obj if isinstance(obj, type) else type(obj)
				break

		qualname = getattr(code, "co_qualname", func)  # Python 3.11+

		out.append({
			"class": cls,
			"function": func,
			"qualname": qualname,
			"file": code.co_filename,
            "lineno": f.f_lineno,
		})

		f = f.f_back

	return out

class Command():
	def __init__(self, execName):
		self.execName = execName
		self.args     = []
		self.cacheable = False
	def set(self, args, cacheable=False):
		self.args = args
		self.cacheable = cacheable
	def run(self):
		strings = [self.execName]
		for i in self.args:
			if isinstance(i, list):
				strings += [''.join([str(j) for j in i])]
			else:
				strings += [str(i)]

		file_hashes = []
		if (self.cacheable):
			for i in list(flatten(self.args)):
				if isinstance(i, Path):
					file_hashes += [hash_file(i)]

		if (self.cacheable):
			tmp = command_cache.get(str(file_hashes+strings))
			if (tmp):
				return tmp

		msg_exec.print('')
		result = None
		try:
			result = subprocess.run(strings, capture_output=True, check=True, start_new_session=True)
			if (result.returncode):
				msg_exec.print(' '.join(strings))
				msg_stdout.print(result.stdout.decode('utf-8'))
				msg_stderr.print(result.stderr.decode('utf-8'))
			stdout, stderr = (result.stdout, result.stderr)
		except subprocess.CalledProcessError as e:
			msg_exec.print(' '.join(strings))
			msg_stdout.print(e.stdout.decode('utf-8'))
			msg_stderr.print(e.stderr.decode('utf-8'))
			raise

		if (self.cacheable):
			command_cache.put(str(file_hashes+strings), result)
		return result
	def print_std(stdout, stderr):
		msg_stdout.print(stdout.decode('utf-8'))
		msg_stderr.print(stderr.decode('utf-8'))
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
		super().__init__(PLATFORM.BIN_AVIFENC)
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
		super().set(["-orientation", path], cacheable=True)
		return self

class Ffmpeg(Command):
	def __init__(self):
		super().__init__(PLATFORM.BIN_FFMPEG)

	def set(self, ffmpeg_args, cacheable=False):
		args = []
		if (self.execName in {"docker", "podman"}):
			permissions = []
			exit_flag = False
			for i in list(flatten(ffmpeg_args)):
				if (isinstance(i, (pathlib.PurePath))):
					permissions += container_permit(i)
			args += ["run", "--rm"]
			args += ["--device", "/dev/dri/renderD128"]
			args += permissions
			args += [PLATFORM.DOCKER_FFMPEG, "-stats"]

		args += ffmpeg_args
		super().set(args, cacheable)

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
		if (not Ffmpeg_ssim().works()):
			functional.set(self, False)
			ret +=  Ffmpeg_ssim().works_str()
		if (not Ffmpeg_libvmaf().works()):
			functional.set(self, False)
			ret += Ffmpeg_libvmaf().works_str()
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
		ffmpeg_stats = Ffmpeg_stats().set(original)
		ffmpeg_stats.run()
		original_timebase = ffmpeg_stats.timebase()
		ffmpeg_stats = Ffmpeg_stats().set(transcoded)
		ffmpeg_stats.run()
		transcoded_timebase = ffmpeg_stats.timebase()
		common_timebase = str(math.lcm(original_timebase, transcoded_timebase))
		super().set(["-i", transcoded, "-i", original, "-filter_complex", "[0:v]settb="+common_timebase+",setpts=PTS-STARTPTS[main];[1:v]settb="+common_timebase+",setpts=PTS-STARTPTS[ref];[main][ref]psnr='stats_file=-'", "-f", "null", "-"], cacheable=True)
		return self
	def run(self):
		result = super().run()
		self.stderr = result.stderr.decode('utf-8')
		self.stdout = result.stdout.decode('utf-8')
		return result
	def psnr(self):
		LINE_RE = re.compile(
			r"n:\s*(?P<n>\d+)\s+"
			r"mse_avg:\s*(?P<mse_avg>-?\d+(?:\.\d+)?)\s+"
			r"mse_y:\s*(?P<mse_y>-?\d+(?:\.\d+)?)\s+"
			r"mse_u:\s*(?P<mse_u>-?\d+(?:\.\d+)?)\s+"
			r"mse_v:\s*(?P<mse_v>-?\d+(?:\.\d+)?)\s+"
			r"psnr_avg:\s*(?P<psnr_avg>(?:inf|-?\d+(?:\.\d+)?))\s+"
			r"psnr_y:\s*(?P<psnr_y>(?:inf|-?\d+(?:\.\d+)?))\s+"
			r"psnr_u:\s*(?P<psnr_u>(?:inf|-?\d+(?:\.\d+)?))\s+"
			r"psnr_v:\s*(?P<psnr_v>(?:inf|-?\d+(?:\.\d+)?))\s*"
		)

		def parse_line(line: str) -> dict:
			m = LINE_RE.fullmatch(line.strip())
			if not m:
				raise ValueError(f"Could not parse line: {line!r}")

			d = m.groupdict()
			d["n"] = int(d["n"])
			for key in ("mse_avg", "mse_y", "mse_u", "mse_v", "psnr_avg", "psnr_y", "psnr_u", "psnr_v"):
				d[key] = float(d[key])
			return d

		data = []
		for line in self.stdout.splitlines():
			parsed = parse_line(line)
			data += [parsed["psnr_avg"]]

		
		return pmean(data, CONFIG.PSNR.power_mean)

class Ffmpeg_ssim(Ffmpeg):
	def set(self, original: Path, transcoded: Path):
		ffmpeg_stats = Ffmpeg_stats().set(original)
		ffmpeg_stats.run()
		original_timebase = ffmpeg_stats.timebase()
		ffmpeg_stats = Ffmpeg_stats().set(transcoded)
		ffmpeg_stats.run()
		transcoded_timebase = ffmpeg_stats.timebase()
		common_timebase = str(math.lcm(original_timebase, transcoded_timebase))
		super().set(["-i", transcoded, "-i", original, "-filter_complex", "[0:v]settb="+common_timebase+",setpts=PTS-STARTPTS[main];[1:v]settb="+common_timebase+",setpts=PTS-STARTPTS[ref];[main][ref]ssim='stats_file=-'", "-f", "null", "-"], cacheable=True)
		return self
	def run(self):
		result = super().run()
		self.stderr = result.stderr.decode('utf-8')
		self.stdout = result.stdout.decode('utf-8')
		return result
	def ssim(self):
		LINE_RE = re.compile(
			r"n:\s*(?P<n>\d+)\s+"
			r"Y:\s*(?P<y>-?\d+(?:\.\d+)?)\s+"
			r"U:\s*(?P<u>-?\d+(?:\.\d+)?)\s+"
			r"V:\s*(?P<v>-?\d+(?:\.\d+)?)\s+"
			r"All:\s*(?P<all>-?\d+(?:\.\d+)?)\s*"
			r"\((?P<db>(?:inf|-?\d+(?:\.\d+)?))\)"
		)

		def parse_line(line: str) -> dict:
			m = LINE_RE.fullmatch(line.strip())
			if not m:
				raise ValueError(f"Could not parse line: {line!r}")

			d = m.groupdict()
			d["n"] = int(d["n"])
			for key in ("y", "u", "v", "all", "db"):
				d[key] = float(d[key])
			return d

		data = []
		for line in self.stdout.splitlines():
			parsed = parse_line(line)
			data += [parsed["all"]]

		return pmean(data, CONFIG.SSIM_DB.power_mean)

	def ssim_db(self):
		tmp = 1.0 - self.ssim()
		if (tmp == 0.0):
			tmp = sys.float_info.min
		return (-10.0 * math.log(tmp) / math.log(10.0))

class Ffmpeg_random(Ffmpeg):
	def set(self, path:Path):
		super().set(["-f", "lavfi", "-i", "nullsrc=s=1920x1080:d=1:r=1", "-vf", "geq=random(1)*255:128:128", path])
		return self

class Ffmpeg_stats(Ffmpeg):
	def set(self, path:Path):
		super().set(["-hide_banner", "-i", path, "-c", "copy", "-f", "null", "-y", "/dev/null"], cacheable=True)
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
		gbr, _ = self.channels_GBR()
		if gbr:
			return gbr
		gray, _ = self.channels_gray()
		if gray:
			return gray
		rgb, _ = self.channels_RGB()
		if rgb:
			return rgb
		yuv, _ = self.channels_YUV()
		if yuv:
			return yuv
		return None
	def channels_GBR(self):
		tmp_re  = grep(r'Stream.*Video.*', self.stderr)
		gbr_re = grep(r'gbrapf16', tmp_re)
		if (len(gbr_re) != 0):
			return (32, "full")
		gbr_re = grep(r'gbrpf32', tmp_re)
		if (len(gbr_re) != 0):
			return (16, "full")
		return (None, None)
	def channels_gray(self):
		tmp_re  = grep(r'Stream.*Video.*', self.stderr)
		gray_re = grep(r'gray16', tmp_re)
		if (len(gray) != 0):
			return (16, "mono")
		gray_re = grep(r'gray', tmp_re)
		if (len(gray) != 0):
			return (8, "mono")
		return (None, None)
	def channels_RGB(self):
		tmp_re = grep(r'Stream.*Video.*', self.stderr)
		rgb_re = grep(r'rgb48', tmp_re) + grep(r'rgba64', tmp_re)
		if (len(rgb_re) != 0):
			return (16, "full")
		rgb_re = grep(r'rgb24', tmp_re) + grep(r'rgba32', tmp_re)
		if (len(rgb_re) != 0):
			return (8, "full")
		return (None, None)
	def channels_YUV(self):
		tmp_re = grep(r'Stream.*Video.*', self.stderr)
		yuv_re = grep(r'yuv444p12', tmp_re) + grep(r'yuv422p12', tmp_re) + grep(r'yuv420p12', tmp_re)
		if (len(yuv_re) != 0):
			return (12, grep(r'4[0-4][0-4]'))
		yuv_re = grep(r'yuv422p10', tmp_re) + grep(r'yuv422p10', tmp_re) + grep(r'yuv420p10', tmp_re)
		if (len(yuv_re) != 0):
			return (10, grep(r'4[0-4][0-4]'))
		yuv_re = grep(r'yuv444p', tmp_re)   + grep(r'yuv422p', tmp_re)   + grep(r'yuv420p',   tmp_re)
		if (len(yuv_re) != 0):
			return (8, grep(r'4[0-4][0-4]'))
		return (None, None)
	def timebase(self):
		tmp_re     = grep(r'Stream.*Video.*',                 self.stderr)
		tmp_re     = grep(r'[1-9][0-9]*\.?[0-9]*[kmbt]? tbn', tmp_re)
		tmp_re     = grep(r'[1-9][0-9]*\.?[0-9]*[kmbt]?',     tmp_re)
		number     = grep(r'[1-9][0-9]*\.*[0-9]*',            tmp_re)
		multiplier = grep(r'[kmbt]?',                         tmp_re)

		number = float(number)
		if (multiplier):
			multipliers = {'k': 1_000, 'm': 1_000_000, 'b': 1_000_000_000, 't': 1_000_000_000_000}
			number *= multipliers[multiplier]

		return int(number)

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
		if (not Ffmpeg_libvmaf().works()):
			functional.set(self, False)
			ret += Ffmpeg_libvmaf().works_str()
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

class Ffmpeg_libvmaf(Ffmpeg):
	def set(self, original: Path, transcoded: Path):
		ffmpeg_stats = Ffmpeg_stats().set(original)
		ffmpeg_stats.run()
		original_timebase = ffmpeg_stats.timebase()
		ffmpeg_stats = Ffmpeg_stats().set(transcoded)
		ffmpeg_stats.run()
		transcoded_timebase = ffmpeg_stats.timebase()
		common_timebase = str(math.lcm(original_timebase, transcoded_timebase))

		#super().set(["-i", transcoded, "-i", original, "-filter_complex", "[0:v]settb="+common_timebase+",setpts=PTS-STARTPTS[main];[1:v]settb="+common_timebase+",setpts=PTS-STARTPTS[ref];[main][ref]libvmaf", "-f", "null", "-"])
		self.tempdir = tempfile.TemporaryDirectory(dir=LOCAL_TMP)
		self.csv = Path(self.tempdir.name) / "vmaf.csv"

		filter_complex = [
			# Adjust timebase
			"[0:v]settb="+common_timebase+",setpts=PTS-STARTPTS[main];[1:v]settb="+common_timebase+",setpts=PTS-STARTPTS[ref];",
			"[main][ref]libvmaf=",
			# Enable all statistics
			"feature=name=adm|name=cambi|name=float_ms_ssim|name=float_ssim|name=motion|name=psnr|name=psnr_hvs|name=vif",
			":log_fmt=csv:log_path='",
			self.csv,
			# Use VMAF_neg
			"':model=version=vmaf_v0.6.1neg"
		]
		super().set(["-i", transcoded, "-i", original, "-filter_complex", filter_complex, "-f", "null", "-"])

		self.stderr = ""
		return self
	def run(self):
		result = super().run()
		self.stderr = result.stderr.decode('utf-8')
		return result
	def psnr_hvs_cb(self):
		data = []
		with open(self.csv, newline="") as f:
			reader = csv.DictReader(f)
			for row in reader:
				data += [float(row["psnr_hvs_cb"])]
		return pmean(data, CONFIG.PSNR_HVS_CB.power_mean)
	def vmaf_db(self):
		data = []
		with open(self.csv, newline="") as f:
			reader = csv.DictReader(f)
			for row in reader:
				tmp = 1.0 - (float(row["vmaf"]) / 100.0)
				tmp = max(tmp, sys.float_info.min)
				tmp = min(tmp, 1.0)
				data += [-10.0 * math.log10(tmp)]
		return pmean(data, CONFIG.VMAF_DB.power_mean)
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
		except subprocess.CalledProcessError as e:
			msg_error.print(e)
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

# evaluate_image()
# 	Too high quality: y=+inf
# 	Too low quality:  y=-inf

def evaluate_image(original: Path, op_source: Path, op_destination: Path, hq=False):
	statistics = {}
	statistics["y"]       = None
	statistics["psnr"]    = None
	statistics["ssim_db"] = None
	statistics["psnr_hvs_cb"] = None
	statistics["vmaf_db"] = None

	# Filesize filter
	# If we're not saving drive space, keep the original
	in_bytes  = original.stat().st_size
	out_bytes = op_destination.stat().st_size

	# Frame match check
	ffmpeg_stats = Ffmpeg_stats().set(original)
	ffmpeg_stats.run()
	in_frames = ffmpeg_stats.frames()
	in_resolution = ffmpeg_stats.resolution()
	ffmpeg_stats = Ffmpeg_stats().set(op_destination)
	ffmpeg_stats.run()
	out_frames = ffmpeg_stats.frames()

	# Handle impossible cases, where something has gone very wrong
	if (out_frames > in_frames):
		raise ValueError
	elif ((out_frames < in_frames) and (out_bytes < int(in_bytes * CONFIG.COMPRESSION_RATIO_THRESHOLD))):
		raise ValueError

	# File size filter
	if ((out_bytes / in_bytes > CONFIG.COMPRESSION_RATIO_THRESHOLD) or (out_frames < in_frames)):
		statistics["y"] = float("+inf")
		return (float("+inf"), statistics)

	# Metrics
	total = 0
	results = []

	total += 1
	ffmpeg_psnr = Ffmpeg_psnr().set(op_source, op_destination)
	ffmpeg_psnr.run()
	psnr = [ffmpeg_psnr.psnr(), CONFIG.PSNR]
	statistics["psnr"] = round(psnr[0], 2)
	if (psnr[0] < psnr[1].get_min(hq)):
		statistics["y"] = float("-inf")
		return (float("-inf"), statistics)
	results += [psnr]

	total += 1
	ffmpeg_ssim = Ffmpeg_ssim().set(op_source, op_destination)
	ffmpeg_ssim.run()
	ssim_db = [ffmpeg_ssim.ssim_db(), CONFIG.SSIM_DB]
	statistics["ssim_db"] = round(ssim_db[0], 2)
	if (ssim_db[0] < ssim_db[1].get_min(hq)):
		statistics["y"] = float("-inf")
		return (float("-inf"), statistics)

	total += 2
	if in_resolution[0] >= 320 and in_resolution[1] >= 176:
		ffmpeg_libvmaf = Ffmpeg_libvmaf().set(op_source, op_destination)
		ffmpeg_libvmaf.run()
		psnr_hvs_cb = [ffmpeg_libvmaf.psnr_hvs_cb(), CONFIG.PSNR_HVS_CB]
		vmaf_db     = [ffmpeg_libvmaf.vmaf_db(),     CONFIG.VMAF_DB]
		statistics["psnr_hvs_cb"] = round(psnr_hvs_cb[0], 2)
		if (psnr_hvs_cb[0] < psnr_hvs_cb[1].get_min(hq)):
			statistics["y"] = float("-inf")
			return (float("-inf"), statistics)
		statistics["vmaf_db"] = round(vmaf_db[0], 2)
		if (vmaf_db[0] < vmaf_db[1].get_min(hq)):
			statistics["y"] = float("-inf")
			return (float("-inf"), statistics)
		results += [psnr_hvs_cb]
		results += [vmaf_db]

	y = []
	for value, metric in results:
		contribution = metric.contribution + (len(results) - total) / len(results)
		if (contribution == 0):
			continue
		final = (value - metric.get_target(hq)) * contribution
		y += [final]

	statistics["y"] = sum(y) / len(y)
	return (sum(y) / len(y), statistics)

class Cache:
	def __init__(self, x, parameters, outBytes, y, statistics):
		self.x         = x
		self.parameters = parameters
		self.outBytes   = outBytes
		self.y          = y
		self.statistics = statistics
	def __str__(self):
		ret =  "{"
		ret +=    "x: "          + str(self.x)
		ret +=    "parameters: " + str(self.parameters)
		ret +=    "outBytes: "   + str(self.outBytes)
		ret +=    "y: "          + str(self.y)
		for i in self.statistics:
			ret += str(i) + " " + str(self.statistics[i])
		ret += "}"
		return ret

class Operation():
	def __init__(self, dependencies, path, outdir, statsFile=None):
		self.dependencies = dependencies
		self.path         = path
		self.outdir       = outdir
		self.statsFile    = statsFile

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

	def outCollision(self):
		for i in self.outFiles():
			if (i.is_file()):
				return True
		return False
	def run(self):
		for i in self.dependencies:
			if(not i().works()):
				return False

		ret = True
		statistics = {}
		statistics["inType"] = self.path.suffix
		statistics["inSize"] = self.path.stat().st_size
		try:
			tmp = self.run_operation()
			if tmp is None:
				ret = False
			else:
				statistics["outSize"] = self.op_destination.stat().st_size
				statistics |= tmp
		except subprocess.CalledProcessError as e:
			msg_error.print(e)
			ret = False

		if (ret and self.statsFile):
			csv_header = []
			csv_line   = []
			for i in statistics:
				csv_header += [i]
				csv_line   += [statistics[i]]
			append_line(self.statsFile, csv=csv_line)

		os.makedirs(self.outdir, exist_ok=True)
		for i in [self.op_destination, self.op_info, self.op_log]:
			if (i.is_file()):
				os.rename(i, self.outdir / i.name)
		return ret

class Brentq_scalar(Operation):
	def __init__(self, dependencies, path, outdir, statsFile, lower_bound, upper_bound):
		super().__init__(dependencies, path, outdir, statsFile)
		lower_bound = int(lower_bound)
		upper_bound = int(upper_bound)
		if (lower_bound > upper_bound):
			lower_bound, upper_bound = upper_bound, lower_bound
		self.lower_bound = lower_bound
		self.upper_bound = upper_bound
		self.cache: dict[list[str], Cache] = {}
	def preRun(self):
		return True
	def cached_f(self, x):
		x = int(x)
		idx = str(self.cache_index(x))
		tmp = self.cache.get(idx)
		if (tmp):
			return tmp.y
		tmp = self.f(x)
		self.cache[idx] = tmp
		return tmp.y
	def brentq(self):
		try:
			msg_info.print(root_scalar(self.cached_f, bracket=[self.lower_bound, self.upper_bound], method='brentq', xtol=0.49))
		except ValueError as e:
			msg_info.print(e)
	def run_operation(self):
		if not self.preRun():
			return None
		self.brentq()

		best = None
		for i in self.cache:
			j = self.cache[i]
			if (len(j.outBytes) == 0):
				continue
			if (not best):
				best = j
			if (math.log(len(j.outBytes), 10) * abs(j.y) < math.log(len(best.outBytes),10) * abs(best.y)):
				best = j

		if best is None:
			return None

		write_binary_file(self.op_destination, best.outBytes)

		statistics = best.statistics
		csv_line = []
		for i in statistics:
			csv_line += [statistics[i]]
		append_line(self.op_info, strings=["best:"], csv=csv_line)
		return statistics

class To_avif(Brentq_scalar):
	def __init__(self, path: Path, outdir: Path, hq: bool):
		super().__init__([Avifenc, Exiftool_orientation, Magick_convert, Magick_mogrify_autoorient], path, outdir, STATS_CSV / "to_avif.csv", 0, 100)

		self.hq = hq
		self.psnr_min    = None
		self.psnr_target = None
		self.ssim_min    = None
		self.ssim_target = None
		self.encode_yuv   = 444
		self.stat_rotated = False
	def outSuffix(self, path):
		return path.with_suffix(".avif")
	def infoSuffix(self, path):
		return path.with_suffix(".avif.txt")
	def logSuffix(self, path):
		return path.with_suffix(".avif.log")
	def preRun(self):
		# rotation
		result = Exiftool_orientation().set(self.op_source).run()
		if (grep(r'Rotate', result.stdout.decode('utf‑8'))):
			rotated = self.op_source.with_suffix(".rotated" + self.op_source.suffix)
			shutil.copyfile(self.op_source, rotated)
			Magick_mogrify_autoorient().set(rotated).run()

			ffmpeg_psnr = Ffmpeg_psnr().set(self.op_source, rotated)
			ffmpeg_psnr.run()
			if (ffmpeg_psnr.psnr() > CONFIG.PSNR.get_target(self.hq) + 5):
				self.op_source = rotated
				self.stat_rotated = True
		return True

	def brentq(self):
		# brentq
		failures = 0
		ffmpeg_stats = Ffmpeg_stats().set(self.op_source)
		ffmpeg_stats.run()
		_, src_gbr  = ffmpeg_stats.channels_GBR()
		_, src_gray = ffmpeg_stats.channels_gray()
		_, src_rgb  = ffmpeg_stats.channels_RGB()
		_, src_yuv  = ffmpeg_stats.channels_YUV()

		dst_yuvs = []

		# Grayscale
		if src_yuv == "400" or src_gray:
			dst_yuvs += ["400"]

		# Color		
		if src_yuv == "444" or src_rgb or src_gbr:
			dst_yuvs += ["444", "422", "420"]
		if src_yuv == "422":
			dst_yuvs += ["422", "420"]
		if src_yuv == "420":
			dst_yuvs += ["420"]

		for i in set(dst_yuvs):
			self.encode_yuv = i
			append_line(self.op_info, strings=["YUV ", i])

			if (not super().brentq()):
				failures += 1

		return (failures < 3)

	def cache_index(self, q: int):
		return Cache(q, (self.encode_yuv), None, None, [])
	def f(self, q: int):
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
		statistics = {}
		statistics["rotated"] = self.stat_rotated
		statistics["yuv"]     = self.encode_yuv
		statistics["q"]       = q

		try:
			result = Avifenc().set(self.op_source, self.op_destination, self.encode_yuv, q).run()
		except subprocess.CalledProcessError as e:
			# Try conversion to .png:
			if (grep(r'Unrecognized file format for input file: ', e.stderr.decode('utf-8'))):
				tmp = self.op_source.with_suffix(".png")
				Magick_convert().set(self.op_source, tmp).run()
				self.op_source = tmp
				result = Avifenc().set(self.op_source, self.op_destination, self.encode_yuv, q).run()
			else:
				raise

		append_binary_file(self.op_log, b''+result.stdout+result.stderr)

		y,evaluation_stats = evaluate_image(self.path, self.op_source, self.op_destination, self.hq)
		statistics |= evaluation_stats
		csv_line = []
		for i in statistics:
			csv_line += [statistics[i]]
		append_line(self.op_info, csv=csv_line)

		# Store results to cache
		data = b''
		if (math.isfinite(y)):
			data = read_binary_file(self.op_destination)

		self.op_destination.unlink()
		return Cache(q, (self.encode_yuv), data, y, statistics)

class To_vaav1(Brentq_scalar):
	def __init__(self, path: Path, outdir: Path, hq: bool):
		super().__init__([Ffmpeg_vaav1, Ffmpeg_psnr, Ffmpeg_libvmaf], path, outdir, STATS_CSV / "to_vaav1.csv", 1, 255)

		self.hq = hq
		self.stat_cropped = False

	def outSuffix(self, path):
		return path.with_suffix(".vaav1.mkv")
	def infoSuffix(self, path):
		return path.with_suffix(".vaav1.txt")
	def logSuffix(self, path):
		return path.with_suffix(".vaav1.log")
	def preRun(self):
		# test for gpu support
		ffmpeg_stats = Ffmpeg_stats().set(self.path)
		ffmpeg_stats.run()
		resolution = ffmpeg_stats.resolution()

		resmod = []
		resmod += [resolution[0] % PLATFORM.VAAV1_RESMOD_HORIZONTAL]
		resmod += [resolution[1] % PLATFORM.VAAV1_RESMOD_VERTICAL]
		op_resolution = []

		for i in [0,1]:
			if (resmod[i] > CONFIG.VAAV1_CROP_PIXELS):
				return False
			tmp = resolution[i] - (resmod[i])
			op_resolution += [tmp]
			if (tmp != resolution[i]):
				self.stat_cropped = True

		if (self.stat_cropped):
			self.op_source = self.path.with_suffix(".croppped" + self.path.suffix)
			ffmpeg_crop = Ffmpeg_crop().set(self.path, self.op_source, op_resolution)
			ffmpeg_crop.run()
		return True

	def cache_index(self, q:int):
		return Cache(q, None, None, None, [])
	def f(self, q: int):
		statistics = {}
		statistics["cropped"] = self.stat_cropped
		statistics["q"]       = q

		result = Ffmpeg_vaav1().set(self.op_source, self.op_destination, q, max_bytes=int(self.path.stat().st_size * CONFIG.COMPRESSION_RATIO_THRESHOLD)).run()
		append_binary_file(self.op_log, b''+result.stdout+result.stderr)
		# detect error

		y,evaluation_stats = evaluate_image(self.path, self.op_source, self.op_destination, self.hq)
		statistics |= evaluation_stats
		csv_line = []
		for i in statistics:
			csv_line += [statistics[i]]
		append_line(self.op_info, csv=csv_line)

		# Store results to cache
		data = b''
		if (math.isfinite(y)):
			data = read_binary_file(self.op_destination)

		self.op_destination.unlink()
		return Cache(q, None, data, y, statistics)

class To_aomav1(Brentq_scalar):
	def __init__(self, path: Path, outdir: Path, hq: bool):
		super().__init__([Ffmpeg_aomav1, Ffmpeg_psnr, Ffmpeg_libvmaf], path, outdir, STATS_CSV / "to_aomav1.csv", 1, 63)

		self.hq = hq
	def outSuffix(self, path):
		return path.with_suffix(".aomav1.mkv")
	def infoSuffix(self, path):
		return path.with_suffix(".aomav1.txt")
	def logSuffix(self, path):
		return path.with_suffix(".aomav1.log")
	def cache_index(self, crf: int):
		return Cache(crf, None, None, None, [])
	def f(self, crf: int):
		statistics = {}
		statistics["crf"] = crf

		result = Ffmpeg_aomav1().set(self.op_source, self.op_destination, crf, max_bytes=int(self.path.stat().st_size * CONFIG.COMPRESSION_RATIO_THRESHOLD)).run()
		append_binary_file(self.op_log, b''+result.stdout+result.stderr)
		# detect error

		y,evaluation_stats = evaluate_image(self.path, self.op_source, self.op_destination, self.hq)
		statistics |= evaluation_stats
		csv_line = []
		for i in statistics:
			csv_line += [statistics[i]]
		append_line(self.op_info, csv=csv_line)

		# Store results to cache
		data = b''
		if (math.isfinite(y)):
			data = read_binary_file(self.op_destination)

		self.op_destination.unlink()
		return Cache(crf, None, data, y, statistics)

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
		self.reset()
	def reset(self):
		self.path    = Path()
		self.outdir  = Path()
		self.tempdir = tempfile.TemporaryDirectory(dir=LOCAL_TMP)

class nop(In_types):
	def run(self):
		return True

class Folder(In_types):
	def set(self, path:Path, dry_run = False):
		if (not path.is_dir()):
			return False
		if (not path.is_relative_to(IN_FOLDER)):
			return False

		self.outdir = OUT / "folder"

		if (not dry_run):
			self.path = Path(self.tempdir.name) / path.name
			os.rename(path, self.path)

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
		if (self.outdir == Path()):
			return True

		for i in self.operations():
			if (i.outCollision()):
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
	def out_subdir(self, path:Path):
		return path.suffix
	def reset(self):
		super().reset()
		self.hq = False
	def set_internal(self, path:Path):
		# Basic checks
		if (not path.is_file()):
			return False
		if (path.stat().st_size > PLATFORM.MAX_FILE_BYTES):
			return False
	
		self.path = Path(self.tempdir.name) / path.name
		self.outdir = OUT / self.out_subdir(path)
		self.outdir = self.outdir / path.parent.relative_to(IN_MEDIA)

		# Compatible suffix
		compatible_suffixes = self.suffixes()
		for i in self.suffixes():
			compatible_suffixes.add(str.upper(i))

		# [idx:none] suffixes match. Otherwise idx is none
		idx = None
		for i,_ in enumerate(path.suffixes):
			full_text = ''.join(str(j) for j in path.suffixes[i:None])
			for regex in compatible_suffixes:
				if (re.fullmatch(regex, full_text)):
					idx = i
					break
		if (idx is None):
			return False

		if ((idx > 0) and (path.suffixes[idx-1] == ".hq")):
			self.hq = True
		if (self.outCollision()):
			return False
		return True

	def set(self, path:Path, dry_run=False):
		ret = self.set_internal(path)
		if (dry_run):
			self.reset()
		else:
			os.rename(path, self.path)
		return ret

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
	def suffixes(self):
		return {r'.*'}
	def preRun(self):
		return

class Jpeg(Image):
	def suffixes(self):
		return {r'\.jpg', r'\.jpeg'}
	def preRun(self):
		Jpegoptim().set(self.path).run()
class Png(Image):
	def suffixes(self):
		return {r'\.png'}
	def preRun(self):
		Optipng().set(self.path).run()
class Tif(Image):
	def suffixes(self):
		return {r'\.tif'}
class Webp(Image):
	def suffixes(self):
		return {r'\.webp'}

class Avi(Video):
	def suffixes(self):
		return {r'\.avi'}
class Mkv(Video):
	def suffixes(self):
		return {r'\.ffv1\.mkv', r'\.h264\.mkv'}
class Mov(Video):
	def suffixes(self):
		return {r'\.mov'}
class Mp4(Video):
	def suffixes(self):
		return {r'\.mp4'}
class Webm(Video):
	def suffixes(self):
		return {r'\.webm'}
class Wmv(Video):
	def suffixes(self):
		return {r'\.wmv'}

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
			if (datatype.set(path, dry_run=True)):
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

	processes = [multiprocessing.Process(target=multiplexer, args=(lock_folder, lock_media)) for i in range(PLATFORM.THREADS)]

	if (check_environment(args)):
		for p in processes:
			p.start()

		signal.pause()
		if (sigint_raised):
			msg_status.print("Received SIGINT. Wait for all threads to finish current processing.")

		for p in processes:
			p.join()

	msg_status.print("exiting")
