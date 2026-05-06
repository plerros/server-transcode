mkdir in
mkdir in/folder
mkdir in/media
mkdir in/user_private
mkdir out
mkdir out/folder
mkdir out/.jpg
mkdir out/.png

sudo mkdir /exports
sudo chown "$(whoami):$(whoami)" /exports

mkdir /exports/in
mkdir /exports/out

sudo mount --bind in  /exports/in
sudo mount --bind out /exports/out

line = "/exports/in  $(cat ip.txt)/24(rw,sync,no_subtree_check)"
grep -qxF "$line" /etc/exports || printf '%s\n' "$line" | sudo tee /etc/exports

line = "/exports/out $(cat ip.txt)/24(rw,sync,no_subtree_check)"
grep -qxF "$line" /etc/exports || printf '%s\n' "$line" | sudo tee /etc/exports

sudo exportfs -a
sudo systemctl restart nfs-kernel-server
