

import math
import multiprocessing
import os
from   pathlib import Path
import re
from   scipy.optimize import root_scalar
import shutil
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

def grep(pattern: re.Pattern, string: str):
	result = re.search(pattern, string)
	if (not result):
		return ""

	return result[0];

def append_line(file_path: str, string="", csv=[]) -> None:
	line=str(string)
	for i in csv:
		line += ","+str(i)

	print("[info  ]", line)
	with open(file_path, mode='a', encoding='utf-8') as f:
		f.write(line+"\n")

def read_binary_file(path: str) -> bytes:
	with open(path, "rb") as f:
		return f.read()

def write_binary_file(path: str, data: bytes) -> None:
	with open(path, "wb") as f:
		f.write(data)

class Command():
	def __init__(self, execName):
		self.execName = execName
		self.args     = []
	def set(self, args):
		self.args = args
	def run(self, capture_output=False):
		strings = [self.execName] + [str(i) for i in self.args]
		print("[exec  ]", strings)
		result = subprocess.run(strings, capture_output=capture_output)
		if (capture_output):
			print("[exec  ]", result.stdout.decode('utf‑8'))
			print("[exec  ]", result.stderr.decode('utf‑8'))
		return result
	def exec_exists(self):
		return (shutil.which(self.execName) is not None)

class Avifenc(Command):
	def __init__(self):
		super().__init__("avifenc")
	def set(self, source: Path, destination: Path, yuv, q):
		super().set(["-j", "8", "--yuv", yuv, "-q", q, "--speed", "0", "--codec", "aom", source, destination])
		return self

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

class Ffmpeg_aomav1(Command):
	def __init__(self):
		super().__init__("ffmpeg")
	def set(self, source: Path, destination: Path, crf):
		super().set(["-i", source, "-c:v", "libaom-av1", "-b:v", 0, "-crf", crf, "-quality", "good", "-speed", 0, "-c:a", "libopus", "-b:a", "128k", destination])
		return self

class Ffmpeg_psnr(Command):
	def __init__(self):
		super().__init__("ffmpeg")
	def set(self, original: Path, transcoded: Path):
		super().set(["-i", transcoded, "-i", original, "-filter_complex", "psnr", "-f", "null", "-"])
		self.stderr     = ""
		return self
	def run(self, capture_output=False):
		result = super().run(capture_output=True)
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

class Ffmpeg_vaav1(Command):
	def __init__(self):
		super().__init__("ffmpeg")
	def set(self, source: Path, destination: Path, q):
		super().set(["-i", source, "-vaapi_device", "/dev/dri/renderD128", "-vf", "'format=nv12,hwupload'", "-c:v", "av1_vaapi", "-b:v", 0, "-q:v", int(q), "-g:v", 10000000, "-compression_level:v", 29, "-c:a", "libopus", "-b:a", "128k", destination])
		return self

class Ffmpeg_vmaf(Command):
	def __init__(self):
		super().__init__("ffmpeg")
	def set(self, original: Path, transcoded: Path):
		super().set(["-i", transcoded, "-i", original, "-lavfi", "libvmaf", "-f", "null", "-"])
		self.stderr     = ""
		return self
	def run(self, capture_output=False):
		result = super().run(capture_output=True)
		self.stderr = result.stderr.decode('utf-8')
		return result
	def vmaf(self):
		tmp_re    = grep(r'VMAF.*'        , self.stderr)
		tmp_re    = grep(r'[0-9]*\.[0-9]*', tmp_re)
		return float(tmp_re)

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
		os.makedirs(self.outdir, exist_ok=True)
		for i in [self.op_destination, self.op_info, self.op_log]:
			os.rename(i, self.outdir / i.name)
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
		result = Exiftool_orientation().set(self.op_source).run(capture_output=True)
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
				print("[exec  ]", root_scalar(self.run_operation, bracket=[0, 100], method='brentq', xtol=0.1, maxiter=int(math.log(100,2))))
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
			print("[info  ]", "best not found")
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

		avif_result = Avifenc().set(self.op_source, self.op_destination, self.encode_yuv, q).run(capture_output=True)	

		# Unsupported filetype
		# should run only once, since we're overwriting the op_source path
		if (grep(r'Unrecognized file format for input file: ', avif_result.stderr.decode('utf-8'))):
			tmp = self.op_source.with_suffix(".png")
			Magick_convert().set(self.op_source, tmp).run()
			self.op_source = tmp
			input_hash = str(Cache_avif(read_binary_file(self.op_source), self.encode_yuv, q, None, None, None))
			avif_result = Avifenc().set(self.op_source, self.op_destination, self.encode_yuv, q).run(capture_output=True)

		append_line(self.op_info, string="done")
		write_binary_file(self.op_log, b''+avif_result.stdout+avif_result.stderr)

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
			print("[exec  ]", root_scalar(self.run_operation, bracket=[1, 63], method='brentq', xtol=0.1, maxiter=int(math.log(64,2))))
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
			print("[info  ]", "best not found")
			ret = False

		append_line(STATS_CSV / "to_aomav1.csv", csv=[stat_inType, stat_inSize, stat_crf, stat_outSize, stat_psnr, stat_vmaf, stat_y])
		return ret

	def run_operation(self, crf: int):
		crf = int(crf)
		input_hash = str(Cache_aomav1(read_binary_file(self.op_source), crf, None, None, None, None))

		if (self.cache.get(input_hash)):
			return (self.cache.get(input_hash)).y

		append_line(self.op_info, string="doing: " + str(crf))
		aomav1_result = Ffmpeg_aomav1().set(self.op_source, self.op_destination, crf).run(capture_output=True)

		append_line(self.op_info, string="done")
		write_binary_file(self.op_log, b''+aomav1_result.stdout+aomav1_result.stderr)
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
			os.makedirs(self.outdir, exist_ok=True)
			os.rename(self.path, self.outdir / self.path.name)
			return True

		return False

class In_types:
	def __init__(self):
		self.path    = Path()
		self.outdir  = Path()
		self.tempdir = tempfile.TemporaryDirectory(dir=LOCAL_TMP)

class Folder(In_types):
	def set(self, path:Path):
		if (not path.is_dir()):
			return False

		if (not path.is_relative_to(IN_FOLDER)):
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

		os.rename(path, self.path)

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
		for i in [Folder] + Image.__subclasses__() + Video.__subclasses__() + [Other]:
			datatype = i()
			if (datatype.set(path)):
				self.datatype = datatype
				return True
		print("[error ]", "Already exists in out/other:", path)
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
	print("[status]", "launching")
	for i in Command.__subclasses__():
		cmd = i()
		if (not cmd.exec_exists()):
			print("Missing:", cmd.execName)
			exit(1)

	print("[status]", "checks OK")

	os.makedirs(USER_PRIVATE, exist_ok=True)
	os.makedirs(IN_FOLDER,    exist_ok=True)
	os.makedirs(IN_MEDIA,     exist_ok=True)
	os.makedirs(STATS_CSV,    exist_ok=True)

	if (LOCAL_TMP.is_dir()):
		shutil.rmtree(LOCAL_TMP)
	os.makedirs(LOCAL_TMP,         exist_ok=True)

	lock_folder = multiprocessing.Lock()
	lock_media  = multiprocessing.Lock()
	processes = [multiprocessing.Process(target=multiplexer, args=(lock_folder, lock_media)) for i in range(os.cpu_count())]
	for p in processes:
		p.start()
	for p in processes:
		p.join()

	print("[status]", "exiting")
