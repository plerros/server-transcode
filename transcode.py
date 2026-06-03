

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

def print_run(data, capture_output=False):
	strings = [str(i) for i in data]
	print(strings)
	return subprocess.run(strings, capture_output=capture_output)

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

class Operation():
	def __init__(self):
		self.path   = Path()
		self.outdir = Path()
	def outCollision(self, path, outdir):
		outFiles = [outdir / path.name]

		outFiles += [outdir / self.outSuffix(path)]
		outFiles += [outdir / self.infoSuffix(path)]

		for i in outFiles:
			if (i.is_file()):
				return True
		return False

class To_avif(Operation):
	def __init__(self, path: Path, outdir: Path, psnr_min, psnr_target):
		self.path     = path
		self.outdir   = outdir

		self.psnr_min    = psnr_min
		self.psnr_target = psnr_target

		self.cache: dict[list[str], Cache_avif] = {}
		self.encode_source = path
		self.encode_destination = Path()
		self.encode_info = Path()
		self.encode_yuv = 444
	def outSuffix(self, path):
		return path.with_suffix(".avif")
	def infoSuffix(self, path):
		return path.with_suffix(".avif.txt")
	def run(self):
		stat_inType = self.path.suffix
		stat_inSize = self.path.stat().st_size
		self.encode_destination = self.outSuffix(self.path)
		self.encode_info        = self.infoSuffix(self.path)

		# rotation
		stat_rotated = False
		result = print_run(["exiftool", "-orientation", self.encode_source], capture_output=True)
		if (grep(r'Rotate', result.stdout.decode('utf‑8'))):
			rotated = self.encode_source.with_suffix(".rotated" + self.encode_source.suffix)
			print_run(["cp", self.encode_source, rotated])
			print_run(["magick", "mogrify", "-auto-orient", rotated])

			ffmpeg_psnr = cmd_ffmpeg_psnr(self.encode_source, rotated)
			ffmpeg_psnr.run()
			if (ffmpeg_psnr.psnr() > self.psnr_target + 5):
				self.encode_source = rotated
				stat_rotated = True

		# brentq
		for i in ["444", "422", "420"]:
			self.encode_yuv = i
			append_line(self.encode_info, string="\n" + "YUV " + i)
			print(root_scalar(self.run_operation, bracket=[0, 100], method='brentq', xtol=0.1, maxiter=int(math.log(100,2))))

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

		print_run(["mkdir", "-p", self.outdir])

		out_avif = self.outdir / self.encode_destination.name
		out_avif_txt = self.outdir / self.encode_info.name

		ret = True

		stat_yuv     = ""
		stat_q       = ""
		stat_outSize = ""
		stat_psnr    = ""
		stat_y       = ""
		if (best):
			write_binary_file(out_avif, best.outBytes)
			append_line(self.encode_info, string="best: "+str(best.yuv)+" "+str(best.q)+" "+str(best.psnr))
			stat_yuv     = best.yuv
			stat_q       = best.q
			stat_outSize = len(best.outBytes)
			stat_psnr    = best.psnr
			stat_y       = best.y
		else:
			print("best not found")
			ret = False

		print_run(["mv", self.encode_info, out_avif_txt])
		append_line("stats/csv/to_avif.csv", csv=[stat_inType, stat_inSize, stat_rotated, stat_yuv, stat_q, stat_outSize, stat_psnr, stat_y])
		return ret

	def run_operation(self, q):
		input_hash = str(Cache_avif(read_binary_file(self.encode_source), self.encode_yuv, q, None, None, None))

		if (self.cache.get(input_hash)):
			return (self.cache.get(input_hash)).y

		append_line(self.encode_info, string="doing: " + str(q))

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
		avif_result = print_run(avif_command + [self.encode_source, self.encode_destination], capture_output=True)
		print(avif_result.stdout.decode('utf‑8'))

		# Unsupported filetype
		# should run only once, since we're overwriting the encode_source path
		if (grep(r'Unrecognized file format for input file: ', avif_result.stderr.decode('utf-8'))):
			tmp = self.encode_source.with_suffix(".png")
			print_run(["magick", "convert", self.encode_source, tmp])
			self.encode_source = tmp
			input_hash = str(Cache_avif(read_binary_file(self.encode_source), self.encode_yuv, q))
			print_run(avif_command + [self.encode_source, self.encode_destination])

		ffmpeg_psnr = cmd_ffmpeg_psnr(self.encode_source, self.encode_destination)
		ffmpeg_psnr.run()
		append_line(self.encode_info, string="done")

		psnr = ffmpeg_psnr.psnr()
		y    = psnr - self.psnr_target

		# compare against original
		if (self.path.stat().st_size < self.encode_destination.stat().st_size):
			y = float("+inf")
			append_line(self.encode_info, string="bigger than source")
		else:
			append_line(self.encode_info, string="psnr " + str(psnr))

		# Store results to cache
		self.cache[input_hash] = Cache_avif(read_binary_file(self.encode_source), self.encode_yuv, q, read_binary_file(self.encode_destination), psnr, y)

		self.encode_destination.unlink()
		return y

class To_vaav1(Operation):
	def __init__(self, path: Path, outdir: Path,  psnr_min, psnr_target, vmaf_min, vmaf_target):
		self.path        = path
		self.outdir      = outdir
		self.psnr_min    = psnr_min
		self.psnr_target = psnr_target
		self.vmaf_min    = vmaf_min
		self.vmaf_target = vmaf_target
	def outSuffix(self, path):
		return path.with_suffix(".vaav1.mkv")
	def infoSuffix(self, path):
		return path.with_suffix(".vaav1.txt")
	def run(self):
		return False

class To_aomav1(Operation):
	def __init__(self, path: Path, outdir: Path,  psnr_min, psnr_target, vmaf_min, vmaf_target):
		self.path        = path
		self.outdir      = outdir
		self.psnr_min    = psnr_min
		self.psnr_target = psnr_target
		self.vmaf_min    = vmaf_min
		self.vmaf_target = vmaf_target
	def outSuffix(self, path):
		return path.with_suffix(".aomav1.mkv")
	def infoSuffix(self, path):
		return path.with_suffix(".aomav1.txt")
	def run(self):
		return False

class Copy(Operation):
	def __init__(self, path: Path, outdir: Path):
		self.path        = path
		self.outdir      = outdir
	def outSuffix(self, path):
		return path
	def infoSuffix(self, path):
		return path
	def run(self):
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
	def outCollision(self, path, outdir):
		for i in self.operations():
			if (i.outCollision(path, outdir)):
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

		suffixes = self.suffixes()
		for i in self.suffixes():
			suffixes.add(str.upper(i))

		if (not (path.suffix in suffixes)):
			return False

		outdir = OUT / path.suffix
		outdir = outdir / path.parent.relative_to(IN_MEDIA)

		if (self.outCollision(path, outdir)):
			return False

		self.path = Path(self.tempdir.name) / path.name
		print_run(["mv", path, self.path])

		self.outdir = outdir
		self.operation = Operation()

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
		to_vaav1 = To_vaav1(self.path, self.outdir, self.psnr_min(), self.psnr_target(), self.vmaf_min(), self.vmaf_target)
		to_aomav1 = To_aomav1(self.path, self.outdir, self.psnr_min(), self.psnr_target(), self.vmaf_min(), self.vmaf_target)
		copy = Copy(self.path, self.outdir)
		return [to_vaav1, to_aomav1, copy]
	def preRun(self):
		return
	def vmaf_min(self):
		return 94
	def vmaf_target(self):
		return 95

class Other(File):
	def set(self, path:Path):
		if (not path.is_file()):
			return False

		self.outdir = OUT / "other"
		self.outdir = self.outdir / path.relative_to(IN_MEDIA)

		if (self.outCollision(path, outdir)):
			return False

		print_run(["mv", path, self.outdir])

		return True
	def operations(self):
		copy = Copy(self.path, self.outdir)
		return [copy]
	def preRun(self):
		return

class Png(Image):
	def suffixes(self):
		return {".png"}
	def preRun(self):
		print_run(["optipng", "-o7", self.path])
class Png_hq(Png):
	def suffixes(self):
		return {".png_hq"}
	def psnr_min(self):
		return 53
	def psnr_target(self):
		return 54
class Jpeg(Image):
	def suffixes(self):
		return {".jpg", ".jpeg"}
	def preRun(self):
		print_run(["jpegoptim", self.path])
class Webp(Image):
	def suffixes(self):
		return {".webp"}

class Mp4(Video):
	def suffixes(self):
		return {".mp4"}
class Mkv(Video):
	def suffixes(self):
		return {".mkv"}

class Transcode:
	def __init__(self):
		self.datatype = Other()
	def set(self, path:Path):
		folder = Folder()

		png  = Png()
		jpeg = Jpeg()
		webp = Webp()

		mp4 = Mp4()
		mkv = Mkv()

		other = Other()

		for i in [folder, png, jpeg, mp4, mkv, other]:
			if (i.set(path)):
				self.datatype = i
				return

	def run(self):
		self.datatype.run()

def multiplexer(lock_media, lock_folder):
	while (True):
		transcode = Transcode()
		with lock_folder:
			folders = [i for i in IN_FOLDER.iterdir() if i.is_dir()]
			for i in folders:
				transcode.set(i)
				break
		transcode.run()

		with lock_media:
			medias = [i for i in IN_MEDIA.rglob("*") if i.is_file()]
			for i in medias:
				transcode.set(i)
				break

		transcode.run()
		time.sleep(10)

if __name__ == "__main__":
	lock_folder = multiprocessing.Lock()
	lock_media  = multiprocessing.Lock()
	processes = [multiprocessing.Process(target=multiplexer, args=(lock_folder, lock_media)) for i in range(os.cpu_count())]
	for p in processes:
		p.start()
	for p in processes:
		p.join()
