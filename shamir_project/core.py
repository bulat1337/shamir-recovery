import json
import os
import re
import stat
import struct
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from secrets import token_bytes
from tempfile import NamedTemporaryFile

from Crypto.Cipher import AES
from Crypto.Protocol.SecretSharing import Shamir

THRESHOLD = 3
SHARE_COUNT = 5
MAX_FILE = 32 * 1024**2
MAX_SHARE_FILE = 1024
HEADER = struct.Struct(">4sB16s12s")
MAGIC = b"SSSF"
VERSION = 1
TAG_SIZE = 16


class FormatError(ValueError):
    """Файл не соответствует формату прототипа."""


@dataclass(frozen=True)
class Share:
    set_id: str
    index: int
    value: bytes

    def encode(self) -> bytes:
        fields = {"v": VERSION, "set_id": self.set_id, "x": self.index, "y": self.value.hex()}
        return (json.dumps(fields, separators=(",", ":")) + "\n").encode("utf-8")

    @classmethod
    def decode(cls, raw: bytes) -> "Share":
        try:
            fields = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_fields)
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise FormatError("не удалось прочитать JSON доли") from exc
        if not isinstance(fields, dict) or fields.keys() != {"v", "set_id", "x", "y"}:
            raise FormatError("ожидались поля v, set_id, x и y")
        if type(fields["v"]) is not int or fields["v"] != VERSION:
            raise FormatError("неподдерживаемая версия доли")
        index = fields["x"]
        if type(index) is not int or not 1 <= index <= SHARE_COUNT:
            raise FormatError("индекс доли должен быть от 1 до 5")
        for field in ("set_id", "y"):
            if not isinstance(fields[field], str) or re.fullmatch("[0-9a-f]{32}", fields[field]) is None:
                raise FormatError(f"поле {field} должно содержать 32 шестнадцатеричных знака")
        return cls(fields["set_id"], index, bytes.fromhex(fields["y"]))


def unique_fields(pairs: list) -> dict:
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError("повтор поля JSON")
        fields[key] = value
    return fields


@dataclass(frozen=True)
class Container:
    header: bytes
    set_id: str
    nonce: bytes
    body: bytes
    tag: bytes

    @classmethod
    def decode(cls, raw: bytes) -> "Container":
        if not HEADER.size + TAG_SIZE <= len(raw) <= HEADER.size + MAX_FILE + TAG_SIZE:
            raise FormatError("длина контейнера вне допустимого диапазона")
        magic, version, set_id, nonce = HEADER.unpack_from(raw)
        if magic != MAGIC or version != VERSION:
            raise FormatError("неизвестный формат или версия контейнера")
        return cls(raw[:HEADER.size], set_id.hex(), nonce, raw[HEADER.size:-TAG_SIZE], raw[-TAG_SIZE:])


@dataclass(frozen=True)
class Entry:
    position: int
    label: str
    share: Share | None
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Inputs:
    container: Container
    entries: tuple[Entry, ...]
    candidates: tuple[Entry, ...]


@dataclass(frozen=True)
class Recovery:
    status: str
    attempts: tuple[tuple[int, ...], ...] = ()
    plaintext: bytes | None = None

    @property
    def valid(self) -> bool:
        return self.plaintext is not None

    @property
    def selected(self) -> tuple[int, ...]:
        return self.attempts[-1] if self.valid else ()


STATUS = {
    "ok": "Файл восстановлен",
    "too_few": "Нужно хотя бы три доли с разными индексами",
    "first_invalid": "В первых трёх файлах есть ошибка формата или повтор индекса",
    "unverified": "Ни один проверенный ключ не подошёл к контейнеру",
}


def encrypt(data: bytes) -> tuple[bytes, tuple[Share, ...]]:
    if len(data) > MAX_FILE:
        raise FormatError("исходный файл больше 32 МиБ")
    key, set_id, nonce = token_bytes(16), token_bytes(16), token_bytes(12)
    header = HEADER.pack(MAGIC, VERSION, set_id, nonce)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce, mac_len=TAG_SIZE)
    cipher.update(header)
    body, tag = cipher.encrypt_and_digest(data)
    shares = tuple(Share(set_id.hex(), x, y) for x, y in Shamir.split(THRESHOLD, SHARE_COUNT, key))
    return header + body + tag, shares


def read_file(path: Path, limit: int) -> bytes:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise FormatError("ожидался обычный файл")
    if info.st_size > limit:
        raise FormatError("файл превышает допустимый размер")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise FormatError("файл превышает допустимый размер")
    return data


def write_file(path: Path, data: bytes) -> None:
    """Атомарная запись с правами временного файла."""
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".shamir-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_share(path: Path) -> bytes | str:
    try:
        return read_file(path, MAX_SHARE_FILE)
    except FormatError as exc:
        return str(exc)
    except OSError as exc:
        return f"ошибка чтения ({exc.strerror or type(exc).__name__})"


def prepare(container: Container, files: list[tuple[str, bytes | str]]) -> Inputs:
    if len(files) > SHARE_COUNT:
        raise FormatError("можно подать не больше пяти файлов долей")
    parsed = []
    values_by_index: dict[int, set[bytes]] = {}
    for label, raw in files:
        try:
            if isinstance(raw, str):
                raise FormatError(raw)
            share = Share.decode(raw)
        except FormatError as exc:
            parsed.append((label, None, str(exc)))
            continue
        parsed.append((label, share, ""))
        values_by_index.setdefault(share.index, set()).add(share.value)

    seen = set()
    entries, candidates = [], []
    for pos, (label, share, error) in enumerate(parsed, start=1):
        if share is None:
            entries.append(Entry(pos, label, None, (error,)))
            continue
        identity = (share.index, share.value)
        notes = []
        if identity in seen:
            notes.append("повтор доли")
        if len(values_by_index[share.index]) > 1:
            notes.append("конфликт значений для этого индекса")
        if share.set_id != container.set_id:
            notes.append("метка другого набора")
        entry = Entry(pos, label, share, tuple(notes))
        entries.append(entry)
        if identity not in seen:
            candidates.append(entry)
            seen.add(identity)
    return Inputs(container, tuple(entries), tuple(candidates))


def load_inputs(container_path: Path, share_paths: list[Path]) -> Inputs:
    if len(share_paths) > SHARE_COUNT:
        raise FormatError("можно подать не больше пяти файлов долей")
    container = Container.decode(read_file(container_path, HEADER.size + MAX_FILE + TAG_SIZE))
    return prepare(container, [(path.name, read_share(path)) for path in share_paths])


def recover(inputs: Inputs, *, search: bool) -> Recovery:
    if search:
        if len({entry.share.index for entry in inputs.candidates}) < THRESHOLD:
            return Recovery("too_few")
        triples = (
            triple for triple in combinations(inputs.candidates, THRESHOLD)
            if len({entry.share.index for entry in triple}) == THRESHOLD
        )
    else:
        first = inputs.entries[:THRESHOLD]
        if len(first) < THRESHOLD:
            return Recovery("too_few")
        if len({entry.share.index for entry in first if entry.share is not None}) != THRESHOLD:
            return Recovery("first_invalid")
        triples = iter((first,))

    container = inputs.container
    attempts = []
    for triple in triples:
        attempts.append(tuple(entry.position for entry in triple))
        key = Shamir.combine([(entry.share.index, entry.share.value) for entry in triple])
        cipher = AES.new(key, AES.MODE_GCM, nonce=container.nonce, mac_len=TAG_SIZE)
        cipher.update(container.header)
        try:
            plaintext = cipher.decrypt_and_verify(container.body, container.tag)
        except ValueError:
            continue
        return Recovery("ok", tuple(attempts), plaintext)
    return Recovery("unverified", tuple(attempts))
