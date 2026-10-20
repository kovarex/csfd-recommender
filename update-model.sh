#!/bin/sh
# On the PC (WSL): exports the database on NFS, downloads it, trains the model and uploads it for the web pages,
# where every user's recommendations are then computed on the fly. Run from anywhere, as often as you like:
#   ./update-model.sh
# SSH asks for the NFS password once per run (or never after `ssh-copy-id` with the login below).
set -e
cd "$(dirname "$0")"
NFS=${CSFD_NFS:-kovarex_csfd-recommender@ssh.nyc1.nearlyfreespeech.net}
DATA=${CSFD_DATA:-$HOME/csfd-data}
# One SSH connection for all the steps below, so the password is asked only once.
SSH_OPTIONS="-o ControlMaster=auto -o ControlPath=$HOME/.ssh/csfd-%r@%h -o ControlPersist=120"
mkdir -p "$HOME/.ssh" "$DATA"
[ -f .venv/bin/activate ] && . .venv/bin/activate

echo "== Exporting the database on NFS"
ssh $SSH_OPTIONS "$NFS" 'cd /home/private/csfd-recommender && python3 -m csfd.crawl export'
echo "== Downloading the export to $DATA"
scp $SSH_OPTIONS "$NFS:/home/private/exports/*.gz" "$DATA/"
echo "== Training"
python3 -m ml.train --data "$DATA" --out model.npz
python3 -m ml.publish --data "$DATA" --model model.npz --out web-model.bin
echo "== Uploading the model for the web"
scp $SSH_OPTIONS web-model.bin "$NFS:/home/protected/csfd-model.bin.tmp"
ssh $SSH_OPTIONS "$NFS" 'chmod 644 /home/protected/csfd-model.bin.tmp && mv /home/protected/csfd-model.bin.tmp /home/protected/csfd-model.bin'
echo "Done: https://csfd-recommender.com/?view=recommend&id=63015"
