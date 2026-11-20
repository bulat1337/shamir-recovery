#!/bin/zsh
set -e
cd -- "${0:A:h}"

py=""
for candidate in "${SHAMIR_PYTHON:-}" python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
    if [[ -n "$candidate" ]] && "$candidate" -c 'import sys; import tkinter; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        py="$candidate"
        break
    fi
done
if [[ -z "$py" ]]; then
    print -u2 'Нужен Python 3.10 или новее с tkinter. Подойдёт установщик с python.org.'
    exit 1
fi

version="$("$py" -c 'import sys; print(sys.implementation.cache_tag)')"
venv="${HOME}/Library/Caches/mfti-shamir/${version}"
if ! "$venv/bin/python" -c 'import Crypto; import tkinter; assert Crypto.__version__ == "3.23.0"' 2>/dev/null; then
    "$py" -m venv "$venv"
    "$venv/bin/python" -m pip install -r requirements.txt
fi
exec "$venv/bin/python" -B -m shamir_project
