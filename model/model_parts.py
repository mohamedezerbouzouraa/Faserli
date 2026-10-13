import glob
import hashlib
import sys

PART = 1_500_000_000 
CHUNK = 64 * 1024 * 1024


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(CHUNK):
            h.update(b)
    return h.hexdigest()

def split(path):
    n = 0
    with open(path, "rb") as src:
        while True:
            written = 0
            out = None
            while written < PART:
                data = src.read(min(CHUNK, PART - written))
                if not data:
                    break
                if out is None:
                    n += 1
                    out = open(f"{path}.part{n:02d}", "wb")
                out.write(data)
                written += len(data)
            if out:
                out.close()
            if written < PART:
                break
    with open(path + ".sha256", "w") as f:
        f.write(sha(path) + "\n")
    print(f"{n} parts written, plus {path}.sha256")


def join(path):
    parts = sorted(glob.glob(path + ".part*"))
    if not parts:
        sys.exit("No part files found next to " + path)
    with open(path, "wb") as out:
        for p in parts:
            with open(p, "rb") as f:
                while b := f.read(CHUNK):
                    out.write(b)
    try:
        expected = open(path + ".sha256").read().split()[0]
        print("checksum OK" if sha(path) == expected else "CHECKSUM MISMATCH, download the parts again")
    except FileNotFoundError:
        print("joined (no .sha256 file to check against)")


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] in ("split", "join"):
    (split if sys.argv[1] == "split" else join)(sys.argv[2])
else:
    sys.exit(__doc__)
