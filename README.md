# Server-Transcode
Server application to transcode files and compress folders.


1. Run on your server
```
echo [IP] > ip.txt
./prepare.sh
./python3 transcode.py
```

2. Mount on client:
```
mkdir in
mkdir out
sudo mount [IP]:/exports/in  in
sudo mount [IP]:/exports/out out
```

3. Transfer files & folders to */in/user_private/*
4. `mv` files (for transcode) to */in/media/*
5. `mv` folders (for compression) to *in/folder/*
6. pick up the results from *out/*

If you're transcoding high quality media, add `.hq` before the file extension like this:
```
image.hq.png
```
