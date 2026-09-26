# Server-Transcode
Compress images, videos and folders.


1. Run `python3 transcode.py`

    That's all. It will run a self test to ensure all the software is installed, and hardware encoding is enabled.

    If something is missing, install it. If you want to skip all current errors do: `python3 transcode.py --nofail`

2. Copy files & folders to *user_private*

    `cp [...] root/in/user_private/[...]`

    Why?

    - Make a copy of your data in case something goes bad.
    - Move your data to the same filesystem as `root/[...]` to take advanate of atomic rename opeartions instead of copy.

3. Cut-paste / Move

    - folder `mv root/media/user_private/[...] root/in/**folder**/[...]`
    - image/video `mv root/media/user_private/[...] root/in/**media**/[...]`

    ⚠️ **WARNING:** Make sure Linux is performing an atomic 'rename' operation:
    | Operation | Is it safe? |
    | --- | --- |
    | cut-paste | ✅ |
    | `mv root/... root/...` | ✅ |
    | copy-paste | ❌ |

4. Output files are written to `root/out/[...]/[...]`

    - `root/out/other/` contains files that weren't transcoded. Either there was an output folder name conflict, or file was unsupported.
    - `root/stats/` contains some statistics

# Configure (optional)

- *platform.py*: configure software binaries, settings that affect CPU & RAM usage, and settings for VAAV1 hardware acceleration
- *config.py*: change how the evaluation metrics (PSNR, SSIM_DB, PSNR_HVS_CB, VMAF_DB) are applied

# Remote operation (optional)
This application uses standard folders for input and output. You can run it in a server, and then open access to these paths via NFS.

An example setup:
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