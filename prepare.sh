mkdir in
mkdir in/folder
mkdir in/media
mkdir in/user_private
mkdir out

sudo mkdir /exports
sudo chown "$(whoami):$(whoami)" /exports

rmdir /exports/in
rmdir /exports/out
rm /exports/in
rm /exports/out

ln -s "$(pwd)/in"  /exports/in
ln -s "$(pwd)/out" /exports/out

line="/exports/in  $(cat ip.txt)/24(rw,sync,no_subtree_check)"
grep -qxF "$line" /etc/exports || printf '%s\n' "$line" | sudo tee -a /etc/exports

line="/exports/out $(cat ip.txt)/24(rw,sync,no_subtree_check)"
grep -qxF "$line" /etc/exports || printf '%s\n' "$line" | sudo tee -a /etc/exports

sudo exportfs -a
sudo systemctl restart nfs-kernel-server
