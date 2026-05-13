#!/bin/python3

import math
import multiprocessing
import os
from   pathlib import Path
import re
from   scipy.optimize import root_scalar
import subprocess
import tempfile
import time

IN_FOLDER = Path("in/folder")
IN_MEDIA  = Path("in/media")

OUT_FOLDER = Path("out/folder")

def print_run(data):
	strings = [str(i) for i in data]
	print(strings)
	subprocess.run(strings)

TMPOUT_EXT = ".tmp.avif"

Q_min = 0
Q_max = 100

def append_line(file_path: str, line: str) -> None:
	print(line)
	with open(file_path, mode='a', encoding='utf-8') as f:
		f.write(line+"\n")

def read_binary_file(path: str) -> bytes:
	with open(path, "rb") as f:
		return f.read()

def write_binary_file(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)

def grep(pattern: re.Pattern, string: str):
	result = re.search(pattern, string)
	if (not result):
		return ""

	return result[0];

class Avifencode:
	def __init__(self, src: Path, yuv: int, q: int, psnr_min: int, psnr_target: int):
		self.src = src
		self.yuv = yuv
		self.q   = q
		self.psnr_min    = psnr_min
		self.psnr_target = psnr_target

		tmp = src.with_suffix(TMPOUT_EXT)
		if (tmp.is_file()):
			tmp.unlink()

		self.enc_src = src
		print(self.src.suffix)
		if (self.src.suffix == ".webp"):
			self.enc_src = self.src.with_suffix(".png")
			print_run(["magick", "convert", self.src, self.enc_src])


		#magick -quality $q_now "$infile" "$tmpfile"

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

		#avifenc -j 8 --yuv $yuv_now -q $q_now --cicp 1/13/0 --speed 0 --codec aom "$infile" "$tmpfile"
		print_run(["avifenc", "-j", "8", "--yuv", self.yuv, "-q", self.q, "--speed", "0", "--codec", "aom", self.enc_src, tmp])

		psnr_out  = subprocess.run(["ffmpeg", "-i", tmp, "-i", self.enc_src, "-filter_complex", "psnr", "-f", "null", "-"], capture_output=True).stderr.decode('utf‑8')
		tmp_re    = grep(r'PSNR.*'        , psnr_out)
		tmp_re    = grep(r'min:.* max'    , tmp_re)
		tmp_re    = grep(r'[0-9]*\.[0-9]*', tmp_re)
		self.psnr = float(tmp_re)
		self.y    = self.psnr - self.psnr_target
		self.outBytes = read_binary_file(tmp)
		if (self.is_bigger()):
			self.y = float("+inf")
		tmp.unlink()

	def is_bigger(self):
		return (len(self.outBytes) >= self.src.stat().st_size)

	def is_worse(self):
		if (self.is_bigger()):
			return True
		
		return (self.psnr < self.psnr_min)

class Parameter:
	def __init__(self, name, values):
		self.name   = name
		self.values = values
		self.value  = None

	def __str__(self):
		ret = "{"
		ret +=   "name: " + self.name + ", "
		ret +=   "values: ["
		for i in self.values:
			if (i):
				ret += " " + str(i)
		ret +=   "], "
		ret +=   "value: "
		if (self.value != None):
			ret += str(self.value)
		ret += "}"
		return(ret)

	def set(self, value):
		self.value = value


class Axis:
	def __init__(self, name, min, max):
		self.name  = name
		self.min   = min
		self.max   = max
		self.value = None

	def __str__(self):
		ret =  "{"
		ret +=    "name: " + str(self.name) + ", "
		ret +=    "min: "  + str(self.min)  + ", "
		ret +=    "max: "  + str(self.max)  + ", "
		ret +=    "value: "
		if (self.value != None):
			ret += str(self.value)
		ret += "}"
		return(ret)

	def set(self, value):
		self.value = value

class MultiDim_Problem:
	def parametersGet(self):
		raise NotImplementedError()
	def axisGet(self):
		raise NotImplementedError()
	def parameterSet(self, parameter: Parameter, value):
		raise NotImplementedError()
	def postProcess(self):
		raise NotImplementedError()
	def solve(self):
		self.solveRecursive(0)
		self.postProcess()
	def solveRecursive(self, current_param_id):
		parameters = self.parametersGet()
		if (current_param_id >= len(parameters)):
			ax = self.axisGet()[0]  # TODO make conditional
			try:
				print(root_scalar(self.run, bracket=[ax.min, ax.max], method='brentq', xtol=0.1, maxiter=int(math.log(ax.max-ax.min,2))))
			except ValueError:
				pass

			return
		parameter = parameters[current_param_id]
		for i in parameter.values:
			self.parameterSet(parameter.name, i)
			self.solveRecursive(current_param_id + 1)

class to_avif(MultiDim_Problem):
	def __init__(self, infile: Path, tmpdir: Path, outdir: Path, parameters):
		self.parameters = []
		for i in parameters:
			self.parameters += [i]
		self.parameters += [Parameter("yuv", [444, 422, 420])]
		self.axis =  []
		self.axis += [Axis("q", 0, 100)]
		self.cache: dict[list[str], Image] = {}

		self.infile       = infile
		self.tmp_avif     = tmpdir / (infile.with_suffix(".avif").name)
		self.tmp_avif_txt = self.tmp_avif.with_suffix(".avif.txt")

		self.outdir       = outdir
		self.out_fail     = outdir / infile.name
		self.out_avif     = outdir / (infile.with_suffix(".avif").name)
		self.out_avif_txt = self.out_avif.with_suffix(".avif.txt")

	def parametersGet(self):
		return(self.parameters)

	def axisGet(self):
		return(self.axis)

	def parameterSet(self, name: str, value):
		for i in self.parameters:
			if (str(i.name) == name):
				i.set(value)
				append_line(self.tmp_avif_txt, "\n"+str(name)+" "+str(value))

	def outfiles(self):
		return [self.out_fail, self.out_avif, self.out_avif_txt]

	def run(self, q:int):
		self.axis[0].set(int(q)) # TODO make conditional
		identifier = ""
		for i in self.parameters:
			identifier += str(i)
		for i in self.axis:
			identifier += str(i)

		if (self.cache.get(identifier)):
			return self.cache.get(identifier).y

		# Setup current parameters
		quality = int(q)
		yuv         = 444
		psnr_min    = 53
		psnr_target = 54
		for i in self.parameters:
			if (i.name == "yuv"):
				yuv = i.value
			if (i.name == "PSNR_MIN"):
				psnr_min = i.value
			if (i.name == "PSNR_TARGET"):
				psnr_target = i.value

		# Run
		append_line(self.tmp_avif_txt, "doing: "+str(quality))
		self.cache[identifier] = Avifencode(self.infile, yuv, quality, psnr_min, psnr_target)
		append_line(self.tmp_avif_txt, "done")

		# Check filesize
		if (self.cache[identifier].is_bigger()):
			append_line(self.tmp_avif_txt, "bigger than source")
		else:
			append_line(self.tmp_avif_txt, "psnr " + str(self.cache[identifier].psnr))
		return (self.cache[identifier].y)

	def postProcess(self):
		best = None
		filtered = []
		for i in self.cache:
			j = self.cache[i]
			if (j.is_worse()):
				continue

			if (not best):
				best = j
			if (math.log(len(j.outBytes), 10) * abs(j.y) < math.log(len(best.outBytes),10) * abs(best.y)):
				best = j

		print_run(["mkdir", "-p", self.outdir])
		if (best):
			if (self.tmp_avif.is_file()):
				self.tmp_avif.unlink()
			write_binary_file(str(self.tmp_avif), best.outBytes)
			append_line(self.tmp_avif_txt, "best: "+str(best.yuv)+" "+str(best.q)+" "+str(best.psnr))
			print_run(["mv", self.tmp_avif, self.out_avif])
			print_run(["mv", self.tmp_avif_txt, self.out_avif_txt])
		else:
			print("best not found")
			print_run(["mv", self.infile, self.out_fail])
			print_run(["mv", self.tmp_avif_txt, self.out_avif_txt])

class Transcode:
	def __init__(self):
		self.initialized = False
		self.path        = Path()
		self.destination = Path()
		self.temp_in     = tempfile.TemporaryDirectory()
		self.temp_out    = tempfile.TemporaryDirectory()
	def set(self, path: Path, destination: Path):
		#check if exists
		if (not (path.is_file() or path.is_dir())):
			return False
		self.path = Path(self.temp_in.name) / path.name
		self.destination = destination
		self.parameters  = []

		if (not self.compatible_suffix()):
			return False
		if (not self.destination_empty()):
			return False
		print_run(["mv", path, self.temp_in.name])
		self.parameters_init()
		self.initialized = True
		return True
	def compatible_suffix(self):
		raise NotImplementedError()
	def destination_empty(self):
		raise NotImplementedError()
	def process_internal(self):
		raise NotImplementedError()
	def parameters_init(self):
		raise NotImplementedError()
	def process(self):
		if (self.initialized):
			self.process_internal()
		self.temp_in.cleanup()
		self.temp_out.cleanup()

class Folder(Transcode):
	def process_internal(self):
		product = Path(self.temp_out.name) / (self.path.with_suffix(".7z").name)
		print_run(["7za", "a", "-t7z", "-m0=lzma2", "-mx=9", "-mfb=273", "-md=29", "-ms=8g", "-mmt=off", "-mmtf=off", "-mqs=on", "-bt", "-bb3", product, self.path])
		print_run(["mv", product, self.destination])
	def parameters_init(self):
		return

class Image(Transcode):
	def parameters_init(self):
		self.parameters = []
		self.parameters += [Parameter("PSNR_MIN",    [44])]
		self.parameters += [Parameter("PSNR_TARGET", [45])]
	def destination_empty(self):
		for i in to_avif(self.path, Path(self.temp_out.name), self.destination, self.parameters).outfiles():
			if (i.is_file()):
				print("Already exists:", i)
				return False
		return True

class PNG(Image):
	def compatible_suffix(self):
		return (self.path.suffix in {".png", ".PNG"})
	def process_internal(self):
		print_run(["optipng", "-o7", self.path])
		to_avif(self.path, Path(self.temp_out.name), self.destination, self.parameters).solve()

class PNG_HQ(PNG):
	def compatible_suffix(self):
		return (self.path.suffix in {".png_hq"})
	def parameters_init(self):
		self.parameters = []
		self.parameters += [Parameter("PSNR_MIN",    [53])]
		self.parameters += [Parameter("PSNR_TARGET", [54])]

class JPEG(Image):
	def compatible_suffix(self):
		return (self.path.suffix in {".jpg", ".JPG", ".jpeg", ".JPEG"})
	def process_internal(self):
		print_run(["jpegoptim", self.path])
		to_avif(self.path, Path(self.temp_out.name), self.destination, self.parameters).solve()

class WEBP(Image):
	def compatible_suffix(self):
		return (self.path.suffix in {".webp", ".WEBP"})
	def process_internal(self):
		to_avif(self.path, Path(self.temp_out.name), self.destination, self.parameters).solve()

def multiplexer(lock_media, lock_folder):
	while (True):
		folder = Folder()
		with lock_folder:
			folders = [i for i in IN_FOLDER.iterdir() if i.is_dir()]
			if (folders):
				folder.set(folders[0], OUT_FOLDER)
		folder.process()

		jpeg   = JPEG()
		png    = PNG()
		png_hq = PNG_HQ()
		webp   = WEBP()
		with lock_media:
			medias = [i for i in IN_MEDIA.rglob("*") if i.is_file()]
			for i in medias:
				destination_path = Path("out") / i.suffix
				destination_path = destination_path / i.parent.relative_to(IN_MEDIA) # preserve user's folder structure

				is_any = False
				is_any |= jpeg.set(  i, destination_path)
				is_any |= png.set(   i, destination_path)
				is_any |= png_hq.set(i, destination_path)
				is_any |= webp.set(  i, destination_path)

				if (is_any):
					break

		jpeg.process()
		png.process()
		png_hq.process()
		webp.process()
		time.sleep(10)

if __name__ == "__main__":
	lock_folder = multiprocessing.Lock()
	lock_media  = multiprocessing.Lock()
	processes = [multiprocessing.Process(target=multiplexer, args=(lock_folder, lock_media)) for i in range(os.cpu_count())]
	for p in processes:
		p.start()
	for p in processes:
		p.join()
