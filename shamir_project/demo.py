from dataclasses import dataclass, replace

from .core import HEADER, Share, encrypt

DEMO_TEXT = """Защита информации · МФТИ
Локальная проверка восстановления файла.

Этот текст зашифрован случайным ключом. Для проверки
сравните восстановленный файл с исходным побайтно.
""".encode()


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    container: bytes
    files: tuple[tuple[str, bytes], ...]
    can_recover: bool


def altered(share: Share) -> Share:
    return replace(share, value=flip_at(share.value, 2 * share.index))


def flip_at(data: bytes, index: int) -> bytes:
    return data[:index] + bytes([data[index] ^ 1]) + data[index + 1:]


def scenarios(data: bytes) -> tuple[Scenario, ...]:
    container, shares = encrypt(data)
    _, foreign = encrypt(b"other")
    a, b, c, d, e = [(f"Доля {share.index}.json", share.encode()) for share in shares]
    bad_a = ("Доля 1 (изменена).json", altered(shares[0]).encode())
    bad_b = ("Доля 2 (изменена).json", altered(shares[1]).encode())
    conflict = ("Доля 1 (другое значение).json", altered(shares[0]).encode())
    marked = ("Доля 1 (другая метка).json", replace(shares[0], set_id=foreign[0].set_id).encode())
    other = tuple((f"Доля {share.index} (другой набор).json", share.encode()) for share in foreign)
    cases = (
        ("intact", "Пять исправных долей", (a, b, c, d, e), True),
        ("one_error", "Одна изменённая доля", (bad_a, b, c, d, e), True),
        ("two_errors", "Две изменённые доли", (bad_a, bad_b, c, d, e), True),
        ("two_available", "Только две доли", (a, b), False),
        ("three_errors", "Три изменённые доли",
         (bad_a, bad_b, ("Доля 3 (изменена).json", altered(shares[2]).encode()), d, e), False),
        ("duplicate", "Повтор доли", (a, a, b, c, d), True),
        ("duplicates_below_threshold", "Три файла с одной долей", (a, a, a), False),
        ("index_conflict", "Два значения с индексом 1", (conflict, a, b, c, d), True),
        ("mixed_sets", "Две доли из другого набора", (*other[:2], c, d, e), True),
        ("foreign_only", "Пять долей из другого набора", other, False),
        ("bad_format", "Три ошибочных формата", (
            ("Усечённый JSON", b'{"v":'), ("Неверная кодировка", b"\xff"),
            ("Неизвестное поле", a[1].rstrip()[:-1] + b',"extra":0}'), c, d
        ), False),
        ("one_bad_format", "Усечённый файл доли", (("Усечённый JSON", b'{"v":'), b, c, d, e), True),
        ("bad_metadata", "Доля с изменённой меткой", (*other[1:3], marked, d, e), True),
    )
    damaged = (
        ("bad_body", "Изменён шифротекст", flip_at(container, HEADER.size)),
        ("bad_tag", "Изменён тег", flip_at(container, len(container) - 1)),
        ("bad_header", "Изменён идентификатор контейнера", flip_at(container, 5)),
        ("bad_version", "Неизвестная версия контейнера", flip_at(container, 4)),
    )
    return tuple(Scenario(name, title, container, files, ok) for name, title, files, ok in cases) + tuple(
        Scenario(name, title, raw, (a, b, c, d, e), False) for name, title, raw in damaged
    )
