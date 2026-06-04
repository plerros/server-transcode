

import math
import multiprocessing
import os
from pathlib import Path
import re
from   scipy.optimize import root_scalar
import subprocess
import tempfile
import time

IN_FOLDER  = Path("in/folder")
IN_MEDIA   = Path("in/media")
OUT        = Path("out")
STATS_CSV  = Path("stats/csv")

def print_run(data, capture_output=False):
	strings = [str(i) for i in data]
	print(strings)
	result = subprocess.run(strings, capture_output=capture_output)
	if (capture_output):
		print(result.stdout.decode('utf‑8'))
		print(result.stderr.decode('utf‑8'))
	return result

def grep(pattern: re.Pattern, string: str):
	result = re.search(pattern, string)
	if (not result):
		return ""

	return result[0];

def append_line(file_path: str, string="", csv=[]) -> None:
	line=str(string)
	for i in csv:
		line += ","+str(i)

	print(line)
	with open(file_path, mode='a', encoding='utf-8') as f:
		f.write(line+"\n")

def read_binary_file(path: str) -> bytes:
	with open(path, "rb") as f:
		return f.read()

def write_binary_file(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)

class cmd_ffmpeg_psnr:
	def __init__(self, original: Path, transcoded: Path):
		self.original   = original
		self.transcoded = transcoded
		self.stderr     = ""

	def run(self):
		self.stderr = print_run(["ffmpeg", "-i", self.transcoded, "-i", self.original, "-filter_complex", "psnr", "-f", "null", "-"], capture_output=True).stderr.decode('utf-8')

	def psnr(self):
		tmp_re    = grep(r'PSNR.*'        , self.stderr)
		tmp_re    = grep(r'min:.* max'    , tmp_re)
		finite    = grep(r'[0-9]*\.[0-9]*', tmp_re)
		infinite  = grep(r'inf', tmp_re)
		if (finite == ""):
			return float(infinite)

		return float(finite)

class cmd_ffmpeg_vmaf:
	def __init__(self, original: Path, transcoded: Path):
		self.original   = original
		self.transcoded = transcoded
		self.stderr     = ""

	def run(self):
		self.stderr = print_run(["ffmpeg", "-i", self.transcoded, "-i", self.original, "-lavfi", "libvmaf", "-f", "null", "-"], capture_output=True).stderr.decode('utf-8')

	def vmaf(self):
		tmp_re    = grep(r'VMAF.*'        , self.stderr)
		tmp_re    = grep(r'[0-9]*\.[0-9]*', tmp_re)
		return float(tmp_re)

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
		ret +=    "crf: "     + str(self.crf)
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
	def __init__(self, path, outdir):
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
		ret = self.run_internal()
		print_run(["mkdir", "-p", self.outdir])
		for i in [self.op_destination, self.op_info, self.op_log]:
			print_run(["mv", i, self.outdir / i.name])
		return ret

class To_avif(Operation):
	def __init__(self, path: Path, outdir: Path, psnr_min, psnr_target):
		super().__init__(path, outdir)

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
		result = print_run(["exiftool", "-orientation", self.op_source], capture_output=True)
		if (grep(r'Rotate', result.stdout.decode('utf‑8'))):
			rotated = self.op_source.with_suffix(".rotated" + self.op_source.suffix)
			print_run(["cp", self.op_source, rotated])
			print_run(["magick", "mogrify", "-auto-orient", rotated])

			ffmpeg_psnr = cmd_ffmpeg_psnr(self.op_source, rotated)
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
				print(root_scalar(self.run_operation, bracket=[0, 100], method='brentq', xtol=0.1, maxiter=int(math.log(100,2))))
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
			print("best not found")
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

		avif_command = ["avifenc", "-j", "8", "--yuv", self.encode_yuv, "-q", q, "--speed", "0", "--codec", "aom"]
		avif_result = print_run(avif_command + [self.op_source, self.op_destination], capture_output=True)

		# Unsupported filetype
		# should run only once, since we're overwriting the op_source path
		if (grep(r'Unrecognized file format for input file: ', avif_result.stderr.decode('utf-8'))):
			tmp = self.op_source.with_suffix(".png")
			print_run(["magick", "convert", self.op_source, tmp])
			self.op_source = tmp
			input_hash = str(Cache_avif(read_binary_file(self.op_source), self.encode_yuv, q))
			print_run(avif_command + [self.op_source, self.op_destination])

		append_line(self.op_info, string="done")

		# compare against original
		psnr = float("nan")
		if (self.path.stat().st_size < self.op_destination.stat().st_size):
			append_line(self.op_info, string="bigger than source")
		else:
			ffmpeg_psnr = cmd_ffmpeg_psnr(self.op_source, self.op_destination)
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
		super().__init__(path, outdir)
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
	def run(self):
		self.run_internal()
	def run_internal(self):
		# test for gpu support
		# brentq
		# best
		return False
	def run_operation(self, q: int):
		q = int(q)
		vaav1_command =  ["ffmpeg", "-i", self.op_source]
		vaav1_command += ["-vaapi_device", "/dev/dri/renderD128"]
		vaav1_command += ["-vf", "'format=nv12,hwupload'", "-c:v", "av1_vaapi", "-b:v", 0, "-q:v", int(q), "-g:v", 10000000, "-compression_level:v", 29]
		vaav1_command += ["-c:a", "libopus", "-b:a", "128k"]
		vaav1_command += [self.op_destination]

class To_aomav1(Operation):
	def __init__(self, path: Path, outdir: Path,  psnr_min, psnr_target, vmaf_min, vmaf_target):
		super().__init__(path, outdir)

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
			print(root_scalar(self.run_operation, bracket=[1, 63], method='brentq', xtol=0.1, maxiter=int(math.log(64,2))))
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
			append_line(self.op_info, string="best: "+str(best.yuv)+" "+str(best.q)+" "+str(best.psnr))
			stat_crf     = best.crf
			stat_outSize = len(best.outBytes)
			stat_psnr    = best.psnr
			stat_vmaf    = best.vmaf
			stat_y       = best.y
		else:
			print("best not found")
			ret = False

		append_line(STATS_CSV / "to_aomav1.csv", csv=[stat_inType, stat_inSize, stat_crf, stat_outSize, stat_psnr, stat_vmaf, stat_y])
		return ret

	def run_operation(self, crf: int):
		crf = int(crf)
		input_hash = str(Cache_aomav1(read_binary_file(self.op_source), crf, None, None, None, None))

		if (self.cache.get(input_hash)):
			return (self.cache.get(input_hash)).y

		append_line(self.op_source, string="doing: " + str(crf))

		aomav1_command =  ["ffmpeg", "-i", self.op_source]
		aomav1_command += ["-c:v", "libaom-av1", "-b:v", 0, "-crf", crf, "-quality", "good", "-speed", 0]
		aomav1_command += ["-c:a", "libopus", "-b:a", "128k"]
		aomav1_command += [self.op_destination]
		aomav1_result = print_run(aomav1_command, capture_output=True)

		append_line(self.op_info, string="done")
		write_binary_file(self.op_log, b''+aomav1_result.stdout+aomav1_result.stderr)
		# detect error

		psnr = float("nan")
		vmaf = float("nan")
		if (self.path.stat().st_size < self.op_destination.stat().st_size):
			append_line(self.op_info, string="bigger than source")
		else:
			ffmpeg_psnr = cmd_ffmpeg_psnr(self.op_source, self.op_destination)
			ffmpeg_psnr.run()
			psnr = ffmpeg_psnr.psnr()
			append_line(self.op_info, string="psnr " + str(psnr))

		if (psnr >= self.psnr_min):
			ffmpeg_vmaf = cmd_ffmpeg_vmaf(self.op_source, self.op_destination)
			ffmpeg_vmaf.run()
			vmaf = ffmpeg_vmaf.vmaf()
		
		y = vmaf - self.vmaf_target

		# Store results to cache
		self.cache[input_hash] = Cache_aomav1(read_binary_file(self.op_source), crf, read_binary_file(self.op_destination), psnr, vmaf, y)
		self.op_destination.unlink()
		return y

class Copy(Operation):
	def __init__(self, path: Path, outdir: Path):
		super().__init__(path, outdir)
	def outSuffix(self, path):
		return path
	def infoSuffix(self, path):
		return path
	def logSuffix(self, path):
		return path
	def run(self):
		if (self.path.is_file()):
			print_run(["mkdir", "-p", self.outdir])
			print_run(["mv", self.path, self.outdir])
			return True

		return False

class In_types:
	def __init__(self):
		self.path    = Path()
		self.outdir  = Path()
		self.tempdir = tempfile.TemporaryDirectory()

class Folder(In_types):
	def set(self, path:Path):
		if (not path.is_dir()):
			return False

		if (not path.is_relative_to(IN_FOLDER)):
			return False

		self.path = Path(self.tempdir.name) / path.name
		print_run(["mv", path, self.path])

		self.outdir = OUT / "folder"
		return True
	def run(self):
		product = self.path.with_suffix(".7z")
		print_run(["7za", "a", "-t7z", "-m0=lzma2", "-mx=9", "-mfb=273", "-md=29", "-ms=8g", "-mmt=off", "-mmtf=off", "-mqs=on", "-bt", "-bb3", product, self.path])
		print_run(["mkdir", "-p", self.outdir])
		print_run(["mv", product, self.outdir])

class File(In_types):
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

	def set(self, path:Path):
		if (not path.is_file()):
			return False

		# Compatible suffix
		is_compatible = False
		compatible_suffixes = self.suffixes()
		for i in self.suffixes():
			compatible_suffixes.add(str.upper(i))
		suffixes = path.suffixes
		for idx, x in enumerate(path.suffixes):
			if (''.join(str(i) for i in suffixes[idx:None])) in compatible_suffixes:
				is_compatible = True
		if (not is_compatible):
			return False

		# Late initialization
		self.path = Path(self.tempdir.name) / path.name
		self.outdir = OUT / path.suffix
		self.outdir = self.outdir / path.parent.relative_to(IN_MEDIA)

		if (self.outCollision()):
			return False

		print_run(["mv", path, self.path])


		return True

	def psnr_min(self):
		return 44
	def psnr_target(self):
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

		print_run(["mv", path, self.path])

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
		print_run(["jpegoptim", self.path])
class Png(Image):
	def suffixes(self):
		return {".png"}
	def preRun(self):
		print_run(["optipng", "-o7", self.path])
class Png_hq(Png):
	def suffixes(self):
		return {".hq.png"}
	def psnr_min(self):
		return 53
	def psnr_target(self):
		return 54
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

class Transcode:
	def __init__(self):
		self.datatype = Other()
	def set(self, path:Path):

		for i in [
			Folder(),
			Jpeg(), Png(), Png_hq(),
			Avi(), Mkv(), Mov(), Mp4(), Webm(),
			Other()
		]:
			if (i.set(path)):
				self.datatype = i
				return True
		print("Already exists in out/other:", path)
		return False

	def run(self):
		self.datatype.run()

def multiplexer(lock_media, lock_folder):
	while (True):
		transcode = Transcode()
		with lock_folder:
			folders = [i for i in IN_FOLDER.iterdir() if i.is_dir()]
			for i in folders:
				if (transcode.set(i)):
					break
		transcode.run()

		with lock_media:
			medias = [i for i in IN_MEDIA.rglob("*") if i.is_file()]
			for i in medias:
				if (transcode.set(i)):
					break

		transcode.run()
		time.sleep(10)

if __name__ == "__main__":
	print_run(["mkdir", "-p", IN_FOLDER])
	print_run(["mkdir", "-p", IN_MEDIA])
	print_run(["mkdir", "-p", "in/user-private"])
	print_run(["mkdir", "-p", STATS_CSV])
	lock_folder = multiprocessing.Lock()
	lock_media  = multiprocessing.Lock()
	processes = [multiprocessing.Process(target=multiplexer, args=(lock_folder, lock_media)) for i in range(os.cpu_count())]
	for p in processes:
		p.start()
	for p in processes:
		p.join()
